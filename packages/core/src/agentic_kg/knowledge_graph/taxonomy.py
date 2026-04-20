"""
Seed taxonomy loader / exporter for Topic entities (E-1, Unit 4).

Provides pure parsing/validation that does not touch Neo4j
(``parse_taxonomy``, ``dump_taxonomy_to_yaml``) and
database-backed loader/exporter built on
``Neo4jRepository.merge_topic`` so imports are idempotent.

Taxonomy YAML schema (recursive)::

    - name: str             # required, length >= 2
      level: str            # required, one of domain/area/subtopic
      description: str      # optional
      source: str           # optional, default "manual"
      openalex_id: str      # optional
      children: list        # optional, same schema, one level deeper

Invariants enforced by ``parse_taxonomy``:

- every node has ``name`` and ``level``
- ``level`` is one of domain/area/subtopic
- a node whose ``level`` is *subtopic* cannot be a root — it must have
  a parent at area level (we detect this via path)
- a node whose ``level`` is *domain* cannot appear as a child
- names at the same (parent, level) must be unique
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import yaml

from agentic_kg.knowledge_graph.models import Topic, TopicLevel

if TYPE_CHECKING:
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

logger = logging.getLogger(__name__)


DEFAULT_TAXONOMY_PATH = (
    Path(__file__).parent / "data" / "seed_taxonomy.yml"
)

_VALID_LEVELS = {lvl.value for lvl in TopicLevel}

# Valid parent→child level transitions.
_ALLOWED_CHILD_LEVELS = {
    None: {"domain"},
    "domain": {"area"},
    "area": {"subtopic"},
    "subtopic": set(),  # Leaf level — no further nesting.
}


class TaxonomyError(ValueError):
    """Raised when taxonomy YAML is malformed or violates schema rules."""


# =============================================================================
# Parsing & validation (pure, no database)
# =============================================================================


def parse_taxonomy(source: str | Path | list | dict) -> list[dict]:
    """
    Parse and validate a taxonomy definition.

    Accepts either a YAML string, a filesystem path, or an already-parsed
    Python structure (useful for tests). Returns a list of root-level nodes
    (domains) with nested ``children``.

    Raises ``TaxonomyError`` on malformed input so callers see a single,
    actionable exception type regardless of which surface failed.
    """
    if isinstance(source, (str, Path)) and _looks_like_path(source):
        path = Path(source)
        if not path.exists():
            raise TaxonomyError(f"Taxonomy file not found: {path}")
        raw = yaml.safe_load(path.read_text())
    elif isinstance(source, str):
        raw = yaml.safe_load(source)
    else:
        raw = source

    if raw is None:
        raise TaxonomyError("Taxonomy is empty")
    if not isinstance(raw, list):
        raise TaxonomyError(
            f"Taxonomy root must be a list of domain nodes, got {type(raw).__name__}"
        )

    for node in raw:
        _validate_node(node, parent_level=None, path="(root)")

    return raw


def _looks_like_path(value: str | Path) -> bool:
    """Heuristic: treat as path if it's a Path or an existing-looking string."""
    if isinstance(value, Path):
        return True
    # A YAML string nearly always contains newlines or colons; a real path does not.
    return "\n" not in value and (value.endswith(".yml") or value.endswith(".yaml"))


def _validate_node(
    node: Any, parent_level: Optional[str], path: str
) -> None:
    if not isinstance(node, dict):
        raise TaxonomyError(
            f"{path}: node must be a mapping, got {type(node).__name__}"
        )

    name = node.get("name")
    level = node.get("level")

    if not isinstance(name, str) or len(name.strip()) < 2:
        raise TaxonomyError(
            f"{path}: 'name' is required and must be a string of length >= 2"
        )
    if level not in _VALID_LEVELS:
        raise TaxonomyError(
            f"{path} ({name}): 'level' must be one of {sorted(_VALID_LEVELS)}, got {level!r}"
        )

    allowed = _ALLOWED_CHILD_LEVELS[parent_level]
    if level not in allowed:
        raise TaxonomyError(
            f"{path} ({name}): a {level!r} node cannot appear under parent level {parent_level!r}"
        )

    children = node.get("children", [])
    if children is None:
        children = []
    if not isinstance(children, list):
        raise TaxonomyError(
            f"{path} ({name}): 'children' must be a list when present"
        )

    seen_child_names: set[tuple[str, str]] = set()
    for child in children:
        child_name = child.get("name") if isinstance(child, dict) else None
        child_level = child.get("level") if isinstance(child, dict) else None
        key = (child_level, child_name)
        if key in seen_child_names:
            raise TaxonomyError(
                f"{path} ({name}): duplicate child {child_level}:{child_name!r}"
            )
        seen_child_names.add(key)
        _validate_node(child, parent_level=level, path=f"{path} > {name}")


