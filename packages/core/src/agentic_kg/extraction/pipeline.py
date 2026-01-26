"""
Paper processing pipeline for research problem extraction.

This module orchestrates the end-to-end extraction workflow:
PDF → Text → Sections → Problems → Relations → Knowledge Graph
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from agentic_kg.extraction.llm_client import BaseLLMClient, get_openai_client
from agentic_kg.extraction.pdf_extractor import (
    ExtractedText,
    PDFExtractionError,
    PDFExtractor,
    get_pdf_extractor,
)
from agentic_kg.extraction.problem_extractor import (
    ExtractionConfig,
    ProblemExtractor,
)
from agentic_kg.extraction.relation_extractor import (
    RelationConfig,
    RelationExtractionResult,
    RelationExtractor,
)
from agentic_kg.extraction.schemas import BatchExtractionResult, ExtractedProblem
from agentic_kg.extraction.section_segmenter import (
    Section,
    SectionSegmenter,
    SectionType,
    SegmentedDocument,
    get_section_segmenter,
)

logger = logging.getLogger(__name__)


class PipelineStageResult(BaseModel):
    """Result from a pipeline stage."""

    stage: str = Field(..., description="Name of the pipeline stage")
    success: bool = Field(..., description="Whether the stage succeeded")
    duration_ms: float = Field(..., description="Stage duration in milliseconds")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    metadata: dict = Field(default_factory=dict, description="Stage-specific metadata")


class PaperProcessingResult(BaseModel):
    """Complete result from processing a paper."""

    paper_doi: Optional[str] = Field(default=None, description="Paper DOI")
    paper_title: Optional[str] = Field(default=None, description="Paper title")
    paper_authors: list[str] = Field(default_factory=list, description="Paper authors")

    # Source information
    source_url: Optional[str] = Field(default=None, description="Source PDF URL")
    source_path: Optional[str] = Field(default=None, description="Local PDF path")

    # Stage results
    stages: list[PipelineStageResult] = Field(
        default_factory=list, description="Results from each pipeline stage"
    )

    # Extraction results
    extracted_text: Optional[ExtractedText] = Field(
        default=None, description="Extracted text from PDF"
    )
    segmented_document: Optional[SegmentedDocument] = Field(
        default=None, description="Segmented document sections"
    )
    extraction_result: Optional[BatchExtractionResult] = Field(
        default=None, description="Extracted problems"
    )
    relation_result: Optional[RelationExtractionResult] = Field(
        default=None, description="Extracted relations"
    )

    # Overall status
    success: bool = Field(default=False, description="Whether processing succeeded")
    total_duration_ms: float = Field(
        default=0.0, description="Total processing time in ms"
    )
    processed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Processing timestamp",
    )

    @property
    def problem_count(self) -> int:
        """Total number of extracted problems."""
        if self.extraction_result:
            return self.extraction_result.total_problems
        return 0

    @property
    def section_count(self) -> int:
        """Number of sections identified."""
        if self.segmented_document:
            return len(self.segmented_document.sections)
        return 0

    @property
    def relation_count(self) -> int:
        """Number of relations extracted."""
        if self.relation_result:
            return self.relation_result.relation_count
        return 0

    def get_problems(self) -> list[ExtractedProblem]:
        """Get all extracted problems."""
        if self.extraction_result:
            return self.extraction_result.all_problems
        return []

    def get_high_confidence_problems(
        self, threshold: float = 0.7
    ) -> list[ExtractedProblem]:
        """Get problems above confidence threshold."""
        return [p for p in self.get_problems() if p.confidence >= threshold]

    class Config:
        arbitrary_types_allowed = True


@dataclass
class PipelineConfig:
    """Configuration for the paper processing pipeline."""

    # PDF extraction
    pdf_timeout: float = 60.0

    # Section segmentation
    min_section_length: int = 100
    skip_references: bool = True

    # Problem extraction
    extraction_config: ExtractionConfig = field(default_factory=ExtractionConfig)

    # Relation extraction
    relation_config: RelationConfig = field(default_factory=RelationConfig)
    extract_relations: bool = True

    # Processing options
    parallel_sections: bool = True
    max_parallel_sections: int = 3

    # Logging
    verbose: bool = False


class PaperProcessingPipeline:
    """
    End-to-end paper processing pipeline.

    Orchestrates the extraction workflow from PDF to structured problems.
    """

    def __init__(
        self,
        client: Optional[BaseLLMClient] = None,
        config: Optional[PipelineConfig] = None,
    ):
        """
        Initialize the pipeline.

        Args:
            client: LLM client for extraction (uses OpenAI by default)
            config: Pipeline configuration
        """
        self.config = config or PipelineConfig()
        self.client = client

        # Initialize extractors (lazy)
        self._pdf_extractor: Optional[PDFExtractor] = None
        self._section_segmenter: Optional[SectionSegmenter] = None
        self._problem_extractor: Optional[ProblemExtractor] = None
        self._relation_extractor: Optional[RelationExtractor] = None

    @property
    def pdf_extractor(self) -> PDFExtractor:
        """Get PDF extractor (lazy initialization)."""
        if self._pdf_extractor is None:
            self._pdf_extractor = get_pdf_extractor()
        return self._pdf_extractor

    @property
    def section_segmenter(self) -> SectionSegmenter:
        """Get section segmenter (lazy initialization)."""
        if self._section_segmenter is None:
            self._section_segmenter = get_section_segmenter()
        return self._section_segmenter

    @property
    def problem_extractor(self) -> ProblemExtractor:
        """Get problem extractor (lazy initialization)."""
        if self._problem_extractor is None:
            client = self.client or get_openai_client()
            self._problem_extractor = ProblemExtractor(
                client=client,
                config=self.config.extraction_config,
            )
        return self._problem_extractor

    @property
    def relation_extractor(self) -> RelationExtractor:
        """Get relation extractor (lazy initialization)."""
        if self._relation_extractor is None:
            self._relation_extractor = RelationExtractor(
                client=self.client,
                config=self.config.relation_config,
            )
        return self._relation_extractor

    async def process_pdf_url(
        self,
        url: str,
        paper_title: Optional[str] = None,
        paper_doi: Optional[str] = None,
        authors: Optional[list[str]] = None,
    ) -> PaperProcessingResult:
        """
        Process a paper from a PDF URL.

        Args:
            url: URL to the PDF file
            paper_title: Optional paper title
            paper_doi: Optional paper DOI
            authors: Optional list of authors

        Returns:
            PaperProcessingResult with all extraction results
        """
        import time

        start_time = time.time()
        result = PaperProcessingResult(
            paper_title=paper_title,
            paper_doi=paper_doi,
            paper_authors=authors or [],
            source_url=url,
        )

        try:
            # Stage 1: PDF Extraction
            stage_start = time.time()
            try:
                extracted_text = await self.pdf_extractor.extract_from_url(
                    url, timeout=self.config.pdf_timeout
                )
                result.extracted_text = extracted_text
                result.stages.append(
                    PipelineStageResult(
                        stage="pdf_extraction",
                        success=True,
                        duration_ms=(time.time() - stage_start) * 1000,
                        metadata={
                            "pages": extracted_text.page_count,
                            "chars": len(extracted_text.full_text),
                        },
                    )
                )
            except PDFExtractionError as e:
                result.stages.append(
                    PipelineStageResult(
                        stage="pdf_extraction",
                        success=False,
                        duration_ms=(time.time() - stage_start) * 1000,
                        error=str(e),
                    )
                )
                result.total_duration_ms = (time.time() - start_time) * 1000
                return result

            # Continue with common processing
            await self._process_extracted_text(result, extracted_text)

        except Exception as e:
            logger.error(f"Pipeline error processing {url}: {e}")
            result.stages.append(
                PipelineStageResult(
                    stage="pipeline_error",
                    success=False,
                    duration_ms=0,
                    error=str(e),
                )
            )

        result.total_duration_ms = (time.time() - start_time) * 1000
        result.success = all(s.success for s in result.stages)
        return result

    async def process_pdf_file(
        self,
        file_path: str | Path,
        paper_title: Optional[str] = None,
        paper_doi: Optional[str] = None,
        authors: Optional[list[str]] = None,
    ) -> PaperProcessingResult:
        """
        Process a paper from a local PDF file.

        Args:
            file_path: Path to the PDF file
            paper_title: Optional paper title
            paper_doi: Optional paper DOI
            authors: Optional list of authors

        Returns:
            PaperProcessingResult with all extraction results
        """
        import time

        start_time = time.time()
        path = Path(file_path)
        result = PaperProcessingResult(
            paper_title=paper_title,
            paper_doi=paper_doi,
            paper_authors=authors or [],
            source_path=str(path),
        )

        try:
            # Stage 1: PDF Extraction
            stage_start = time.time()
            try:
                extracted_text = self.pdf_extractor.extract_from_file(path)
                result.extracted_text = extracted_text
                result.stages.append(
                    PipelineStageResult(
                        stage="pdf_extraction",
                        success=True,
                        duration_ms=(time.time() - stage_start) * 1000,
                        metadata={
                            "pages": extracted_text.page_count,
                            "chars": len(extracted_text.full_text),
                        },
                    )
                )
            except PDFExtractionError as e:
                result.stages.append(
                    PipelineStageResult(
                        stage="pdf_extraction",
                        success=False,
                        duration_ms=(time.time() - stage_start) * 1000,
                        error=str(e),
                    )
                )
                result.total_duration_ms = (time.time() - start_time) * 1000
                return result

            # Continue with common processing
            await self._process_extracted_text(result, extracted_text)

        except Exception as e:
            logger.error(f"Pipeline error processing {file_path}: {e}")
            result.stages.append(
                PipelineStageResult(
                    stage="pipeline_error",
                    success=False,
                    duration_ms=0,
                    error=str(e),
                )
            )

        result.total_duration_ms = (time.time() - start_time) * 1000
        result.success = all(s.success for s in result.stages)
        return result

    async def process_text(
        self,
        text: str,
        paper_title: Optional[str] = None,
        paper_doi: Optional[str] = None,
        authors: Optional[list[str]] = None,
    ) -> PaperProcessingResult:
        """
        Process a paper from raw text.

        Args:
            text: Full text of the paper
            paper_title: Optional paper title
            paper_doi: Optional paper DOI
            authors: Optional list of authors

        Returns:
            PaperProcessingResult with all extraction results
        """
        import time

        start_time = time.time()
        result = PaperProcessingResult(
            paper_title=paper_title,
            paper_doi=paper_doi,
            paper_authors=authors or [],
        )

        # Create mock extracted text
        extracted_text = ExtractedText(
            pages=[],
            source="direct_text",
            metadata={"text_length": len(text)},
        )
        extracted_text._full_text = text
        result.extracted_text = extracted_text

        result.stages.append(
            PipelineStageResult(
                stage="text_input",
                success=True,
                duration_ms=0,
                metadata={"chars": len(text)},
            )
        )

        try:
            await self._process_extracted_text(result, extracted_text)
        except Exception as e:
            logger.error(f"Pipeline error processing text: {e}")
            result.stages.append(
                PipelineStageResult(
                    stage="pipeline_error",
                    success=False,
                    duration_ms=0,
                    error=str(e),
                )
            )

        result.total_duration_ms = (time.time() - start_time) * 1000
        result.success = all(s.success for s in result.stages)
        return result

    async def _process_extracted_text(
        self,
        result: PaperProcessingResult,
        extracted_text: ExtractedText,
    ) -> None:
        """
        Process extracted text through remaining pipeline stages.

        Args:
            result: Result object to update
            extracted_text: Text extracted from PDF
        """
        import time

        # Stage 2: Section Segmentation
        stage_start = time.time()
        try:
            segmented = self.section_segmenter.segment(extracted_text.full_text)
            result.segmented_document = segmented

            # Filter sections if configured
            sections = segmented.sections
            if self.config.skip_references:
                sections = [
                    s for s in sections if s.section_type != SectionType.REFERENCES
                ]
            sections = [
                s
                for s in sections
                if len(s.content) >= self.config.min_section_length
            ]

            result.stages.append(
                PipelineStageResult(
                    stage="section_segmentation",
                    success=True,
                    duration_ms=(time.time() - stage_start) * 1000,
                    metadata={
                        "total_sections": len(segmented.sections),
                        "filtered_sections": len(sections),
                    },
                )
            )
        except Exception as e:
            result.stages.append(
                PipelineStageResult(
                    stage="section_segmentation",
                    success=False,
                    duration_ms=(time.time() - stage_start) * 1000,
                    error=str(e),
                )
            )
            return

        # Stage 3: Problem Extraction
        stage_start = time.time()
        try:
            extraction_result = await self.problem_extractor.extract_from_sections(
                sections=sections,
                paper_title=result.paper_title or "Unknown",
                paper_doi=result.paper_doi,
                authors=result.paper_authors,
            )
            result.extraction_result = extraction_result

            result.stages.append(
                PipelineStageResult(
                    stage="problem_extraction",
                    success=True,
                    duration_ms=(time.time() - stage_start) * 1000,
                    metadata={
                        "sections_processed": len(extraction_result.results),
                        "problems_extracted": extraction_result.total_problems,
                        "token_usage": extraction_result.total_tokens,
                    },
                )
            )
        except Exception as e:
            result.stages.append(
                PipelineStageResult(
                    stage="problem_extraction",
                    success=False,
                    duration_ms=(time.time() - stage_start) * 1000,
                    error=str(e),
                )
            )
            return

        # Stage 4: Relation Extraction (optional)
        if self.config.extract_relations and result.problem_count >= 2:
            stage_start = time.time()
            try:
                all_problems = result.get_problems()

                # Use full text for relation extraction
                relation_result = await self.relation_extractor.extract_from_text_with_llm(
                    text=extracted_text.full_text[:5000],  # Limit text for LLM
                    problems=all_problems,
                    paper_title=result.paper_title,
                )
                result.relation_result = relation_result

                result.stages.append(
                    PipelineStageResult(
                        stage="relation_extraction",
                        success=True,
                        duration_ms=(time.time() - stage_start) * 1000,
                        metadata={
                            "relations_extracted": relation_result.relation_count,
                        },
                    )
                )
            except Exception as e:
                result.stages.append(
                    PipelineStageResult(
                        stage="relation_extraction",
                        success=False,
                        duration_ms=(time.time() - stage_start) * 1000,
                        error=str(e),
                    )
                )

    def get_priority_sections(
        self, segmented: SegmentedDocument, top_n: int = 3
    ) -> list[Section]:
        """
        Get the highest priority sections for extraction.

        Args:
            segmented: Segmented document
            top_n: Number of top sections to return

        Returns:
            List of highest priority sections
        """
        # Get sections sorted by priority
        priority_sections = segmented.get_sections_by_priority()

        # Filter by configured settings
        filtered = []
        for section in priority_sections:
            if self.config.skip_references and section.section_type == SectionType.REFERENCES:
                continue
            if len(section.content) < self.config.min_section_length:
                continue
            filtered.append(section)

        return filtered[:top_n]


# Module-level singleton
_pipeline: Optional[PaperProcessingPipeline] = None


def get_pipeline(
    client: Optional[BaseLLMClient] = None,
    config: Optional[PipelineConfig] = None,
) -> PaperProcessingPipeline:
    """
    Get the singleton pipeline instance.

    Args:
        client: Optional LLM client
        config: Optional pipeline configuration

    Returns:
        PaperProcessingPipeline instance
    """
    global _pipeline

    if _pipeline is None:
        _pipeline = PaperProcessingPipeline(client=client, config=config)

    return _pipeline


def reset_pipeline() -> None:
    """Reset the singleton (useful for testing)."""
    global _pipeline
    _pipeline = None
