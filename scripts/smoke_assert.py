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
_DEGRADED_STATUS = "completed_with_errors"
_INSPECTABLE_STATUSES = ("completed", _DEGRADED_STATUS)

# Citation-population failure reasons that mean Semantic Scholar could not
# be reached, as opposed to reachable-but-unaware-of-this-paper.
_INFRA_REASONS = ("s2_lookup_failed", "s2_fetch_failed", "populate_raised")

_MAX_DETAIL_LINES = 3


def _citation_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """Pull the I-58 citation fields out of the result JSON.

    ``reported`` is False for result files produced before citation
    reporting existed — in that case we know nothing, and say so rather
    than inventing a zero.
    """
    reported = "citation_population_attempted" in result
    refs_reported = "citation_references_seen" in result

    def _i(key: str) -> int:
        value = result.get(key, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    failures = result.get("citation_failures")
    failures = failures if isinstance(failures, dict) else {}
    attempted = _i("citation_population_attempted")
    succeeded = _i("citation_population_succeeded")
    infra = sum(
        v for k, v in failures.items()
        if k in _INFRA_REASONS and isinstance(v, int)
    )

    # MAJOR-1 / S9 fail-closed: the degraded status is only safe to inspect
    # when the evidence that justifies it travels with it. A degraded status
    # with no citation fields (schema drift, a future reason for the status,
    # a filtered artifact) is unattributable, and unattributable is never a
    # pass.
    unattributable = result.get("status") == _DEGRADED_STATUS and not reported
    unmeasured = reported and attempted > 0 and succeeded == 0

    return {
        "reported": reported,
        "refs_reported": refs_reported,
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": _i("citation_population_failed"),
        "edges": _i("citation_edges_created"),
        "edges_existing": _i("citation_edges_existing"),
        "stubs": _i("citation_stubs_created"),
        "refs_seen": _i("citation_references_seen"),
        "refs_with_doi": _i("citation_references_with_doi"),
        "refs_no_doi": _i("citation_references_no_doi"),
        "failures": failures,
        "details": result.get("citation_failure_details") or {},
        "infra": infra,
        "unattributable": unattributable,
        # Nothing at all was measured.
        "unmeasured": unmeasured,
        # MAJOR-3: no ratio threshold — the question is whether the
        # infrastructure worked for every paper we asked about. Data gaps
        # (no_s2_id: S2 answered, has no record) do not count against us.
        "coverage_incomplete": unattributable or unmeasured or infra > 0,
    }


def _coverage_line(ev: dict[str, Any]) -> str:
    """One line answering: how much of the corpus did we actually measure?"""
    if ev["unattributable"]:
        return (
            f"  COVERAGE: UNATTRIBUTABLE -- status is {_DEGRADED_STATUS!r} but "
            "the result JSON carries no citation fields, so this run's "
            "citation state cannot be verified."
        )
    if not ev["reported"]:
        return (
            "  COVERAGE: UNKNOWN -- this result JSON predates citation "
            "reporting, so no count can be attributed to this run."
        )
    if ev["attempted"] == 0:
        return (
            "  COVERAGE: NOT ATTEMPTED -- citation population never ran "
            "(populate_citations off, or no paper was imported)."
        )
    if ev["succeeded"] == 0:
        kind = "SEMANTIC SCHOLAR UNREACHABLE" if ev["infra"] else (
            "NO SEMANTIC SCHOLAR RECORD"
        )
        return (
            f"  COVERAGE: NONE ({ev['succeeded']} of {ev['attempted']} "
            f"measured) -- {kind}."
        )
    if ev["infra"]:
        return (
            f"  COVERAGE: PARTIAL ({ev['succeeded']} of {ev['attempted']} "
            f"measured) -- {ev['infra']} attempt(s) could not reach Semantic "
            "Scholar. Part of the corpus is unmeasured."
        )
    if ev["failed"]:
        return (
            f"  COVERAGE: COMPLETE for reachable papers ({ev['succeeded']} of "
            f"{ev['attempted']} measured) -- {ev['failed']} paper(s) have no "
            "Semantic Scholar record; a corpus gap, not a run fault."
        )
    return (
        f"  COVERAGE: COMPLETE ({ev['succeeded']} of {ev['attempted']} measured)."
    )


def _evidence_line(counts: dict[str, int], ev: dict[str, Any]) -> str:
    """One line answering: what did the papers we DID measure actually say?

    R4 MAJOR-2: the first cut called every measured-but-zero-edge run a
    REGRESSION, which mislabelled genuinely reference-less papers and
    DOI-less reference lists. ``citation_graph`` drops DOI-less references
    by design, so only DOI-bearing references that produced no edge are
    evidence of a linker fault.
    """
    if ev["unattributable"]:
        return (
            "  EVIDENCE: NONE -- no citation fields to reason from; "
            f"cites={counts['cites']} proves nothing either way."
        )
    if not ev["reported"] or ev["attempted"] == 0:
        return (
            f"  EVIDENCE: NONE -- nothing was measured; cites={counts['cites']} "
            "is not evidence of absence."
        )
    if ev["succeeded"] == 0:
        return (
            f"  EVIDENCE: NONE -- 0 of {ev['attempted']} attempt(s) returned a "
            f"reference list; cites={counts['cites']} is NOT evidence that "
            "these papers cite nothing."
        )
    if not ev["refs_reported"]:
        return (
            "  EVIDENCE: UNKNOWN -- this result JSON reports no reference "
            "counts, so genuine absence cannot be told from a linker fault."
        )
    if ev["refs_seen"] == 0:
        return (
            "  EVIDENCE: NO REFERENCES AT SOURCE -- Semantic Scholar returned "
            f"0 reference entries across the {ev['succeeded']} measured "
            "paper(s). Zero CITES edges is absence at the source, not a code "
            "fault."
        )
    if ev["refs_with_doi"] == 0:
        return (
            f"  EVIDENCE: NO DOI-BEARING REFERENCES -- {ev['refs_seen']} "
            "reference(s) returned, all dropped for lacking a DOI (dropped by "
            "design). No CITES edge was possible."
        )
    if ev["edges"] == 0 and ev["edges_existing"] > 0:
        # MINOR-B: link_paper_cites_paper is idempotent, so a re-run over an
        # already-populated graph writes nothing and used to be labelled a
        # regression.
        return (
            f"  EVIDENCE: ALREADY LINKED -- {ev['edges_existing']} CITES "
            "edge(s) were already present, so this run wrote none. Not a "
            "regression; a re-run over an already-populated graph."
        )
    if ev["edges"] == 0:
        return (
            f"  EVIDENCE: REGRESSION -- {ev['refs_with_doi']} DOI-bearing "
            f"reference(s) resolved across {ev['succeeded']} measured "
            "paper(s), but 0 CITES edges were written (0 already present). "
            "Look at create_or_promote_paper_stub / link_paper_cites_paper."
        )
    already = (
        f" ({ev['edges_existing']} already present)"
        if ev["edges_existing"] else ""
    )
    return (
        f"  EVIDENCE: {ev['edges']} CITES edge(s) written from "
        f"{ev['refs_with_doi']} DOI-bearing reference(s){already}."
    )


def _citation_report(
    counts: dict[str, int],
    ev: dict[str, Any],
    result: dict[str, Any],
) -> list[str]:
    """Coverage and evidence, always printed — pass or fail.

    R4 MAJOR-3: the first cut returned [] on any passing run, so a run
    with two-thirds of its corpus unmeasured printed nothing at all.
    These two verdicts are independent: coverage is about how much we
    measured, evidence is about what the measured subset said. Reporting
    them separately is what stops a throttled run being labelled "not a
    throttling artifact".
    """
    lines = [_coverage_line(ev), _evidence_line(counts, ev)]

    if ev["failures"]:
        reasons = ", ".join(f"{k}={v}" for k, v in sorted(ev["failures"].items()))
        lines.append(f"    citation failure reasons: {reasons}")
    if ev["details"]:
        for doi, msg in list(ev["details"].items())[:_MAX_DETAIL_LINES]:
            lines.append(f"      {doi}: {msg}")
    if ev["refs_no_doi"]:
        lines.append(
            f"    references dropped for lacking a DOI: {ev['refs_no_doi']}"
        )

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

    I-58 adds two citation clauses.

    ``CITES edges >= 1`` additionally fails when this run could not
    attribute the count — an unattributable number is not a pass, even
    if the graph happens to hold CITES edges from an earlier write.

    ``citation coverage complete`` is a separate named check so partial
    coverage shows up in the PASS/FAIL table rather than hiding behind a
    green CITES row.

    ``citations`` omitted (unit callers) keeps the count-only behaviour.
    """
    ev = citations or {}
    unattributable = bool(ev.get("unmeasured")) or bool(ev.get("unattributable"))
    return {
        "papers >= 1":                  counts["papers"] >= 1,
        "RESEARCHES topic edges >= 1":  counts["topic_edges"] >= 1,
        "ResearchConcept nodes >= 1":   counts["concepts"] >= 1,
        "Model OR Method >= 1":         (counts["models"] + counts["methods"]) >= 1,
        "CITES edges >= 1":             counts["cites"] >= 1 and not unattributable,
        "citation coverage complete":   not ev.get("coverage_incomplete", False),
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
            f"edges_written={citations['edges']} "
            f"edges_existing={citations['edges_existing']} "
            f"stubs={citations['stubs']} "
            f"refs_seen={citations['refs_seen']} "
            f"refs_with_doi={citations['refs_with_doi']}"
        )
    else:
        print("  citations: not reported by this ingest result")
    for line in _citation_report(counts, citations, result):
        print(line)
    print()

    failed: list[str] = []
    for name, ok in checks.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: {name}")
        if not ok:
            failed.append(name)
        # NIT-D: "PASS: citation coverage complete" directly under
        # "COVERAGE: UNKNOWN" reads as a claim we cannot make. Say plainly
        # that the check was not evaluated rather than silently inheriting
        # a pass for backward compatibility.
        if ok and name == "citation coverage complete" and not citations["reported"]:
            print(
                "        (not evaluated -- this result JSON reports no "
                "citation fields; passed for backward compatibility)"
            )

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
