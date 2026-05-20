#!/usr/bin/env python3
"""
Emit YAML data files for the Jekyll site.

Replaces the previous HTML-manipulating generator. Reads from
``llm/features/BACKLOG.md``, ``construction/sprints/*.md``, and the
fenced ``# docs-stats`` YAML block in ``llm/memory_bank/activeContext.md``
— emits ``docs/_data/{backlog,sprints,status}.yml`` for Liquid templates
to consume.

Usage:
    python .github/scripts/generate_site_data.py
    python .github/scripts/generate_site_data.py --root /path/to/repo

Exits non-zero when:
  * The ``# docs-stats`` block is missing or fails Pydantic validation
    (AC-15).
  * Fewer than 50% of BACKLOG.md's detected table rows parse into
    feature records (AC-10).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger("generate_site_data")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DOCS_STATS_MIN_PARSE_RATIO = 0.5
DOCS_STATS_FENCE = "```"
DOCS_STATS_FENCE_LANG = "yaml"
DOCS_STATS_MARKER = "# docs-stats"

_ID_PATTERN = re.compile(r"^(~~)?\s*([A-Z]+-\d+[a-z]?)\s*(~~)?$")
_CATEGORY_HEADING = re.compile(
    r"^##\s+Category\s+\d+\s*[:—\-]\s*(?P<name>.+?)\s*$"
)
_TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")
_TABLE_SEPARATOR = re.compile(r"^\|[\s:\-|]+\|\s*$")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DocsStats(BaseModel):
    """Authoritative site-dashboard metrics extracted from activeContext."""

    last_updated: str = Field(..., min_length=1)
    graph_nodes: int = Field(..., ge=0)
    graph_edges: int = Field(..., ge=0)
    problem_mentions: int = Field(..., ge=0)
    problem_concepts: int = Field(..., ge=0)
    sanity_checks: str = Field(..., min_length=1)
    completed_sprints: int = Field(..., ge=0)
    tests_passing: int = Field(..., ge=0)

    @field_validator("last_updated", mode="before")
    @classmethod
    def _coerce_date_to_iso(cls, v: object) -> object:
        # YAML auto-parses `2026-04-16` into a `datetime.date` — accept that
        # transparently and re-emit it as ISO-8601 so downstream Jekyll
        # templates see a consistent string.
        if isinstance(v, (_dt.date, _dt.datetime)):
            return v.isoformat()
        return v


class DocsStatsError(ValueError):
    """Raised when the docs-stats block is missing, malformed, or invalid."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def backlog(self) -> Path:
        return self.root / "llm/features/BACKLOG.md"

    @property
    def active_context(self) -> Path:
        return self.root / "llm/memory_bank/activeContext.md"

    @property
    def sprints_dir(self) -> Path:
        return self.root / "construction/sprints"

    @property
    def out_dir(self) -> Path:
        return self.root / "docs/_data"

    @classmethod
    def from_root(cls, root: Optional[Path] = None) -> "Paths":
        if root is None:
            # Script lives at <root>/.github/scripts/. parents[2] is the repo root.
            root = Path(__file__).resolve().parents[2]
        return cls(root=root)


# ---------------------------------------------------------------------------
# docs-stats
# ---------------------------------------------------------------------------


def extract_docs_stats_block(text: str) -> str:
    """
    Return the YAML body of the fenced ``# docs-stats`` block.

    A valid block looks like::

        ```yaml
        # docs-stats
        key: value
        ```

    The first non-empty line inside the fence must be ``# docs-stats``,
    which lets us ignore prose that happens to mention ``# docs-stats``
    elsewhere in the file.
    """
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i].strip()
        if line == f"{DOCS_STATS_FENCE}{DOCS_STATS_FENCE_LANG}":
            # Scan past blank lines inside the fence to find the marker.
            j = i + 1
            marker_seen = False
            while j < n and lines[j].strip() != DOCS_STATS_FENCE:
                stripped = lines[j].strip()
                if not marker_seen and stripped:
                    marker_seen = stripped == DOCS_STATS_MARKER
                    if not marker_seen:
                        break  # first content line wasn't the marker; skip block
                j += 1
            if marker_seen:
                if j >= n:
                    raise DocsStatsError(
                        "docs-stats block is not terminated with a closing '```'"
                    )
                return "\n".join(lines[i + 1 : j]).strip()
        i += 1
    raise DocsStatsError(
        "docs-stats block not found: expected a fenced YAML block whose "
        f"first content line is '{DOCS_STATS_MARKER}'"
    )


def load_docs_stats(path: Path) -> DocsStats:
    """Read the activeContext file and parse-validate its docs-stats block."""
    if not path.exists():
        raise DocsStatsError(f"activeContext file not found: {path}")
    try:
        body = extract_docs_stats_block(path.read_text(encoding="utf-8"))
    except DocsStatsError as e:
        raise DocsStatsError(f"{path}: {e}") from None

    try:
        data = yaml.safe_load(body) or {}
    except yaml.YAMLError as e:
        raise DocsStatsError(f"{path}: docs-stats block is not valid YAML: {e}") from None
    if not isinstance(data, dict):
        raise DocsStatsError(
            f"{path}: docs-stats block must be a mapping, got {type(data).__name__}"
        )

    try:
        return DocsStats.model_validate(data)
    except ValidationError as e:
        raise DocsStatsError(f"{path}: docs-stats block failed validation: {e}") from None


