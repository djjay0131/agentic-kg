#!/usr/bin/env python3
"""Remove synthetic E2E rows from a knowledge-graph database.

The integration/E2E suites mint Papers with DOIs under ``10.1/TEST-`` (for
example ``TEST title for 10.1/TEST-ab12cd``). Run isolation and per-test
teardown keep new runs clean, but rows written before those guards existed
still appear in staging listings.

This is a one-off, idempotent cleanup. It is **dry-run by default** and only
deletes when ``--apply`` is passed:

    # show what would be deleted
    python scripts/cleanup_e2e_data.py

    # delete it
    python scripts/cleanup_e2e_data.py --apply

Connection settings come from the same environment variables the repository
uses (``NEO4J_URI``, ``NEO4J_USERNAME``, ``NEO4J_PASSWORD``, ``NEO4J_DATABASE``).
Do not run ``--apply`` against staging without the owner's approval.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from a checkout without installing core first.
sys.path.insert(0, str(Path(__file__).parent.parent / "packages" / "core" / "src"))

from agentic_kg.knowledge_graph.repository import get_repository  # noqa: E402

DEFAULT_DOI_PREFIX = "10.1/TEST-"

COUNT_QUERY = """
MATCH (n)
WHERE n.doi STARTS WITH $prefix
RETURN count(n) AS n
"""

DELETE_QUERY = """
MATCH (n)
WHERE n.doi STARTS WITH $prefix
DETACH DELETE n
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete matching nodes (default: dry run).",
    )
    parser.add_argument(
        "--prefix",
        default=DEFAULT_DOI_PREFIX,
        help=f"DOI prefix to delete (default: {DEFAULT_DOI_PREFIX}).",
    )
    args = parser.parse_args(argv)

    repo = get_repository()
    with repo.session() as session:
        record = session.run(COUNT_QUERY, prefix=args.prefix).single()
        matched = int(record["n"]) if record else 0

        deleted = 0
        if args.apply and matched:
            result = session.run(DELETE_QUERY, prefix=args.prefix)
            deleted = result.consume().counters.nodes_deleted

    summary = {
        "prefix": args.prefix,
        "dry_run": not args.apply,
        "matched": matched,
        "deleted": deleted,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
