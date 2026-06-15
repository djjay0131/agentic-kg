"""E-8 V2 Unit 7 — CLI flag + Cloud Run Job env var forwarding.

Covers AC-13 (CLI flag → ingest_papers) and AC-14 (env var → ingest_papers).
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agentic_kg.cli import build_parser, main

# =============================================================================
# AC-13 — CLI flag is wired through ingest_papers
# =============================================================================


class TestCLIPopulateCitationsFlag:
    def test_default_is_true(self):
        parser = build_parser()
        ns = parser.parse_args(["ingest", "--query", "x"])
        assert ns.populate_citations is True

    def test_no_flag_flips_to_false(self):
        parser = build_parser()
        ns = parser.parse_args([
            "ingest", "--query", "x", "--no-populate-citations",
        ])
        assert ns.populate_citations is False

    def test_main_default_passes_true_to_ingest_papers(self):
        with patch("agentic_kg.ingestion.ingest_papers") as fake_ingest:
            fake_ingest.return_value = MagicMock(
                status="ok", extraction_errors=[],
            )
            # Wrap as a coroutine so the await in run_ingest is satisfied.
            fake_ingest.side_effect = AsyncMock(return_value=MagicMock(
                status="ok", extraction_errors=[],
            ))
            main(["ingest", "--query", "test"])

        kwargs = fake_ingest.call_args.kwargs
        assert kwargs["populate_citations"] is True

    def test_main_no_flag_passes_false_to_ingest_papers(self):
        with patch("agentic_kg.ingestion.ingest_papers") as fake_ingest:
            fake_ingest.side_effect = AsyncMock(return_value=MagicMock(
                status="ok", extraction_errors=[],
            ))
            main([
                "ingest", "--query", "test", "--no-populate-citations",
            ])

        kwargs = fake_ingest.call_args.kwargs
        assert kwargs["populate_citations"] is False


# =============================================================================
# AC-14 — Cloud Run Job env var POPULATE_CITATIONS
# =============================================================================


class TestJobRunnerEnvVar:
    def test_unset_defaults_to_true(self, monkeypatch):
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.delenv("POPULATE_CITATIONS", raising=False)
        config = _parse_env()
        assert config["populate_citations"] is True

    def test_env_true_is_true(self, monkeypatch):
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.setenv("POPULATE_CITATIONS", "true")
        assert _parse_env()["populate_citations"] is True

    def test_env_false_is_false(self, monkeypatch):
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.setenv("POPULATE_CITATIONS", "false")
        assert _parse_env()["populate_citations"] is False

    def test_env_false_case_insensitive(self, monkeypatch):
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.setenv("POPULATE_CITATIONS", "FALSE")
        assert _parse_env()["populate_citations"] is False

    def test_env_unknown_value_treated_as_true(self, monkeypatch):
        """Any non-`false` value (including malformed input) preserves the
        default-on behavior — operators have to type `false` to disable."""
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.setenv("POPULATE_CITATIONS", "garbage")
        assert _parse_env()["populate_citations"] is True
