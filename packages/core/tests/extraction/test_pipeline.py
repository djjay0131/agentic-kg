"""
Unit tests for paper processing pipeline.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from agentic_kg.extraction.llm_client import LLMResponse, TokenUsage
from agentic_kg.extraction.pdf_extractor import ExtractedPage, ExtractedText
from agentic_kg.extraction.pipeline import (
    PaperProcessingPipeline,
    PaperProcessingResult,
    PipelineConfig,
    PipelineStageResult,
    get_pipeline,
    reset_pipeline,
)
from agentic_kg.extraction.problem_extractor import ExtractionConfig
from agentic_kg.extraction.relation_extractor import (
    ExtractedRelation,
    RelationConfig,
    RelationExtractionResult,
    RelationType,
)
from agentic_kg.extraction.schemas import (
    BatchExtractionResult,
    ExtractedProblem,
    ExtractionResult,
)
from agentic_kg.extraction.section_segmenter import (
    Section,
    SectionType,
    SegmentedDocument,
)


class TestPipelineConfig:
    """Tests for PipelineConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = PipelineConfig()

        assert config.pdf_timeout == 60.0
        assert config.min_section_length == 100
        assert config.skip_references is True
        assert config.extract_relations is True
        assert config.parallel_sections is True

    def test_custom_config(self):
        """Test custom configuration."""
        config = PipelineConfig(
            pdf_timeout=30.0,
            min_section_length=50,
            skip_references=False,
            extract_relations=False,
        )

        assert config.pdf_timeout == 30.0
        assert config.min_section_length == 50
        assert config.skip_references is False
        assert config.extract_relations is False

    def test_nested_configs(self):
        """Test nested extraction and relation configs."""
        config = PipelineConfig(
            extraction_config=ExtractionConfig(min_confidence=0.8),
            relation_config=RelationConfig(min_confidence=0.7),
        )

        assert config.extraction_config.min_confidence == 0.8
        assert config.relation_config.min_confidence == 0.7


class TestPipelineStageResult:
    """Tests for PipelineStageResult model."""

    def test_successful_stage(self):
        """Test successful stage result."""
        result = PipelineStageResult(
            stage="pdf_extraction",
            success=True,
            duration_ms=1234.5,
            metadata={"pages": 10},
        )

        assert result.stage == "pdf_extraction"
        assert result.success is True
        assert result.error is None
        assert result.metadata["pages"] == 10

    def test_failed_stage(self):
        """Test failed stage result."""
        result = PipelineStageResult(
            stage="section_segmentation",
            success=False,
            duration_ms=500.0,
            error="Failed to parse sections",
        )

        assert result.success is False
        assert result.error == "Failed to parse sections"


class TestPaperProcessingResult:
    """Tests for PaperProcessingResult model."""

    @pytest.fixture
    def sample_result(self):
        """Create sample processing result."""
        return PaperProcessingResult(
            paper_title="Test Paper",
            paper_doi="10.1234/test",
            paper_authors=["Author One", "Author Two"],
            source_url="https://example.com/paper.pdf",
            extraction_result=BatchExtractionResult(
                paper_title="Test Paper",
                paper_doi="10.1234/test",
                results=[
                    ExtractionResult(
                        section_type="limitations",
                        problems=[
                            ExtractedProblem(
                                statement="Problem statement with enough characters here.",
                                quoted_text="quoted text here",
                                confidence=0.9,
                            ),
                            ExtractedProblem(
                                statement="Another problem statement with enough chars.",
                                quoted_text="another quote here",
                                confidence=0.6,
                            ),
                        ],
                    ),
                ],
            ),
            relation_result=RelationExtractionResult(
                relations=[
                    ExtractedRelation(
                        source_problem_id="Problem A",
                        target_problem_id="Problem B",
                        relation_type=RelationType.EXTENDS,
                        confidence=0.8,
                        evidence="Evidence text here",
                    ),
                ],
            ),
            stages=[
                PipelineStageResult(
                    stage="pdf_extraction",
                    success=True,
                    duration_ms=1000,
                ),
                PipelineStageResult(
                    stage="section_segmentation",
                    success=True,
                    duration_ms=500,
                ),
            ],
            success=True,
            total_duration_ms=3000,
        )

    def test_problem_count(self, sample_result):
        """Test problem count property."""
        assert sample_result.problem_count == 2

    def test_relation_count(self, sample_result):
        """Test relation count property."""
        assert sample_result.relation_count == 1

    def test_get_problems(self, sample_result):
        """Test getting all problems."""
        problems = sample_result.get_problems()
        assert len(problems) == 2

    def test_get_high_confidence_problems(self, sample_result):
        """Test filtering by confidence."""
        high_conf = sample_result.get_high_confidence_problems(threshold=0.7)
        assert len(high_conf) == 1
        assert high_conf[0].confidence == 0.9

    def test_empty_result(self):
        """Test empty result defaults."""
        result = PaperProcessingResult()

        assert result.problem_count == 0
        assert result.relation_count == 0
        assert result.section_count == 0
        assert result.get_problems() == []


