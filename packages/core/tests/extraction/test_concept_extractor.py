"""E-8 Unit 5 — ConceptExtractor.

Covers AC-3: paper-level concept extraction, confidence threshold filtering,
LLMError degradation, empty-input skip.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.extraction.concept_extractor import ConceptExtractor
from agentic_kg.extraction.llm_client import LLMError, LLMResponse, TokenUsage
from agentic_kg.extraction.schemas import ExtractedResearchConcept
from pydantic import BaseModel, Field


class _ConceptEnvelope(BaseModel):
    """Mirror of the envelope ConceptExtractor uses for instructor responses."""

    concepts: list[ExtractedResearchConcept] = Field(default_factory=list)


def _response(concepts: list[ExtractedResearchConcept]) -> LLMResponse:
    return LLMResponse(
        content=_ConceptEnvelope(concepts=concepts),
        usage=TokenUsage(total_tokens=200),
    )


@pytest.fixture
def mock_client() -> MagicMock:
    c = MagicMock()
    c.extract = AsyncMock()
    return c


@pytest.fixture
def extractor(mock_client) -> ConceptExtractor:
    return ConceptExtractor(client=mock_client, min_confidence=0.7)


class TestConceptExtractorExtract:
    @pytest.mark.asyncio
    async def test_returns_concepts_above_threshold(self, extractor, mock_client):
        mock_client.extract.return_value = _response(
            [
                ExtractedResearchConcept(
                    name="attention mechanism",
                    aliases=["self-attention"],
                    quoted_text="we use multi-head self-attention layers",
                    confidence=0.95,
                ),
                ExtractedResearchConcept(
                    name="dropout",
                    quoted_text="we apply dropout to each layer",
                    confidence=0.5,
                ),
            ]
        )

        out = await extractor.extract(paper_title="A", sections_text="abstract")
        assert len(out) == 1
        assert out[0].name == "attention mechanism"

    @pytest.mark.asyncio
    async def test_empty_input_skips_llm_call(self, extractor, mock_client):
        out = await extractor.extract(paper_title="A", sections_text="")
        assert out == []
        mock_client.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_only_input_skips_llm_call(self, extractor, mock_client):
        out = await extractor.extract(paper_title="A", sections_text="   \n  ")
        assert out == []
        mock_client.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_error_returns_empty_and_warns(
        self, extractor, mock_client, caplog
    ):
        mock_client.extract.side_effect = LLMError("upstream 500")
        with caplog.at_level(logging.WARNING):
            out = await extractor.extract(paper_title="A", sections_text="abstract")
        assert out == []
        assert any("Concept extraction failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_passes_prompt_to_client(self, extractor, mock_client):
        mock_client.extract.return_value = _response([])
        await extractor.extract(paper_title="My paper", sections_text="abstract here")
        kwargs = mock_client.extract.call_args.kwargs
        assert "My paper" in kwargs["prompt"]
        assert "abstract here" in kwargs["prompt"]
        # The system prompt warns against generic terms.
        assert "machine learning" in kwargs["system_prompt"].lower() or (
            "generic" in kwargs["system_prompt"].lower()
        )

    @pytest.mark.asyncio
    async def test_returns_extracted_concept_instances(self, extractor, mock_client):
        mock_client.extract.return_value = _response(
            [
                ExtractedResearchConcept(
                    name="attention",
                    quoted_text="self-attention layers",
                    confidence=0.95,
                )
            ]
        )
        out = await extractor.extract(paper_title="A", sections_text="abstract")
        assert all(isinstance(c, ExtractedResearchConcept) for c in out)


class TestConceptExtractorConfig:
    def test_default_min_confidence(self):
        e = ConceptExtractor(client=MagicMock())
        assert e.min_confidence == 0.7

    def test_custom_min_confidence(self):
        e = ConceptExtractor(client=MagicMock(), min_confidence=0.9)
        assert e.min_confidence == 0.9
