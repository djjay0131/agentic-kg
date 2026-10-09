"""Tests for ``scripts/smoke_assert.py``.

Covers AC-6 (Cypher-count-driven graph-shape assertions), AC-7 (status
pre-check), AC-8 (missing/unparseable JSON).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load scripts/smoke_assert.py as a module without polluting sys.path
# with the whole scripts/ directory.
_SMOKE_ASSERT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "smoke_assert.py"
)
_spec = importlib.util.spec_from_file_location(
    "smoke_assert", _SMOKE_ASSERT_PATH,
)
smoke_assert = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["smoke_assert"] = smoke_assert
_spec.loader.exec_module(smoke_assert)  # type: ignore[union-attr]


# =============================================================================
# Helpers
# =============================================================================


def _completed_result_json() -> dict:
    """Minimal ``IngestionResult`` shape with status=completed."""
    return {"status": "completed", "extraction_errors": {}}


def _write_result(tmp_path: Path, payload: dict | str) -> Path:
    """Write JSON (or raw string) to a temp file and return its path."""
    p = tmp_path / "ingest_result.json"
    if isinstance(payload, dict):
        p.write_text(json.dumps(payload))
    else:
        p.write_text(payload)
    return p


def _make_session_returning(counts: dict[str, int]) -> MagicMock:
    """Build a mock Neo4j session whose single-shot ``run(...).single()``
    returns dict-indexable row-like data with the given counts."""
    session = MagicMock()
    row = MagicMock()
    # Neo4j Result.single() returns a Record — accessed by string key.
    row.__getitem__.side_effect = lambda k: counts[k]
    session.run.return_value.single.return_value = row
    return session


def _mock_repo(session: MagicMock) -> MagicMock:
    """Repository whose ``session()`` is a context manager yielding
    ``session``."""
    repo = MagicMock()
    ctx = MagicMock()
    ctx.__enter__ = lambda self: session
    ctx.__exit__ = lambda self, *a: None
    repo.session.return_value = ctx
    return repo


def _happy_counts() -> dict[str, int]:
    return {
        "papers": 3,
        "topic_edges": 5,
        "concepts": 4,
        "models": 2,
        "methods": 3,
        "cites": 8,
        "tagged": 3,
    }


# =============================================================================
# AC-8: missing / unparseable JSON
# =============================================================================


class TestLoadFailure:
    def test_missing_file_returns_1(self, tmp_path, capsys):
        missing = tmp_path / "nope.json"
        rc = smoke_assert.main(str(missing))
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL:" in out
        assert "cannot open" in out
        # The message quotes the path with !r, and repr() doubles
        # backslashes -- so on Windows str(missing) is not a substring.
        # Stripping repr's quotes matches on both platforms.
        assert repr(str(missing))[1:-1] in out

    def test_invalid_json_returns_1(self, tmp_path, capsys):
        bad = _write_result(tmp_path, "not { valid json")
        rc = smoke_assert.main(str(bad))
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL:" in out
        assert "invalid JSON" in out


# =============================================================================
# AC-7: status pre-check runs before Neo4j
# =============================================================================


class TestStatusPrecheck:
    def test_failed_status_returns_1(self, tmp_path, capsys):
        result_path = _write_result(tmp_path, {
            "status": "failed",
            "error": "boom",
            "extraction_errors": {"10.1/a": "PDF processing failed"},
        })
        # If the pre-check runs first, we should never construct a repo.
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
        ) as get_repo:
            rc = smoke_assert.main(str(result_path))

        assert rc == 1
        get_repo.assert_not_called()
        out = capsys.readouterr().out
        assert "FAIL: ingest_papers status='failed'" in out
        # extraction_errors surfaces to stdout for diagnosis.
        assert "PDF processing failed" in out

    def test_missing_status_returns_1(self, tmp_path, capsys):
        # Missing the "status" field at all still fails pre-check.
        result_path = _write_result(tmp_path, {"nostatus": True})
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
        ) as get_repo:
            rc = smoke_assert.main(str(result_path))
        assert rc == 1
        get_repo.assert_not_called()

    def test_dry_run_status_returns_1(self, tmp_path):
        """dry_run isn't a smoke pass; the ingest didn't actually write."""
        result_path = _write_result(tmp_path, {"status": "dry_run"})
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
        ):
            rc = smoke_assert.main(str(result_path))
        assert rc == 1


# =============================================================================
# AC-6: standard-strictness Cypher checks
# =============================================================================


class TestGraphChecks:
    def test_all_checks_pass_returns_0(self, tmp_path, capsys):
        result_path = _write_result(tmp_path, _completed_result_json())
        session = _make_session_returning(_happy_counts())
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ):
            rc = smoke_assert.main(str(result_path))

        assert rc == 0
        out = capsys.readouterr().out
        assert "Smoke test PASSED." in out
        # All 7 checks report PASS (I-58 added "citation coverage complete").
        assert out.count("PASS:") == 7
        # Raw counts printed for diagnosis.
        assert "papers=3" in out
        assert "cites=8" in out

    def test_zero_papers_fails(self, tmp_path, capsys):
        counts = _happy_counts()
        counts["papers"] = 0
        result_path = _write_result(tmp_path, _completed_result_json())
        session = _make_session_returning(counts)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ):
            rc = smoke_assert.main(str(result_path))

        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL: papers >= 1" in out
        assert "Smoke test FAILED: 1 check(s) failed." in out

    def test_zero_topic_edges_fails(self, tmp_path, capsys):
        counts = _happy_counts()
        counts["topic_edges"] = 0
        result_path = _write_result(tmp_path, _completed_result_json())
        session = _make_session_returning(counts)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ):
            rc = smoke_assert.main(str(result_path))

        assert rc == 1
        assert "FAIL: RESEARCHES topic edges >= 1" in capsys.readouterr().out

    def test_zero_concepts_fails(self, tmp_path, capsys):
        counts = _happy_counts()
        counts["concepts"] = 0
        result_path = _write_result(tmp_path, _completed_result_json())
        session = _make_session_returning(counts)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ):
            rc = smoke_assert.main(str(result_path))

        assert rc == 1
        assert "FAIL: ResearchConcept nodes >= 1" in capsys.readouterr().out

    def test_models_and_methods_both_zero_fails(self, tmp_path, capsys):
        counts = _happy_counts()
        counts["models"] = 0
        counts["methods"] = 0
        result_path = _write_result(tmp_path, _completed_result_json())
        session = _make_session_returning(counts)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ):
            rc = smoke_assert.main(str(result_path))

        assert rc == 1
        assert "FAIL: Model OR Method >= 1" in capsys.readouterr().out

    def test_models_only_still_passes(self, tmp_path):
        """AC-6 says the check is Model OR Method — either alone is fine."""
        counts = _happy_counts()
        counts["models"] = 1
        counts["methods"] = 0
        result_path = _write_result(tmp_path, _completed_result_json())
        session = _make_session_returning(counts)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ):
            rc = smoke_assert.main(str(result_path))

        assert rc == 0

    def test_methods_only_still_passes(self, tmp_path):
        counts = _happy_counts()
        counts["models"] = 0
        counts["methods"] = 1
        result_path = _write_result(tmp_path, _completed_result_json())
        session = _make_session_returning(counts)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ):
            rc = smoke_assert.main(str(result_path))

        assert rc == 0

    def test_zero_cites_fails(self, tmp_path, capsys):
        counts = _happy_counts()
        counts["cites"] = 0
        result_path = _write_result(tmp_path, _completed_result_json())
        session = _make_session_returning(counts)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ):
            rc = smoke_assert.main(str(result_path))

        assert rc == 1
        assert "FAIL: CITES edges >= 1" in capsys.readouterr().out

    def test_zero_taxonomy_hash_fails(self, tmp_path, capsys):
        counts = _happy_counts()
        counts["tagged"] = 0
        result_path = _write_result(tmp_path, _completed_result_json())
        session = _make_session_returning(counts)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ):
            rc = smoke_assert.main(str(result_path))

        assert rc == 1
        assert "FAIL: taxonomy_hash on >= 1 Paper" in capsys.readouterr().out

    def test_multiple_failures_reported_together(self, tmp_path, capsys):
        counts = _happy_counts()
        counts["papers"] = 0
        counts["cites"] = 0
        counts["tagged"] = 0
        result_path = _write_result(tmp_path, _completed_result_json())
        session = _make_session_returning(counts)
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ):
            rc = smoke_assert.main(str(result_path))

        assert rc == 1
        out = capsys.readouterr().out
        assert "Smoke test FAILED: 3 check(s) failed." in out


# =============================================================================
# Pure-function unit coverage
# =============================================================================


class TestEvaluateChecks:
    def test_all_zeros_all_fail(self):
        counts = {k: 0 for k in (
            "papers", "topic_edges", "concepts",
            "models", "methods", "cites", "tagged",
        )}
        result = smoke_assert._evaluate_checks(counts)
        # Citation coverage passes on a bare count dict (nothing was
        # claimed about citations), so it is the one non-False entry.
        assert not any(
            v for k, v in result.items() if k != "citation coverage complete"
        )
        assert len(result) == 7

    def test_boundary_at_one(self):
        counts = {k: 1 for k in (
            "papers", "topic_edges", "concepts",
            "models", "methods", "cites", "tagged",
        )}
        result = smoke_assert._evaluate_checks(counts)
        assert all(result.values())

    def test_model_or_method_sum_boundary(self):
        counts = {
            "papers": 1, "topic_edges": 1, "concepts": 1,
            "models": 0, "methods": 0,
            "cites": 1, "tagged": 1,
        }
        assert not smoke_assert._evaluate_checks(counts)["Model OR Method >= 1"]
        counts["methods"] = 1
        assert smoke_assert._evaluate_checks(counts)["Model OR Method >= 1"]


class TestRunGraphChecks:
    def test_returns_counts_from_row(self):
        session = _make_session_returning(_happy_counts())
        counts = smoke_assert._run_graph_checks(session)
        assert counts == _happy_counts()
        # Single Cypher round trip (matches AC-6 contract).
        assert session.run.call_count == 1


class TestLoadResult:
    def test_valid_json_returns_dict(self, tmp_path):
        p = _write_result(tmp_path, {"status": "completed"})
        result, err = smoke_assert._load_result(str(p))
        assert result == {"status": "completed"}
        assert err is None

    def test_missing_file_returns_error(self, tmp_path):
        result, err = smoke_assert._load_result(str(tmp_path / "no.json"))
        assert result is None
        assert err is not None
        assert "cannot open" in err

    def test_invalid_json_returns_error(self, tmp_path):
        p = _write_result(tmp_path, "not-json{")
        result, err = smoke_assert._load_result(str(p))
        assert result is None
        assert err is not None
        assert "invalid JSON" in err


# =============================================================================
# Issue #58 / R4: coverage and evidence are separate verdicts
#
# R4 MAJOR-2: the first cut called every measured-but-zero-edge run a
# REGRESSION, mislabelling genuinely reference-less papers and printing
# "not a throttling artifact" directly above evidence of throttling.
# R4 MAJOR-3: partial coverage was an unconditional all-clear whose
# diagnosis was suppressed entirely on a pass.
# R4 MAJOR-1/S9: a degraded status with no citation evidence passed.
# =============================================================================


def _citation_result_json(
    attempted: int,
    succeeded: int,
    failed: int = 0,
    edges: int = 0,
    stubs: int = 0,
    refs_seen: int | None = None,
    refs_with_doi: int | None = None,
    refs_no_doi: int = 0,
    edges_existing: int = 0,
    failures: dict | None = None,
    status: str = "completed",
    **extra,
) -> dict:
    payload = {
        "status": status,
        "extraction_errors": {},
        "citation_population_attempted": attempted,
        "citation_population_succeeded": succeeded,
        "citation_population_failed": failed,
        "citation_edges_created": edges,
        "citation_edges_existing": edges_existing,
        "citation_stubs_created": stubs,
        "citation_failures": failures or {},
    }
    if refs_seen is not None:
        payload["citation_references_seen"] = refs_seen
        payload["citation_references_with_doi"] = (
            refs_with_doi if refs_with_doi is not None else refs_seen - refs_no_doi
        )
        payload["citation_references_no_doi"] = refs_no_doi
    payload.update(extra)
    return payload


def _run_smoke(tmp_path, payload: dict, counts: dict[str, int]) -> int:
    result_path = _write_result(tmp_path, payload)
    session = _make_session_returning(counts)
    with patch(
        "agentic_kg.knowledge_graph.repository.get_repository",
        return_value=_mock_repo(session),
    ):
        return smoke_assert.main(str(result_path))


def _zero_cites() -> dict[str, int]:
    counts = _happy_counts()
    counts["cites"] = 0
    return counts


class TestCoverageVerdict:
    def test_all_attempts_failed_says_unreachable(self, tmp_path, capsys):
        payload = _citation_result_json(
            attempted=3, succeeded=0, failed=3,
            failures={"s2_lookup_failed": 3},
            status="completed_with_errors",
            sources_rate_limited=1,
            search_errors={"semantic_scholar": "Rate limit exceeded"},
        )
        rc = _run_smoke(tmp_path, payload, _zero_cites())
        out = capsys.readouterr().out

        assert rc == 1  # the gate is not weakened
        assert "FAIL: CITES edges >= 1" in out
        assert "FAIL: citation coverage complete" in out
        assert "COVERAGE: NONE (0 of 3 measured) -- SEMANTIC SCHOLAR UNREACHABLE" in out
        assert "EVIDENCE: NONE" in out
        assert "is NOT evidence that these papers cite nothing" in out
        assert "citation failure reasons: s2_lookup_failed=3" in out
        assert "search sources rate-limited this run: 1" in out
        # An infrastructure outage is never called a regression.
        assert "REGRESSION" not in out

    def test_populate_raised_is_an_infrastructure_failure(self, tmp_path, capsys):
        """BLOCKING-1: a crashing citation phase is an attempt, not a no-op."""
        payload = _citation_result_json(
            attempted=3, succeeded=0, failed=3,
            failures={"populate_raised": 3},
            status="completed_with_errors",
        )
        rc = _run_smoke(tmp_path, payload, _happy_counts())  # cites = 8
        out = capsys.readouterr().out

        assert rc == 1
        assert "SEMANTIC SCHOLAR UNREACHABLE" in out
        assert "NOT ATTEMPTED" not in out

    def test_no_s2_record_is_not_an_outage(self, tmp_path, capsys):
        payload = _citation_result_json(
            attempted=2, succeeded=0, failed=2,
            failures={"no_s2_id": 2},
            status="completed_with_errors",
        )
        rc = _run_smoke(tmp_path, payload, _zero_cites())
        out = capsys.readouterr().out

        assert rc == 1
        assert "COVERAGE: NONE (0 of 2 measured) -- NO SEMANTIC SCHOLAR RECORD" in out
        assert "UNREACHABLE" not in out

    def test_partial_infra_failure_is_partial_coverage_and_fails(
        self, tmp_path, capsys,
    ):
        """MAJOR-3: 1-of-3 measured is not an all-clear, even with edges."""
        payload = _citation_result_json(
            attempted=3, succeeded=1, failed=2, edges=19,
            refs_seen=40, refs_no_doi=5,
            failures={"s2_lookup_failed": 2},
            status="completed_with_errors",
        )
        rc = _run_smoke(tmp_path, payload, _happy_counts())
        out = capsys.readouterr().out

        assert rc == 1
        assert "COVERAGE: PARTIAL (1 of 3 measured)" in out
        assert "Part of the corpus is unmeasured." in out
        assert "FAIL: citation coverage complete" in out
        # The CITES evidence itself is real, so that row still passes.
        assert "PASS: CITES edges >= 1" in out

    def test_data_gap_only_still_passes_but_is_reported(self, tmp_path, capsys):
        """no_s2_id is a corpus property; gating on it would be permanently red."""
        payload = _citation_result_json(
            attempted=3, succeeded=2, failed=1, edges=19,
            refs_seen=40, refs_no_doi=5,
            failures={"no_s2_id": 1},
        )
        rc = _run_smoke(tmp_path, payload, _happy_counts())
        out = capsys.readouterr().out

        assert rc == 0
        assert "COVERAGE: COMPLETE for reachable papers (2 of 3 measured)" in out
        assert "a corpus gap, not a run fault" in out
        assert "citation failure reasons: no_s2_id=1" in out

    def test_clean_run_still_reports_coverage_on_a_pass(self, tmp_path, capsys):
        """MAJOR-3: the report is never suppressed, pass or fail."""
        payload = _citation_result_json(
            attempted=3, succeeded=3, edges=19, refs_seen=40, refs_no_doi=5,
        )
        rc = _run_smoke(tmp_path, payload, _happy_counts())
        out = capsys.readouterr().out

        assert rc == 0
        assert "COVERAGE: COMPLETE (3 of 3 measured)." in out
        assert "EVIDENCE: 19 CITES edge(s) written from 35 DOI-bearing" in out

    def test_never_attempted_is_reported_as_not_attempted(self, tmp_path, capsys):
        rc = _run_smoke(
            tmp_path, _citation_result_json(attempted=0, succeeded=0), _zero_cites(),
        )
        out = capsys.readouterr().out
        assert rc == 1
        assert "COVERAGE: NOT ATTEMPTED" in out

    def test_legacy_result_json_says_unknown_not_zero(self, tmp_path, capsys):
        rc = _run_smoke(tmp_path, _completed_result_json(), _zero_cites())
        out = capsys.readouterr().out
        assert rc == 1
        assert "citations: not reported by this ingest result" in out
        assert "COVERAGE: UNKNOWN" in out
        # Legacy JSON must not be called a regression either.
        assert "REGRESSION" not in out

    def test_legacy_result_json_with_edges_still_passes(self, tmp_path):
        """Backward compatibility: a pre-I-58 result is not retroactively red."""
        rc = _run_smoke(tmp_path, _completed_result_json(), _happy_counts())
        assert rc == 0

    def test_failure_details_surface_per_doi(self, tmp_path, capsys):
        payload = _citation_result_json(
            attempted=1, succeeded=0, failed=1,
            failures={"s2_lookup_failed": 1},
            status="completed_with_errors",
            citation_failure_details={
                "10.48550/arXiv.2309.15217":
                    "s2_lookup_failed: 429 Too Many Requests",
            },
        )
        rc = _run_smoke(tmp_path, payload, _zero_cites())
        out = capsys.readouterr().out
        assert rc == 1
        assert "10.48550/arXiv.2309.15217" in out
        assert "429 Too Many Requests" in out


class TestEvidenceVerdict:
    def test_regression_only_when_doi_bearing_refs_produced_no_edge(
        self, tmp_path, capsys,
    ):
        payload = _citation_result_json(
            attempted=3, succeeded=3, edges=0, refs_seen=40, refs_no_doi=5,
        )
        rc = _run_smoke(tmp_path, payload, _zero_cites())
        out = capsys.readouterr().out

        assert rc == 1
        assert "FAIL: CITES edges >= 1" in out
        assert "EVIDENCE: REGRESSION -- 35 DOI-bearing reference(s) resolved" in out
        assert "create_or_promote_paper_stub / link_paper_cites_paper" in out
        # Coverage was fine; only the evidence is damning.
        assert "PASS: citation coverage complete" in out

    def test_empty_reference_lists_are_absence_not_regression(
        self, tmp_path, capsys,
    ):
        """MAJOR-2(a): S2 answered with nothing to cite. Not a code fault."""
        payload = _citation_result_json(
            attempted=3, succeeded=3, edges=0, refs_seen=0,
        )
        rc = _run_smoke(tmp_path, payload, _zero_cites())
        out = capsys.readouterr().out

        assert rc == 1  # the smoke still wants CITES edges
        assert "EVIDENCE: NO REFERENCES AT SOURCE" in out
        assert "absence at the source, not a code fault" in out
        assert "REGRESSION" not in out

    def test_all_references_lacking_dois_is_not_a_regression(
        self, tmp_path, capsys,
    ):
        payload = _citation_result_json(
            attempted=3, succeeded=3, edges=0,
            refs_seen=40, refs_with_doi=0, refs_no_doi=40,
        )
        rc = _run_smoke(tmp_path, payload, _zero_cites())
        out = capsys.readouterr().out

        assert rc == 1
        assert "EVIDENCE: NO DOI-BEARING REFERENCES -- 40 reference(s)" in out
        assert "references dropped for lacking a DOI: 40" in out
        assert "REGRESSION" not in out

    def test_throttled_mixed_run_is_never_called_a_code_fault(
        self, tmp_path, capsys,
    ):
        """MAJOR-2(b): 1 succeeded with 0 refs + 2 throttled.

        The old output asserted "not a throttling artifact" on the line
        directly above ``s2_fetch_failed=2``.
        """
        payload = _citation_result_json(
            attempted=3, succeeded=1, failed=2, edges=0, refs_seen=0,
            failures={"s2_fetch_failed": 2},
            status="completed_with_errors",
        )
        rc = _run_smoke(tmp_path, payload, _zero_cites())
        out = capsys.readouterr().out

        assert rc == 1
        assert "COVERAGE: PARTIAL (1 of 3 measured)" in out
        assert "EVIDENCE: NO REFERENCES AT SOURCE" in out
        assert "not a throttling artifact" not in out
        assert "REGRESSION" not in out

    def test_missing_reference_counts_will_not_claim_a_regression(
        self, tmp_path, capsys,
    ):
        payload = _citation_result_json(attempted=3, succeeded=3, edges=0)
        rc = _run_smoke(tmp_path, payload, _zero_cites())
        out = capsys.readouterr().out

        assert rc == 1
        assert "EVIDENCE: UNKNOWN" in out
        assert "REGRESSION" not in out


class TestDegradedStatusIsInspectableButFailsClosed:
    def test_completed_with_errors_does_not_short_circuit(self, tmp_path, capsys):
        payload = _citation_result_json(
            attempted=3, succeeded=0, failed=3,
            failures={"s2_lookup_failed": 3},
            status="completed_with_errors",
        )
        result_path = _write_result(tmp_path, payload)
        session = _make_session_returning(_zero_cites())
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
            return_value=_mock_repo(session),
        ) as get_repo:
            rc = smoke_assert.main(str(result_path))

        assert rc == 1
        get_repo.assert_called_once()
        out = capsys.readouterr().out
        assert "=== Smoke-test graph-shape assertions ===" in out
        assert "PASS: papers >= 1" in out

    def test_degraded_status_without_evidence_fails_closed(self, tmp_path, capsys):
        """S9: the widened status must not be a hole.

        A degraded status whose justifying fields are missing is
        unattributable, and unattributable is never a pass.
        """
        payload = {"status": "completed_with_errors", "extraction_errors": {}}
        rc = _run_smoke(tmp_path, payload, _happy_counts())  # cites = 8
        out = capsys.readouterr().out

        assert rc == 1
        assert "COVERAGE: UNATTRIBUTABLE" in out
        assert "FAIL: CITES edges >= 1" in out
        assert "FAIL: citation coverage complete" in out

    def test_unrecognized_status_still_short_circuits(self, tmp_path, capsys):
        result_path = _write_result(tmp_path, {"status": "running"})
        with patch(
            "agentic_kg.knowledge_graph.repository.get_repository",
        ) as get_repo:
            rc = smoke_assert.main(str(result_path))
        assert rc == 1
        get_repo.assert_not_called()
        assert "FAIL: ingest_papers status='running'" in capsys.readouterr().out


class TestEvaluateChecksCitationGate:
    def _ones(self) -> dict[str, int]:
        return {k: 1 for k in (
            "papers", "topic_edges", "concepts",
            "models", "methods", "cites", "tagged",
        )}

    def test_unmeasured_flips_cites_check_false(self):
        counts = self._ones()
        assert smoke_assert._evaluate_checks(counts)["CITES edges >= 1"]
        assert not smoke_assert._evaluate_checks(
            counts, {"unmeasured": True},
        )["CITES edges >= 1"]

    def test_unattributable_flips_cites_check_false(self):
        assert not smoke_assert._evaluate_checks(
            self._ones(), {"unattributable": True},
        )["CITES edges >= 1"]

    def test_coverage_check_is_independent_of_cites(self):
        checks = smoke_assert._evaluate_checks(
            self._ones(), {"coverage_incomplete": True},
        )
        assert checks["CITES edges >= 1"] is True
        assert checks["citation coverage complete"] is False

    def test_citation_gate_leaves_other_checks_untouched(self):
        checks = smoke_assert._evaluate_checks(
            self._ones(), {"unmeasured": True, "coverage_incomplete": True},
        )
        assert len(checks) == 7
        assert all(
            v for k, v in checks.items()
            if k not in ("CITES edges >= 1", "citation coverage complete")
        )


class TestCitationEvidence:
    def test_absent_fields_are_not_reported_as_zero(self):
        ev = smoke_assert._citation_evidence({"status": "completed"})
        assert ev["reported"] is False
        assert ev["unmeasured"] is False
        assert ev["coverage_incomplete"] is False

    def test_attempted_with_no_successes_is_unmeasured(self):
        ev = smoke_assert._citation_evidence(_citation_result_json(3, 0, failed=3))
        assert ev["reported"] is True
        assert ev["unmeasured"] is True
        assert ev["coverage_incomplete"] is True

    def test_partial_infra_failure_is_incomplete_but_measured(self):
        ev = smoke_assert._citation_evidence(_citation_result_json(
            3, 1, failed=2, failures={"s2_fetch_failed": 2},
        ))
        assert ev["unmeasured"] is False
        assert ev["infra"] == 2
        assert ev["coverage_incomplete"] is True

    def test_data_gap_alone_is_complete_coverage(self):
        ev = smoke_assert._citation_evidence(_citation_result_json(
            3, 2, failed=1, failures={"no_s2_id": 1},
        ))
        assert ev["infra"] == 0
        assert ev["coverage_incomplete"] is False

    def test_zero_attempts_is_not_unmeasured(self):
        ev = smoke_assert._citation_evidence(_citation_result_json(0, 0))
        assert ev["unmeasured"] is False
        assert ev["coverage_incomplete"] is False

    def test_degraded_status_without_fields_is_unattributable(self):
        ev = smoke_assert._citation_evidence({"status": "completed_with_errors"})
        assert ev["unattributable"] is True
        assert ev["coverage_incomplete"] is True

    def test_non_int_values_degrade_to_zero(self):
        ev = smoke_assert._citation_evidence({
            "citation_population_attempted": "3",
            "citation_population_succeeded": None,
            "citation_failures": "oops",
        })
        assert ev["attempted"] == 0
        assert ev["succeeded"] == 0
        assert ev["failures"] == {}


# =============================================================================
# R5: idempotent re-runs, and not claiming a pass we cannot justify
# =============================================================================


class TestIdempotentRerun:
    def test_already_linked_is_not_a_regression(self, tmp_path, capsys):
        """MINOR-B: a re-run over a populated graph writes no new edges."""
        payload = _citation_result_json(
            attempted=3, succeeded=3, edges=0, edges_existing=28,
            refs_seen=33, refs_no_doi=5,
        )
        rc = _run_smoke(tmp_path, payload, _happy_counts())
        out = capsys.readouterr().out

        assert rc == 0
        assert "EVIDENCE: ALREADY LINKED -- 28 CITES edge(s)" in out
        assert "REGRESSION" not in out

    def test_regression_still_fires_when_nothing_was_already_present(
        self, tmp_path, capsys,
    ):
        payload = _citation_result_json(
            attempted=3, succeeded=3, edges=0, edges_existing=0,
            refs_seen=40, refs_no_doi=5,
        )
        rc = _run_smoke(tmp_path, payload, _zero_cites())
        out = capsys.readouterr().out

        assert rc == 1
        assert "EVIDENCE: REGRESSION" in out
        assert "(0 already present)" in out

    def test_mixed_new_and_existing_edges_reported(self, tmp_path, capsys):
        payload = _citation_result_json(
            attempted=3, succeeded=3, edges=7, edges_existing=21,
            refs_seen=33, refs_no_doi=5,
        )
        rc = _run_smoke(tmp_path, payload, _happy_counts())
        out = capsys.readouterr().out

        assert rc == 0
        assert "EVIDENCE: 7 CITES edge(s) written from 28 DOI-bearing" in out
        assert "(21 already present)" in out


class TestCoverageCheckHonesty:
    def test_legacy_pass_is_labelled_not_evaluated(self, tmp_path, capsys):
        """NIT-D: a bare PASS under COVERAGE: UNKNOWN claims too much."""
        rc = _run_smoke(tmp_path, _completed_result_json(), _happy_counts())
        out = capsys.readouterr().out

        assert rc == 0
        assert "COVERAGE: UNKNOWN" in out
        assert "PASS: citation coverage complete" in out
        assert "(not evaluated" in out
        assert "passed for backward compatibility" in out

    def test_reported_pass_carries_no_caveat(self, tmp_path, capsys):
        payload = _citation_result_json(
            attempted=3, succeeded=3, edges=19, refs_seen=40, refs_no_doi=5,
        )
        rc = _run_smoke(tmp_path, payload, _happy_counts())
        out = capsys.readouterr().out

        assert rc == 0
        assert "not evaluated" not in out
