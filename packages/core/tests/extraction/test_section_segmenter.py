"""
Unit tests for section segmentation.
"""

import logging
import re
from pathlib import Path

import pytest
from agentic_kg.extraction.section_segmenter import (
    SECTION_PRIORITY,
    Section,
    SectionSegmenter,
    SectionType,
    SegmentedDocument,
    get_section_segmenter,
    reset_section_segmenter,
)


class TestSectionType:
    """Tests for SectionType enum."""

    def test_section_type_values(self):
        """Test that all expected section types exist."""
        assert SectionType.ABSTRACT == "abstract"
        assert SectionType.INTRODUCTION == "introduction"
        assert SectionType.LIMITATIONS == "limitations"
        assert SectionType.FUTURE_WORK == "future_work"
        assert SectionType.CONCLUSION == "conclusion"
        assert SectionType.REFERENCES == "references"

    def test_section_priority_order(self):
        """Test that priority order is correct."""
        # Limitations should have highest priority (lowest number)
        assert SECTION_PRIORITY[SectionType.LIMITATIONS] < SECTION_PRIORITY[SectionType.INTRODUCTION]
        assert SECTION_PRIORITY[SectionType.FUTURE_WORK] < SECTION_PRIORITY[SectionType.METHODS]
        assert SECTION_PRIORITY[SectionType.DISCUSSION] < SECTION_PRIORITY[SectionType.EXPERIMENTS]

        # References should have lowest priority
        assert SECTION_PRIORITY[SectionType.REFERENCES] > SECTION_PRIORITY[SectionType.CONCLUSION]


class TestSection:
    """Tests for Section dataclass."""

    def test_create_section(self):
        """Test creating a section."""
        section = Section(
            section_type=SectionType.INTRODUCTION,
            title="1. Introduction",
            content="This is the introduction content.",
        )

        assert section.section_type == SectionType.INTRODUCTION
        assert section.title == "1. Introduction"
        assert "introduction content" in section.content

    def test_word_count(self):
        """Test word count calculation."""
        section = Section(
            section_type=SectionType.ABSTRACT,
            title="Abstract",
            content="One two three four five",
        )

        assert section.word_count == 5

    def test_priority(self):
        """Test section priority."""
        limitations = Section(
            section_type=SectionType.LIMITATIONS,
            title="Limitations",
            content="Content",
        )
        introduction = Section(
            section_type=SectionType.INTRODUCTION,
            title="Introduction",
            content="Content",
        )

        assert limitations.priority < introduction.priority

    def test_subsections(self):
        """Test subsections list."""
        main = Section(
            section_type=SectionType.METHODS,
            title="Methods",
            content="Main content",
            subsections=[
                Section(
                    section_type=SectionType.UNKNOWN,
                    title="3.1 Data",
                    content="Data description",
                )
            ],
        )

        assert len(main.subsections) == 1
        assert main.subsections[0].title == "3.1 Data"


class TestSegmentedDocument:
    """Tests for SegmentedDocument dataclass."""

    @pytest.fixture
    def sample_document(self):
        """Create a sample segmented document."""
        return SegmentedDocument(
            sections=[
                Section(SectionType.ABSTRACT, "Abstract", "Abstract content" * 10),
                Section(SectionType.INTRODUCTION, "Introduction", "Intro content" * 10),
                Section(SectionType.METHODS, "Methods", "Methods content" * 10),
                Section(SectionType.LIMITATIONS, "Limitations", "Limitations content" * 10),
                Section(SectionType.CONCLUSION, "Conclusion", "Conclusion content" * 10),
                Section(SectionType.REFERENCES, "References", "References content" * 10),
            ],
            full_text="Full document text",
            detected_structure=True,
        )

    def test_get_sections_by_type(self, sample_document):
        """Test getting sections by type."""
        abstracts = sample_document.get_sections_by_type(SectionType.ABSTRACT)

        assert len(abstracts) == 1
        assert abstracts[0].title == "Abstract"

    def test_get_sections_by_type_empty(self, sample_document):
        """Test getting sections for type that doesn't exist."""
        future_work = sample_document.get_sections_by_type(SectionType.FUTURE_WORK)

        assert len(future_work) == 0

    def test_get_sections_by_priority(self, sample_document):
        """Test getting sections by priority."""
        priority_sections = sample_document.get_sections_by_priority(max_priority=5)

        # Should get limitations and conclusion (priority <= 5)
        types = [s.section_type for s in priority_sections]
        assert SectionType.LIMITATIONS in types
        assert SectionType.CONCLUSION in types
        assert SectionType.REFERENCES not in types

    def test_get_problem_sections(self, sample_document):
        """Test getting sections likely to contain problems."""
        problem_sections = sample_document.get_problem_sections()

        types = [s.section_type for s in problem_sections]
        assert SectionType.LIMITATIONS in types
        assert SectionType.CONCLUSION in types

    def test_empty_document(self):
        """Test empty document."""
        doc = SegmentedDocument()

        assert doc.sections == []
        assert doc.full_text == ""
        assert doc.detected_structure is False