# =============================================================================
# Database-backed loader / exporter
# =============================================================================


def load_taxonomy(
    repo: "Neo4jRepository",
    source: str | Path | list | None = None,
    generate_embeddings: bool = True,
) -> dict[str, int]:
    """
    Load a taxonomy into Neo4j idempotently.

    Uses ``repo.merge_topic`` so re-running against the same graph is a no-op
    for unchanged nodes. Creates SUBTOPIC_OF edges automatically via
    ``merge_topic`` (which sets ``parent_id``).

    Args:
        repo: Repository instance.
        source: YAML string, path, list of dicts, or ``None`` to use the
            bundled seed taxonomy.
        generate_embeddings: When True, each merged topic gets an embedding.
            Set to False in tests to avoid the OpenAI dependency.

    Returns:
        Dict with counts {"created": int, "matched": int} summarizing the
        number of new nodes vs. pre-existing matches touched.
    """
    if source is None:
        source = DEFAULT_TAXONOMY_PATH

    taxonomy = parse_taxonomy(source)

    stats = {"created": 0, "matched": 0}
    for root in taxonomy:
        _load_subtree(
            repo=repo,
            node=root,
            parent_id=None,
            stats=stats,
            generate_embeddings=generate_embeddings,
        )

    logger.info(
        f"Loaded taxonomy: {stats['created']} created, {stats['matched']} matched"
    )
    return stats


def _load_subtree(
    repo: "Neo4jRepository",
    node: dict,
    parent_id: Optional[str],
    stats: dict[str, int],
    generate_embeddings: bool,
) -> str:
    """Merge a subtree rooted at ``node``; return the node's id."""
    topic = Topic(
        name=node["name"],
        description=node.get("description"),
        level=TopicLevel(node["level"]),
        parent_id=parent_id,
        source=node.get("source", "manual"),
        openalex_id=node.get("openalex_id"),
    )

    # Probe first so we can report created vs. matched — merge_topic itself
    # is a pure MERGE so either outcome is safe.
    existed_before = _topic_exists(repo, topic.name, topic.level, parent_id)

    merged = repo.merge_topic(topic, generate_embedding=generate_embeddings)

    if existed_before:
        stats["matched"] += 1
    else:
        stats["created"] += 1

    for child in node.get("children") or []:
        _load_subtree(
            repo=repo,
            node=child,
            parent_id=merged.id,
            stats=stats,
            generate_embeddings=generate_embeddings,
        )

    return merged.id


def _topic_exists(
    repo: "Neo4jRepository",
    name: str,
    level: TopicLevel,
    parent_id: Optional[str],
) -> bool:
    """Return True if a Topic with identical (name, level, parent_id) exists."""
    def _check(tx, nm, lv, pid):
        result = tx.run(
            """
            MATCH (t:Topic {name: $name, level: $level, parent_id: $parent_id})
            RETURN t.id
            LIMIT 1
            """,
            name=nm,
            level=lv,
            parent_id=pid,
        )
        return result.single() is not None

    with repo.session() as session:
        return session.execute_read(
            lambda tx: _check(tx, name, level.value, parent_id)
        )


def export_taxonomy(repo: "Neo4jRepository") -> list[dict]:
    """
    Export the taxonomy currently stored in Neo4j as a nested list of dicts.

    Shape matches the YAML schema (``name``, ``level``, ``description``,
    ``source``, ``openalex_id``, ``children``). Embeddings are not exported
    — the loader regenerates them on import so YAML stays human-readable.
    """
    def build(topic: Topic) -> dict:
        children = repo.get_topic_children(topic.id)
        payload: dict[str, Any] = {
            "name": topic.name,
            "level": topic.level.value,
        }
        if topic.description:
            payload["description"] = topic.description
        if topic.source and topic.source != "manual":
            payload["source"] = topic.source
        if topic.openalex_id:
            payload["openalex_id"] = topic.openalex_id
        if children:
            payload["children"] = [build(c) for c in children]
        return payload

    roots = repo.get_topics_by_level(TopicLevel.DOMAIN)
    return [build(r) for r in roots]


def dump_taxonomy_to_yaml(taxonomy: list[dict], path: str | Path) -> None:
    """Write a taxonomy structure to ``path`` as pretty YAML."""
    text = yaml.safe_dump(
        taxonomy,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    Path(path).write_text(text)


def taxonomy_to_yaml(taxonomy: list[dict]) -> str:
    """Serialize a taxonomy structure to a YAML string."""
    return yaml.safe_dump(
        taxonomy,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
