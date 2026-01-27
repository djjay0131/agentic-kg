"""
Problem Extractor Core.

Implements the main logic for extracting structured research problems
from paper sections using LLM-based extraction with the instructor library.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from agentic_kg.extraction.llm_client import (
    BaseLLMClient,
    LLMError,
    OpenAIClient,
    get_openai_client,
)
from agentic_kg.extraction.prompts import get_extraction_prompt
from agentic_kg.extraction.schemas import (
    BatchExtractionResult,
    ExtractedProblem,
    ExtractionResult,
)
from agentic_kg.extraction.section_segmenter import Section, SectionType

logger = logging.getLogger(__name__)


@dataclass
class ExtractionConfig:
    """Configuration for the problem extractor."""

    # LLM settings
    model: str = "gpt-4-turbo"
    temperature: float = 0.1

    # Extraction settings
    min_confidence: float = 0.5  # Minimum confidence to keep a problem
    max_problems_per_section: int = 10  # Cap on problems per section
    skip_low_priority_sections: bool = False  # Skip references, appendix, etc.

    # Section priority threshold (1=highest, 100=lowest)
    max_section_priority: int = 20  # Only process sections with priority <= this

    # Retry settings
    max_retries: int = 3
    retry_on_empty: bool = True  # Retry if no problems found (may be extraction issue)


@dataclass
class ProblemExtractor:
    """
    Extracts structured research problems from paper sections.

    Uses LLM-based extraction with the instructor library for
    structured output that conforms to the ExtractionResult schema.
    """

    client: Optional[BaseLLMClient] = None
    config: ExtractionConfig = field(default_factory=ExtractionConfig)

    def __post_init__(self):
        """Initialize the LLM client if not provided."""
        if self.client is None:
            self.client = get_openai_client(
                model=self.config.model,
                temperature=self.config.temperature,
            )

    async def extract_from_section(
        self,
        section: Section,
        paper_title: str,
        authors: Optional[list[str]] = None,
    ) -> ExtractionResult:
        """
        Extract problems from a single section.

        Args:
            section: The section to extract from.
            paper_title: Title of the paper.
            authors: List of author names (optional).

        Returns:
            ExtractionResult containing extracted problems.

        Raises:
            LLMError: If extraction fails after retries.
        """
        logger.info(
            f"Extracting from section: {section.section_type.value} "
            f"({section.word_count} words)"
        )

        # Skip low priority sections if configured
        if self.config.skip_low_priority_sections:
            if section.priority > self.config.max_section_priority:
                logger.debug(f"Skipping low priority section: {section.section_type}")
                return ExtractionResult(
                    problems=[],
                    section_type=section.section_type.value,
                    extraction_notes="Skipped due to low priority",
                )

        # Get formatted prompt
        prompt = get_extraction_prompt(
            section_text=section.content,
            section_type=section.section_type,
            paper_title=paper_title,
            authors=authors,
        )

        # Attempt extraction with retries
        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                response = await self.client.extract(
                    prompt=prompt.user_prompt,
                    response_model=ExtractionResult,
                    system_prompt=prompt.system_prompt,
                )

                result = response.content

                # Validate and filter results
                result = self._filter_results(result)

                # Log token usage
                logger.debug(
                    f"Extraction used {response.usage.total_tokens} tokens, "
                    f"found {result.problem_count} problems"
                )

                # Check if retry on empty is enabled
                if self.config.retry_on_empty and result.problem_count == 0:
                    if attempt < self.config.max_retries - 1:
                        logger.debug(
                            f"No problems found, retrying ({attempt + 1}/{self.config.max_retries})"
                        )
                        continue

                return result

            except LLMError as e:
                last_error = e
                logger.warning(f"Extraction attempt {attempt + 1} failed: {e}")
                if attempt < self.config.max_retries - 1:
                    continue
                raise

        # Should not reach here, but handle edge case
        if last_error:
            raise last_error

        return ExtractionResult(
            problems=[],
            section_type=section.section_type.value,
            extraction_notes="Extraction failed after all retries",
        )

    async def extract_from_sections(
        self,
        sections: list[Section],
        paper_title: str,
        paper_doi: Optional[str] = None,
        authors: Optional[list[str]] = None,
    ) -> BatchExtractionResult:
        """
        Extract problems from multiple sections.

        Args:
            sections: List of sections to extract from.
            paper_title: Title of the paper.
            paper_doi: DOI of the paper (optional).
            authors: List of author names (optional).

        Returns:
            BatchExtractionResult with all extracted problems.
        """
        results = []
        total_problems = 0
        total_high_confidence = 0

        # Sort sections by priority (lowest number = highest priority)
        sorted_sections = sorted(sections, key=lambda s: s.priority)

        for section in sorted_sections:
            try:
                result = await self.extract_from_section(
                    section=section,
                    paper_title=paper_title,
                    authors=authors,
                )

                results.append(result)
                total_problems += result.problem_count
                total_high_confidence += len(result.high_confidence_problems)

                logger.info(
                    f"Extracted {result.problem_count} problems from {section.section_type.value}"
                )

            except LLMError as e:
                logger.error(f"Failed to extract from {section.section_type}: {e}")
                results.append(
                    ExtractionResult(
                        problems=[],
                        section_type=section.section_type.value,
                        extraction_notes=f"Extraction failed: {e}",
                    )
                )

        return BatchExtractionResult(
            results=results,
            paper_title=paper_title,
            paper_doi=paper_doi,
            total_problems=total_problems,
            total_high_confidence=total_high_confidence,
        )

    async def extract_from_text(
        self,
        text: str,
        section_type: SectionType,
        paper_title: str,
        authors: Optional[list[str]] = None,
    ) -> ExtractionResult:
        """
        Extract problems from raw text (without Section object).

        Args:
            text: The text content to extract from.
            section_type: Type of section (for prompt selection).
            paper_title: Title of the paper.
            authors: List of author names (optional).

        Returns:
            ExtractionResult containing extracted problems.
        """
        # Create a temporary Section object
        section = Section(
            section_type=section_type,
            title=section_type.value.replace("_", " ").title(),
            content=text,
        )

        return await self.extract_from_section(
            section=section,
            paper_title=paper_title,
            authors=authors,
        )

    def _filter_results(self, result: ExtractionResult) -> ExtractionResult:
        """
        Filter and validate extraction results.

        Args:
            result: Raw extraction result from LLM.

        Returns:
            Filtered ExtractionResult.
        """
        filtered_problems = []

        for problem in result.problems:
            # Filter by confidence
            if problem.confidence < self.config.min_confidence:
                logger.debug(
                    f"Filtering low-confidence problem: {problem.confidence:.2f}"
                )
                continue

            # Validate problem has required content
            if not self._validate_problem(problem):
                logger.debug("Filtering invalid problem")
                continue

            filtered_problems.append(problem)

        # Apply max problems limit
        if len(filtered_problems) > self.config.max_problems_per_section:
            # Keep highest confidence problems
            filtered_problems = sorted(
                filtered_problems, key=lambda p: p.confidence, reverse=True
            )[: self.config.max_problems_per_section]

        return ExtractionResult(
            problems=filtered_problems,
            section_type=result.section_type,
            extraction_notes=result.extraction_notes,
        )

    def _validate_problem(self, problem: ExtractedProblem) -> bool:
        """
        Validate that a problem has required content.

        Args:
            problem: Problem to validate.

        Returns:
            True if valid, False otherwise.
        """
        # Check statement length
        if len(problem.statement) < 20:
            return False

        # Check quoted text exists
        if len(problem.quoted_text) < 10:
            return False

        return True


# Singleton instance
_extractor: Optional[ProblemExtractor] = None


def get_problem_extractor(
    config: Optional[ExtractionConfig] = None,
) -> ProblemExtractor:
    """
    Get or create the singleton problem extractor.

    Args:
        config: Extraction configuration (optional).

    Returns:
        ProblemExtractor instance.
    """
    global _extractor

    if _extractor is None:
        _extractor = ProblemExtractor(config=config or ExtractionConfig())

    return _extractor


def reset_problem_extractor() -> None:
    """Reset the singleton problem extractor (for testing)."""
    global _extractor
    _extractor = None
