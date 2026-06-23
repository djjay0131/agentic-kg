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


class TestJobRunnerEntityPipelineEnvVars:
    """AC-2: entity-pipeline-orchestration env vars."""

    def test_extract_entities_defaults_true(self, monkeypatch):
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.delenv("EXTRACT_ENTITIES", raising=False)
        assert _parse_env()["extract_entities"] is True

    def test_extract_entities_false_disables(self, monkeypatch):
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.setenv("EXTRACT_ENTITIES", "FALSE")
        assert _parse_env()["extract_entities"] is False

    def test_normalize_cross_entity_defaults_true(self, monkeypatch):
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.delenv("NORMALIZE_CROSS_ENTITY", raising=False)
        assert _parse_env()["normalize_cross_entity_collisions"] is True

    def test_normalize_cross_entity_false_disables(self, monkeypatch):
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.setenv("NORMALIZE_CROSS_ENTITY", "false")
        assert _parse_env()["normalize_cross_entity_collisions"] is False

    def test_force_reextract_defaults_false(self, monkeypatch):
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.delenv("FORCE_REEXTRACT", raising=False)
        assert _parse_env()["force_reextract"] is False

    def test_force_reextract_true_enables(self, monkeypatch):
        from agentic_kg.job_runner import _parse_env

        monkeypatch.setenv("INGEST_QUERY", "x")
        monkeypatch.setenv("FORCE_REEXTRACT", "TRUE")
        assert _parse_env()["force_reextract"] is True


class TestCLIEntityPipelineFlags:
    """AC-1: CLI flag parsing for the 3 new flags."""

    def test_extract_entities_defaults_true(self):
        from agentic_kg.cli import build_parser

        parser = build_parser()
        ns = parser.parse_args(["ingest", "--query", "x"])
        assert ns.extract_entities is True
        assert ns.normalize_cross_entity_collisions is True
        assert ns.force_reextract is False

    def test_no_extract_entities_flips_false(self):
        from agentic_kg.cli import build_parser

        parser = build_parser()
        ns = parser.parse_args([
            "ingest", "--query", "x", "--no-extract-entities",
        ])
        assert ns.extract_entities is False

    def test_no_normalize_cross_entity_flips_false(self):
        from agentic_kg.cli import build_parser

        parser = build_parser()
        ns = parser.parse_args([
            "ingest", "--query", "x", "--no-normalize-cross-entity",
        ])
        assert ns.normalize_cross_entity_collisions is False

    def test_force_reextract_flips_true(self):
        from agentic_kg.cli import build_parser

        parser = build_parser()
        ns = parser.parse_args([
            "ingest", "--query", "x", "--force-reextract",
        ])
        assert ns.force_reextract is True

    def test_main_forwards_all_three_flags(self):
        from agentic_kg.cli import main

        with patch("agentic_kg.ingestion.ingest_papers") as fake:
            fake.side_effect = AsyncMock(return_value=MagicMock(
                status="ok", extraction_errors=[],
            ))
            main([
                "ingest", "--query", "x",
                "--no-extract-entities",
                "--no-normalize-cross-entity",
                "--force-reextract",
            ])

        kwargs = fake.call_args.kwargs
        assert kwargs["extract_entities"] is False
        assert kwargs["normalize_cross_entity_collisions"] is False
        assert kwargs["force_reextract"] is True


class TestJobRunnerMainForwarding:
    """AC-14 end-to-end: the Cloud Run Job entrypoint reads the env var
    and forwards the value all the way to ``ingest_papers``."""

    def test_main_forwards_populate_citations_to_ingest_papers(
        self, monkeypatch,
    ):
        import agentic_kg.job_runner as jr

        monkeypatch.setenv("INGEST_QUERY", "test query")
        monkeypatch.setenv("POPULATE_CITATIONS", "false")

        captured: dict = {}

        def _fake_asyncio_run(coro):
            # Closing the coroutine to avoid the "never awaited" warning.
            try:
                coro.close()
            except Exception:
                pass
            return MagicMock(status="ok", total_problems=0, extraction_errors=[])

        fake_ingest = MagicMock(side_effect=lambda **kw: captured.update(kw))

        monkeypatch.setattr(jr, "asyncio", MagicMock(run=_fake_asyncio_run))
        monkeypatch.setattr(jr, "ingest_papers", fake_ingest)
        monkeypatch.setattr(jr, "persist_ingestion_run", lambda *a, **k: True)
        monkeypatch.setattr(jr.sys, "exit", lambda code: None)

        jr.main()

        assert "populate_citations" in captured
        assert captured["populate_citations"] is False