class TestSectionSegmenter:
    """Tests for SectionSegmenter class."""

    @pytest.fixture
    def segmenter(self):
        """Create a section segmenter."""
        return SectionSegmenter()

    def test_initialization(self, segmenter):
        """Test segmenter initialization."""
        assert segmenter.min_section_words == 20
        assert segmenter.max_heading_length == 100
        assert segmenter.detect_subsections is True

    def test_classify_heading_introduction(self, segmenter):
        """Test classifying introduction headings."""
        assert segmenter._classify_heading("Introduction") == SectionType.INTRODUCTION
        assert segmenter._classify_heading("1. Introduction") == SectionType.INTRODUCTION
        assert segmenter._classify_heading("1 Introduction") == SectionType.INTRODUCTION
        assert segmenter._classify_heading("INTRODUCTION") == SectionType.INTRODUCTION

    def test_classify_heading_methods(self, segmenter):
        """Test classifying methods headings."""
        assert segmenter._classify_heading("Methods") == SectionType.METHODS
        assert segmenter._classify_heading("Methodology") == SectionType.METHODS
        assert segmenter._classify_heading("3. Approach") == SectionType.METHODS
        assert segmenter._classify_heading("Our Method") == SectionType.METHODS
        assert segmenter._classify_heading("Proposed Framework") == SectionType.METHODS

    def test_classify_heading_limitations(self, segmenter):
        """Test classifying limitations headings."""
        assert segmenter._classify_heading("Limitations") == SectionType.LIMITATIONS
        assert segmenter._classify_heading("5. Limitations") == SectionType.LIMITATIONS
        assert segmenter._classify_heading("Limitations and Future Work") == SectionType.LIMITATIONS
        assert segmenter._classify_heading("Threats to Validity") == SectionType.LIMITATIONS

    def test_classify_heading_future_work(self, segmenter):
        """Test classifying future work headings."""
        assert segmenter._classify_heading("Future Work") == SectionType.FUTURE_WORK
        assert segmenter._classify_heading("6. Future Directions") == SectionType.FUTURE_WORK
        assert segmenter._classify_heading("Open Problems") == SectionType.FUTURE_WORK

    def test_classify_heading_conclusion(self, segmenter):
        """Test classifying conclusion headings."""
        assert segmenter._classify_heading("Conclusion") == SectionType.CONCLUSION
        assert segmenter._classify_heading("Conclusions") == SectionType.CONCLUSION
        assert segmenter._classify_heading("7. Concluding Remarks") == SectionType.CONCLUSION

    def test_classify_heading_references(self, segmenter):
        """Test classifying references headings."""
        assert segmenter._classify_heading("References") == SectionType.REFERENCES
        assert segmenter._classify_heading("Bibliography") == SectionType.REFERENCES

    def test_classify_heading_unknown(self, segmenter):
        """Test that unknown headings return UNKNOWN."""
        assert segmenter._classify_heading("Some Random Text") == SectionType.UNKNOWN
        assert segmenter._classify_heading("Deep Learning Model") == SectionType.UNKNOWN

    def test_segment_empty_text(self, segmenter):
        """Test segmenting empty text."""
        result = segmenter.segment("")

        assert result.sections == []
        assert result.detected_structure is False

    def test_segment_no_structure(self, segmenter):
        """Test segmenting text with no clear structure."""
        text = """
        This is just some text without any section headings.
        It continues for a while with various content but never
        introduces any standard academic section headings.
        More content follows here in the same unstructured manner.
        """ * 5

        result = segmenter.segment(text)

        assert len(result.sections) == 1
        assert result.sections[0].section_type == SectionType.UNKNOWN
        assert result.detected_structure is False

    def test_segment_basic_paper(self, segmenter):
        """Test segmenting a basic paper structure."""
        text = """
Abstract

This paper presents our research on a topic. We investigate several
aspects and provide comprehensive analysis. Our results show significant
improvements over baseline methods.

Introduction

The field of machine learning has seen tremendous growth in recent years.
This paper addresses a specific problem that has been largely overlooked.
We propose a novel approach to solve this challenge effectively.

Methods

Our approach consists of three main components. First, we preprocess the
data using standard techniques. Second, we apply our novel algorithm.
Third, we post-process the results for final evaluation.

Conclusion

In this paper, we have presented a new approach to solving an important
problem. Our experimental results demonstrate the effectiveness of our
method. Future work will focus on extending these findings.

References

[1] Smith et al. Previous work on this topic.
[2] Jones et al. Another relevant paper.
"""

        result = segmenter.segment(text)

        assert result.detected_structure is True
        assert len(result.sections) >= 4

        types = [s.section_type for s in result.sections]
        assert SectionType.ABSTRACT in types
        assert SectionType.INTRODUCTION in types
        assert SectionType.METHODS in types
        assert SectionType.CONCLUSION in types

    def test_segment_with_limitations(self, segmenter):
        """Test that limitations section is properly detected."""
        text = """
Introduction

This paper presents our work on an important problem in the field.
We address challenges that have not been adequately solved before.
Our approach provides significant improvements in key metrics.

Methods

We use a novel deep learning architecture with attention mechanisms.
The model is trained on large-scale datasets for comprehensive coverage.
Training proceeds for multiple epochs with careful hyperparameter tuning.

Limitations

Our approach has several limitations that should be noted. First, the
computational cost is significant, requiring substantial GPU resources.
Second, the model may not generalize well to domains outside training.
Third, we have not evaluated on all possible benchmark datasets.

Conclusion

We have presented a new approach with promising results. Despite the
limitations mentioned above, our work provides a solid foundation.
Future work will address these limitations and extend applicability.
"""

        result = segmenter.segment(text)

        types = [s.section_type for s in result.sections]
        assert SectionType.LIMITATIONS in types

        limitations_section = result.get_sections_by_type(SectionType.LIMITATIONS)[0]
        assert "computational cost" in limitations_section.content

    def test_segment_numbered_headings(self, segmenter):
        """Test handling of numbered section headings."""
        # Use lower min_section_words for this test since sections have limited content
        segmenter_small = SectionSegmenter(min_section_words=10)
        text = """
1. Introduction

This is the introduction with numbered heading format commonly used
in conference papers. The content discusses the problem statement.

2. Related Work

Prior work has explored various aspects of this problem space.
We review key contributions and identify remaining gaps.

3. Methodology

Our proposed approach builds on existing techniques while introducing
novel components for improved performance on the target task.

4. Experiments

We evaluate our method on standard benchmark datasets and compare
against strong baseline methods from recent literature.

5. Conclusion

The experimental results demonstrate the effectiveness of our approach.
We conclude with a summary of contributions and future directions.
"""

        result = segmenter_small.segment(text)

        types = [s.section_type for s in result.sections]
        assert SectionType.INTRODUCTION in types
        assert SectionType.RELATED_WORK in types
        assert SectionType.METHODS in types
        assert SectionType.EXPERIMENTS in types
        assert SectionType.CONCLUSION in types

    def test_segment_preserves_content(self, segmenter):
        """Test that section content is preserved correctly."""
        # Use lower min_section_words for this test since sections have limited content
        segmenter_small = SectionSegmenter(min_section_words=10)
        text = """
Introduction

This specific sentence should appear in the introduction section.
Another sentence with unique content for verification purposes.

Conclusion

This conclusion sentence should be in the conclusion section only.
Final remarks are added here for completeness of the document.
"""

        result = segmenter_small.segment(text)

        intro = result.get_sections_by_type(SectionType.INTRODUCTION)
        assert len(intro) == 1
        assert "specific sentence" in intro[0].content

        conclusion = result.get_sections_by_type(SectionType.CONCLUSION)
        assert len(conclusion) == 1
        assert "conclusion sentence" in conclusion[0].content


