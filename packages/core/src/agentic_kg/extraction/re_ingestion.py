"""Purge-then-rewrite for re-ingestion (E-8 AC-13).

Re-ingesting a paper that already exists in the graph is implemented as
a hard purge of the paper's extraction footprint, then a fresh
extraction run. The guardrail prevents accidentally destroying
non-extraction state (manual ``SOLVED_BY`` edges, human-curated tags,
inbound edges from other papers' pipelines).

Caller responsibilities:

- The CLI ``--force-rewrite`` flag is what sets ``force_rewrite=True``
  here. Without it, a paper with any non-extraction incident edges is
  refused.
- Shared ``Topic`` and ``ResearchConcept`` nodes are intentionally NOT
  deleted — they may be referenced by other papers. Re-extraction will
  recreate the appropriate edges and dedup-merge any concepts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

logger = logging.getLogger(__name__)


class PurgeReport(BaseModel):
    """Outcome of a single paper's purge."""

    paper_doi: str
    problems_deleted: int = 0
    mentions_deleted: int = 0
    edges_deleted: int = 0
    collateral_edge_loss: list[dict] = Field(default_factory=list)


class PurgeBlocked(Exception):
    """Raised when non-extraction edges block a non-forced purge."""

    def __init__(self, paper_doi: str, blocking_edges: list[dict]):
        self.paper_doi = paper_doi
        self.blocking_edges = blocking_edges
        labels = ", ".join(
            f"{e['problem_id']} -[{e['relationship_type']}]-> {e['other_node']}"
            for e in blocking_edges
        )
        super().__init__(
            f"Refusing to purge {paper_doi}: non-extraction edges present "
            f"[{labels}]. Re-run with --force-rewrite to override."
        )


# Edges considered "extraction footprint" — safe to delete on re-ingestion.
_EXTRACTION_EDGE_TYPES = frozenset(
    {
        "EXTRACTED_FROM",
        "BELONGS_TO",
        "DISCUSSES",
        "INVOLVES_CONCEPT",
        "HAS_TOPIC",
        "INSTANCE_OF",
        "AUTHORED_BY",  # Paper→Author — preserved on Paper but rewritten via importer
    }
)


def _find_non_extraction_edges(
    repo: "Neo4jRepository", paper_doi: str
) -> list[dict]:
    """Return non-extraction incident edges on this paper's Problem nodes."""
    query = """
    MATCH (paper:Paper {doi: $doi})<-[:EXTRACTED_FROM]-(:ProblemMention)
          -[:INSTANCE_OF]->(pc:ProblemConcept)<-[:INSTANCE_OF]-(other_mention:ProblemMention)
    OPTIONAL MATCH (pc)-[r]-(other)
    WHERE NOT type(r) IN $extraction_edges
    WITH pc, r, other
    WHERE r IS NOT NULL
    RETURN pc.id AS problem_id, type(r) AS relationship_type,
           coalesce(other.id, other.doi, '?') AS other_node
    // tagged: non_extraction_edges
    """
    with repo.session() as session:
        result = session.run(
            query,
            doi=paper_doi,
            extraction_edges=list(_EXTRACTION_EDGE_TYPES),
        )
        return [dict(record) for record in result]


def purge_paper_extraction(
    repo: "Neo4jRepository",
    paper_doi: str,
    *,
    force_rewrite: bool = False,
) -> PurgeReport:
    """Purge a paper's extraction footprint.

    Args:
        repo: Neo4j repository.
        paper_doi: Paper to purge.
        force_rewrite: If True, proceed even when non-extraction edges exist
            on this paper's Problem nodes. Reports forced losses in the
            return value so the verify log captures the data damage.

    Raises:
        PurgeBlocked: If ``force_rewrite=False`` and non-extraction edges
            are present.
    """
    blocking = _find_non_extraction_edges(repo, paper_doi)
    if blocking and not force_rewrite:
        raise PurgeBlocked(paper_doi=paper_doi, blocking_edges=blocking)

    report = PurgeReport(paper_doi=paper_doi)
    if blocking:
        report.collateral_edge_loss = blocking
        logger.warning(
            "Forced re-ingestion of %s loses %d non-extraction edges",
            paper_doi,
            len(blocking),
        )

    with repo.session() as session:
        # 1. Drop BELONGS_TO from Paper → Topic (E-1) and HAS_TOPIC from Problem nodes.
        session.run(
            """
            MATCH (p:Paper {doi: $doi})-[r:BELONGS_TO]->(:Topic)
            DELETE r
            """,
            doi=paper_doi,
        )
        session.run(
            """
            MATCH (p:Paper {doi: $doi})<-[:EXTRACTED_FROM]-(:ProblemMention)
                  -[:INSTANCE_OF]->(pc:ProblemConcept)-[r:HAS_TOPIC]->(:Topic)
            DELETE r
            """,
            doi=paper_doi,
        )

        # 2. Drop DISCUSSES from Paper → ResearchConcept (E-2).
        session.run(
            """
            MATCH (p:Paper {doi: $doi})-[r:DISCUSSES]->(:ResearchConcept)
            DELETE r
            """,
            doi=paper_doi,
        )

        # 3. Drop INVOLVES_CONCEPT from this paper's ProblemConcept → ResearchConcept.
        session.run(
            """
            MATCH (p:Paper {doi: $doi})<-[:EXTRACTED_FROM]-(:ProblemMention)
                  -[:INSTANCE_OF]->(pc:ProblemConcept)-[r:INVOLVES_CONCEPT]->(:ResearchConcept)
            DELETE r
            """,
            doi=paper_doi,
        )

        # 4. Delete EXTRACTED_FROM edges (Paper ← ProblemMention).
        session.run(
            """
            MATCH (:ProblemMention)-[r:EXTRACTED_FROM]->(p:Paper {doi: $doi})
            DELETE r
            """,
            doi=paper_doi,
        )

        # 5. Detach-delete ProblemMention nodes attributable to this paper.
        result = session.run(
            """
            MATCH (m:ProblemMention)
            WHERE m.paper_doi = $doi
            DETACH DELETE m
            RETURN count(m) AS deleted_mentions
            """,
            doi=paper_doi,
        )
        row = result.single()
        if row is not None:
            report.mentions_deleted = row.get("deleted_mentions", 0) or 0

        # 6. Delete Problem nodes whose only EXTRACTED_FROM was this paper.
        #    (Problems may be shared via other mentions; we only delete
        #    those with no surviving incident edges of any extraction type.)
        result = session.run(
            """
            MATCH (p:Problem)
            WHERE NOT EXISTS {
                MATCH (p)<-[:INSTANCE_OF|:EXTRACTED_FROM]-(:ProblemMention)
            }
            AND p.paper_doi = $doi
            DETACH DELETE p
            RETURN count(p) AS deleted_problems
            """,
            doi=paper_doi,
        )
        row = result.single()
        if row is not None:
            report.problems_deleted = row.get("deleted_problems", 0) or 0

        # 7. Clear the Paper node's extraction status — re-extraction will rewrite.
        session.run(
            """
            MATCH (p:Paper {doi: $doi})
            SET p.extraction_incomplete = false,
                p.extraction_failed_extractors = ''
            """,
            doi=paper_doi,
        )

    return report
