"""
Pytest fixtures for extraction pipeline tests.

Provides shared test data, mock objects, and singleton reset fixtures
used across extraction test modules.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_kg.extraction.batch import BatchConfig, BatchJob, BatchProgress, JobStatus, reset_batch_processor
from agentic_kg.extraction.llm_client import LLMResponse, TokenUsage
from agentic_kg.extraction.pdf_extractor import ExtractedPage, ExtractedText
from agentic_kg.extraction.pipeline import PipelineConfig, PipelineStageResult, reset_pipeline
from agentic_kg.extraction.problem_extractor import ExtractionConfig, reset_problem_extractor
from agentic_kg.extraction.relation_extractor import (
    ExtractedRelation,
    RelationConfig,
    RelationExtractionResult,
    RelationType,
    reset_relation_extractor,
)
from agentic_kg.extraction.schemas import (
    BatchExtractionResult,
    ExtractedAssumption,
    ExtractedBaseline,
    ExtractedConstraint,
    ExtractedDataset,
    ExtractedMetric,
    ExtractedProblem,
    ExtractionResult,
)
from agentic_kg.extraction.section_segmenter import Section, SectionType, SegmentedDocument


# =============================================================================
# Singleton Reset (autouse)
# =============================================================================


@pytest.fixture(autouse=True)
def reset_extraction_singletons():
    """Reset all extraction singletons before and after each test."""
    reset_pipeline()
    reset_batch_processor()
    reset_relation_extractor()
    reset_problem_extractor()
    yield
    reset_pipeline()
    reset_batch_processor()
    reset_relation_extractor()
    reset_problem_extractor()


# =============================================================================
# Configuration Fixtures
# =============================================================================


@pytest.fixture
def pipeline_config() -> PipelineConfig:
    """Test pipeline configuration with fast settings."""
    return PipelineConfig(
        pdf_timeout=10.0,
        min_section_length=50,
        skip_references=True,
        extract_relations=False,
        parallel_sections=False,
        verbose=False,
    )


@pytest.fixture
def pipeline_config_with_relations() -> PipelineConfig:
    """Test pipeline configuration with relation extraction enabled."""
    return PipelineConfig(
        pdf_timeout=10.0,
        min_section_length=50,
        skip_references=True,
        extract_relations=True,
        parallel_sections=False,
    )


@pytest.fixture
def extraction_config() -> ExtractionConfig:
    """Test extraction configuration."""
    return ExtractionConfig()


@pytest.fixture
def relation_config() -> RelationConfig:
    """Test relation extraction configuration."""
    return RelationConfig()


@pytest.fixture
def batch_config() -> BatchConfig:
    """Test batch configuration with fast settings."""
    return BatchConfig(
        max_concurrent=2,
        max_retries=1,
        retry_delay=0.1,
        store_to_kg=False,
        db_path=None,  # In-memory
    )


# =============================================================================
# Sample Text Fixtures
# =============================================================================


SAMPLE_PAPER_TEXT = """\
Abstract

We present a novel approach to improving transformer efficiency for
long-context understanding in natural language processing. Current
methods struggle with quadratic attention complexity.

1 Introduction

Large language models have transformed natural language processing,
but their computational requirements remain a significant barrier.
The quadratic complexity of self-attention limits practical deployment
for long documents exceeding 4096 tokens.

2 Related Work

Prior approaches include sparse attention patterns (Child et al., 2019),
linear attention mechanisms (Katharopoulos et al., 2020), and
retrieval-augmented methods (Borgeaud et al., 2022).

3 Methods

We propose a hierarchical attention mechanism that processes documents
in chunks and aggregates representations through a learned routing
function. This reduces complexity from O(n^2) to O(n log n).

4 Experiments

We evaluate on three benchmarks: SCROLLS, LongBench, and our novel
UltraLong dataset containing documents up to 128K tokens.

5 Results

Our method achieves state-of-the-art results on SCROLLS (87.3 F1)
and LongBench (72.1 accuracy) while using 60% less memory.

6 Discussion

While our approach significantly reduces memory usage, it introduces
a trade-off in latency due to the routing overhead. Future work should
explore more efficient routing strategies.

7 Limitations

Our evaluation is limited to English-language documents. The hierarchical
chunking strategy may not generalize well to languages with different
syntactic structures. Additionally, the routing function requires
pre-training which adds to the overall training cost.

8 Conclusion

We have demonstrated that hierarchical attention with learned routing
can effectively handle long-context understanding while maintaining
competitive performance. Key open problems include: extending to
multilingual settings and reducing the routing overhead.

References