class TestSectionSegmenterEdgeCases:
    """Edge case tests for section segmentation."""

    @pytest.fixture
    def segmenter(self):
        """Create a section segmenter."""
        return SectionSegmenter(min_section_words=10)

    def test_very_short_sections_filtered(self, segmenter):
        """Test that very short sections are filtered out."""
        text = """
Introduction

This introduction has enough words to be considered valid content
for our section segmentation purposes and testing needs.

Short Section

Too short.

Conclusion

The conclusion also has sufficient words to pass the minimum word
count threshold that we have configured for testing.
"""

        result = segmenter.segment(text)

        # The "Short Section" should be filtered out
        types = [s.section_type for s in result.sections]
        assert SectionType.INTRODUCTION in types
        assert SectionType.CONCLUSION in types
        # Short section was classified as UNKNOWN and filtered
        assert sum(1 for t in types if t == SectionType.UNKNOWN) == 0

    def test_case_insensitive_headings(self, segmenter):
        """Test that heading detection is case insensitive."""
        text = """
INTRODUCTION

Upper case heading content with enough words for the minimum
threshold to be satisfied in our testing scenario today.

introduction

Lower case heading content with enough words for the minimum
threshold to be satisfied in our testing scenario today.

Introduction

Mixed case heading content with enough words for the minimum
threshold to be satisfied in our testing scenario today.
"""

        result = segmenter.segment(text)

        intro_sections = result.get_sections_by_type(SectionType.INTRODUCTION)
        assert len(intro_sections) == 3

    def test_heading_variations(self, segmenter):
        """Test various heading format variations."""
        # Experiments and Evaluation should both map to EXPERIMENTS
        assert segmenter._classify_heading("Experiments") == SectionType.EXPERIMENTS
        assert segmenter._classify_heading("Evaluation") == SectionType.EXPERIMENTS
        assert segmenter._classify_heading("Experimental Setup") == SectionType.EXPERIMENTS

        # Results variations
        assert segmenter._classify_heading("Results") == SectionType.RESULTS
        assert segmenter._classify_heading("Results and Analysis") == SectionType.RESULTS


