"""Tests for the generate_description_with_self_check helper (E-6 Unit 3).

Mocks BaseLLMClient.extract — no real LLM calls.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.extraction.llm_client import LLMResponse, TokenUsage
from agentic_kg.knowledge_graph.description_generation import (
    DescriptionWithSelfCheck,
    _build_aliases_hint,
    generate_description_with_self_check,
)


def _passing_response(description: str = "A great description sentence here.") -> LLMResponse:
    return LLMResponse(
        content=DescriptionWithSelfCheck(
            description=description,
            is_factually_grounded=True,
            is_concise=True,
            is_specific=True,
            is_not_tautological=True,
        ),
        usage=TokenUsage(total_tokens=100),
    )


def _failing_response(
    rejection_reason: str = "too generic",
    is_specific: bool = False,
) -> LLMResponse:
    return LLMResponse(
        content=DescriptionWithSelfCheck(
            description="A " + "x" * 25,
            is_factually_grounded=True,
            is_concise=True,
            is_specific=is_specific,
            is_not_tautological=True,
            rejection_reason=rejection_reason,
        ),
        usage=TokenUsage(total_tokens=100),
    )


@pytest.fixture
def mock_client() -> MagicMock:
    c = MagicMock()
    c.extract = AsyncMock()
    return c


# =============================================================================
# AC-2: Happy path
# =============================================================================


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_returns_description_when_all_gates_pass(self, mock_client):
        expected = "A clear description of the entity here."
        mock_client.extract.return_value = _passing_response(description=expected)

        result = await generate_description_with_self_check(
            entity_type="method",
            name="contrastive learning",
            aliases=["InfoNCE"],
            llm_client=mock_client,
        )

        assert result == expected

    @pytest.mark.asyncio
    async def test_extract_called_with_correct_prompt_shape(self, mock_client):
        mock_client.extract.return_value = _passing_response()

        await generate_description_with_self_check(
            entity_type="method",
            name="contrastive learning",
            aliases=["InfoNCE"],
            llm_client=mock_client,
        )

        kwargs = mock_client.extract.call_args.kwargs
        # The user prompt contains the entity type, the name, and the aliases hint.
        assert "method" in kwargs["prompt"]
        assert "contrastive learning" in kwargs["prompt"]
        assert "InfoNCE" in kwargs["prompt"]
        # The system prompt is the V1 constant — basic sanity check on key phrases.
        assert "self-evaluate" in kwargs["system_prompt"].lower()
        # The response model is our schema.
        assert kwargs["response_model"] is DescriptionWithSelfCheck


# =============================================================================
# AC-3: Self-validation rejection
# =============================================================================


class TestSelfValidationRejection:
    @pytest.mark.asyncio
    async def test_returns_none_when_any_gate_false(self, mock_client):
        mock_client.extract.return_value = _failing_response(
            rejection_reason="too generic",
        )

        result = await generate_description_with_self_check(
            entity_type="method",
            name="X technique",
            aliases=[],
            llm_client=mock_client,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_rejection_logs_warn_with_reason(
        self, mock_client, caplog,
    ):
        mock_client.extract.return_value = _failing_response(
            rejection_reason="not specific enough",
        )

        with caplog.at_level(logging.WARNING):
            await generate_description_with_self_check(
                entity_type="model",
                name="some model",
                aliases=[],
                llm_client=mock_client,
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "self-validation rejected" in r.message.lower()
            and "not specific enough" in r.message
            for r in warnings
        )

    @pytest.mark.asyncio
    async def test_rejection_with_no_reason_uses_placeholder(
        self, mock_client, caplog,
    ):
        # LLM didn't fill rejection_reason — helper should still log gracefully.
        mock_client.extract.return_value = LLMResponse(
            content=DescriptionWithSelfCheck(
                description="A reasonable description sentence here.",
                is_factually_grounded=False,
                is_concise=True,
                is_specific=True,
                is_not_tautological=True,
            ),
            usage=TokenUsage(total_tokens=100),
        )

        with caplog.at_level(logging.WARNING):
            result = await generate_description_with_self_check(
                entity_type="concept",
                name="some concept",
                aliases=[],
                llm_client=mock_client,
            )

        assert result is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("no reason given" in r.message for r in warnings)


# =============================================================================
# AC-4: LLM exception
# =============================================================================


class TestLLMException:
    @pytest.mark.asyncio
    async def test_returns_none_when_llm_raises(self, mock_client):
        mock_client.extract.side_effect = RuntimeError("openai down")

        result = await generate_description_with_self_check(
            entity_type="topic",
            name="topic name",
            aliases=[],
            llm_client=mock_client,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_exception_logs_warn_with_entity_name(
        self, mock_client, caplog,
    ):
        mock_client.extract.side_effect = RuntimeError("timeout")

        with caplog.at_level(logging.WARNING):
            await generate_description_with_self_check(
                entity_type="topic",
                name="a specific topic",
                aliases=[],
                llm_client=mock_client,
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "LLM call failed" in r.message
            and "a specific topic" in r.message
            for r in warnings
        )

    @pytest.mark.asyncio
    async def test_helper_does_not_reraise(self, mock_client):
        """Verify no exception propagates regardless of what the LLM does."""
        mock_client.extract.side_effect = RuntimeError("any error")

        # Should not raise.
        result = await generate_description_with_self_check(
            entity_type="method",
            name="X",
            aliases=[],
            llm_client=mock_client,
        )
        assert result is None


# =============================================================================
# Prompt construction edge cases
# =============================================================================


class TestAliasesHint:
    def test_empty_list(self):
        assert _build_aliases_hint([]) == ""

    def test_single_alias(self):
        assert _build_aliases_hint(["a"]) == " (also known as: a)"

    def test_caps_at_three_aliases(self):
        result = _build_aliases_hint(["a", "b", "c", "d", "e"])
        assert "a" in result
        assert "b" in result
        assert "c" in result
        assert "d" not in result  # capped
        assert "e" not in result

    def test_exactly_three_aliases(self):
        """Boundary: list of length 3 — none truncated, none added."""
        result = _build_aliases_hint(["a", "b", "c"])
        assert result == " (also known as: a, b, c)"

    def test_filters_empty_strings(self):
        # If the first 3 aliases are empty strings, hint stays empty.
        assert _build_aliases_hint(["", "", ""]) == ""

    def test_filters_some_empty_strings(self):
        # Mixed empties: only non-empty go in.
        result = _build_aliases_hint(["", "real", ""])
        assert result == " (also known as: real)"
