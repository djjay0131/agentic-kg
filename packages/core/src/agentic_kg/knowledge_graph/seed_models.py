"""Canonical Model seed loader (E-3, Unit 6).

Loads a YAML file of curated Models into the knowledge graph with
``is_canonical=True``. The loader is idempotent — re-running against the
same graph merges existing nodes via embedding dedup rather than creating
duplicates. Adding entries to the YAML lands via PR review; the verify-
time eval (AC-10) re-runs on each change.

YAML schema::

    - name: BERT
      description: optional longer text used for embedding
      aliases: [bert-base, bert-large]
      architecture: transformer
      model_type: language_model
      year_introduced: 2018
      introducing_paper_doi: 10.18653/v1/N19-1423
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

import yaml
from pydantic import BaseModel, Field, ValidationError

if TYPE_CHECKING:
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

logger = logging.getLogger(__name__)


DEFAULT_SEED_PATH = Path(__file__).parent / "data" / "seed_models.yml"


class SeedModelEntry(BaseModel):
    """A single canonical Model entry in the seed YAML."""

    name: str = Field(..., min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=400)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    architecture: Optional[str] = None
    model_type: Optional[str] = None
    year_introduced: Optional[int] = None
    introducing_paper_doi: Optional[str] = None


def parse_seed_models(source: Union[str, Path, list]) -> list[SeedModelEntry]:
    """Parse a seed YAML (or in-memory list) into validated entries.

    Args:
        source: YAML string, Path to a YAML file, or an already-parsed
            list of dicts.

    Returns:
        List of validated ``SeedModelEntry`` objects in source order.

    Raises:
        ValueError: For empty input, non-list root, duplicate names, or
            entries that fail Pydantic validation. The error message
            names the offending entry by index when possible.
    """
    if isinstance(source, Path) or (
        isinstance(source, str) and (
            source.endswith(".yml") or source.endswith(".yaml")
        ) and "\n" not in source
    ):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Seed file not found: {path}")
        raw = yaml.safe_load(path.read_text())
    elif isinstance(source, str):
        raw = yaml.safe_load(source)
    else:
        raw = source

    if raw is None:
        raise ValueError("Seed YAML is empty")
    if not isinstance(raw, list):
        raise ValueError(
            f"Seed YAML root must be a list of entries, got {type(raw).__name__}"
        )

    entries: list[SeedModelEntry] = []
    seen_names: set[str] = set()
    for idx, item in enumerate(raw):
        try:
            entry = SeedModelEntry(**(item or {}))
        except ValidationError as e:
            raise ValueError(
                f"Seed entry {idx} failed validation: {e}"
            ) from e
        if entry.name in seen_names:
            raise ValueError(f"duplicate entry name {entry.name!r} at index {idx}")
        seen_names.add(entry.name)
        entries.append(entry)
    return entries


def load_seed_models(
    repo: "Neo4jRepository",
    path: Union[str, Path] = DEFAULT_SEED_PATH,
) -> dict[str, int]:
    """Idempotently load curated Models into the knowledge graph.

    Each entry is passed through ``create_or_merge_model`` with
    ``is_canonical=True``. On first load, every entry produces a new node;
    on re-load, every entry merges into its existing node (alias-merge,
    description fill-in). Counts the two outcomes for the caller.

    Args:
        repo: Neo4j repository.
        path: Path to the YAML file. Defaults to the bundled seed.

    Returns:
        ``{"created": int, "merged": int}`` summary.
    """
    entries = parse_seed_models(Path(path))

    stats = {"created": 0, "merged": 0}
    for entry in entries:
        _, created = repo.create_or_merge_model(
            name=entry.name,
            description=entry.description,
            aliases=list(entry.aliases),
            architecture=entry.architecture,
            model_type=entry.model_type,
            year_introduced=entry.year_introduced,
            introducing_paper_doi=entry.introducing_paper_doi,
            is_canonical=True,
        )
        if created:
            stats["created"] += 1
        else:
            stats["merged"] += 1

    logger.info(
        f"Loaded seed models: {stats['created']} created, "
        f"{stats['merged']} merged"
    )
    return stats