# =============================================================================
# SEG-1 — Roman-numeral section headings
# See llm/features/seg1-roman-numeral-headings.md
# =============================================================================


class TestSEG1RomanNumeralHeadings:
    """AC-1: Roman-numeral headings classify correctly.

    Every one of these returned UNKNOWN before SEG-1, which is why an
    IEEE paper produced no usable extractor input at all.
    """

    @pytest.fixture
    def segmenter(self):
        return SectionSegmenter()

    @pytest.mark.parametrize(
        "heading,expected",
        [
            # fact_completion (IEEE Access) and empire (IEEE ESEM), verbatim.
            ("I. INTRODUCTION", SectionType.INTRODUCTION),
            ("II. BACKGROUND", SectionType.BACKGROUND),
            ("II. RELATED WORK", SectionType.RELATED_WORK),
            ("IV. EVALUATION", SectionType.EXPERIMENTS),
            ("V. RESULTS", SectionType.RESULTS),
            ("VI. THREATS TO VALIDITY", SectionType.LIMITATIONS),
            ("VII. DISCUSSION", SectionType.DISCUSSION),
            ("VIII. CONCLUSION", SectionType.CONCLUSION),
            # Numeral-form coverage: close-paren, no separator dot, lowercase,
            # and the subtractive/additive forms (IX, XI).
            ("XI) Results", SectionType.RESULTS),
            ("I Introduction", SectionType.INTRODUCTION),
            ("iv. evaluation", SectionType.EXPERIMENTS),
            ("IX. Appendix", SectionType.APPENDIX),
        ],
    )
    def test_roman_numeral_heading_is_classified(self, segmenter, heading, expected):
        assert segmenter._classify_heading(heading) == expected

    @pytest.mark.parametrize(
        "heading",
        [
            # AC-3 (Decision 1): IEEE letters mark SUBSECTIONS. Promoting them
            # to top level truncates the parent — measured at -13,137 chars on
            # fact_completion, where "B. RESULTS AND DISCUSSION" is a child of
            # "IV. EVALUATION".
            "B. RESULTS AND DISCUSSION",
            "A. EVALUATION PROTOCOL",
            "C. Data Analysis",
            # AC-4 (Decision 3): hierarchical numbering is the same failure
            # mode — "4.4. Results" inside "4. Evaluation" cost
            # hypothesis_generation 5,585 chars.
            "4.4. Results",
            "4.4. Experimental setup",
            "3.1 Methods",
            "II.B. Results",
            # AC-4: a numeral needs a separator, so no bare-letter smuggling.
            "Vresults",
            "Xmethods",
            # AC-4: Roman-looking English words. "Vision" has no separator;
            # "Civil" is not a supported numeral value.
            "Vision",
            "Civil Approach",
            "Introductory Remarks",
        ],
    )
    def test_non_top_level_prefix_stays_unknown(self, segmenter, heading):
        assert segmenter._classify_heading(heading) == SectionType.UNKNOWN

    @pytest.mark.parametrize(
        "heading,expected",
        [
            # Upper end of the supported range (XXX + VIII).
            ("XXXVIII. Results", SectionType.RESULTS),
            ("XXIII. Discussion", SectionType.DISCUSSION),
            ("XIV. Conclusion", SectionType.CONCLUSION),
            # Subtractive forms.
            ("IX. Results", SectionType.RESULTS),
            ("XIX. Results", SectionType.RESULTS),
        ],
    )
    def test_multi_character_numerals(self, segmenter, heading, expected):
        """Papers never reach XL, but XIV/XIX/XXIII are ordinary."""
        assert segmenter._classify_heading(heading) == expected

    @pytest.mark.parametrize("heading", ["IIII. Results", "VV. Results"])
    def test_invalid_numeral_forms_are_not_enumerators(self, segmenter, heading):
        """`IIII` and `VV` are not Roman numerals. Rejecting them keeps the
        enumerator from being a general 'strip leading letters' rule."""
        assert segmenter._classify_heading(heading) == SectionType.UNKNOWN

    @pytest.mark.parametrize("heading", ["", "   ", "\t", ".", "IV."])
    def test_degenerate_headings_are_unknown(self, segmenter, heading):
        """Empty, whitespace-only, and a numeral with no title at all. The
        last one matters: "IV." alone must not classify as anything."""
        assert segmenter._classify_heading(heading) == SectionType.UNKNOWN

    def test_raw_heading_text_is_preserved_as_title(self, segmenter):
        """Only the classification input is normalized; Section.title keeps
        the numeral so an operator can still find the heading in the PDF."""
        text = """
I. INTRODUCTION

Enough words here to clear the minimum section length for this test so
that the section survives the short-section filter applied afterwards.
"""
        result = segmenter.segment(text)

        intro = result.get_sections_by_type(SectionType.INTRODUCTION)
        assert len(intro) == 1
        assert intro[0].title == "I. INTRODUCTION"


