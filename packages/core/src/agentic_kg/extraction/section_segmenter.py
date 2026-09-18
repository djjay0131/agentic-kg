"""
Section Segmentation Module.

Identifies and extracts distinct sections from academic papers using
heuristic pattern matching with optional LLM fallback for ambiguous cases.
"""

import logging
import re
import statistics
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


# SEG-4 D4: a positional abstract must read as prose, not as a title page.
#
# Prose width is measured RELATIVE to the document, not in absolute characters.
# Line width is a property of column layout: the median body-line width is 106
# on cskg2 (single column) but 59 on fact_completion, 60 on empire and 69 on
# hypothesis_generation (all two-column). An absolute threshold of 70 would
# work on cskg2 and silently find nothing on an entire class of paper.
_PROSE_WIDTH_FACTOR = 0.75

# Guard 2 thresholds. Measured: cskg2's real lead paragraph scores 7 sentences
# and 0.8% digits; its author/journal block scores 0 sentences and 13.7%
# digits. The thresholds sit between the two with room on both sides.
_MIN_SENTENCES = 2
_MAX_DIGIT_DENSITY = 0.05

# Guard 1. Without it the rule builds 2,058-2,649-char "abstracts" out of title
# pages on SIX of the eight papers -- and because those fall inside SEG-3's
# 500-4,000 plausibility band, AC-16 would not catch them. Measured: the
# genuine cskg2 lead paragraph hits NONE of these terms; every rejected title
# page hits three or four. The guard makes the rule order-independent and safe
# for future label-less papers, not just for this corpus.
_TITLE_PAGE_FURNITURE = re.compile(
    r"@|https?://|doi\.org|Contents lists available|ScienceDirect"
    r"|\baccepted\b|\breceived\b|www\.",
    re.IGNORECASE,
)

# SEG-4 D5: a section holding this much of the body is probable
# under-segmentation -- an unrecognized heading let its predecessor swallow the
# rest of the paper. Measured over the eight ground-truth papers: swallowed
# spans run 41.3%-100% of body words, the largest HEALTHY section is ~31.4%.
# The threshold sits in that gap, with roughly nine points of margin either
# side. Move it and the margin has to be argued again.
_UNDER_SEGMENTATION_SHARE = 0.40

# Below this the share is meaningless -- and every hand-written test fixture in
# the repo would warn.
_MIN_BODY_WORDS_FOR_CHECK = 500

# DEVIATION FROM THE SPEC, and the reason for it. With N body sections the
# smallest possible maximum share is 1/N, so at N <= 2 the smallest max share
# is 50% -- already above the 40% threshold. The share check would therefore
# fire on EVERY two-section document regardless of how well it was segmented,
# which is a warning carrying no information. Measured consequence on the
# committed gold corpus, where the keep-list leaves only 2-4 sections per
# paper: `kg_construction_survey` has exactly two body sections (gold says
# abstract + introduction, the honest answer for a 94-page survey) and warned
# at 83.5% purely because of this arithmetic.
#
# Below this floor the detector does NOT go silent -- it reports the section
# COUNT instead of the share. Skipping outright would mute the worst case:
# cskg2 at base had exactly one body section holding 100% of the paper, which
# is precisely cause (4).
_MIN_BODY_SECTIONS_FOR_CHECK = 3

