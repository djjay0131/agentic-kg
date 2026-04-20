"""
Schema v2 → v3 migration: convert ``domain`` strings to Topic entities.

Pre-condition (v2): Problem, ProblemMention, and ProblemConcept carry a
flat ``domain`` string like ``"NLP"``.

Post-condition (v3): every distinct former ``domain`` value exists as a
``Topic`` node with ``source = 'migrated'`` and ``level = 'area'``, a
``BELONGS_TO`` edge links each source node to its Topic, and the
``domain`` property has been removed from the source nodes.

Step 2 (``dedup_migrated_topics``) embeds each migrated Topic and merges
any pair whose cosine similarity is above ``MERGE_THRESHOLD`` — this
catches synonyms like ``"NLP"`` vs ``"Natural Language Processing"``.

The migration is idempotent. Running it again against a partially or
fully migrated graph is safe:

- MERGE creates missing Topic nodes / BELONGS_TO edges, touches nothing
  that already exists.
- REMOVE is a no-op on nodes whose ``domain`` has already been cleared.
- Dedup never re-merges already-merged nodes because the source node of
  the losing topic has been deleted.

The AC-12 calibration study will tune ``MERGE_THRESHOLD`` once we have
real migration data; ``0.9`` is the starting value from the spec.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

logger = logging.getLogger(__name__)


# Starting threshold for embedding-based dedup (AC-12 calibration pending).
MERGE_THRESHOLD = 0.90


# Cypher that creates a Topic per distinct domain + BELONGS_TO edges.
# Uses parameterized source set so tests can exercise a subset of labels.
_MIGRATE_CYPHER = """
MATCH (n)
WHERE n.domain IS NOT NULL
  AND any(lbl IN labels(n) WHERE lbl IN $labels)
WITH DISTINCT n.domain AS domain_name
MERGE (t:Topic {name: domain_name, level: 'area', parent_id: NULL})
ON CREATE SET
    t.id = randomUUID(),
    t.source = 'migrated',
    t.problem_count = 0,
    t.paper_count = 0,
    t.created_at = $now,
    t.updated_at = $now
WITH collect({topic: t, name: domain_name}) AS pairs
UNWIND pairs AS pair
MATCH (src)
WHERE src.domain = pair.name
  AND any(lbl IN labels(src) WHERE lbl IN $labels)
MERGE (src)-[:BELONGS_TO]->(pair.topic)
REMOVE src.domain
RETURN count(DISTINCT pair.topic) AS topics_created_or_matched,
       count(src) AS sources_migrated
"""


_DEFAULT_SOURCE_LABELS: tuple[str, ...] = (
    "Problem",
    "ProblemMention",
    "ProblemConcept",
)


@dataclass
class MigrationReport:
    """Summary of a migration run (both steps)."""

    topics_touched: int
    sources_migrated: int
    dedup_merges: int
    threshold: float


def migrate_domains_to_topics(
    repo: "Neo4jRepository",
    labels: tuple[str, ...] = _DEFAULT_SOURCE_LABELS,
) -> dict[str, int]:
    """
    Step 1: create Topic nodes and BELONGS_TO edges from legacy ``domain``.

    Returns a dict ``{"topics_touched", "sources_migrated"}`` where
    *topics_touched* counts Topic nodes created or matched, and
    *sources_migrated* counts source nodes that had a BELONGS_TO edge
    created (MERGE-idempotent, so a re-run returns 0s once nothing has
    ``domain`` left).
    """
    def _run(tx, lbls: list[str], now: str) -> dict:
        result = tx.run(_MIGRATE_CYPHER, labels=lbls, now=now)
        record = result.single()
        if record is None:
            return {"topics_touched": 0, "sources_migrated": 0}
        return {
            "topics_touched": record["topics_created_or_matched"],
            "sources_migrated": record["sources_migrated"],
        }

    with repo.session() as session:
        counts = session.execute_write(
            lambda tx: _run(
                tx,
                list(labels),
                datetime.now(timezone.utc).isoformat(),
            )
        )

    logger.info(
        f"Domain→Topic migration: touched {counts['topics_touched']} topics, "
        f"migrated {counts['sources_migrated']} source edges"
    )
    return counts


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length non-zero vectors."""
    if len(a) != len(b):
        raise ValueError(
            f"Embedding length mismatch: {len(a)} vs {len(b)}"
        )
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _pick_canonical(a: dict, b: dict) -> tuple[dict, dict]:
    """
    Given two near-duplicate Topic records, return (keep, drop).

    Prefers the one with the longer (more descriptive) name; on tie,
    prefers the older one. This gives stable behavior across reruns.
    """
    if len(a["name"]) != len(b["name"]):
        keep, drop = (a, b) if len(a["name"]) > len(b["name"]) else (b, a)
    else:
        a_ts = a.get("created_at") or ""
        b_ts = b.get("created_at") or ""
        keep, drop = (a, b) if a_ts <= b_ts else (b, a)
    return keep, drop