class TestSEG1PatternTableIntegrity:
    """AC-5: guards the 34-literal prefix-group removal.

    Four of these types (background, discussion, acknowledgments, appendix)
    had no coverage at all before SEG-1, so a keyword dropped during the
    edit would have failed silently.
    """

    @pytest.fixture
    def segmenter(self):
        return SectionSegmenter()

    @pytest.mark.parametrize(
        "heading,expected",
        [
            ("Abstract", SectionType.ABSTRACT),
            ("IV. Introduction", SectionType.INTRODUCTION),
            ("II. Related Work", SectionType.RELATED_WORK),
            ("II. Background", SectionType.BACKGROUND),
            ("III. Methods", SectionType.METHODS),
            ("IV. Experiments", SectionType.EXPERIMENTS),
            ("V. Results", SectionType.RESULTS),
            ("VI. Discussion", SectionType.DISCUSSION),
            ("VII. Limitations", SectionType.LIMITATIONS),
            ("Future Work", SectionType.FUTURE_WORK),
            ("VIII. Conclusion", SectionType.CONCLUSION),
            ("Acknowledgments", SectionType.ACKNOWLEDGMENTS),
            ("References", SectionType.REFERENCES),
            ("Appendix A", SectionType.APPENDIX),
        ],
    )
    def test_every_section_type_has_a_working_pattern(
        self, segmenter, heading, expected
    ):
        assert segmenter._classify_heading(heading) == expected

    def test_every_section_type_except_unknown_has_patterns(self):
        """A type present in the enum but absent from the table can never be
        detected — a silent hole."""
        missing = [
            t.value
            for t in SectionType
            if t is not SectionType.UNKNOWN
            and not SectionSegmenter.SECTION_PATTERNS.get(t)
        ]
        assert missing == []

    def test_compiled_cache_covers_the_source_table(self):
        """`_compiled_patterns` is a CLASS attribute compiled once per process
        behind `if not self._compiled_patterns`, so it never recompiles. A
        stale cache would silently classify against the previous table."""
        segmenter = SectionSegmenter()

        assert set(segmenter._compiled_patterns) == set(
            SectionSegmenter.SECTION_PATTERNS
        )
        for section_type, patterns in SectionSegmenter.SECTION_PATTERNS.items():
            assert len(segmenter._compiled_patterns[section_type]) == len(
                patterns
            ), section_type

    def test_patterns_carry_no_numeric_prefix_group(self):
        """AC-11: numbering is stripped in _classify_heading, so a pattern
        re-introducing its own prefix group means two mechanisms."""
        offenders = [
            pattern
            for patterns in SectionSegmenter.SECTION_PATTERNS.values()
            for pattern in patterns
            if r"\d" in pattern
        ]
        assert offenders == []


