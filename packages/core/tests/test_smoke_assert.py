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
        # All 6 checks report PASS.
        assert out.count("PASS:") == 6
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
        assert not any(result.values())
        assert len(result) == 6

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
# Issue #58: citation-population failures must be legible, not a bare zero
#
# The old CITES check was binary — cites>=1 or bust — so the two failure
# modes an operator actually needs to tell apart (Semantic Scholar was
# unreachable vs. the citation graph regressed) rendered identically.
# These tests pin the diagnosis text and the "unmeasured is never a pass"
# rule.
# =============================================================================


def _citation_result_json(
    attempted: int,
    succeeded: int,
    failed: int = 0,
    edges: int = 0,
    stubs: int = 0,
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
        "citation_stubs_created": stubs,
        "citation_failures": failures or {},
    }
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


class TestCitationDiagnosis:
    def test_all_citation_failures_report_infrastructure(self, tmp_path, capsys):
        counts = _happy_counts()
        counts["cites"] = 0
        payload = _citation_result_json(
            attempted=3, succeeded=0, failed=3,
            failures={"s2_lookup_failed": 3},
            status="completed_with_errors",
            sources_rate_limited=1,
            search_errors={"semantic_scholar": "Rate limit exceeded"},
        )
        rc = _run_smoke(tmp_path, payload, counts)
        out = capsys.readouterr().out

        assert rc == 1  # still a hard failure — the gate is not weakened
        assert "FAIL: CITES edges >= 1" in out
        assert "DIAGNOSIS: NOT MEASURED (INFRASTRUCTURE)" in out
        assert "attempted on 3 paper(s) and succeeded on 0" in out
        assert "is NOT evidence that these papers cite nothing" in out
        assert "citation failure reasons: s2_lookup_failed=3" in out
        # Corroborating evidence already present in the result JSON.
        assert "search sources rate-limited this run: 1" in out
        assert "Rate limit exceeded" in out
        # The REGRESSION verdict must NOT appear for an infra failure.
        assert "REGRESSION" not in out

    def test_all_failures_without_infra_reason_say_no_s2_record(
        self, tmp_path, capsys,
    ):
        counts = _happy_counts()
        counts["cites"] = 0
        payload = _citation_result_json(
            attempted=2, succeeded=0, failed=2,
            failures={"no_s2_id": 2},
            status="completed_with_errors",
        )
        rc = _run_smoke(tmp_path, payload, counts)
        out = capsys.readouterr().out

        assert rc == 1
        assert "DIAGNOSIS: NOT MEASURED (NO SEMANTIC SCHOLAR RECORD)" in out
        assert "INFRASTRUCTURE" not in out

    def test_s2_reachable_but_zero_citations_reports_regression(
        self, tmp_path, capsys,
    ):
        """The other failure: S2 answered for every paper, nothing was written."""
        counts = _happy_counts()
        counts["cites"] = 0
        payload = _citation_result_json(attempted=3, succeeded=3, edges=0)
        rc = _run_smoke(tmp_path, payload, counts)
        out = capsys.readouterr().out

        assert rc == 1
        assert "FAIL: CITES edges >= 1" in out
        assert "DIAGNOSIS: REGRESSION" in out
        assert "returned reference lists for 3 paper(s)" in out
        assert "NOT MEASURED" not in out

    def test_unmeasured_fails_even_when_cites_edges_exist(self, tmp_path, capsys):
        """An unattributable count is not a pass.

        CITES edges may be left over from an earlier write; if this run
        measured nothing, it proved nothing.
        """
        counts = _happy_counts()  # cites = 8
        payload = _citation_result_json(
            attempted=3, succeeded=0, failed=3,
            failures={"s2_fetch_failed": 3},
            status="completed_with_errors",
        )
        rc = _run_smoke(tmp_path, payload, counts)
        out = capsys.readouterr().out

        assert rc == 1
        assert "FAIL: CITES edges >= 1" in out
        assert "DIAGNOSIS: NOT MEASURED (INFRASTRUCTURE)" in out

    def test_partial_citation_success_with_edges_passes(self, tmp_path, capsys):
        """Something real was measured and edges landed — a legitimate pass."""
        counts = _happy_counts()
        payload = _citation_result_json(
            attempted=3, succeeded=1, failed=2, edges=19,
            failures={"s2_lookup_failed": 2},
        )
        rc = _run_smoke(tmp_path, payload, counts)
        out = capsys.readouterr().out

        assert rc == 0
        assert "Smoke test PASSED." in out
        assert "citations: attempted=3 succeeded=1 failed=2" in out
        assert "DIAGNOSIS" not in out

    def test_never_attempted_is_reported_as_not_attempted(self, tmp_path, capsys):
        counts = _happy_counts()
        counts["cites"] = 0
        payload = _citation_result_json(attempted=0, succeeded=0)
        rc = _run_smoke(tmp_path, payload, counts)
        out = capsys.readouterr().out

        assert rc == 1
        assert "DIAGNOSIS: NOT ATTEMPTED" in out

    def test_legacy_result_json_says_unknown_not_zero(self, tmp_path, capsys):
        """A pre-I-58 result file cannot attribute its zero; say so."""
        counts = _happy_counts()
        counts["cites"] = 0
        rc = _run_smoke(tmp_path, _completed_result_json(), counts)
        out = capsys.readouterr().out

        assert rc == 1
        assert "citations: not reported by this ingest result" in out
        assert "DIAGNOSIS: UNKNOWN" in out

    def test_citation_failure_details_surface_per_doi(self, tmp_path, capsys):
        counts = _happy_counts()
        counts["cites"] = 0
        payload = _citation_result_json(
            attempted=1, succeeded=0, failed=1,
            failures={"s2_lookup_failed": 1},
            status="completed_with_errors",
            citation_failure_details={
                "10.48550/arXiv.2309.15217":
                    "s2_lookup_failed: 429 Too Many Requests",
            },
        )
        rc = _run_smoke(tmp_path, payload, counts)
        out = capsys.readouterr().out

        assert rc == 1
        assert "10.48550/arXiv.2309.15217" in out
        assert "429 Too Many Requests" in out


