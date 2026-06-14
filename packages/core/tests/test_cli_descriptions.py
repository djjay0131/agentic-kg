"""E-6 Unit 6: CLI tests for ``--no-generate-description`` and the
``_llm_client_for_description`` silent-fallback helper.

Covers AC-12 (silent fallback on missing OPENAI_API_KEY), AC-13 (CLI flag
flows to the async sibling), AC-11 (default behavior preserved when the
flag is set or no key is present).

Mocks the repository — no Neo4j needed. Mocks the LLM client factory so
we never reach OpenAI.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agentic_kg.cli import _llm_client_for_description, build_parser, main

# =============================================================================
# Argparse: new flag is registered on all three subcommands
# =============================================================================


class TestArgparseFlag:
    @pytest.mark.parametrize(
        "subcmd",
        ["create-concept", "create-model", "create-method"],
    )
    def test_default_is_generate_true(self, subcmd):
        parser = build_parser()
        ns = parser.parse_args([subcmd, "--name", "x"])
        # Per spec AC-13, the CLI default IS generation-on.
        assert ns.generate_description is True

    @pytest.mark.parametrize(
        "subcmd",
        ["create-concept", "create-model", "create-method"],
    )
    def test_no_generate_flag_flips_to_false(self, subcmd):
        parser = build_parser()
        ns = parser.parse_args([
            subcmd, "--name", "x", "--no-generate-description",
        ])
        assert ns.generate_description is False


# =============================================================================
# _llm_client_for_description: silent fallback semantics
# =============================================================================


class TestLLMClientForDescription:
    def test_returns_none_when_requested_false(self, monkeypatch):
        # API key present but the flag was off → still skip.
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        assert _llm_client_for_description(requested=False) is None

    def test_returns_none_when_api_key_missing(self, monkeypatch, caplog):
        """AC-12: missing OPENAI_API_KEY → silent fallback with WARN."""
        import logging
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with caplog.at_level(logging.WARNING):
            client = _llm_client_for_description(requested=True)
        assert client is None
        assert any(
            "OPENAI_API_KEY not set" in r.message for r in caplog.records
        )

    def test_builds_client_when_requested_and_key_present(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        with patch("agentic_kg.extraction.llm_client.create_llm_client") as f:
            sentinel = MagicMock(name="OpenAIClient")
            f.return_value = sentinel
            result = _llm_client_for_description(requested=True)
        assert result is sentinel
        f.assert_called_once()


# =============================================================================
# Handler dispatch — async path when flag default + key present
# =============================================================================


class TestRunCreateMethodAsyncDispatch:
    @patch("agentic_kg.cli._llm_client_for_description")
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_async_path_used_when_default_and_llm_available(
        self, get_repo, llm_factory,
    ):
        repo = MagicMock()
        merged = MagicMock(id="m-1", name="fine-tuning", aliases=[])
        # acreate_or_merge_method is async — return a coroutine.
        repo.acreate_or_merge_method = AsyncMock(
            return_value=(merged, True),
        )
        get_repo.return_value = repo
        llm_factory.return_value = MagicMock(name="OpenAIClient")

        main(["create-method", "--name", "fine-tuning"])

        repo.acreate_or_merge_method.assert_called_once()
        kwargs = repo.acreate_or_merge_method.call_args.kwargs
        assert kwargs["generate_description"] is True
        assert kwargs["llm_client"] is not None
        # The sync path was NOT used.
        repo.create_or_merge_method.assert_not_called()

    @patch("agentic_kg.cli._llm_client_for_description")
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_sync_path_used_when_no_generate_flag(
        self, get_repo, llm_factory,
    ):
        """--no-generate-description → sync path, no LLM."""
        repo = MagicMock()
        repo.create_or_merge_method.return_value = (
            MagicMock(id="m-1", name="x", aliases=[]), True,
        )
        get_repo.return_value = repo
        llm_factory.return_value = None  # CLI didn't try to build one.

        main([
            "create-method", "--name", "x", "--no-generate-description",
        ])

        repo.create_or_merge_method.assert_called_once()
        repo.acreate_or_merge_method.assert_not_called()

    @patch("agentic_kg.cli._llm_client_for_description")
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_sync_path_used_when_llm_unavailable(
        self, get_repo, llm_factory,
    ):
        """Default flag but no LLM client (e.g., missing API key) → sync fallback."""
        repo = MagicMock()
        repo.create_or_merge_method.return_value = (
            MagicMock(id="m-1", name="x", aliases=[]), True,
        )
        get_repo.return_value = repo
        llm_factory.return_value = None  # silent fallback fired

        main(["create-method", "--name", "x"])

        repo.create_or_merge_method.assert_called_once()
        repo.acreate_or_merge_method.assert_not_called()


# =============================================================================
# Smoke: same dispatch wiring for the other two commands
# =============================================================================


class TestRunCreateModelAsyncDispatch:
    @patch("agentic_kg.cli._llm_client_for_description")
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_async_path_used_when_default_and_llm_available(
        self, get_repo, llm_factory,
    ):
        repo = MagicMock()
        merged = MagicMock(
            id="m-1", name="BERT", aliases=[], is_canonical=False,
        )
        repo.acreate_or_merge_model = AsyncMock(return_value=(merged, True))
        get_repo.return_value = repo
        llm_factory.return_value = MagicMock(name="OpenAIClient")

        main(["create-model", "--name", "BERT"])

        repo.acreate_or_merge_model.assert_called_once()
        kwargs = repo.acreate_or_merge_model.call_args.kwargs
        assert kwargs["generate_description"] is True


class TestRunCreateConceptAsyncDispatch:
    @patch("agentic_kg.cli._llm_client_for_description")
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_async_path_used_when_default_and_llm_available(
        self, get_repo, llm_factory,
    ):
        repo = MagicMock()
        merged = MagicMock(id="c-1", name="attention", aliases=[])
        repo.acreate_or_merge_research_concept = AsyncMock(
            return_value=(merged, True),
        )
        get_repo.return_value = repo
        llm_factory.return_value = MagicMock(name="OpenAIClient")

        main(["create-concept", "--name", "attention"])

        repo.acreate_or_merge_research_concept.assert_called_once()
        kwargs = repo.acreate_or_merge_research_concept.call_args.kwargs
        assert kwargs["generate_description"] is True


class TestCLIPrintsSuccessFromAsync:
    """Adversarial regression sentinel: catch a future removal of asyncio.run.

    If the CLI calls the async method but forgets to await it (e.g., the
    asyncio.run wrapper is removed), then `concept, created = <coroutine>`
    raises TypeError because a coroutine can't be unpacked. So the test
    would crash before reaching the print line. Asserting the success
    message lands in stdout enforces the awaited contract end-to-end.
    """

    @patch("agentic_kg.cli._llm_client_for_description")
    @patch("agentic_kg.knowledge_graph.repository.get_repository")
    def test_create_method_prints_success_via_async_path(
        self, get_repo, llm_factory, capsys,
    ):
        repo = MagicMock()
        merged = MagicMock(
            id="m-1", name="fine-tuning", aliases=["FT"],
        )
        repo.acreate_or_merge_method = AsyncMock(
            return_value=(merged, True),
        )
        get_repo.return_value = repo
        llm_factory.return_value = MagicMock(name="OpenAIClient")

        main(["create-method", "--name", "fine-tuning"])

        out = capsys.readouterr().out
        assert "Created" in out
        assert "fine-tuning" in out