class TestSEG1UnmatchedHeadingDebugLog:
    """AC-14: the segmenter's rejections are the only record of
    SEG-3/4/5-class misses, and were previously unobservable."""

    @pytest.fixture
    def segmenter(self):
        return SectionSegmenter(min_section_words=5)

    TEXT = """
I. INTRODUCTION

Body text long enough to survive the minimum section word filter here.

III. SciCheck

Body text long enough to survive the minimum section word filter here.

IV. EVALUATION

Body text long enough to survive the minimum section word filter here.

B. RESULTS AND DISCUSSION

Body text long enough to survive the minimum section word filter here.
"""

    def test_unmatched_candidate_headings_logged_at_debug(self, segmenter, caplog):
        with caplog.at_level(
            logging.DEBUG, logger="agentic_kg.extraction.section_segmenter"
        ):
            segmenter.segment(self.TEXT)

        logged = "\n".join(r.getMessage() for r in caplog.records)
        # SEG-5: named after the contribution. SEG-1 does not fix it, but it
        # should at least be visible.
        assert "'III. SciCheck'" in logged
        # Decision 1: a lettered subsection, correctly rejected.
        assert "'B. RESULTS AND DISCUSSION'" in logged

    def test_nothing_logged_above_debug(self, segmenter, caplog):
        """An unmatched heading is normal for real papers — it must not be
        noise at INFO or WARNING."""
        with caplog.at_level(
            logging.INFO, logger="agentic_kg.extraction.section_segmenter"
        ):
            segmenter.segment(self.TEXT)

        assert caplog.records == []


