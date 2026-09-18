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


# SEG-1: Roman numerals I..XXXVIII. The lookahead guarantees a non-empty
# match, so "C. Data Analysis" cannot match an empty numeral and then eat the
# ".". L/C/D/M are deliberately excluded — a paper with 40+ top-level sections
# is out of scope, and admitting them would widen the false-positive surface.
_ROMAN = r"(?=[IVX])X{0,3}(?:IX|IV|V?I{0,3})"

# A separator is REQUIRED after the numeral. That is what keeps single letters
# out ("B. RESULTS" offers no numeral; "Vision" offers no separator), and it is
# flat by design — no (?:\.\d+)* — so a numbered SUBSECTION like "4.4. Results"
# stays UNKNOWN and its content remains inside its parent span. Admitting
# either form was measured to lose more content than it recovers; see
# llm/features/seg1-roman-numeral-headings.md, Decisions 1 and 3.
_ENUMERATOR = re.compile(rf"^(?:\d+|{_ROMAN})(?:[.)]\s*|\s+)", re.IGNORECASE)

# SEG-3: a letter-spaced line is one whose every token is a single letter. The
# PDF extractor renders Elsevier's small-caps headings that way, so
# "A B S T R A C T" arrives as a STANDALONE line that needs de-spacing, not
# run-in handling. Collapsing whitespace is safe ONLY here -- doing it
# universally would turn "Related Work" into "RelatedWork" and break that
# pattern. Three letters minimum, so "A B" and a bare "A" are untouched.
_LETTER_SPACED = re.compile(r"^(?:[A-Za-z]\s+){2,}[A-Za-z]\s*$")

# SEG-3: run-in labels. Each pattern consumes the label AND its delimiter, so
# ``match.end()`` is where the section's content begins on that same line.
#
# Whitespace is a delimiter only after an ALL-CAPS label. Both variants score
# zero false positives on these eight papers, so the corpus cannot separate
# them -- but this project's domain is computer science, where "abstract syntax
# tree" and "abstract representation" are ordinary prose. A false abstract
# heading mid-paper is not a harmless mislabel: it opens a new span and
# therefore TRUNCATES whatever section contained that line. The constraint has
# a measured recall cost of zero, against a destructive failure mode.
#
# The table stays minimal -- abstract only. It is a mechanism for a structural
# property (a heading sharing its line with body text), not a shadow
# vocabulary; ``SECTION_PATTERNS`` has no slot for the offset, which is the
# entire reason this exists separately.
_RUN_IN_HEADINGS: tuple[tuple[re.Pattern, "SectionType"], ...] = ()


def _match_run_in(stripped: str) -> Optional[tuple["SectionType", int]]:
    """Return ``(type, offset-after-label)`` for a run-in heading, else None.

    A bare "Abstract" line matches neither pattern -- the first needs trailing
    text, the second a delimiter -- and falls through to the standalone
    pattern, so this does not double-handle the case that already worked.
    """
    for pattern, section_type in _RUN_IN_HEADINGS:
        match = pattern.match(stripped)
        if match:
            return section_type, match.end()
    return None



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


