"""The deterministic nightly query planner.

Given the catalog and a date, pick tonight's queries such that:

* the run stays within ``max_papers`` (the sum of the selected queries'
  ``limit`` never exceeds it),
* over consecutive nights every enabled query is covered, and
* the same date always yields the same plan.

The rotation is the whole trick. Enabled queries are sorted by descending
``weight`` and then by ``id`` (a total order, so ties are stable), and the
sorted list is rotated by ``day_index % n``. The planner then walks the rotated
list and keeps a query while it fits the remaining budget. Because the rotation
advances one position per day, every query reaches the front of the list once
every ``n`` nights, and the front query always fits (a single ``limit`` is at
most ``max_papers`` whenever the catalog is valid for that budget), so full
coverage follows without any randomness. ``weight`` decides who is served first
when the budget cannot hold everyone.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Sequence

from agentic_kg.pipeline.catalog import QueryCatalog, QuerySpec, load_catalog

#: Default paper budget for one night. Matches the Terraform default for
#: ``nightly_max_papers`` and the ``NIGHTLY_MAX_PAPERS`` Job env var.
DEFAULT_MAX_PAPERS = 50

#: Env var names the CLI/Job read.
ENV_MAX_PAPERS = "NIGHTLY_MAX_PAPERS"


@dataclass(frozen=True)
class PlannedQuery:
    """One query selected for tonight, in execution order."""

    query_id: str
    query: str
    topic: str
    limit: int
    weight: float

    @classmethod
    def from_spec(cls, spec: QuerySpec) -> "PlannedQuery":
        return cls(
            query_id=spec.id,
            query=spec.query,
            topic=spec.topic,
            limit=spec.limit,
            weight=spec.weight,
        )

    def to_dict(self) -> dict:
        return {
            "query_id": self.query_id,
            "query": self.query,
            "topic": self.topic,
            "limit": self.limit,
            "weight": self.weight,
        }


def _ordered_enabled(catalog: QueryCatalog) -> list[QuerySpec]:
    """Enabled specs in the stable priority order (weight desc, id asc)."""
    return sorted(catalog.enabled, key=lambda spec: (-spec.weight, spec.id))


def plan_queries(
    catalog: QueryCatalog,
    *,
    day: date,
    max_papers: int = DEFAULT_MAX_PAPERS,
) -> list[PlannedQuery]:
    """Select tonight's queries.

    Args:
        catalog: The validated query catalog.
        day: The night's date. The same date always produces the same plan.
        max_papers: Upper bound on the summed ``limit`` of the plan.

    Raises:
        ValueError: ``max_papers`` is smaller than every query's ``limit``, so
            no plan can both run and stay in budget.
    """
    if max_papers < 1:
        raise ValueError(f"max_papers must be >= 1, got {max_papers}")

    ordered = _ordered_enabled(catalog)
    if not ordered:
        return []

    smallest = min(spec.limit for spec in ordered)
    if smallest > max_papers:
        raise ValueError(
            f"max_papers={max_papers} is below every query's limit "
            f"(smallest is {smallest}); no query can run within budget"
        )

    # Rotate so the first-enabled query changes each night.
    offset = day.toordinal() % len(ordered)
    rotated = ordered[offset:] + ordered[:offset]

    selected: list[PlannedQuery] = []
    used = 0
    for spec in rotated:
        if used + spec.limit > max_papers:
            continue
        selected.append(PlannedQuery.from_spec(spec))
        used += spec.limit
    return selected


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic_kg.pipeline.plan",
        description="Print tonight's planned ingest queries as JSON.",
    )
    parser.add_argument(
        "--date",
        help="Plan for this date (YYYY-MM-DD, UTC). Defaults to today UTC.",
    )
    parser.add_argument(
        "--catalog",
        default=None,
        help="Path to ingest-queries.yaml (default: NIGHTLY_CATALOG or repo config/).",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="Paper budget (default: NIGHTLY_MAX_PAPERS or 50).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Indent the JSON output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    day = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
    max_papers = args.max_papers
    if max_papers is None:
        max_papers = int(os.environ.get(ENV_MAX_PAPERS, str(DEFAULT_MAX_PAPERS)))

    catalog = load_catalog(args.catalog)
    planned = plan_queries(catalog, day=day, max_papers=max_papers)
    print(
        json.dumps(
            [item.to_dict() for item in planned],
            indent=2 if args.pretty else None,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
