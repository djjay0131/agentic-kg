"""E-8 V2 Unit 3 — MethodExtractor.

Covers AC-4: happy path, confidence filter, empty-section skip,
LLMError degradation, prompt shape.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.extraction.llm_client import LLMError, LLMResponse, TokenUsage
from agentic_kg.extraction.method_extractor import MethodExtractor
from agentic_kg.extraction.schemas import ExtractedMethod
from pydantic import BaseModel, Field


class _MethodEnvelope(BaseModel):
    methods: list[ExtractedMethod] = Field(default_factory=list)


def _response(methods: list[ExtractedMethod]) -> LLMResponse:
    return LLMResponse(
        content=_MethodEnvelope(methods=methods),
        usage=TokenUsage(total_tokens=200),
    )


@pytest.fixture
def mock_client() -> MagicMock:
    c = MagicMock()
    c.extract = AsyncMock()
    return c


@pytest.fixture
def extractor(mock_client) -> MethodExtractor:
    return MethodExtractor(client=mock_client, min_confidence=0.7)


class TestMethodExtractorHappyPath:
    @pytest.mark.asyncio
    async def test_returns_methods_above_threshold(
        self, extractor, mock_client,
    ):
        mock_client.extract.return_value = _response(
            [
                ExtractedMethod(
                    name="contrastive learning",
                    aliases=["InfoNCE"],
                    method_type="training",
                    quoted_text="we adopt contrastive learning",
                    confidence=0.92,
                ),
                ExtractedMethod(
                    name="grid search",
                    quoted_text="we perform a grid search over",
                    confidence=0.5,
                ),
            ]
        )
        out = await extractor.extract(
            paper_title="A", sections_text="content",
        )
        assert len(out) == 1
        assert out[0].name == "contrastive learning"

    @pytest.mark.asyncio
    async def test_returns_extracted_method_instances(
        self, extractor, mock_client,
    ):
        mock_client.extract.return_value = _response(
            [
                ExtractedMethod(
                    name="fine-tuning",
                    quoted_text="we fine-tune the model",
                )
            ]
        )
        out = await extractor.extract(paper_title="A", sections_text="x")
        assert all(isinstance(m, ExtractedMethod) for m in out)

    @pytest.mark.asyncio
    async def test_confidence_at_threshold_is_included(
        self, extractor, mock_client,
    ):
        """Boundary: confidence == min_confidence (0.7) must pass the >=
        filter."""
        mock_client.extract.return_value = _response(
            [
                ExtractedMethod(
                    name="fine-tuning",
                    quoted_text="we fine-tune the model",
                    confidence=0.7,
                )
            ]
        )
        out = await extractor.extract(paper_title="A", sections_text="x")
        assert len(out) == 1
        assert out[0].confidence == 0.7


class TestMethodExtractorEmptySection:
    @pytest.mark.asyncio
    async def test_empty_string_skips_llm(self, extractor, mock_client):
        out = await extractor.extract(paper_title="A", sections_text="")
        assert out == []
        mock_client.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_only_skips_llm(self, extractor, mock_client):
        out = await extractor.extract(
            paper_title="A", sections_text="   \n  ",
        )
        assert out == []
        mock_client.extract.assert_not_called()


class TestMethodExtractorErrorHandling:
    @pytest.mark.asyncio
    async def test_llm_error_returns_empty_and_warns(
        self, extractor, mock_client, caplog,
    ):
        mock_client.extract.side_effect = LLMError("upstream 429")
        with caplog.at_level(logging.WARNING):
            out = await extractor.extract(
                paper_title="A", sections_text="content",
            )
        assert out == []
        assert any(
            "Method extraction failed" in r.message for r in caplog.records
        )


class TestMethodExtractorPrompt:
    @pytest.mark.asyncio
    async def test_passes_prompt_with_title_and_section(
        self, extractor, mock_client,
    ):
        mock_client.extract.return_value = _response([])
        await extractor.extract(
            paper_title="My paper", sections_text="abstract here",
        )
        kwargs = mock_client.extract.call_args.kwargs
        assert "My paper" in kwargs["prompt"]
        assert "abstract here" in kwargs["prompt"]


class TestMethodExtractorConfig:
    def test_default_min_confidence(self):
        e = MethodExtractor(client=MagicMock())
        assert e.min_confidence == 0.7

    def test_custom_min_confidence(self):
        e = MethodExtractor(client=MagicMock(), min_confidence=0.85)
        assert e.min_confidence == 0.85

    def test_no_client_falls_back_to_get_openai(self, monkeypatch):
        sentinel = MagicMock(name="OpenAIClient")
        monkeypatch.setattr(
            "agentic_kg.extraction.method_extractor.get_openai_client",
            lambda: sentinel,
        )
        e = MethodExtractor()
        assert e.client is sentinel
