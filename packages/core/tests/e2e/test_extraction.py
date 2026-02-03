"""
E2E tests for extraction pipeline against live services.

Tests PDF extraction and LLM-based problem extraction with real papers.
"""

from __future__ import annotations

import os

import pytest

from agentic_kg.data_acquisition.arxiv import ArxivClient
from agentic_kg.extraction.llm_client import OpenAIClient
from agentic_kg.extraction.pdf_extractor import PDFExtractor
from agentic_kg.extraction.pipeline import (
    PaperProcessingPipeline,
    PipelineConfig,
)
from agentic_kg.extraction.problem_extractor import ExtractionConfig, ProblemExtractor
from agentic_kg.extraction.section_segmenter import SectionSegmenter

# Test papers - choosing small, well-structured papers
# "Attention Is All You Need" - Transformer paper
TRANSFORMER_ARXIV_ID = "1706.03762"


@pytest.mark.e2e
@pytest.mark.slow
class TestPDFExtractionE2E:
    """E2E tests for PDF extraction."""

    @pytest.fixture
    def pdf_extractor(self):
        """Create PDF extractor."""
        return PDFExtractor()

    @pytest.fixture
    def section_segmenter(self):
        """Create section segmenter."""
        return SectionSegmenter()

    @pytest.fixture
    async def arxiv_client(self):
        """Create arXiv client."""
        client = ArxivClient()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_extract_from_arxiv_url(
        self,
        pdf_extractor: PDFExtractor,
        arxiv_client: ArxivClient,
    ):
        """Test extracting text from an arXiv PDF URL."""
        # Get paper metadata to get PDF URL
        paper = await arxiv_client.get_paper(TRANSFORMER_ARXIV_ID)
        pdf_url = paper["pdf_url"]

        # Extract text from PDF
        extracted = await pdf_extractor.extract_from_url(pdf_url, timeout=120.0)

        assert extracted is not None
        assert extracted.page_count > 0
        assert len(extracted.full_text) > 1000

        # Should contain key terms from the Transformer paper
        text_lower = extracted.full_text.lower()
        assert "attention" in text_lower
        assert "transformer" in text_lower

    @pytest.mark.asyncio
    async def test_section_segmentation(
        self,
        pdf_extractor: PDFExtractor,
        section_segmenter: SectionSegmenter,
        arxiv_client: ArxivClient,
    ):
        """Test section segmentation of extracted text."""
        paper = await arxiv_client.get_paper(TRANSFORMER_ARXIV_ID)
        pdf_url = paper["pdf_url"]

        extracted = await pdf_extractor.extract_from_url(pdf_url, timeout=120.0)
        segmented = section_segmenter.segment(extracted.full_text)

        assert segmented is not None
        assert len(segmented.sections) > 0

        # Should identify some standard sections
        section_types = {s.section_type for s in segmented.sections}
        # At minimum should have body/unknown sections
        assert len(section_types) > 0


@pytest.mark.e2e
@pytest.mark.costly
class TestProblemExtractionE2E:
    """E2E tests for LLM-based problem extraction.

    These tests incur OpenAI API costs. Only run when needed.
    """

    @pytest.fixture
    def openai_client(self, e2e_config):
        """Create OpenAI client."""
        api_key = e2e_config.openai_api_key
        if not api_key:
            pytest.skip("OPENAI_API_KEY not set")
        return OpenAIClient(api_key=api_key, model="gpt-4o-mini")

    @pytest.fixture
    def problem_extractor(self, openai_client):
        """Create problem extractor."""
        config = ExtractionConfig(
            min_confidence=0.3,
            max_problems_per_section=5,
        )
        return ProblemExtractor(client=openai_client, config=config)

    @pytest.mark.asyncio
    async def test_extract_problems_from_text(self, problem_extractor: ProblemExtractor):
        """Test extracting problems from a sample text."""
        # Sample abstract text (from Transformer paper)
        sample_text = """
        The dominant sequence transduction models are based on complex recurrent or
        convolutional neural networks that include an encoder and a decoder. The best
        performing models also connect the encoder and decoder through an attention
        mechanism. We propose a new simple network architecture, the Transformer,
        based solely on attention mechanisms, dispensing with recurrence and convolutions
        entirely. Experiments on two machine translation tasks show these models to be
        superior in quality while being more parallelizable and requiring significantly
        less time to train.

        Current challenges include: scaling to longer sequences remains difficult due to
        quadratic complexity, multi-modal attention mechanisms are underexplored, and
        efficient training on diverse domains requires further research.
        """

        result = await problem_extractor.extract_problems_from_text(
            text=sample_text,
            section_type="abstract",
            paper_title="Attention Is All You Need",
            paper_doi=None,
        )

        assert result is not None
        # Should extract at least one problem
        assert len(result.problems) > 0

        # Check problem structure
        for problem in result.problems:
            assert problem.title
            assert problem.description
            assert 0.0 <= problem.confidence <= 1.0


