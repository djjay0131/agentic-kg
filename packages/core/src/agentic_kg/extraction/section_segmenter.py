"""
Section Segmentation Module.

Identifies and extracts distinct sections from academic papers using
heuristic pattern matching with optional LLM fallback for ambiguous cases.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SectionType(str, Enum):
    """Types of sections commonly found in academic papers."""

    ABSTRACT = "abstract"
    INTRODUCTION = "introduction"
    RELATED_WORK = "related_work"
    BACKGROUND = "background"
    METHODS = "methods"
    EXPERIMENTS = "experiments"
    RESULTS = "results"
    DISCUSSION = "discussion"
    LIMITATIONS = "limitations"
    FUTURE_WORK = "future_work"
    CONCLUSION = "conclusion"
    ACKNOWLEDGMENTS = "acknowledgments"
    REFERENCES = "references"
    APPENDIX = "appendix"
    UNKNOWN = "unknown"


# Priority order for problem extraction (higher priority = more likely to contain problems)
SECTION_PRIORITY = {
    SectionType.LIMITATIONS: 1,
    SectionType.FUTURE_WORK: 2,
    SectionType.DISCUSSION: 3,
    SectionType.CONCLUSION: 4,
    SectionType.INTRODUCTION: 5,
    SectionType.RESULTS: 6,
    SectionType.EXPERIMENTS: 7,
    SectionType.METHODS: 8,
    SectionType.RELATED_WORK: 9,
    SectionType.BACKGROUND: 10,
    SectionType.ABSTRACT: 11,
    SectionType.ACKNOWLEDGMENTS: 99,
    SectionType.REFERENCES: 100,
    SectionType.APPENDIX: 100,
    SectionType.UNKNOWN: 50,
}


@dataclass
class Section:
    """Represents a section of an academic paper."""

    section_type: SectionType
    title: str  # Original heading text
    content: str  # Section text content
    start_char: int = 0  # Character offset in original document
    end_char: int = 0  # Character offset in original document
    confidence: float = 1.0  # Confidence of section type classification
    subsections: list["Section"] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        """Get word count of section content."""
        return len(self.content.split())

    @property
    def priority(self) -> int:
        """Get extraction priority for this section type."""
        return SECTION_PRIORITY.get(self.section_type, 50)


@dataclass
class SegmentedDocument:
    """Represents a segmented academic paper."""

    sections: list[Section] = field(default_factory=list)
    full_text: str = ""
    detected_structure: bool = False  # Whether clear section structure was found

    def get_sections_by_type(self, section_type: SectionType) -> list[Section]:
        """Get all sections of a specific type."""
        return [s for s in self.sections if s.section_type == section_type]

    def get_sections_by_priority(self, max_priority: int = 10) -> list[Section]:
        """Get sections ordered by extraction priority."""
        priority_sections = [s for s in self.sections if s.priority <= max_priority]
        return sorted(priority_sections, key=lambda s: s.priority)

    def get_problem_sections(self) -> list[Section]:
        """Get sections most likely to contain research problems."""
        # Prioritize limitations, future work, discussion, conclusion
        return self.get_sections_by_priority(max_priority=5)


class SectionSegmenter:
    """
    Segment academic papers into sections.

    Uses heuristic pattern matching to identify section headings and
    classify them into standard section types.
    """

    # Heading patterns with section type mappings
    SECTION_PATTERNS = {
        SectionType.ABSTRACT: [
            r"^abstract\s*$",
            r"^summary\s*$",
        ],
        SectionType.INTRODUCTION: [
            r"^(?:\d+\.?\s*)?introduction\s*$",
            r"^(?:\d+\.?\s*)?overview\s*$",
            r"^1\.?\s*introduction\s*$",
        ],
        SectionType.RELATED_WORK: [
            r"^(?:\d+\.?\s*)?related\s+work\s*$",
            r"^(?:\d+\.?\s*)?prior\s+work\s*$",
            r"^(?:\d+\.?\s*)?literature\s+review\s*$",
            r"^(?:\d+\.?\s*)?related\s+research\s*$",
        ],
        SectionType.BACKGROUND: [
            r"^(?:\d+\.?\s*)?background\s*$",
            r"^(?:\d+\.?\s*)?preliminaries\s*$",
            r"^(?:\d+\.?\s*)?problem\s+(?:statement|formulation|definition)\s*$",
        ],
        SectionType.METHODS: [
            r"^(?:\d+\.?\s*)?method(?:s|ology)?\s*$",
            r"^(?:\d+\.?\s*)?approach\s*$",
            r"^(?:\d+\.?\s*)?(?:our\s+)?(?:proposed\s+)?(?:method|approach|framework|model)\s*$",
            r"^(?:\d+\.?\s*)?technique(?:s)?\s*$",
            r"^(?:\d+\.?\s*)?algorithm\s*$",
        ],
        SectionType.EXPERIMENTS: [
            r"^(?:\d+\.?\s*)?experiment(?:s|al)?\s*(?:setup|settings)?\s*$",
            r"^(?:\d+\.?\s*)?evaluation\s*$",
            r"^(?:\d+\.?\s*)?empirical\s+(?:study|evaluation|analysis)\s*$",
            r"^(?:\d+\.?\s*)?(?:experimental\s+)?setup\s*$",
        ],
        SectionType.RESULTS: [
            r"^(?:\d+\.?\s*)?results?\s*$",
            r"^(?:\d+\.?\s*)?(?:experimental\s+)?results?\s+(?:and\s+)?(?:analysis|discussion)?\s*$",
            r"^(?:\d+\.?\s*)?findings\s*$",
            r"^(?:\d+\.?\s*)?results?\s+and\s+discussion\s*$",
        ],
        SectionType.DISCUSSION: [
            r"^(?:\d+\.?\s*)?discussion\s*$",
            r"^(?:\d+\.?\s*)?analysis\s*$",
            r"^(?:\d+\.?\s*)?interpretation\s*$",
        ],
        SectionType.LIMITATIONS: [
            r"^(?:\d+\.?\s*)?limitation(?:s)?\s*$",
            r"^(?:\d+\.?\s*)?limitation(?:s)?\s+(?:and\s+)?(?:future\s+work|directions)?\s*$",
            r"^(?:\d+\.?\s*)?(?:current\s+)?limitation(?:s)?\s*$",
            r"^(?:\d+\.?\s*)?threats?\s+to\s+validity\s*$",
            r"^(?:\d+\.?\s*)?(?:potential\s+)?(?:limitation(?:s)?|weakness(?:es)?)\s*$",
        ],
        SectionType.FUTURE_WORK: [
            r"^(?:\d+\.?\s*)?future\s+(?:work|directions?|research)\s*$",
            r"^(?:\d+\.?\s*)?(?:directions?\s+for\s+)?future\s+(?:work|research)\s*$",
            r"^(?:\d+\.?\s*)?open\s+(?:problems?|questions?|issues?)\s*$",
            r"^(?:\d+\.?\s*)?next\s+steps?\s*$",
        ],
        SectionType.CONCLUSION: [
            r"^(?:\d+\.?\s*)?conclusion(?:s)?\s*$",
            r"^(?:\d+\.?\s*)?concluding\s+remarks?\s*$",
            r"^(?:\d+\.?\s*)?conclusion(?:s)?\s+(?:and\s+)?(?:future\s+work)?\s*$",
            r"^(?:\d+\.?\s*)?summary\s+and\s+conclusion(?:s)?\s*$",
        ],
        SectionType.ACKNOWLEDGMENTS: [
            r"^acknowledgment(?:s)?\s*$",
            r"^acknowledgement(?:s)?\s*$",
        ],
        SectionType.REFERENCES: [
            r"^references?\s*$",
            r"^bibliography\s*$",
            r"^(?:cited\s+)?literature\s*$",
        ],
        SectionType.APPENDIX: [
            r"^appendix\s*[a-z]?\s*$",
            r"^appendices\s*$",
            r"^supplementary\s+(?:material|information)\s*$",
        ],
    }

    # Compile all patterns
    _compiled_patterns: dict[SectionType, list[re.Pattern]] = {}

    def __init__(
        self,
        min_section_words: int = 20,
        max_heading_length: int = 100,
        detect_subsections: bool = True,
    ):
        """
        Initialize the section segmenter.

        Args:
            min_section_words: Minimum words for a valid section.
            max_heading_length: Maximum characters for a heading line.
            detect_subsections: Whether to detect subsection headings.
        """
        self.min_section_words = min_section_words
        self.max_heading_length = max_heading_length
        self.detect_subsections = detect_subsections

        # Compile patterns once
        if not self._compiled_patterns:
            self._compile_patterns()

    @classmethod
    def _compile_patterns(cls):
        """Compile regex patterns for section detection."""
        for section_type, patterns in cls.SECTION_PATTERNS.items():
            cls._compiled_patterns[section_type] = [
                re.compile(pattern, re.IGNORECASE | re.MULTILINE)
                for pattern in patterns
            ]

    def segment(self, text: str) -> SegmentedDocument:
        """
        Segment text into sections.

        Args:
            text: Full text of the document.

        Returns:
            SegmentedDocument with identified sections.
        """
        if not text or not text.strip():
            return SegmentedDocument(full_text=text)

        # Find all potential heading positions
        headings = self._find_headings(text)

        if not headings:
            # No clear structure found, return as single unknown section
            return SegmentedDocument(
                sections=[
                    Section(
                        section_type=SectionType.UNKNOWN,
                        title="",
                        content=text,
                        start_char=0,
                        end_char=len(text),
                    )
                ],
                full_text=text,
                detected_structure=False,
            )

        # Extract sections between headings
        sections = self._extract_sections(text, headings)

        # Filter out very short sections
        sections = [s for s in sections if s.word_count >= self.min_section_words]

        return SegmentedDocument(
            sections=sections,
            full_text=text,
            detected_structure=True,
        )

    def _find_headings(self, text: str) -> list[tuple[int, int, str, SectionType]]:
        """
        Find all heading positions in the text.

        Returns list of (start_pos, end_pos, heading_text, section_type) tuples.
        """
        headings = []
        lines = text.split("\n")
        current_pos = 0

        for line in lines:
            stripped = line.strip()

            # Skip empty lines and very long lines
            if not stripped or len(stripped) > self.max_heading_length:
                current_pos += len(line) + 1
                continue

            # Check if line matches any section pattern
            section_type = self._classify_heading(stripped)

            if section_type != SectionType.UNKNOWN:
                end_pos = current_pos + len(line)
                headings.append((current_pos, end_pos, stripped, section_type))

            current_pos += len(line) + 1

        return headings

    def _classify_heading(self, heading_text: str) -> SectionType:
        """
        Classify a heading into a section type.

        Args:
            heading_text: The heading text to classify.

        Returns:
            SectionType for the heading, or UNKNOWN if no match.
        """
        cleaned = heading_text.strip()

        for section_type, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.match(cleaned):
                    return section_type

        return SectionType.UNKNOWN

    def _extract_sections(
        self,
        text: str,
        headings: list[tuple[int, int, str, SectionType]],
    ) -> list[Section]:
        """
        Extract section content between headings.

        Args:
            text: Full document text.
            headings: List of heading positions and types.

        Returns:
            List of Section objects.
        """
        sections = []

        for i, (start_pos, heading_end, heading_text, section_type) in enumerate(headings):
            # Content starts after the heading
            content_start = heading_end

            # Content ends at the next heading or end of document
            if i + 1 < len(headings):
                content_end = headings[i + 1][0]
            else:
                content_end = len(text)

            # Extract content
            content = text[content_start:content_end].strip()

            sections.append(
                Section(
                    section_type=section_type,
                    title=heading_text,
                    content=content,
                    start_char=start_pos,
                    end_char=content_end,
                )
            )

        return sections

    def segment_with_abstract(self, text: str) -> SegmentedDocument:
        """
        Segment text, with special handling for abstract extraction.

        The abstract may appear before any heading, so we try to extract
        it separately first.

        Args:
            text: Full text of the document.

        Returns:
            SegmentedDocument with identified sections.
        """
        sections = []
        remaining_text = text

        # Try to find abstract at the beginning
        abstract_match = re.search(
            r"^(?:abstract\s*[:\-]?\s*)(.*?)(?=\n\s*(?:\d+\.?\s*)?(?:introduction|1\.|I\.))",
            text,
            re.IGNORECASE | re.DOTALL,
        )

        if abstract_match:
            abstract_content = abstract_match.group(1).strip()
            if len(abstract_content.split()) >= self.min_section_words:
                sections.append(
                    Section(
                        section_type=SectionType.ABSTRACT,
                        title="Abstract",
                        content=abstract_content,
                        start_char=abstract_match.start(),
                        end_char=abstract_match.end(),
                    )
                )
                remaining_text = text[abstract_match.end():]

        # Segment the rest normally
        remaining_doc = self.segment(remaining_text)

        # Adjust character offsets for remaining sections
        offset = len(text) - len(remaining_text)
        for section in remaining_doc.sections:
            section.start_char += offset
            section.end_char += offset

        sections.extend(remaining_doc.sections)

        return SegmentedDocument(
            sections=sections,
            full_text=text,
            detected_structure=len(sections) > 1,
        )


# Singleton instance
_segmenter: Optional[SectionSegmenter] = None


def get_section_segmenter(
    min_section_words: int = 20,
    max_heading_length: int = 100,
) -> SectionSegmenter:
    """
    Get or create the singleton section segmenter.

    Args:
        min_section_words: Minimum words for a valid section.
        max_heading_length: Maximum characters for a heading.

    Returns:
        SectionSegmenter instance.
    """
    global _segmenter

    if _segmenter is None:
        _segmenter = SectionSegmenter(
            min_section_words=min_section_words,
            max_heading_length=max_heading_length,
        )

    return _segmenter


def reset_section_segmenter() -> None:
    """Reset the singleton section segmenter (for testing)."""
    global _segmenter
    _segmenter = None
