"""
Unit tests for section segmentation.
"""

import pytest

from agentic_kg.extraction.section_segmenter import (
    Section,
    SectionSegmenter,
    SectionType,
    SegmentedDocument,
    SECTION_PRIORITY,
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