class TestDegradedStatusIsInspectable:
    def test_completed_with_errors_does_not_short_circuit(self, tmp_path, capsys):
        """The degraded status must still run the graph checks.

        Early-exiting on it would hide exactly the evidence the operator
        needs to tell the two failure cases apart.
        """
        counts = _happy_counts()
        counts["cites"] = 0
        payload = _citation_result_json(
            attempted=3, succeeded=0, failed=3,
            failures={"s2_lookup_failed": 3},
            status="completed_with_errors",
        )
        result_path = _write_result(tmp_path, payload)
        session = _make_session_returning(counts)
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
    def test_unmeasured_flips_cites_check_false(self):
        counts = {k: 1 for k in (
            "papers", "topic_edges", "concepts",
            "models", "methods", "cites", "tagged",
        )}
        assert smoke_assert._evaluate_checks(counts)["CITES edges >= 1"]
        assert not smoke_assert._evaluate_checks(
            counts, {"unmeasured": True},
        )["CITES edges >= 1"]

    def test_citation_gate_leaves_other_checks_untouched(self):
        counts = {k: 1 for k in (
            "papers", "topic_edges", "concepts",
            "models", "methods", "cites", "tagged",
        )}
        checks = smoke_assert._evaluate_checks(counts, {"unmeasured": True})
        assert len(checks) == 6
        assert all(v for k, v in checks.items() if k != "CITES edges >= 1")


class TestCitationEvidence:
    def test_absent_fields_are_not_reported_as_zero(self):
        ev = smoke_assert._citation_evidence({"status": "completed"})
        assert ev["reported"] is False
        assert ev["unmeasured"] is False

    def test_attempted_with_no_successes_is_unmeasured(self):
        ev = smoke_assert._citation_evidence(_citation_result_json(3, 0, failed=3))
        assert ev["reported"] is True
        assert ev["unmeasured"] is True

    def test_partial_success_is_measured(self):
        ev = smoke_assert._citation_evidence(_citation_result_json(3, 1, failed=2))
        assert ev["unmeasured"] is False

    def test_zero_attempts_is_not_unmeasured(self):
        ev = smoke_assert._citation_evidence(_citation_result_json(0, 0))
        assert ev["unmeasured"] is False

    def test_non_int_values_degrade_to_zero(self):
        ev = smoke_assert._citation_evidence({
            "citation_population_attempted": "3",
            "citation_population_succeeded": None,
            "citation_failures": "oops",
        })
        assert ev["attempted"] == 0
        assert ev["succeeded"] == 0
        assert ev["failures"] == {}