# Excluded from the denominator AND never reported: `references` alone reaches
# 31.8% of TOTAL words on fact_completion and is legitimately large.
_TAIL_TYPES = frozenset({
    SectionType.REFERENCES,
    SectionType.ACKNOWLEDGMENTS,
    SectionType.APPENDIX,
})


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
            # SEG-4 D1/D2: Nature Scientific Data. This IS that journal's
            # introduction; mapping it to BACKGROUND reads the name literally,
            # costs -15,932 chars because background is not in the keep-list,
            # and hides the citation chain's spine concept.
            r"^background\s*(?:&|and)\s*summary\s*$",
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
            # SEG-4 D1/D2: Nature Scientific Data's evaluation section.
            r"^technical\s+validation\s*$",
            r"^experiment(?:s|al)?\s*(?:setup|settings)?\s*$",
            r"^evaluation\s*$",
            r"^empirical\s+(?:study|evaluation|analysis)\s*$",
            r"^(?:experimental\s+)?setup\s*$",
        ],
        SectionType.RESULTS: [
            # SEG-4 D1/D2: Nature Scientific Data. Describes the released
            # artifact, not the method. Mapping it to METHODS measures +6,138
            # chars, but that is a false label chosen to game the keep-list:
            # if the keep-list is too narrow -- and SEG-7's data says it is --
            # that is SEG-7's decision to make openly.
            r"^data\s+records\s*$",
            r"^results?\s*$",
            r"^(?:experimental\s+)?results?\s+(?:and\s+)?(?:analysis|discussion)?\s*$",
            r"^findings\s*$",
            r"^results?\s+and\s+discussion\s*$",
        ],
        SectionType.DISCUSSION: [
            # SEG-4 D1/D2: Nature Scientific Data. Guidance and caveats.
            r"^usage\s+notes\s*$",
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

        doc = SegmentedDocument(
            sections=sections,
            full_text=text,
            detected_structure=True,
        )
        # SEG-4 D5. Placed in the segmenter, not in ingestion.py: this is a
        # judgement about segmentation QUALITY and it needs the section list,
        # so it belongs where that list is built -- and it then fires for every
        # caller (CLI, API, Cloud Run Job, the measurement harness), not only
        # ingest_papers. SEG-1's AC-13 warning stays where it is; that one is
        # about extractor INPUT, which is an ingestion concern.
        # SEG-4 D4. Runs AFTER normal segmentation, so it can only fire when
        # SEG-3's label patterns found nothing -- the two never both produce
        # an abstract. Before this, cause (3) was only partially fixed.
        self._add_positional_abstract(text, doc)
        self._warn_under_segmentation(doc)
        return doc

    def _add_positional_abstract(
        self,
        text: str,
        doc: SegmentedDocument,
    ) -> None:
        """No abstract label anywhere -> the abstract is the maximal run of
        prose-width lines immediately preceding the first recognized heading.

        Nature *Scientific Data* prints no label; the abstract is simply the
        lead paragraph. Title, author and journal-header lines are short and
        irregular, so they break the run -- that is the whole mechanism, and it
        needs no length cap and no tuning constant *provided the terminator
        exists*. It did not until SEG-4 PR-1 added the journal vocabulary:
        measured, an uncapped positional rule ran 109 lines to ``Methods`` and
        produced a 12,157-character "abstract" that swallowed the paper's real
        introduction. That is the hard ordering behind this method's placement.
        """
        if any(s.section_type is SectionType.ABSTRACT for s in doc.sections):
            return
        first = min((s.start_char for s in doc.sections), default=None)
        if first is None:
            return

        widths = [
            len(line.strip())
            for line in text.split("\n")
            if len(line.strip()) > 20
        ]
        if not widths:
            return
        min_width = _PROSE_WIDTH_FACTOR * statistics.median(widths)

        lines = text[:first].split("\n")
        # text[:first] ends at a newline, so the split leaves a blank tail that
        # would break the run on its very first step. Measured: dropping this
        # is the difference between finding cskg2's abstract and finding
        # nothing at all.
        while lines and not lines[-1].strip():
            lines.pop()

        index = len(lines)
        while index > 0 and len(lines[index - 1].strip()) >= min_width:
            index -= 1
        span = "\n".join(lines[index:]).strip()

        if len(span.split()) < self.min_section_words:
            return
        # Guard 1 -- publisher furniture. Catches a span running from the
        # document start through the title block.
        if _TITLE_PAGE_FURNITURE.search(span):
            logger.debug("positional abstract rejected (title-page furniture)")
            return
        # Guard 2 -- does it read as prose? Not redundant with Guard 1, and not
        # for the reason first assumed: a title-page span contains the paper's
        # actual abstract prose, so it READS as prose and only the denylist can
        # reject it. Conversely a pure metadata block fails this test
        # decisively, which is what covers a long author line absorbed into the
        # run -- something the denylist would miss.
        sentences = len(re.findall(r"\.(?:\s|$)", span))
        digit_density = sum(c.isdigit() for c in span) / len(span)
        if sentences < _MIN_SENTENCES or digit_density >= _MAX_DIGIT_DENSITY:
            logger.debug(
                "positional abstract rejected (not prose: %d sentences, "
                "%.1f%% digits)",
                sentences,
                100 * digit_density,
            )
            return

        doc.sections.insert(
            0,
            Section(
                section_type=SectionType.ABSTRACT,
                title="(positional)",
                content=span,
                start_char=len("\n".join(lines[:index])),
                end_char=first,
            ),
        )

    def _warn_under_segmentation(self, doc: SegmentedDocument) -> None:
        """Warn when one section holds most of the body.

        An unrecognized heading is not a *missed* section -- it is absorbed
        into its predecessor, silently, producing one enormous mislabelled span
        rather than an obviously broken result. This warns; it deliberately
        does NOT guess where the missing boundary was. Guessing is SEG-5's
        positional-fallback option and needs its own measurement.

        Observability only: ``doc`` is not modified.
        """
        body = [s for s in doc.sections if s.section_type not in _TAIL_TYPES]
        total = sum(s.word_count for s in body)
        if total < _MIN_BODY_WORDS_FOR_CHECK:
            return
        # See _MIN_BODY_SECTIONS_FOR_CHECK: at N <= 2 the threshold is below
        # 1/N and the SHARE check cannot fail to fire, so it says nothing.
        #
        # Silence would be the wrong answer, though, because N <= 2 on a large
        # document is itself the worst case this detector exists for: at base,
        # cskg2 had exactly ONE body section holding 100% of the paper. So the
        # small-N case gets its own signal rather than being skipped -- a
        # different fact, reported as a different fact. Only the count is
        # stated; no share, because the share is arithmetically forced.
        if len(body) < _MIN_BODY_SECTIONS_FOR_CHECK:
            logger.warning(
                "under-segmentation: only %d recognized body section(s) in a "
                "%d-word document (%s). A share is not reported because at "
                "N<=%d it is arithmetically forced; the missing headings lie "
                "inside these spans.",
                len(body),
                total,
                ", ".join(repr(s.title) for s in body) or "(none)",
                _MIN_BODY_SECTIONS_FOR_CHECK - 1,
            )
            return
        for index, section in enumerate(body):
            share = section.word_count / total
            if share < _UNDER_SEGMENTATION_SHARE:
                continue
            # The actionable half: the missing heading lies between this span
            # and the next one that WAS recognized. No root cause is named --
            # attribution would be a guess, and the two facts together let a
            # human read the line in seconds.
            following = (
                body[index + 1].title
                if index + 1 < len(body)
                else "(end of document)"
            )
            logger.warning(
                "under-segmentation: %r (%s) holds %.1f%% of body words "
                "(%d of %d); next recognized heading is %r",
                section.title,
                section.section_type.value,
                100 * share,
                section.word_count,
                total,
                following,
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
