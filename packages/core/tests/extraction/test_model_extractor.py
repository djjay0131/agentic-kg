"""E-8 V2 Unit 3 — ModelExtractor.

Covers AC-2 + AC-3: happy path, confidence filter, empty-section skip,
LLMError degradation, prompt shape, return type.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.extraction.llm_client import LLMError, LLMResponse, TokenUsage
from agentic_kg.extraction.model_extractor import ModelExtractor
from agentic_kg.extraction.schemas import ExtractedModel
from pydantic import BaseModel, Field


class _ModelEnvelope(BaseModel):
    models: list[ExtractedModel] = Field(default_factory=list)


def _response(models: list[ExtractedModel]) -> LLMResponse:
    return LLMResponse(
        content=_ModelEnvelope(models=models),
        usage=TokenUsage(total_tokens=200),
    )


@pytest.fixture
def mock_client() -> MagicMock:
    c = MagicMock()
    c.extract = AsyncMock()
    return c


@pytest.fixture
def extractor(mock_client) -> ModelExtractor:
    return ModelExtractor(client=mock_client, min_confidence=0.7)


class TestModelExtractorHappyPath:
    @pytest.mark.asyncio
    async def test_returns_models_above_threshold(self, extractor, mock_client):
        mock_client.extract.return_value = _response(
            [
                ExtractedModel(
                    name="BERT",
                    aliases=["bert-base"],
                    architecture="transformer",
                    model_type="language_model",
                    year_introduced=2018,
                    quoted_text="we fine-tune BERT-base",
                    confidence=0.95,
                ),
                ExtractedModel(
                    name="GPT-2",
                    quoted_text="we compare against GPT-2",
                    confidence=0.5,
                ),
            ]
        )
        out = await extractor.extract(
            paper_title="A paper", sections_text="content here",
        )
        assert len(out) == 1
        assert out[0].name == "BERT"

    @pytest.mark.asyncio
    async def test_returns_extracted_model_instances(
        self, extractor, mock_client,
    ):
        mock_client.extract.return_value = _response(
            [ExtractedModel(name="BERT", quoted_text="we use BERT-base")]
        )
        out = await extractor.extract(paper_title="A", sections_text="x")
        assert all(isinstance(m, ExtractedModel) for m in out)

    @pytest.mark.asyncio
    async def test_confidence_at_threshold_is_included(
        self, extractor, mock_client,
    ):
        """Boundary: confidence == min_confidence (0.7) must pass the >=
        filter. Catches a future bug where someone flips >= to >."""
        mock_client.extract.return_value = _response(
            [
                ExtractedModel(
                    name="BERT",
                    quoted_text="we use BERT-base",
                    confidence=0.7,
                )
            ]
        )
        out = await extractor.extract(paper_title="A", sections_text="x")
        assert len(out) == 1
        assert out[0].confidence == 0.7


class TestModelExtractorEmptySection:
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


class TestModelExtractorErrorHandling:
    @pytest.mark.asyncio
    async def test_llm_error_returns_empty_and_warns(
        self, extractor, mock_client, caplog,
    ):
        mock_client.extract.side_effect = LLMError("upstream 500")
        with caplog.at_level(logging.WARNING):
            out = await extractor.extract(
                paper_title="A", sections_text="content",
            )
        assert out == []
        assert any(
            "Model extraction failed" in r.message for r in caplog.records
        )


class TestModelExtractorPrompt:
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
        # System prompt warns against generic terms.
        assert (
            "transformer architecture" in kwargs["system_prompt"].lower()
            or "do not" in kwargs["system_prompt"].lower()
        )


class TestModelExtractorConfig:
    def test_default_min_confidence(self):
        e = ModelExtractor(client=MagicMock())
        assert e.min_confidence == 0.7

    def test_custom_min_confidence(self):
        e = ModelExtractor(client=MagicMock(), min_confidence=0.9)
        assert e.min_confidence == 0.9

    def test_no_client_falls_back_to_get_openai(self, monkeypatch):
        sentinel = MagicMock(name="OpenAIClient")
        monkeypatch.setattr(
            "agentic_kg.extraction.model_extractor.get_openai_client",
            lambda: sentinel,
        )
        e = ModelExtractor()
        assert e.client is sentinel