def _ensure_embeddings(
    repo: "Neo4jRepository",
    source: str = "migrated",
) -> int:
    """Embed any ``source='migrated'`` Topic missing an embedding."""
    from agentic_kg.knowledge_graph.embeddings import generate_topic_embedding

    def _pick(tx, src: str) -> list[dict]:
        result = tx.run(
            """
            MATCH (t:Topic {source: $src})
            WHERE t.embedding IS NULL
            RETURN t.id AS id, t.name AS name, t.description AS description
            """,
            src=src,
        )
        return [dict(r) for r in result]

    with repo.session() as session:
        needs = session.execute_read(lambda tx: _pick(tx, source))

    count = 0
    for row in needs:
        try:
            emb = generate_topic_embedding(row["name"], row.get("description"))
        except Exception as e:
            logger.warning(
                f"Failed to embed migrated topic {row['id']} ({row['name']}): {e}"
            )
            continue
        with repo.session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    """
                    MATCH (t:Topic {id: $id})
                    SET t.embedding = $emb, t.updated_at = $now
                    """,
                    id=row["id"],
                    emb=emb,
                    now=datetime.now(timezone.utc).isoformat(),
                )
            )
        count += 1

    if count:
        logger.info(f"Embedded {count} migrated topic(s)")
    return count


def _fetch_topics_for_dedup(
    repo: "Neo4jRepository", source: str
) -> list[dict]:
    """Return migrated topics with embeddings, ordered by creation time."""
    def _fetch(tx, src: str) -> list[dict]:
        result = tx.run(
            """
            MATCH (t:Topic {source: $src})
            WHERE t.embedding IS NOT NULL
            RETURN t.id AS id, t.name AS name,
                   t.embedding AS embedding,
                   t.created_at AS created_at
            ORDER BY t.created_at
            """,
            src=src,
        )
        return [dict(r) for r in result]

    with repo.session() as session:
        return session.execute_read(lambda tx: _fetch(tx, source))