class TestSEG1RealIEEEExcerpt:
    """AC-6: document-level behaviour on real PyMuPDF output.

    This is the assertion that catches the *silent* failure mode. A test that
    only checks "some sections were returned" passes on every paper in the
    ground-truth set today, including the one that yielded zero extractor
    input. Fixture provenance: fixtures/segmenter/README.md.
    """

    FIXTURE = (
        Path(__file__).parent / "fixtures" / "segmenter" / "ieee_roman_excerpt.txt"
    )

    @pytest.fixture
    def excerpt(self):
        return self.FIXTURE.read_text(encoding="utf-8")

    @pytest.fixture
    def doc(self, excerpt):
        return SectionSegmenter().segment(excerpt)

    def test_extractor_wanted_types_are_recovered(self, doc):
        """Pre-SEG-1 this document produced only `references`, so the
        extractor input was the empty string."""
        types = {s.section_type for s in doc.sections}
        assert SectionType.INTRODUCTION in types
        assert SectionType.EXPERIMENTS in types

    def test_extractor_input_is_non_empty(self, doc):
        """The headline SEG-1 outcome: 0 chars becomes non-zero, so the paper
        stops being skipped as `failed_thin`."""
        wanted = {
            SectionType.ABSTRACT,
            SectionType.INTRODUCTION,
            SectionType.METHODS,
            SectionType.EXPERIMENTS,
        }
        kept = sum(
            len(s.content) for s in doc.sections if s.section_type in wanted
        )
        assert kept > 0

    def test_lettered_subsections_do_not_become_sections(self, doc):
        """Decision 1, measured at -13,137 chars on the full paper."""
        # Any single leading uppercase letter EXCEPT I/V/X, which are numerals
        # and are supposed to match ("I. INTRODUCTION").
        lettered = re.compile(r"^[A-HJ-UWY-Z][.)]\s")
        titles = [s.title for s in doc.sections]
        assert not [t for t in titles if lettered.match(t)]

    def test_running_header_does_not_become_a_section(self, doc):
        """`A. Borrego et al.: ...` is 88 chars — under max_heading_length,
        so it is offered to the classifier on every page."""
        assert not [s for s in doc.sections if s.title.startswith("A. Borrego")]
        assert not [s for s in doc.sections if "VOLUME" in s.title]

    def test_subsection_content_stays_inside_its_parent(self, doc):
        """The point of leaving `B.` unmatched: its content must remain in
        the `experiments` span, not leak into a discarded `results` one."""
        experiments = doc.get_sections_by_type(SectionType.EXPERIMENTS)
        assert len(experiments) == 1
        # A phrase that appears only beneath "B. RESULTS AND DISCUSSION".
        assert "All CAFE variants outperform" in experiments[0].content

    def test_contribution_named_heading_still_missed(self, doc):
        """`III. SciCheck` is a SEG-5 miss and SEG-1 does not fix it. Pinned
        so the SEG-5 work has a red test to turn green."""
        assert not [s for s in doc.sections if s.title == "III. SciCheck"]
        assert SectionType.METHODS not in {s.section_type for s in doc.sections}

    def test_run_in_abstract_still_missed(self, doc):
        """Likewise SEG-3. Neither IEEE paper gains an abstract from SEG-1."""
        assert doc.get_sections_by_type(SectionType.ABSTRACT) == []

    def test_ingestion_would_no_longer_drop_this_paper(self, doc):
        """The claim SEG-1 actually makes, across the segmenter → ingestion
        boundary that the unit tests above each test only one side of.

        Pre-fix, `_build_extractor_section_text` returned "" for this
        document, which is below MIN_USABLE_CHARS, so `_acquire_full_text`
        categorized the paper `failed_thin` and `ingest_papers` skipped it —
        a paper carrying 32 of the 53 chain entities, dropped.
        """
        from agentic_kg.ingestion import (
            MIN_USABLE_CHARS,
            _build_extractor_section_text,
            _missing_wanted_sections,
        )

        section_text = _build_extractor_section_text(doc)

        assert len(section_text.strip()) >= MIN_USABLE_CHARS
        # And the residual gaps are reported rather than passing silently.
        assert _missing_wanted_sections(doc) == ["abstract", "methods"]


