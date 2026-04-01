"""
Tests for the Cloud Run Job runner module.

Tests environment variable parsing, IngestionRun persistence,
exit code determination, and the main entrypoint.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from agentic_kg.job_runner import (
    _parse_env,
    persist_ingestion_run,
    _determine_exit_code,
    main,
)
from agentic_kg.ingestion import IngestionResult


# =============================================================================
# _parse_env Tests
# =============================================================================


class TestParseEnv:
    """Tests for environment variable parsing."""

    def test_missing_query_exits_with_code_2(self, monkeypatch):
        """Missing INGEST_QUERY causes sys.exit(2)."""
        monkeypatch.delenv("INGEST_QUERY", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            _parse_env()
        assert exc_info.value.code == 2

    def test_minimal_config_with_defaults(self, monkeypatch):
        """Only INGEST_QUERY required; others use defaults."""
        monkeypatch.setenv("INGEST_QUERY", "graph retrieval")
        monkeypatch.delenv("INGEST_LIMIT", raising=False)
        monkeypatch.delenv("INGEST_SOURCES", raising=False)
        monkeypatch.delenv("INGEST_TRACE_ID", raising=False)
        monkeypatch.delenv("INGEST_AGENT_WORKFLOW", raising=False)
        monkeypatch.delenv("INGEST_MIN_CONFIDENCE", raising=False)

        config = _parse_env()

        assert config["query"] == "graph retrieval"
        assert config["limit"] == 20
        assert config["sources"] is None
        assert config["enable_agent_workflow"] is True
        assert config["min_extraction_confidence"] == 0.5
        assert config["trace_id"].startswith("ingest-")

    def test_full_config_from_env(self, monkeypatch):
        """All env vars parsed correctly."""
        monkeypatch.setenv("INGEST_QUERY", "knowledge graphs")
        monkeypatch.setenv("INGEST_LIMIT", "10")
        monkeypatch.setenv("INGEST_SOURCES", "arxiv,semantic_scholar")
        monkeypatch.setenv("INGEST_TRACE_ID", "ingest-custom123")
        monkeypatch.setenv("INGEST_AGENT_WORKFLOW", "false")
        monkeypatch.setenv("INGEST_MIN_CONFIDENCE", "0.7")

        config = _parse_env()

        assert config["query"] == "knowledge graphs"
        assert config["limit"] == 10
        assert config["sources"] == ["arxiv", "semantic_scholar"]
        assert config["trace_id"] == "ingest-custom123"
        assert config["enable_agent_workflow"] is False
        assert config["min_extraction_confidence"] == 0.7

    def test_empty_sources_string_becomes_none(self, monkeypatch):
        """Empty INGEST_SOURCES string results in None."""
        monkeypatch.setenv("INGEST_QUERY", "test")
        monkeypatch.setenv("INGEST_SOURCES", "")

        config = _parse_env()
        assert config["sources"] is None

    def test_sources_with_whitespace_trimmed(self, monkeypatch):
        """Source names are trimmed of whitespace."""
        monkeypatch.setenv("INGEST_QUERY", "test")
        monkeypatch.setenv("INGEST_SOURCES", " arxiv , openalex ")

        config = _parse_env()
        assert config["sources"] == ["arxiv", "openalex"]


# =============================================================================
# persist_ingestion_run Tests
# =============================================================================


class TestPersistIngestionRun:
    """Tests for Neo4j IngestionRun node persistence."""

    def _make_result(self, **kwargs):
        return IngestionResult(
            trace_id=kwargs.get("trace_id", "ingest-test"),
            query=kwargs.get("query", "test"),
            status=kwargs.get("status", "completed"),
            papers_found=kwargs.get("papers_found", 10),
            papers_imported=kwargs.get("papers_imported", 8),
            papers_extracted=kwargs.get("papers_extracted", 6),
            papers_skipped_no_pdf=kwargs.get("papers_skipped_no_pdf", 2),
            total_problems=kwargs.get("total_problems", 20),
            concepts_created=kwargs.get("concepts_created", 15),
            concepts_linked=kwargs.get("concepts_linked", 5),
            extraction_errors=kwargs.get("extraction_errors", {}),
        )

    def test_writes_ingestion_run_node(self):
        """Persists all fields to Neo4j."""
        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        result = self._make_result(
            extraction_errors={"10.1/a": "timeout"}
        )
        started_at = datetime(2026, 3, 31, 10, 0, 0, tzinfo=timezone.utc)

        with patch("agentic_kg.job_runner.get_repository", return_value=mock_repo):
            success = persist_ingestion_run("ingest-test", "test query", result, started_at)

        assert success is True
        mock_session.run.assert_called_once()
        call_args = mock_session.run.call_args
        props = call_args.kwargs["props"]
        assert props["trace_id"] == "ingest-test"
        assert props["query"] == "test query"
        assert props["status"] == "completed"
        assert props["papers_found"] == 10
        assert props["total_problems"] == 20
        assert json.loads(props["extraction_errors"]) == {"10.1/a": "timeout"}
        assert props["started_at"] == "2026-03-31T10:00:00+00:00"

    def test_returns_false_on_neo4j_failure(self):
        """Returns False if Neo4j write fails."""
        mock_repo = MagicMock()
        mock_repo.session.side_effect = RuntimeError("Connection refused")

        result = self._make_result()
        started_at = datetime.now(timezone.utc)

        with patch("agentic_kg.job_runner.get_repository", return_value=mock_repo):
            success = persist_ingestion_run("ingest-test", "test", result, started_at)

        assert success is False


# =============================================================================
# _determine_exit_code Tests
# =============================================================================


class TestDetermineExitCode:
    """Tests for exit code determination."""

    def test_complete_returns_0(self):
        result = IngestionResult(trace_id="t", query="q", status="completed")
        assert _determine_exit_code(result) == 0

    def test_failed_returns_2(self):
        result = IngestionResult(trace_id="t", query="q", status="failed")
        assert _determine_exit_code(result) == 2

    def test_partial_errors_returns_1(self):
        result = IngestionResult(
            trace_id="t", query="q", status="completed",
            extraction_errors={"10.1/a": "timeout"},
        )
        assert _determine_exit_code(result) == 1

    def test_failed_with_errors_returns_2(self):
        """Fatal failure takes precedence over partial errors."""
        result = IngestionResult(
            trace_id="t", query="q", status="failed",
            extraction_errors={"10.1/a": "timeout"},
        )
        assert _determine_exit_code(result) == 2


# =============================================================================
# main() Tests
# =============================================================================


class TestMain:
    """Tests for the main entrypoint."""

    def test_main_complete_exits_0(self, monkeypatch):
        """Successful ingestion exits with code 0."""
        monkeypatch.setenv("INGEST_QUERY", "test")
        monkeypatch.setenv("INGEST_TRACE_ID", "ingest-main-test")

        mock_result = IngestionResult(
            trace_id="ingest-main-test",
            query="test",
            status="completed",
            papers_found=5,
        )

        with (
            patch("agentic_kg.job_runner.ingest_papers", new_callable=AsyncMock) as mock_ingest,
            patch("agentic_kg.job_runner.persist_ingestion_run", return_value=True),
        ):
            mock_ingest.return_value = mock_result
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_ingest.assert_called_once()
        call_kwargs = mock_ingest.call_args.kwargs
        assert call_kwargs["query"] == "test"

    def test_main_partial_exits_1(self, monkeypatch):
        """Partial success (some errors) exits with code 1."""
        monkeypatch.setenv("INGEST_QUERY", "test")
        monkeypatch.setenv("INGEST_TRACE_ID", "ingest-partial")

        mock_result = IngestionResult(
            trace_id="ingest-partial",
            query="test",
            status="completed",
            extraction_errors={"10.1/a": "pdf failed"},
        )

        with (
            patch("agentic_kg.job_runner.ingest_papers", new_callable=AsyncMock) as mock_ingest,
            patch("agentic_kg.job_runner.persist_ingestion_run", return_value=True),
        ):
            mock_ingest.return_value = mock_result
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1

    def test_main_fatal_exits_2(self, monkeypatch):
        """Fatal failure exits with code 2."""
        monkeypatch.setenv("INGEST_QUERY", "test")
        monkeypatch.setenv("INGEST_TRACE_ID", "ingest-fatal")

        mock_result = IngestionResult(
            trace_id="ingest-fatal",
            query="test",
            status="failed",
            error="API unreachable",
        )

        with (
            patch("agentic_kg.job_runner.ingest_papers", new_callable=AsyncMock) as mock_ingest,
            patch("agentic_kg.job_runner.persist_ingestion_run", return_value=True),
        ):
            mock_ingest.return_value = mock_result
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2

    def test_main_missing_query_exits_2(self, monkeypatch):
        """Missing INGEST_QUERY exits with code 2 before ingestion starts."""
        monkeypatch.delenv("INGEST_QUERY", raising=False)

        with patch("agentic_kg.job_runner.ingest_papers") as mock_ingest:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        mock_ingest.assert_not_called()

    def test_main_persists_ingestion_run(self, monkeypatch):
        """Main calls persist_ingestion_run with correct arguments."""
        monkeypatch.setenv("INGEST_QUERY", "graph retrieval")
        monkeypatch.setenv("INGEST_TRACE_ID", "ingest-persist-test")

        mock_result = IngestionResult(
            trace_id="ingest-persist-test",
            query="graph retrieval",
            status="completed",
        )

        with (
            patch("agentic_kg.job_runner.ingest_papers", new_callable=AsyncMock) as mock_ingest,
            patch("agentic_kg.job_runner.persist_ingestion_run") as mock_persist,
        ):
            mock_ingest.return_value = mock_result
            mock_persist.return_value = True
            with pytest.raises(SystemExit):
                main()

        mock_persist.assert_called_once()
        args = mock_persist.call_args
        assert args[0][0] == "ingest-persist-test"
        assert args[0][1] == "graph retrieval"
        assert args[0][2] is mock_result

    def test_main_passes_all_config_to_ingest(self, monkeypatch):
        """All env vars are forwarded to ingest_papers."""
        monkeypatch.setenv("INGEST_QUERY", "test")
        monkeypatch.setenv("INGEST_LIMIT", "15")
        monkeypatch.setenv("INGEST_SOURCES", "arxiv")
        monkeypatch.setenv("INGEST_AGENT_WORKFLOW", "false")
        monkeypatch.setenv("INGEST_MIN_CONFIDENCE", "0.8")
        monkeypatch.setenv("INGEST_TRACE_ID", "ingest-cfg")

        mock_result = IngestionResult(trace_id="ingest-cfg", query="test", status="completed")

        with (
            patch("agentic_kg.job_runner.ingest_papers", new_callable=AsyncMock) as mock_ingest,
            patch("agentic_kg.job_runner.persist_ingestion_run", return_value=True),
        ):
            mock_ingest.return_value = mock_result
            with pytest.raises(SystemExit):
                main()

        call_kwargs = mock_ingest.call_args.kwargs
        assert call_kwargs["query"] == "test"
        assert call_kwargs["limit"] == 15
        assert call_kwargs["sources"] == ["arxiv"]
        assert call_kwargs["enable_agent_workflow"] is False
        assert call_kwargs["min_extraction_confidence"] == 0.8
