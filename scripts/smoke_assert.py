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
        OPTIONAL MATCH (:Paper)-[r1:RESEARCHES]->(:Topic)
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


# I-58: statuses under which the graph is worth inspecting. A run that
# could not measure citations still wrote papers/topics/concepts, and
# early-exiting on it would hide exactly the evidence an operator needs.
_INSPECTABLE_STATUSES = ("completed", "completed_with_errors")

# Citation-population failure reasons that mean Semantic Scholar was
# unreachable, as opposed to reachable-but-unaware-of-this-paper.
_INFRA_REASONS = ("s2_lookup_failed", "s2_fetch_failed")


def _citation_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """Pull the I-58 citation-population fields out of the result JSON.

    ``reported`` is False for result files produced before citation
    reporting existed — in that case we know nothing, and say so rather
    than inventing a zero.
    """
    reported = "citation_population_attempted" in result

    def _i(key: str) -> int:
        value = result.get(key, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    failures = result.get("citation_failures")
    failures = failures if isinstance(failures, dict) else {}
    attempted = _i("citation_population_attempted")
    succeeded = _i("citation_population_succeeded")
    return {
        "reported": reported,
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": _i("citation_population_failed"),
        "edges": _i("citation_edges_created"),
        "stubs": _i("citation_stubs_created"),
        "failures": failures,
        "details": result.get("citation_failure_details") or {},
        # The load-bearing predicate: citation population ran and not one
        # attempt came back with a reference list. Nothing was measured.
        "unmeasured": reported and attempted > 0 and succeeded == 0,
    }


def _citation_diagnosis(
    counts: dict[str, int],
    ev: dict[str, Any],
    result: dict[str, Any],
) -> list[str]:
    """Explain, in plain words, WHY the CITES check landed where it did.

    Returns the lines to print. Empty when citations were measured and
    edges landed — there is nothing to explain.
    """
    if not ev["unmeasured"] and counts["cites"] >= 1:
        return []

    lines: list[str] = []
    if ev["unmeasured"]:
        infra = sum(ev["failures"].get(r, 0) for r in _INFRA_REASONS)
        kind = (
            "INFRASTRUCTURE"
            if infra
            else "NO SEMANTIC SCHOLAR RECORD"
        )
        lines.append(
            f"  DIAGNOSIS: NOT MEASURED ({kind}) -- citation population was "
            f"attempted on {ev['attempted']} paper(s) and succeeded on 0."
        )
        lines.append(
            f"    cites={counts['cites']} is NOT evidence that these papers "
            "cite nothing. This run measured nothing about citations."
        )
        if infra:
            lines.append(
                f"    {infra} of {ev['attempted']} attempt(s) could not reach "
                "Semantic Scholar (lookup/fetch raised: throttling, network, "
                "or an open circuit breaker)."
            )
    elif ev["reported"] and ev["attempted"] == 0:
        lines.append(
            "  DIAGNOSIS: NOT ATTEMPTED -- citation population never ran "
            "(populate_citations off, or no paper was imported)."
        )
        lines.append(
            "    cites=0 is not evidence of absence; nothing was measured."
        )
    elif not ev["reported"]:
        lines.append(
            "  DIAGNOSIS: UNKNOWN -- this result JSON predates citation "
            "reporting, so the zero cannot be attributed."
        )
    else:
        lines.append(
            f"  DIAGNOSIS: REGRESSION -- Semantic Scholar was reachable and "
            f"returned reference lists for {ev['succeeded']} paper(s), but "
            f"{ev['edges']} CITES edge(s) were written."
        )
        lines.append(
            "    This is a real citation-graph failure, not a throttling "
            "artifact. Look at populate_citations / link_paper_cites_paper."
        )

    if ev["failures"]:
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(ev["failures"].items()))
        lines.append(f"    citation failure reasons: {reasons}")
    if ev["details"]:
        for doi, msg in list(ev["details"].items())[:3]:
            lines.append(f"      {doi}: {msg}")

    # Corroborating evidence already present in the result JSON.
    rate_limited = result.get("sources_rate_limited") or 0
    search_errors = result.get("search_errors") or {}
    if rate_limited:
        lines.append(f"    search sources rate-limited this run: {rate_limited}")
    if search_errors:
        lines.append(f"    search_errors: {search_errors}")
    return lines


def _evaluate_checks(
    counts: dict[str, int],
    citations: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """Apply the AC-6 standard-strictness checks against raw counts.

    I-58: the CITES check additionally fails when citation population
    could not be measured at all, even if the graph happens to hold
    CITES edges from some earlier write. An unattributable count is not
    a pass. ``citations`` omitted (unit callers) keeps the count-only
    behaviour.
    """
    unmeasured = bool(citations and citations.get("unmeasured"))
    return {
        "papers >= 1":                  counts["papers"] >= 1,
        "RESEARCHES topic edges >= 1":  counts["topic_edges"] >= 1,
        "ResearchConcept nodes >= 1":   counts["concepts"] >= 1,
        "Model OR Method >= 1":         (counts["models"] + counts["methods"]) >= 1,
        "CITES edges >= 1":             counts["cites"] >= 1 and not unmeasured,
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

    # AC-7: pre-check status before touching Neo4j. I-58 adds
    # "completed_with_errors" as inspectable — the run finished, it just
    # could not measure everything, and the graph checks below are how
    # the operator learns which part.
    if result.get("status") not in _INSPECTABLE_STATUSES:
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

    citations = _citation_evidence(result)
    checks = _evaluate_checks(counts, citations)

    print("\n=== Smoke-test graph-shape assertions ===")
    print(
        f"  papers={counts['papers']}, topic_edges={counts['topic_edges']}, "
        f"concepts={counts['concepts']}, models={counts['models']}, "
        f"methods={counts['methods']}, cites={counts['cites']}, "
        f"taxonomy_hash_papers={counts['tagged']}"
    )
    if citations["reported"]:
        print(
            f"  citations: attempted={citations['attempted']} "
            f"succeeded={citations['succeeded']} failed={citations['failed']} "
            f"edges_written={citations['edges']} stubs={citations['stubs']}"
        )
    else:
        print("  citations: not reported by this ingest result")
    for line in _citation_diagnosis(counts, citations, result):
        print(line)
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