class TestSEG1SegmentWithAbstractUntouched:
    """AC-12: SEG-2 owns the fix-or-delete decision on
    segment_with_abstract. SEG-1 must not silently resolve it."""

    def test_segment_with_abstract_still_matches_plain_segment(self):
        """The defect is that `^` anchors to the document start without
        re.MULTILINE, so a real paper's title block defeats it. Preserved."""
        segmenter = SectionSegmenter(min_section_words=5)
        text = """
SciCheck: Completing Scientific Facts in Knowledge Graphs
Ana Borrego, Daniel Ayala, Inma Hernandez

ABSTRACT In the last few years we have witnessed the emergence of several
knowledge graphs that describe research knowledge.

I. INTRODUCTION

Body text long enough to survive the minimum section word filter here.
"""
        plain = segmenter.segment(text)
        with_abstract = segmenter.segment_with_abstract(text)

        assert [(s.section_type, s.title) for s in plain.sections] == [
            (s.section_type, s.title) for s in with_abstract.sections
        ]

    def test_abstract_branch_fires_only_at_the_very_start_of_the_document(self):
        """Pins the exact shape of the SEG-2 defect, so SEG-2 can decide from
        evidence rather than re-derive it.

        The regex is built with re.IGNORECASE | re.DOTALL but **no
        re.MULTILINE**, so `^` anchors to offset 0 of the whole document. It
        therefore fires only when the literal word "Abstract" is the first
        thing in the text — which no PDF is, because every one opens with a
        title and author block. This synthetic document is the only kind that
        reaches the branch.
        """
        segmenter = SectionSegmenter(min_section_words=5)
        text = (
            "Abstract: this synthetic document begins with the abstract label "
            "at offset zero, which is the only way the branch is reachable.\n"
            "Introduction\n"
            "Body text long enough to survive the minimum word filter here.\n"
        )

        doc = segmenter.segment_with_abstract(text)

        abstracts = doc.get_sections_by_type(SectionType.ABSTRACT)
        assert len(abstracts) == 1
        assert abstracts[0].title == "Abstract"
        assert "synthetic document begins" in abstracts[0].content
        # Offsets of the trailing sections are shifted past the abstract.
        intro = doc.get_sections_by_type(SectionType.INTRODUCTION)[0]
        assert intro.start_char > abstracts[0].end_char - len(text)

    def test_abstract_branch_skipped_when_the_abstract_is_too_short(self):
        """The inner min_section_words guard: matched, but not kept."""
        segmenter = SectionSegmenter(min_section_words=50)
        text = "Abstract: too short.\nIntroduction\n" + "word " * 100

        doc = segmenter.segment_with_abstract(text)

        assert doc.get_sections_by_type(SectionType.ABSTRACT) == []


class TestGetSectionSegmenter:
    """Tests for singleton access."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_section_segmenter()

    def teardown_method(self):
        """Reset singleton after each test."""
        reset_section_segmenter()

    def test_returns_segmenter_instance(self):
        """Test that get_section_segmenter returns a segmenter."""
        segmenter = get_section_segmenter()

        assert isinstance(segmenter, SectionSegmenter)

    def test_returns_same_instance(self):
        """Test that get_section_segmenter returns singleton."""
        segmenter1 = get_section_segmenter()
        segmenter2 = get_section_segmenter()

        assert segmenter1 is segmenter2

    def test_reset_clears_singleton(self):
        """Test that reset clears the singleton."""
        segmenter1 = get_section_segmenter()
        reset_section_segmenter()
        segmenter2 = get_section_segmenter()

        assert segmenter1 is not segmenter2
