"""
Tests for CLI ingest subcommand.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentic_kg.cli import build_parser, main
from agentic_kg.ingestion import IngestionResult, SanityCheck


class TestIngestParser:
    """Tests for ingest argument parsing."""

    def test_ingest_subcommand_exists(self):
        parser = build_parser()
        args = parser.parse_args(["ingest", "--query", "test"])
        assert args.command == "ingest"
        assert args.query == "test"

    def test_ingest_default_limit(self):
        parser = build_parser()
        args = parser.parse_args(["ingest", "--query", "test"])
        assert args.limit == 20

    def test_ingest_custom_limit(self):
        parser = build_parser()
        args = parser.parse_args(["ingest", "--query", "test", "--limit", "10"])
        assert args.limit == 10

    def test_ingest_sources(self):
        parser = build_parser()
        args = parser.parse_args(["ingest", "--query", "test", "--sources", "arxiv", "openalex"])
        assert args.sources == ["arxiv", "openalex"]

    def test_ingest_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["ingest", "--query", "test", "--dry-run"])
        assert args.dry_run is True

    def test_ingest_no_agent_workflow(self):
        parser = build_parser()
        args = parser.parse_args(["ingest", "--query", "test", "--no-agent-workflow"])
        assert args.no_agent_workflow is True

    def test_ingest_sanity_check_only(self):
        parser = build_parser()
        args = parser.parse_args(["ingest", "--sanity-check-only"])
        assert args.sanity_check_only is True

    def test_ingest_json_output(self):
        parser = build_parser()
        args = parser.parse_args(["ingest", "--query", "test", "--json"])
        assert args.json_output is True

    def test_ingest_verbose(self):
        parser = build_parser()
        args = parser.parse_args(["ingest", "--query", "test", "-v"])
        assert args.verbose is True


class TestIngestCLIExecution:
    """Tests for CLI ingest execution."""

    def test_ingest_calls_ingest_papers(self):
        """Verify CLI calls ingest_papers with correct arguments."""
        mock_result = IngestionResult(
            trace_id="test-001",
            query="graph retrieval",
            status="completed",
            papers_found=5,
        )

        with patch("agentic_kg.ingestion.ingest_papers", new_callable=AsyncMock) as mock_ingest:
            mock_ingest.return_value = mock_result
            with patch("agentic_kg.ingestion.run_sanity_checks"):
                main(["ingest", "--query", "graph retrieval", "--limit", "10", "--json"])

        mock_ingest.assert_called_once()
        call_kwargs = mock_ingest.call_args
        assert call_kwargs.kwargs["query"] == "graph retrieval"
        assert call_kwargs.kwargs["limit"] == 10

    def test_ingest_dry_run_passes_flag(self):
        """Verify dry_run flag is passed through."""
        mock_result = IngestionResult(
            trace_id="test-001",
            query="test",
            status="dry_run",
        )

        with patch("agentic_kg.ingestion.ingest_papers", new_callable=AsyncMock) as mock_ingest:
            mock_ingest.return_value = mock_result
            main(["ingest", "--query", "test", "--dry-run", "--json"])

        assert mock_ingest.call_args.kwargs["dry_run"] is True

    def test_ingest_sanity_check_only(self):
        """Verify --sanity-check-only runs checks without ingestion."""
        checks = [SanityCheck(name="test", passed=True, count=0, description="ok")]

        with patch("agentic_kg.ingestion.run_sanity_checks", return_value=checks) as mock_checks:
            with patch("agentic_kg.ingestion.ingest_papers") as mock_ingest:
                with pytest.raises(SystemExit) as exc_info:
                    main(["ingest", "--sanity-check-only", "--json"])
                assert exc_info.value.code == 0

        mock_checks.assert_called_once()
        mock_ingest.assert_not_called()

    def test_ingest_no_query_without_sanity_check_exits(self):
        """Without --query and without --sanity-check-only, exit with error."""
        with pytest.raises(SystemExit) as exc_info:
            main(["ingest"])
        assert exc_info.value.code == 1

    def test_ingest_failed_exits_nonzero(self):
        """Failed ingestion exits with code 1."""
        mock_result = IngestionResult(
            trace_id="test-001",
            query="test",
            status="failed",
            error="Something broke",
        )

        with patch("agentic_kg.ingestion.ingest_papers", new_callable=AsyncMock) as mock_ingest:
            mock_ingest.return_value = mock_result
            with pytest.raises(SystemExit) as exc_info:
                main(["ingest", "--query", "test", "--json"])
        assert exc_info.value.code == 1