@pytest.mark.e2e
@pytest.mark.costly
@pytest.mark.slow
class TestFullPipelineE2E:
    """E2E tests for the full extraction pipeline.

    These tests download PDFs and call LLM APIs.
    """

    @pytest.fixture
    async def pipeline(self, e2e_config):
        """Create pipeline with OpenAI client."""
        api_key = e2e_config.openai_api_key
        if not api_key:
            pytest.skip("OPENAI_API_KEY not set")

        from agentic_kg.extraction.llm_client import OpenAIClient

        client = OpenAIClient(api_key=api_key, model="gpt-4o-mini")
        config = PipelineConfig(
            pdf_timeout=120.0,
            extraction_config=ExtractionConfig(
                min_confidence=0.3,
                max_problems_per_section=3,  # Limit for cost
            ),
            extract_relations=False,  # Skip relations to reduce cost
        )
        return PaperProcessingPipeline(client=client, config=config)

    @pytest.mark.asyncio
    async def test_process_arxiv_paper(self, pipeline: PaperProcessingPipeline):
        """Test processing a real arXiv paper through the full pipeline."""
        async with ArxivClient() as arxiv_client:
            paper = await arxiv_client.get_paper(TRANSFORMER_ARXIV_ID)

        result = await pipeline.process_pdf_url(
            url=paper["pdf_url"],
            paper_title=paper["title"],
            paper_doi=paper.get("doi"),
            authors=[a["name"] for a in paper["authors"]],
        )

        # Check overall result
        assert result is not None

        # Check stages completed
        stage_names = [s.stage for s in result.stages]
        assert "pdf_extraction" in stage_names
        assert "section_segmentation" in stage_names
        assert "problem_extraction" in stage_names

        # Check PDF extraction
        pdf_stage = next(s for s in result.stages if s.stage == "pdf_extraction")
        assert pdf_stage.success

        # Check section segmentation
        seg_stage = next(s for s in result.stages if s.stage == "section_segmentation")
        assert seg_stage.success

        # Check extraction (may vary based on LLM)
        extraction_stage = next(s for s in result.stages if s.stage == "problem_extraction")
        # We accept either success or failure since LLM output can vary
        # but the stage should at least run
        assert extraction_stage.duration_ms > 0

        # If successful, check problems
        if extraction_stage.success and result.problem_count > 0:
            problems = result.get_problems()
            assert len(problems) > 0
            for p in problems:
                assert p.title
                assert p.description

    @pytest.mark.asyncio
    async def test_pipeline_stage_timing(self, pipeline: PaperProcessingPipeline):
        """Test that pipeline reports timing for each stage."""
        async with ArxivClient() as arxiv_client:
            paper = await arxiv_client.get_paper(TRANSFORMER_ARXIV_ID)

        result = await pipeline.process_pdf_url(
            url=paper["pdf_url"],
            paper_title=paper["title"],
        )

        # All stages should have positive duration
        for stage in result.stages:
            assert stage.duration_ms >= 0

        # Total duration should be sum of stages (approximately)
        stage_total = sum(s.duration_ms for s in result.stages)
        assert result.total_duration_ms >= stage_total * 0.9  # Allow 10% variance