Child et al. (2019). Generating long sequences with sparse transformers.
Katharopoulos et al. (2020). Transformers are RNNs.
Borgeaud et al. (2022). Improving language models by retrieving from trillions of tokens.
"""


@pytest.fixture
def sample_paper_text() -> str:
    """Sample academic paper text with standard section structure."""
    return SAMPLE_PAPER_TEXT


@pytest.fixture
def sample_introduction_text() -> str:
    """Sample introduction section text."""
    return (
        "Large language models have transformed natural language processing, "
        "but their computational requirements remain a significant barrier. "
        "The quadratic complexity of self-attention limits practical deployment "
        "for long documents exceeding 4096 tokens."
    )


@pytest.fixture
def sample_limitations_text() -> str:
    """Sample limitations section text."""
    return (
        "Our evaluation is limited to English-language documents. The hierarchical "
        "chunking strategy may not generalize well to languages with different "
        "syntactic structures. Additionally, the routing function requires "
        "pre-training which adds to the overall training cost."
    )


# =============================================================================
# PDF Extraction Fixtures
# =============================================================================


@pytest.fixture
def sample_extracted_page() -> ExtractedPage:
    """Sample extracted page from a PDF."""
    return ExtractedPage(
        page_number=1,
        text=SAMPLE_PAPER_TEXT[:500],
        width=612.0,
        height=792.0,
    )


@pytest.fixture
def sample_extracted_text() -> ExtractedText:
    """Sample extracted text from a PDF."""
    pages = [
        ExtractedPage(
            page_number=1,
            text=SAMPLE_PAPER_TEXT[:800],
            width=612.0,
            height=792.0,
        ),
        ExtractedPage(
            page_number=2,
            text=SAMPLE_PAPER_TEXT[800:],
            width=612.0,
            height=792.0,
        ),
    ]
    return ExtractedText(
        pages=pages,
        source="test_paper.pdf",
        metadata={"test": True},
    )


# =============================================================================
# Section Fixtures
# =============================================================================


@pytest.fixture
def sample_section_introduction() -> Section:
    """Sample introduction section."""
    return Section(
        section_type=SectionType.INTRODUCTION,
        title="Introduction",
        content=(
            "Large language models have transformed natural language processing, "
            "but their computational requirements remain a significant barrier. "
            "The quadratic complexity of self-attention limits practical deployment "
            "for long documents exceeding 4096 tokens."
        ),
        start_char=100,
        end_char=400,
        confidence=0.95,
    )


@pytest.fixture
def sample_section_limitations() -> Section:
    """Sample limitations section."""
    return Section(
        section_type=SectionType.LIMITATIONS,
        title="Limitations",
        content=(
            "Our evaluation is limited to English-language documents. The hierarchical "
            "chunking strategy may not generalize well to languages with different "
            "syntactic structures. Additionally, the routing function requires "
            "pre-training which adds to the overall training cost."
        ),
        start_char=1500,
        end_char=1800,
        confidence=0.9,
    )


@pytest.fixture
def sample_section_discussion() -> Section:
    """Sample discussion section."""
    return Section(
        section_type=SectionType.DISCUSSION,
        title="Discussion",
        content=(
            "While our approach significantly reduces memory usage, it introduces "
            "a trade-off in latency due to the routing overhead. Future work should "
            "explore more efficient routing strategies."
        ),
        start_char=1200,
        end_char=1500,
        confidence=0.85,
    )


@pytest.fixture
def sample_sections(
    sample_section_introduction,
    sample_section_limitations,
    sample_section_discussion,
) -> list[Section]:
    """List of sample sections for extraction."""
    return [
        sample_section_introduction,
        sample_section_limitations,
        sample_section_discussion,
    ]


@pytest.fixture
def sample_segmented_document(sample_sections) -> SegmentedDocument:
    """Sample segmented document."""
    return SegmentedDocument(
        sections=sample_sections,
        full_text=SAMPLE_PAPER_TEXT,
        detected_structure=True,
    )


# =============================================================================
# Extraction Result Fixtures
# =============================================================================


@pytest.fixture
def sample_extracted_problem() -> ExtractedProblem:
    """Sample extracted problem with high confidence."""
    return ExtractedProblem(
        statement=(
            "How can we reduce the quadratic attention complexity of transformer "
            "models for processing long documents while maintaining competitive performance?"
        ),
        domain="Natural Language Processing",
        assumptions=[
            ExtractedAssumption(
                text="Current attention mechanisms have O(n^2) complexity",
                implicit=False,
                confidence=0.95,
            ),
        ],
        constraints=[
            ExtractedConstraint(
                text="Must maintain competitive performance on standard benchmarks",
                constraint_type="methodological",
                confidence=0.85,
            ),
        ],
        datasets=[
            ExtractedDataset(
                name="SCROLLS",
                available=True,
                description="Long-context benchmark suite",
            ),
        ],
        metrics=[
            ExtractedMetric(
                name="F1",
                description="Token-level F1 score",
                baseline_value=87.3,
            ),
        ],
        baselines=[
            ExtractedBaseline(
                name="Sparse Transformers",
                paper_reference="Child et al. (2019)",
            ),
        ],
        quoted_text=(
            "The quadratic complexity of self-attention limits practical deployment "
            "for long documents exceeding 4096 tokens."
        ),
        confidence=0.92,
        reasoning="Explicitly stated as a core limitation motivating the work",
    )


@pytest.fixture
def sample_extracted_problem_low_confidence() -> ExtractedProblem:
    """Sample extracted problem with low confidence."""
    return ExtractedProblem(
        statement=(
            "The routing function in hierarchical attention mechanisms requires "
            "additional pre-training, increasing the overall computational cost "
            "of the training pipeline."
        ),
        domain="Natural Language Processing",
        quoted_text=(
            "the routing function requires pre-training which adds to the "
            "overall training cost"
        ),
        confidence=0.55,
        reasoning="Mentioned as a secondary limitation, unclear if standalone problem",
    )


@pytest.fixture
def sample_extraction_result(sample_extracted_problem) -> ExtractionResult:
    """Sample extraction result from a single section."""
    return ExtractionResult(
        problems=[sample_extracted_problem],
        section_type="limitations",
        extraction_notes="Extracted from limitations section",
    )


@pytest.fixture
def sample_batch_extraction_result(
    sample_extracted_problem,
    sample_extracted_problem_low_confidence,
) -> BatchExtractionResult:
    """Sample batch extraction result from multiple sections."""
    return BatchExtractionResult(
        results=[
            ExtractionResult(
                problems=[sample_extracted_problem],
                section_type="limitations",
            ),
            ExtractionResult(
                problems=[sample_extracted_problem_low_confidence],
                section_type="discussion",
            ),
        ],
        paper_title="Hierarchical Attention for Long-Context Understanding",
        paper_doi="10.1234/test.2024.001",
        total_problems=2,
        total_high_confidence=1,
    )


# =============================================================================
# Relation Extraction Fixtures
# =============================================================================


@pytest.fixture
def sample_extracted_relation() -> ExtractedRelation:
    """Sample extracted relation between problems."""
    return ExtractedRelation(
        source_index=0,
        target_index=1,
        relation_type=RelationType.LEADS_TO,
        confidence=0.8,
        evidence="Addressing attention complexity would reduce routing overhead",
    )


@pytest.fixture
def sample_relation_result(sample_extracted_relation) -> RelationExtractionResult:
    """Sample relation extraction result."""
    return RelationExtractionResult(
        relations=[sample_extracted_relation],
        relation_count=1,
    )


# =============================================================================
# LLM Client Fixtures
# =============================================================================


@pytest.fixture
def sample_token_usage() -> TokenUsage:
    """Sample token usage from LLM call."""
    return TokenUsage(
        prompt_tokens=500,
        completion_tokens=200,
        total_tokens=700,
    )


@pytest.fixture
def sample_llm_response(sample_token_usage) -> LLMResponse:
    """Sample LLM response."""
    return LLMResponse(
        content="Extracted problems as structured output",
        model="gpt-4-turbo",
        usage=sample_token_usage,
        finish_reason="stop",
    )


@pytest.fixture
def mock_llm_client():
    """Mock LLM client that returns predictable responses."""
    client = MagicMock()
    client.extract = AsyncMock()
    client.extract_batch = AsyncMock()
    client.model = "gpt-4-turbo"
    client.provider = "openai"
    return client


# =============================================================================
# Pipeline Stage Fixtures
# =============================================================================


@pytest.fixture
def sample_stage_results() -> list[PipelineStageResult]:
    """Sample pipeline stage results for a successful run."""
    return [
        PipelineStageResult(
            stage="pdf_extraction",
            success=True,
            duration_ms=150.0,
            metadata={"pages": 2, "chars": 2500},
        ),
        PipelineStageResult(
            stage="section_segmentation",
            success=True,
            duration_ms=25.0,
            metadata={"total_sections": 8, "filtered_sections": 5},
        ),
        PipelineStageResult(
            stage="problem_extraction",
            success=True,
            duration_ms=3200.0,
            metadata={"sections_processed": 3, "problems_extracted": 2, "token_usage": 700},
        ),
    ]


# =============================================================================
# Batch Processing Fixtures
# =============================================================================


@pytest.fixture
def sample_batch_job() -> BatchJob:
    """Sample batch job."""
    return BatchJob(
        job_id="job-001",
        batch_id="batch-test-001",
        paper_doi="10.1234/test.2024.001",
        pdf_url="https://arxiv.org/pdf/2401.12345.pdf",
        paper_title="Test Paper on Transformer Efficiency",
        status=JobStatus.PENDING,
    )


@pytest.fixture
def sample_batch_progress() -> BatchProgress:
    """Sample batch progress report."""
    return BatchProgress(
        batch_id="batch-test-001",
        total_jobs=10,
        completed_jobs=7,
        failed_jobs=1,
        pending_jobs=1,
        in_progress_jobs=1,
        total_problems=15,
        total_processing_time_ms=45000.0,
    )


# =============================================================================
# Paper Metadata Fixtures
# =============================================================================


@pytest.fixture
def sample_paper_doi() -> str:
    """Sample paper DOI for extraction tests."""
    return "10.1234/test.2024.001"


@pytest.fixture
def sample_paper_title() -> str:
    """Sample paper title for extraction tests."""
    return "Hierarchical Attention for Long-Context Understanding"


@pytest.fixture
def sample_paper_authors() -> list[str]:
    """Sample paper authors for extraction tests."""
    return ["Alice Researcher", "Bob Scientist", "Carol Engineer"]
