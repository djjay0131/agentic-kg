"""E-7 Unit 6 — disambiguate_pair routing LLM call.

Covers AC-4 (happy path), AC-5 (self-validation rejection), AC-6 (confidence
threshold rejection), AC-7 (LLM exception), AC-18 (out-of-pair pick guard).
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.extraction.cross_entity_normalizer import (
    AmbiguousPair,
    DisambiguationDecision,
    _format_kinds_block,
    disambiguate_pair,
)
from agentic_kg.extraction.llm_client import LLMError, LLMResponse, TokenUsage
from agentic_kg.extraction.schemas import (
    ExtractedMethod,
    ExtractedResearchConcept,
)


def _concept(name: str = "attention") -> ExtractedResearchConcept:
    return ExtractedResearchConcept(
        name=name, quoted_text="text from concept extraction here",
        confidence=0.9,
    )


def _method(name: str = "attention") -> ExtractedMethod:
    return ExtractedMethod(
        name=name, quoted_text="text from method extraction here",
        confidence=0.9,
    )


def _pair_cm(surface: str = "attention") -> AmbiguousPair:
    """Concept-Method pair, the common case."""
    return AmbiguousPair(
        surface=surface,
        extractions={"concept": _concept(surface), "method": _method(surface)},
        trigger="exact",
    )


def _decision(
    *,
    picked: str = "concept",
    confidence: float = 0.9,
    grounded: bool = True,
    specific: bool = True,
    reason: str = None,
) -> LLMResponse:
    return LLMResponse(
        content=DisambiguationDecision(
            picked_kind=picked,
            confidence=confidence,
            is_grounded_in_paper_context=grounded,
            is_specific_to_one_kind=specific,
            rejection_reason=reason,
        ),
        usage=TokenUsage(total_tokens=200),
    )


@pytest.fixture
def llm_client() -> MagicMock:
    c = MagicMock()
    c.extract = AsyncMock()
    return c


# =============================================================================
# AC-4 — happy path
# =============================================================================


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_picked_returned_when_all_gates_pass(self, llm_client):
        llm_client.extract.return_value = _decision(
            picked="concept", confidence=0.9,
        )
        picked, reason = await disambiguate_pair(
            _pair_cm(),
            paper_title="A paper",
            paper_excerpt="ABC",
            llm_client=llm_client,
        )
        assert picked == "concept"
        assert reason is None

    @pytest.mark.asyncio
    async def test_extract_called_with_correct_prompts_and_model(
        self, llm_client,
    ):
        llm_client.extract.return_value = _decision()
        await disambiguate_pair(
            _pair_cm(),
            paper_title="My paper",
            paper_excerpt="paper context here",
            llm_client=llm_client,
        )
        kwargs = llm_client.extract.call_args.kwargs
        assert "My paper" in kwargs["prompt"]
        assert "attention" in kwargs["prompt"]
        assert "paper context here" in kwargs["prompt"]
        # Pseudo-XML excerpt delimiters wrap the untrusted block (AC-20).
        assert "<paper-excerpt>" in kwargs["prompt"]
        # The response model IS the self-validating decision schema.
        assert kwargs["response_model"] is DisambiguationDecision
        # The system prompt carries the security clause (AC-20).
        assert "UNTRUSTED" in kwargs["system_prompt"].upper()


# =============================================================================
# AC-5 — self-validation rejection
# =============================================================================


class TestSelfValidationRejection:
    @pytest.mark.asyncio
    async def test_grounded_false_rejects(self, llm_client, caplog):
        llm_client.extract.return_value = _decision(
            grounded=False, reason="insufficient context",
        )
        with caplog.at_level(logging.WARNING):
            picked, reason = await disambiguate_pair(
                _pair_cm(),
                paper_title="A",
                paper_excerpt="",
                llm_client=llm_client,
            )
        assert picked is None
        assert "insufficient context" in reason
        assert any("Disambiguation rejected" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_specific_false_rejects(self, llm_client):
        llm_client.extract.return_value = _decision(
            specific=False, reason="both readings legitimate",
        )
        picked, reason = await disambiguate_pair(
            _pair_cm(),
            paper_title="A", paper_excerpt="",
            llm_client=llm_client,
        )
        assert picked is None
        assert "both readings legitimate" in reason

    @pytest.mark.asyncio
    async def test_reason_falls_back_when_llm_omits_it(self, llm_client):
        # LLM returned a failing gate but no rejection_reason filled in.
        llm_client.extract.return_value = _decision(
            grounded=False, reason=None,
        )
        picked, reason = await disambiguate_pair(
            _pair_cm(),
            paper_title="A", paper_excerpt="",
            llm_client=llm_client,
        )
        assert picked is None
        assert "gate" in reason.lower()


# =============================================================================
# AC-6 — confidence boundary
# =============================================================================


class TestConfidenceBoundary:
    @pytest.mark.asyncio
    async def test_just_below_threshold_rejects(self, llm_client):
        llm_client.extract.return_value = _decision(confidence=0.69)
        picked, reason = await disambiguate_pair(
            _pair_cm(),
            paper_title="A", paper_excerpt="",
            llm_client=llm_client, min_confidence=0.7,
        )
        assert picked is None
        assert "below threshold" in reason

    @pytest.mark.asyncio
    async def test_at_threshold_accepts(self, llm_client):
        """AC-6 inclusive boundary: confidence == 0.7 must pass."""
        llm_client.extract.return_value = _decision(confidence=0.70)
        picked, reason = await disambiguate_pair(
            _pair_cm(),
            paper_title="A", paper_excerpt="",
            llm_client=llm_client, min_confidence=0.7,
        )
        assert picked == "concept"
        assert reason is None


# =============================================================================
# AC-7 — LLM exception never propagates
# =============================================================================


class TestLLMException:
    @pytest.mark.asyncio
    async def test_llm_error_returns_none_with_reason(
        self, llm_client, caplog,
    ):
        llm_client.extract.side_effect = LLMError("openai 500")
        with caplog.at_level(logging.WARNING):
            picked, reason = await disambiguate_pair(
                _pair_cm(),
                paper_title="A", paper_excerpt="",
                llm_client=llm_client,
            )
        assert picked is None
        assert "llm call failed" in reason
        assert any(
            "Disambiguation failed" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_generic_exception_caught(self, llm_client):
        llm_client.extract.side_effect = RuntimeError("transient bug")
        picked, reason = await disambiguate_pair(
            _pair_cm(),
            paper_title="A", paper_excerpt="",
            llm_client=llm_client,
        )
        assert picked is None
        assert "transient bug" in reason


# =============================================================================
# AC-18 — out-of-pair pick defensive guard
# =============================================================================


class TestOutOfPairGuard:
    @pytest.mark.asyncio
    async def test_picks_a_kind_not_in_the_pair(self, llm_client):
        """LLM returns 'method' but pair was {concept, model} only."""
        llm_client.extract.return_value = _decision(
            picked="method",  # not present in the pair below
            confidence=0.95,
        )
        from agentic_kg.extraction.schemas import ExtractedModel

        pair = AmbiguousPair(
            surface="attention",
            extractions={
                "concept": _concept("attention"),
                "model": ExtractedModel(
                    name="attention",
                    quoted_text="text from model extraction here",
                    confidence=0.9,
                ),
            },
            trigger="exact",
        )
        picked, reason = await disambiguate_pair(
            pair,
            paper_title="A", paper_excerpt="",
            llm_client=llm_client,
        )
        assert picked is None
        assert "not in pair" in reason


# =============================================================================
# Prompt block formatting
# =============================================================================


class TestFormatKindsBlock:
    def test_includes_each_kind_in_pair(self):
        pair = _pair_cm()
        block = _format_kinds_block(pair)
        assert "concept:" in block
        assert "method:" in block
        assert "name=\"attention\"" in block
        # Each kind's quoted_text wrapped in its delimiter (AC-20).
        assert "<quote-concept>" in block
        assert "<quote-method>" in block

    def test_skips_kinds_not_in_pair(self):
        pair = AmbiguousPair(
            surface="XX",
            extractions={"concept": _concept("XX")},
            trigger="exact",
        )
        block = _format_kinds_block(pair)
        assert "concept:" in block
        assert "model:" not in block
        assert "method:" not in block