class TestPaperProcessingPipeline:
    """Tests for PaperProcessingPipeline class."""

    @pytest.fixture
    def mock_client(self):
        """Create mock LLM client."""
        client = MagicMock()
        client.extract = AsyncMock()
        return client

    @pytest.fixture
    def pipeline(self, mock_client):
        """Create pipeline with mock client."""
        return PaperProcessingPipeline(client=mock_client)

    @pytest.fixture
    def sample_extracted_text(self):
        """Create sample extracted text."""
        return ExtractedText(
            pages=[
                ExtractedPage(page_num=1, text="Abstract: This paper studies..."),
                ExtractedPage(
                    page_num=2,
                    text="""
                    1. Introduction

                    This is the introduction section with background information.

                    2. Methods

                    We used various methods to analyze the data.

                    3. Limitations

                    Our approach has several limitations. First, we only tested on
                    small datasets due to computational constraints. Second, the
                    model requires significant training time.

                    4. Conclusion

                    We presented a new approach to the problem.
                    """,
                ),
            ],
            source="test.pdf",
            metadata={},
        )

    @pytest.fixture
    def sample_problems(self):
        """Create sample extracted problems."""
        return [
            ExtractedProblem(
                statement="Deep learning models require significant computational resources.",
                quoted_text="significant computational resources needed",
                confidence=0.9,
            ),
            ExtractedProblem(
                statement="Training time for large models is prohibitive.",
                quoted_text="requires significant training time",
                confidence=0.85,
            ),
        ]

    def test_lazy_initialization(self, pipeline):
        """Test that extractors are lazily initialized."""
        # Initially None
        assert pipeline._pdf_extractor is None
        assert pipeline._section_segmenter is None

        # Accessed on demand
        pdf_ext = pipeline.pdf_extractor
        assert pdf_ext is not None
        assert pipeline._pdf_extractor is not None

    @pytest.mark.asyncio
    async def test_process_text_success(self, pipeline, mock_client, sample_problems):
        """Test successful text processing."""
        # Mock section segmenter
        mock_segmented = SegmentedDocument(
            sections=[
                Section(
                    section_type=SectionType.LIMITATIONS,
                    title="Limitations",
                    content="Our approach has limitations with computational resources and training time." * 10,
                ),
            ],
            raw_text="Full paper text here",
        )

        with patch.object(
            pipeline.section_segmenter, "segment", return_value=mock_segmented
        ):
            # Mock problem extractor
            mock_extraction = BatchExtractionResult(
                paper_title="Test Paper",
                results=[
                    ExtractionResult(
                        section_type="limitations",
                        problems=sample_problems,
                    ),
                ],
            )

            with patch.object(
                pipeline.problem_extractor,
                "extract_from_sections",
                new_callable=AsyncMock,
                return_value=mock_extraction,
            ):
                result = await pipeline.process_text(
                    text="Full paper text here with limitations and conclusions.",
                    paper_title="Test Paper",
                    paper_doi="10.1234/test",
                )

        assert result.success is True
        assert result.paper_title == "Test Paper"
        assert len(result.stages) >= 2  # text_input, segmentation, extraction

    @pytest.mark.asyncio
    async def test_process_text_with_relations(self, pipeline, mock_client, sample_problems):
        """Test processing with relation extraction enabled."""
        pipeline.config.extract_relations = True

        mock_segmented = SegmentedDocument(
            sections=[
                Section(
                    section_type=SectionType.LIMITATIONS,
                    title="Limitations",
                    content="Limitations content here..." * 20,
                ),
            ],
            raw_text="Full text",
        )

        mock_extraction = BatchExtractionResult(
            paper_title="Test",
            results=[
                ExtractionResult(
                    section_type="limitations",
                    problems=sample_problems,
                ),
            ],
        )

        mock_relations = RelationExtractionResult(
            relations=[
                ExtractedRelation(
                    source_problem_id="Problem 1",
                    target_problem_id="Problem 2",
                    relation_type=RelationType.RELATED_TO,
                    confidence=0.7,
                    evidence="Both about resources",
                ),
            ],
        )

        with patch.object(pipeline.section_segmenter, "segment", return_value=mock_segmented):
            with patch.object(
                pipeline.problem_extractor,
                "extract_from_sections",
                new_callable=AsyncMock,
                return_value=mock_extraction,
            ):
                with patch.object(
                    pipeline.relation_extractor,
                    "extract_from_text_with_llm",
                    new_callable=AsyncMock,
                    return_value=mock_relations,
                ):
                    result = await pipeline.process_text(
                        text="Paper text here",
                        paper_title="Test",
                    )

        assert result.relation_count == 1

    @pytest.mark.asyncio
    async def test_process_text_skips_short_sections(self, pipeline, mock_client):
        """Test that short sections are skipped."""
        pipeline.config.min_section_length = 100

        mock_segmented = SegmentedDocument(
            sections=[
                Section(
                    section_type=SectionType.LIMITATIONS,
                    title="Limitations",
                    content="Too short",  # Less than 100 chars
                ),
                Section(
                    section_type=SectionType.CONCLUSION,
                    title="Conclusion",
                    content="This is a longer conclusion section with more content." * 5,
                ),
            ],
            raw_text="Full text",
        )

        mock_extraction = BatchExtractionResult(
            paper_title="Test",
            results=[],
        )

        with patch.object(pipeline.section_segmenter, "segment", return_value=mock_segmented):
            with patch.object(
                pipeline.problem_extractor,
                "extract_from_sections",
                new_callable=AsyncMock,
                return_value=mock_extraction,
            ) as mock_extract:
                await pipeline.process_text(text="Paper text", paper_title="Test")

                # Should only receive the long section
                call_args = mock_extract.call_args
                sections = call_args.kwargs["sections"]
                assert len(sections) == 1
                assert sections[0].section_type == SectionType.CONCLUSION

    @pytest.mark.asyncio
    async def test_process_text_skips_references(self, pipeline, mock_client):
        """Test that references section is skipped."""
        pipeline.config.skip_references = True

        mock_segmented = SegmentedDocument(
            sections=[
                Section(
                    section_type=SectionType.LIMITATIONS,
                    title="Limitations",
                    content="Limitations content here with sufficient length for processing." * 5,
                ),
                Section(
                    section_type=SectionType.REFERENCES,
                    title="References",
                    content="[1] Paper reference here..." * 10,
                ),
            ],
            raw_text="Full text",
        )

        mock_extraction = BatchExtractionResult(
            paper_title="Test",
            results=[],
        )

        with patch.object(pipeline.section_segmenter, "segment", return_value=mock_segmented):
            with patch.object(
                pipeline.problem_extractor,
                "extract_from_sections",
                new_callable=AsyncMock,
                return_value=mock_extraction,
            ) as mock_extract:
                await pipeline.process_text(text="Paper text", paper_title="Test")

                call_args = mock_extract.call_args
                sections = call_args.kwargs["sections"]
                assert len(sections) == 1
                assert sections[0].section_type == SectionType.LIMITATIONS

    @pytest.mark.asyncio
    async def test_process_pdf_file_success(self, pipeline, mock_client):
        """Test processing local PDF file."""
        mock_extracted = ExtractedText(
            pages=[ExtractedPage(page_num=1, text="Paper content here")],
            source="test.pdf",
            metadata={},
        )

        mock_segmented = SegmentedDocument(
            sections=[
                Section(
                    section_type=SectionType.ABSTRACT,
                    title="Abstract",
                    content="Abstract content here with sufficient length." * 5,
                ),
            ],
            raw_text="Paper content",
        )

        mock_extraction = BatchExtractionResult(
            paper_title="Test",
            results=[],
        )

        with patch.object(
            pipeline.pdf_extractor,
            "extract_from_file",
            return_value=mock_extracted,
        ):
            with patch.object(
                pipeline.section_segmenter, "segment", return_value=mock_segmented
            ):
                with patch.object(
                    pipeline.problem_extractor,
                    "extract_from_sections",
                    new_callable=AsyncMock,
                    return_value=mock_extraction,
                ):
                    result = await pipeline.process_pdf_file(
                        file_path="/path/to/paper.pdf",
                        paper_title="Test Paper",
                    )

        assert result.source_path == "/path/to/paper.pdf"
        assert len(result.stages) >= 1

    @pytest.mark.asyncio
    async def test_process_pdf_url_success(self, pipeline, mock_client):
        """Test processing PDF from URL."""
        mock_extracted = ExtractedText(
            pages=[ExtractedPage(page_num=1, text="Paper content here")],
            source="https://example.com/paper.pdf",
            metadata={},
        )

        mock_segmented = SegmentedDocument(
            sections=[
                Section(
                    section_type=SectionType.ABSTRACT,
                    title="Abstract",
                    content="Abstract content here with sufficient length." * 5,
                ),
            ],
            raw_text="Paper content",
        )

        mock_extraction = BatchExtractionResult(
            paper_title="Test",
            results=[],
        )

        with patch.object(
            pipeline.pdf_extractor,
            "extract_from_url",
            new_callable=AsyncMock,
            return_value=mock_extracted,
        ):
            with patch.object(
                pipeline.section_segmenter, "segment", return_value=mock_segmented
            ):
                with patch.object(
                    pipeline.problem_extractor,
                    "extract_from_sections",
                    new_callable=AsyncMock,
                    return_value=mock_extraction,
                ):
                    result = await pipeline.process_pdf_url(
                        url="https://example.com/paper.pdf",
                        paper_title="Test Paper",
                    )

        assert result.source_url == "https://example.com/paper.pdf"

    @pytest.mark.asyncio
    async def test_handles_segmentation_error(self, pipeline, mock_client):
        """Test handling of segmentation errors."""
        with patch.object(
            pipeline.section_segmenter,
            "segment",
            side_effect=Exception("Segmentation failed"),
        ):
            result = await pipeline.process_text(
                text="Paper text here",
                paper_title="Test",
            )

        # Should have failed at segmentation
        seg_stage = next(
            (s for s in result.stages if s.stage == "section_segmentation"), None
        )
        assert seg_stage is not None
        assert seg_stage.success is False
        assert "Segmentation failed" in seg_stage.error

    def test_get_priority_sections(self, pipeline):
        """Test getting priority sections."""
        segmented = SegmentedDocument(
            sections=[
                Section(
                    section_type=SectionType.REFERENCES,
                    title="References",
                    content="[1] Reference..." * 50,
                ),
                Section(
                    section_type=SectionType.LIMITATIONS,
                    title="Limitations",
                    content="Limitations content here..." * 50,
                ),
                Section(
                    section_type=SectionType.FUTURE_WORK,
                    title="Future Work",
                    content="Future work content here..." * 50,
                ),
                Section(
                    section_type=SectionType.ABSTRACT,
                    title="Abstract",
                    content="Abstract content here..." * 50,
                ),
            ],
            raw_text="Full text",
        )

        priority = pipeline.get_priority_sections(segmented, top_n=2)

        # Should get limitations and future_work (highest priority)
        # but not references (skipped)
        assert len(priority) == 2
        types = [s.section_type for s in priority]
        assert SectionType.REFERENCES not in types
        assert SectionType.LIMITATIONS in types
        assert SectionType.FUTURE_WORK in types


class TestGetPipeline:
    """Tests for singleton access."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_pipeline()

    def teardown_method(self):
        """Reset singleton after each test."""
        reset_pipeline()

    def test_returns_pipeline_instance(self):
        """Test that get_pipeline returns a pipeline."""
        with patch(
            "agentic_kg.extraction.pipeline.get_openai_client"
        ) as mock_get_client:
            mock_get_client.return_value = MagicMock()
            pipeline = get_pipeline()
            assert isinstance(pipeline, PaperProcessingPipeline)

    def test_returns_same_instance(self):
        """Test singleton pattern."""
        with patch(
            "agentic_kg.extraction.pipeline.get_openai_client"
        ) as mock_get_client:
            mock_get_client.return_value = MagicMock()
            pipeline1 = get_pipeline()
            pipeline2 = get_pipeline()
            assert pipeline1 is pipeline2

    def test_reset_clears_singleton(self):
        """Test reset clears singleton."""
        with patch(
            "agentic_kg.extraction.pipeline.get_openai_client"
        ) as mock_get_client:
            mock_get_client.return_value = MagicMock()
            pipeline1 = get_pipeline()
            reset_pipeline()
            pipeline2 = get_pipeline()
            assert pipeline1 is not pipeline2
