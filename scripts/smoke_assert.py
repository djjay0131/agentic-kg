"""Smoke test assertions — batch-level entity coverage.

Run after ``agentic-kg ingest --json > result.json``. Asserts that the
expected graph shape landed for at least one paper in the batch.
Exits 0 on pass, 1 on fail with a per-check PASS/FAIL report on stdout.

See: ``llm/features/ci-smoke-test-ingestion.md`` (AC-6 / AC-7 / AC-8).
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _load_result(result_path: str) -> tuple[dict[str, Any] | None, str | None]:
    """Read the ingest result JSON. Returns ``(result, error)`` where
    exactly one is populated. Never raises."""
    try:
        with open(result_path) as f:
            return json.load(f), None
    except OSError as e:
        return None, f"cannot open result file {result_path!r}: {e}"
    except json.JSONDecodeError as e:
        return None, f"invalid JSON in {result_path!r}: {e}"


def _run_graph_checks(session: Any) -> dict[str, int]:
    """Run the single-round-trip Cypher query and return raw counts.

    Split out so tests can inject a mock session without patching the
    module's Neo4j driver. Uses chained ``OPTIONAL MATCH`` + ``WITH`` so
    all six counts land in one row.
    """
    row = session.run("""
        OPTIONAL MATCH (p:Paper)
          WITH count(p) AS papers
        OPTIONAL MATCH (:Paper)-[r1:BELONGS_TO]->(:Topic)
          WITH papers, count(r1) AS topic_edges
        OPTIONAL MATCH (c:ResearchConcept)
          WITH papers, topic_edges, count(c) AS concepts
        OPTIONAL MATCH (m:Model)
          WITH papers, topic_edges, concepts, count(m) AS models
        OPTIONAL MATCH (mt:Method)
          WITH papers, topic_edges, concepts, models, count(mt) AS methods
        OPTIONAL MATCH (:Paper)-[r2:CITES]->()
          WITH papers, topic_edges, concepts, models, methods,
               count(r2) AS cites
        OPTIONAL MATCH (p2:Paper) WHERE p2.taxonomy_hash IS NOT NULL
          WITH papers, topic_edges, concepts, models, methods, cites,
               count(p2) AS tagged
        RETURN papers, topic_edges, concepts, models, methods, cites, tagged
    """).single()
    return {
        "papers": row["papers"],
        "topic_edges": row["topic_edges"],
        "concepts": row["concepts"],
        "models": row["models"],
        "methods": row["methods"],
        "cites": row["cites"],
        "tagged": row["tagged"],
    }


def _evaluate_checks(counts: dict[str, int]) -> dict[str, bool]:
    """Apply the AC-6 standard-strictness checks against raw counts."""
    return {
        "papers >= 1":                  counts["papers"] >= 1,
        "BELONGS_TO topic edges >= 1":  counts["topic_edges"] >= 1,
        "ResearchConcept nodes >= 1":   counts["concepts"] >= 1,
        "Model OR Method >= 1":         (counts["models"] + counts["methods"]) >= 1,
        "CITES edges >= 1":             counts["cites"] >= 1,
        "taxonomy_hash on >= 1 Paper":  counts["tagged"] >= 1,
    }


def main(result_path: str) -> int:
    """Read the ingest result, verify status, run graph checks, report.

    Returns the intended process exit code (0=pass, 1=fail).
    """
    result, err = _load_result(result_path)
    if err is not None:
        print(f"FAIL: {err}")
        return 1
    assert result is not None  # narrows for type-checkers

    # AC-7: pre-check status before touching Neo4j.
    if result.get("status") != "completed":
        print(f"FAIL: ingest_papers status={result.get('status')!r}")
        errs = result.get("extraction_errors")
        if errs:
            print(f"  extraction_errors: {errs}")
        return 1

    # AC-6: run the Cypher checks. Import here so the early-exit paths
    # above don't pay the import cost.
    from agentic_kg.knowledge_graph.repository import get_repository

    repo = get_repository()
    with repo.session() as session:
        counts = _run_graph_checks(session)

    checks = _evaluate_checks(counts)

    print("\n=== Smoke-test graph-shape assertions ===")
    print(
        f"  papers={counts['papers']}, topic_edges={counts['topic_edges']}, "
        f"concepts={counts['concepts']}, models={counts['models']}, "
        f"methods={counts['methods']}, cites={counts['cites']}, "
        f"taxonomy_hash_papers={counts['tagged']}"
    )
    print()

    failed: list[str] = []
    for name, ok in checks.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: {name}")
        if not ok:
            failed.append(name)

    if failed:
        print(f"\nSmoke test FAILED: {len(failed)} check(s) failed.")
        return 1
    print("\nSmoke test PASSED.")
    return 0


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    if len(sys.argv) != 2:
        print("usage: smoke_assert.py <ingest_result.json>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
