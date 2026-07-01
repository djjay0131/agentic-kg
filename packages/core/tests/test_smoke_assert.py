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
        assert str(missing) in out

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
        assert "FAIL: BELONGS_TO topic edges >= 1" in capsys.readouterr().out

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