def _merge_duplicate_topic(
    repo: "Neo4jRepository", keep_id: str, drop_id: str
) -> None:
    """
    Move every BELONGS_TO / RESEARCHES edge from ``drop`` to ``keep`` and
    delete ``drop``. Recomputes ``problem_count`` and ``paper_count`` on
    ``keep`` after the merge.
    """
    def _merge(tx, k: str, d: str, now: str) -> None:
        tx.run(
            """
            MATCH (src)-[r:BELONGS_TO]->(drop:Topic {id: $drop_id})
            MATCH (keep:Topic {id: $keep_id})
            MERGE (src)-[:BELONGS_TO]->(keep)
            DELETE r
            """,
            keep_id=k,
            drop_id=d,
        )
        tx.run(
            """
            MATCH (src)-[r:RESEARCHES]->(drop:Topic {id: $drop_id})
            MATCH (keep:Topic {id: $keep_id})
            MERGE (src)-[:RESEARCHES]->(keep)
            DELETE r
            """,
            keep_id=k,
            drop_id=d,
        )
        tx.run(
            "MATCH (t:Topic {id: $id}) DETACH DELETE t",
            id=d,
        )
        # Recompute counts on the survivor.
        tx.run(
            """
            MATCH (keep:Topic {id: $keep_id})
            OPTIONAL MATCH (keep)<-[:BELONGS_TO]-(p)
            WHERE p:Problem OR p:ProblemMention OR p:ProblemConcept
            WITH keep, count(DISTINCT p) AS pc
            OPTIONAL MATCH (keep)<-[:RESEARCHES]-(paper:Paper)
            WITH keep, pc, count(DISTINCT paper) AS pac
            SET keep.problem_count = pc,
                keep.paper_count = pac,
                keep.updated_at = $now
            """,
            keep_id=k,
            now=now,
        )

    with repo.session() as session:
        session.execute_write(
            lambda tx: _merge(
                tx, keep_id, drop_id, datetime.now(timezone.utc).isoformat()
            )
        )


def dedup_migrated_topics(
    repo: "Neo4jRepository",
    threshold: float = MERGE_THRESHOLD,
    source: str = "migrated",
) -> list[dict]:
    """
    Merge migrated Topics whose embeddings are above the cosine threshold.

    Returns a list of ``{"kept_id", "dropped_id", "kept_name",
    "dropped_name", "score"}`` entries describing each merge.

    Side effects:
    - Embeds any migrated Topic still missing an embedding.
    - Rewires BELONGS_TO / RESEARCHES edges onto the canonical Topic.
    - Recomputes denormalized counts on the survivor.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0, 1]; got {threshold}")

    _ensure_embeddings(repo, source=source)

    merges: list[dict] = []
    # Loop until no more merges happen — accounts for A≈B and B≈C both
    # triggering cascading merges.
    while True:
        topics = _fetch_topics_for_dedup(repo, source=source)
        if len(topics) < 2:
            break

        pair_found = False
        for i in range(len(topics)):
            for j in range(i + 1, len(topics)):
                a, b = topics[i], topics[j]
                score = _cosine_similarity(a["embedding"], b["embedding"])
                if score < threshold:
                    continue
                keep, drop = _pick_canonical(a, b)
                _merge_duplicate_topic(
                    repo, keep_id=keep["id"], drop_id=drop["id"]
                )
                merges.append(
                    {
                        "kept_id": keep["id"],
                        "kept_name": keep["name"],
                        "dropped_id": drop["id"],
                        "dropped_name": drop["name"],
                        "score": score,
                    }
                )
                logger.info(
                    f"Dedup merge: kept '{keep['name']}', dropped "
                    f"'{drop['name']}' (score={score:.3f})"
                )
                pair_found = True
                break
            if pair_found:
                break
        if not pair_found:
            break

    logger.info(f"Dedup complete: {len(merges)} merge(s) at threshold={threshold}")
    return merges


def run_migration(
    repo: "Neo4jRepository",
    threshold: float = MERGE_THRESHOLD,
    labels: tuple[str, ...] = _DEFAULT_SOURCE_LABELS,
) -> MigrationReport:
    """
    Orchestrate the full v2 → v3 migration (step 1 + step 2 + counts).

    Safe to run repeatedly. Re-runs after a clean migration are no-ops
    (step 1 has nothing to do, step 2 finds no duplicates, count drift
    is nil).
    """
    counts = migrate_domains_to_topics(repo, labels=labels)
    merges = dedup_migrated_topics(repo, threshold=threshold)
    # Step 1 creates Topics with problem_count=0; a single reconciliation
    # pass at the end brings the denormalized counts in sync with the
    # BELONGS_TO / RESEARCHES edges we just created.
    repo.reconcile_topic_counts()
    return MigrationReport(
        topics_touched=counts["topics_touched"],
        sources_migrated=counts["sources_migrated"],
        dedup_merges=len(merges),
        threshold=threshold,
    )