# SEG-3: populated here rather than above because it binds ``SectionType``.
# ``ABSTRACT\s+(?=\S)`` requires trailing text; the second pattern requires a
# delimiter. Neither matches a bare "Abstract" line.
_RUN_IN_HEADINGS = (
    (re.compile(r"^ABSTRACT\s+(?=\S)"), SectionType.ABSTRACT),
    (
        re.compile(r"^abstract\s*[\u2014\u2013\-.:]\s*", re.IGNORECASE),
        SectionType.ABSTRACT,
    ),
)


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

    # Heading patterns with section type mappings.
    #
    # SEG-1: patterns carry NO section-number prefix group. Numbering (Arabic
    # or Roman) is stripped once in ``_classify_heading`` via ``_ENUMERATOR``,
    # so new vocabulary added here supports "IV." and "4." for free — and
    # cannot forget to.
    SECTION_PATTERNS = {
        SectionType.ABSTRACT: [
            r"^abstract\s*$",
            r"^summary\s*$",
        ],
        SectionType.INTRODUCTION: [
            r"^introduction\s*$",
            r"^overview\s*$",
        ],
        SectionType.RELATED_WORK: [
            r"^related\s+work\s*$",
            r"^prior\s+work\s*$",
            r"^literature\s+review\s*$",
            r"^related\s+research\s*$",
        ],
        SectionType.BACKGROUND: [
            r"^background\s*$",
            r"^preliminaries\s*$",
            r"^problem\s+(?:statement|formulation|definition)\s*$",
        ],
        SectionType.METHODS: [
            r"^method(?:s|ology)?\s*$",
            r"^approach\s*$",
            r"^(?:our\s+)?(?:proposed\s+)?(?:method|approach|framework|model)\s*$",
            r"^technique(?:s)?\s*$",
            r"^algorithm\s*$",
        ],
        SectionType.EXPERIMENTS: [
            r"^experiment(?:s|al)?\s*(?:setup|settings)?\s*$",
            r"^evaluation\s*$",
            r"^empirical\s+(?:study|evaluation|analysis)\s*$",
            r"^(?:experimental\s+)?setup\s*$",
        ],
        SectionType.RESULTS: [
            r"^results?\s*$",
            r"^(?:experimental\s+)?results?\s+(?:and\s+)?(?:analysis|discussion)?\s*$",
            r"^findings\s*$",
            r"^results?\s+and\s+discussion\s*$",
        ],
        SectionType.DISCUSSION: [
            r"^discussion\s*$",
            r"^analysis\s*$",
            r"^interpretation\s*$",
        ],
        SectionType.LIMITATIONS: [
            r"^limitation(?:s)?\s*$",
            r"^limitation(?:s)?\s+(?:and\s+)?(?:future\s+work|directions)?\s*$",
            r"^(?:current\s+)?limitation(?:s)?\s*$",
            r"^threats?\s+to\s+validity\s*$",
            r"^(?:potential\s+)?(?:limitation(?:s)?|weakness(?:es)?)\s*$",
        ],
        SectionType.FUTURE_WORK: [
            r"^future\s+(?:work|directions?|research)\s*$",
            r"^(?:directions?\s+for\s+)?future\s+(?:work|research)\s*$",
            r"^open\s+(?:problems?|questions?|issues?)\s*$",
            r"^next\s+steps?\s*$",
        ],
        SectionType.CONCLUSION: [
            r"^conclusion(?:s)?\s*$",
            r"^concluding\s+remarks?\s*$",
            r"^conclusion(?:s)?\s+(?:and\s+)?(?:future\s+work)?\s*$",
            r"^summary\s+and\s+conclusion(?:s)?\s*$",
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

            if not stripped:
                current_pos += len(line) + 1
                continue

            run_in = _match_run_in(stripped)

            # SEG-3 Decision 5: the length guard applies to the LABEL, not to
            # the body text trailing it. fact_completion's run-in line is 96 of
            # the allowed 100 -- a four-character cliff, and line wrapping is a
            # property of the PDF and the extractor version, so a re-extraction
            # could silently un-fix that paper with no error.
            if run_in is None and len(stripped) > self.max_heading_length:
                current_pos += len(line) + 1
                continue

            if run_in is not None:
                # SEG-3 Decision 3: content starts after the LABEL, so the
                # abstract keeps its opening words and reads as prose. Without
                # this the kept abstract would begin mid-sentence -- empire's
                # would open "Empirical research in requirements".
                section_type, label_end = run_in
                indent = len(line) - len(line.lstrip())
                headings.append((
                    current_pos,
                    current_pos + indent + label_end,
                    stripped[:label_end].strip(),
                    section_type,
                ))
                current_pos += len(line) + 1
                continue

            # Check if line matches any section pattern
            section_type = self._classify_heading(stripped)

            if section_type != SectionType.UNKNOWN:
                end_pos = current_pos + len(line)
                headings.append((current_pos, end_pos, stripped, section_type))
            else:
                # SEG-1: what the segmenter *rejects* is the only record of a
                # SEG-3/4/5-class miss (a heading named after the paper's
                # contribution, a journal-specific name, a run-in abstract).
                # Without this, diagnosing bad segmentation means writing a
                # script. DEBUG-only: unmatched lines are normal and frequent.
                logger.debug("unmatched candidate heading: %r", stripped)

            current_pos += len(line) + 1

        return headings

    def _classify_heading(self, heading_text: str) -> SectionType:
        """
        Classify a heading into a section type.

        Args:
            heading_text: The heading text to classify.

        Returns:
            SectionType for the heading, or UNKNOWN if no match.

        SEG-1: any leading section number is stripped before matching, so
        "IV. EVALUATION" and "4. Evaluation" both reach the ``evaluation``
        pattern. Only ONE enumerator is ever removed — ``_ENUMERATOR`` is
        ``^``-anchored without ``re.MULTILINE``, so no second match is
        reachable, and ``count=1`` states that intent explicitly. Hence
        "II.B. Results" becomes "B. Results" and stays UNKNOWN: a
        Roman-then-letter subsection is still a subsection.

        The caller keeps the raw text as ``Section.title``; only the
        classification input is normalized.
        """
        cleaned = _ENUMERATOR.sub("", heading_text.strip(), count=1)

        # SEG-3: the PDF extractor renders Elsevier's small-caps headings
        # letter-spaced ("A B S T R A C T"). De-space only lines that are
        # ENTIRELY letter-spaced; see ``_LETTER_SPACED``. "A R T I C L E I N F O"
        # collapses to a token matching no pattern, which is the right outcome.
        if _LETTER_SPACED.match(cleaned):
            cleaned = re.sub(r"\s+", "", cleaned)

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