# ---------------------------------------------------------------------------
# BACKLOG.md
# ---------------------------------------------------------------------------


def _split_row(line: str) -> list[str]:
    inner = _TABLE_ROW.match(line).group(1)
    return [cell.strip() for cell in inner.split("|")]


def _slug_header(cell: str) -> str:
    cleaned = cell.strip().lower()
    cleaned = re.sub(r"[^\w\s-]", "", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "col"


def _clean_markdown(cell: str) -> str:
    # Strip strikethrough, bold, italic markers used for status wording.
    return re.sub(r"[*~]+", "", cell).strip()


def parse_backlog(path: Path) -> list[dict]:
    """
    Parse the BACKLOG markdown and return a flat list of feature records.

    Each record has: id, resolved, category, feature, status, priority,
    notes (when the table exposes those columns — variable-column tables
    fall back to generic ``col_N`` keys so unusual categories still render).

    Warns on every table row that looks like a feature row but doesn't
    yield a parseable ID. When more than half the detected rows fail to
    parse, raises ``ValueError`` so the caller can fail CI.
    """
    if not path.exists():
        logger.warning("BACKLOG.md not found at %s; emitting empty list", path)
        return []

    category = None
    headers: list[str] = []
    detected = 0
    parsed: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()

        heading = _CATEGORY_HEADING.match(line)
        if heading:
            category = heading.group("name").strip()
            headers = []
            continue

        if not _TABLE_ROW.match(line):
            continue

        if _TABLE_SEPARATOR.match(line):
            continue

        cells = _split_row(line)

        # Header row: the first cell's literal text is "#".
        if cells and cells[0].strip() == "#":
            headers = [_slug_header(c) for c in cells]
            continue

        # Looks like a feature row (first cell contains an ID-ish token).
        detected += 1
        id_match = _ID_PATTERN.match(cells[0])
        if not id_match:
            logger.warning(
                "Skipping unparseable backlog row in category %r: %r",
                category,
                line,
            )
            continue

        record = {
            "id": id_match.group(2),
            "resolved": bool(id_match.group(1) or id_match.group(3)),
            "category": category,
        }
        # Fill named columns from the header; everything else goes to col_N.
        for idx, cell in enumerate(cells[1:], start=1):
            value = _clean_markdown(cell)
            if idx < len(headers) and headers[idx] not in record:
                record[headers[idx]] = value
            else:
                record[f"col_{idx}"] = value
        parsed.append(record)

    if detected and (len(parsed) / detected) < DOCS_STATS_MIN_PARSE_RATIO:
        raise ValueError(
            f"Only {len(parsed)} of {detected} BACKLOG rows parsed; "
            f"below minimum ratio {DOCS_STATS_MIN_PARSE_RATIO:.2f}"
        )

    return parsed


# ---------------------------------------------------------------------------
# Sprints
# ---------------------------------------------------------------------------


_SPRINT_HEADING = re.compile(r"^#\s+Sprint\s+(?P<num>\d+)\s*:\s*(?P<name>.+?)\s*$")
_SPRINT_STATUS = re.compile(r"^\*\*Status:\*\*\s*(?P<status>.+?)\s*$", re.MULTILINE)


def parse_sprints(sprints_dir: Path) -> list[dict]:
    """Enumerate ``sprint-*.md`` files and pull their number / name / status."""
    if not sprints_dir.exists():
        logger.warning("sprints dir not found at %s; emitting empty list", sprints_dir)
        return []

    items: list[dict] = []
    for path in sorted(sprints_dir.glob("sprint-*.md")):
        text = path.read_text(encoding="utf-8")
        title_line = text.splitlines()[0] if text else ""
        title_match = _SPRINT_HEADING.match(title_line)
        if not title_match:
            logger.warning("Sprint file %s has no '# Sprint N: name' heading", path.name)
            continue
        status_match = _SPRINT_STATUS.search(text)
        items.append(
            {
                "number": int(title_match.group("num")),
                "name": title_match.group("name").strip(),
                "status": status_match.group("status").strip() if status_match else "Unknown",
                "filename": path.name,
            }
        )
    return items


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def generate(paths: Paths) -> dict[str, Path]:
    """Run every emitter and return the output paths keyed by data file name."""
    stats = load_docs_stats(paths.active_context)
    backlog = parse_backlog(paths.backlog)
    sprints = parse_sprints(paths.sprints_dir)

    outputs = {
        "status.yml": paths.out_dir / "status.yml",
        "backlog.yml": paths.out_dir / "backlog.yml",
        "sprints.yml": paths.out_dir / "sprints.yml",
    }
    write_yaml(outputs["status.yml"], stats.model_dump())
    write_yaml(outputs["backlog.yml"], {"items": backlog})
    write_yaml(outputs["sprints.yml"], {"items": sprints})
    return outputs


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repo root (defaults to the parent of .github/scripts/)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        format="%(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    args = _parse_args(argv)
    paths = Paths.from_root(args.root)
    try:
        outputs = generate(paths)
    except DocsStatsError as e:
        logger.error("docs-stats validation failed: %s", e)
        return 2
    except ValueError as e:
        logger.error("backlog parse failed: %s", e)
        return 3

    for name, path in outputs.items():
        logger.info("wrote %s", path.relative_to(paths.root))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI, not tests
    sys.exit(main())
