"""SEG-4 PR-3: the positional abstract.

Spec: ``llm/features/seg4-journal-vocabulary-under-segmentation.md``,
Decision 4. Acceptance criteria AC-4 .. AC-7.

Nature *Scientific Data* prints no ``Abstract`` label — the abstract is simply
the lead paragraph. That is the fifth of the five conventions in the
ground-truth set and the one SEG-3 deliberately deferred, because catching it
needs a positional rule and a positional rule needs a terminator:
``Background & Summary`` was unrecognized until SEG-4 PR-1, and **measured, an
uncapped positional rule ran 109 lines to ``Methods`` and produced a
12,157-character "abstract" that swallowed the paper's real introduction**.

The rule: the abstract is the maximal run of consecutive *prose-width* lines
immediately preceding the first recognized heading. Two guards, covering
different failures.
"""

import logging

import pytest
from agentic_kg.extraction.section_segmenter import (
    _MAX_DIGIT_DENSITY,
    _MIN_SENTENCES,
    _PROSE_WIDTH_FACTOR,
    _TITLE_PAGE_FURNITURE,
    SectionSegmenter,
    SectionType,
)

LOGGER = "agentic_kg.extraction.section_segmenter"


@pytest.fixture
def segmenter() -> SectionSegmenter:
    return SectionSegmenter()


def _prose(sentences: int = 8, width: int = 100) -> str:
    """Lines of prose-width text with real sentence terminators and no digits."""
    line = ("the rapid evolution of scholarly knowledge graphs " * 4)[:width - 2]
    return "\n".join(f"{line}." for _ in range(sentences))


def _body(words: int = 60) -> str:
    return " ".join(["content"] * words)


# =============================================================================
# AC-4 — the rule finds a label-less lead paragraph
# =============================================================================


class TestPositionalAbstract:
    def test_a_label_less_lead_paragraph_becomes_the_abstract(self, segmenter):
        text = f"{_prose()}\nBackground & Summary\n{_body()}\nMethods\n{_body()}\n"
        doc = segmenter.segment(text)
        abstracts = doc.get_sections_by_type(SectionType.ABSTRACT)
        assert len(abstracts) == 1
        assert abstracts[0].content.startswith("the rapid evolution")

    def test_the_abstract_is_first_in_document_order(self, segmenter):
        text = f"{_prose()}\nBackground & Summary\n{_body()}\nMethods\n{_body()}\n"
        doc = segmenter.segment(text)
        assert doc.sections[0].section_type == SectionType.ABSTRACT

    def test_it_stops_at_the_first_recognized_heading(self, segmenter):
        """The tripwire the spec inherited from SEG-3's AC-16. The harness
        scores MORE chars as an improvement, so an abstract that swallowed the
        introduction would be reported as a success."""
        text = f"{_prose()}\nBackground & Summary\n{_body()}\nMethods\n{_body()}\n"
        doc = segmenter.segment(text)
        assert doc.sections[1].section_type == SectionType.INTRODUCTION
        assert "content" not in doc.sections[0].content

    def test_a_short_irregular_line_breaks_the_run(self, segmenter):
        """Title, author and journal-header lines are short and irregular, so
        they break the run. That is the whole mechanism — no length cap and no
        tuning constant are needed once the terminator exists."""
        text = (
            "CS-KG 2.0: A Large-scale Knowledge\n"
            "& Enrico Motta\n"                    # 15 chars: breaks the run
            f"{_prose()}\n"
            f"Background & Summary\n{_body()}\nMethods\n{_body()}\n"
        )
        doc = segmenter.segment(text)
        abstract = doc.get_sections_by_type(SectionType.ABSTRACT)[0]
        assert "Enrico Motta" not in abstract.content
        assert "CS-KG 2.0" not in abstract.content


# =============================================================================
# AC-5 — it never competes with a labelled abstract
# =============================================================================


class TestNeverCompetesWithALabel:
    """The rule runs AFTER normal segmentation, so it can only fire when
    SEG-3's label patterns found nothing. The two never both produce an
    abstract."""

    @pytest.mark.parametrize(
        "label",
        [
            "Abstract",
            "A B S T R A C T",
            "ABSTRACT In the last few years, we have witnessed",
        ],
    )
    def test_a_labelled_paper_gets_exactly_one_abstract(self, segmenter, label):
        text = f"{label}\n{_prose()}\n1. Introduction\n{_body()}\n2. Methods\n{_body()}\n"
        doc = segmenter.segment(text)
        assert len(doc.get_sections_by_type(SectionType.ABSTRACT)) == 1

    def test_clean_prose_before_a_label_does_not_become_a_second_abstract(
        self, segmenter,
    ):
        """The sensitive case, and the one that makes mutation N5 red.

        A label at character 0 leaves nothing in front of it, so removing the
        early return is unobservable there. Give the paper a title block of
        prose-width lines carrying no publisher furniture and the guards can no
        longer help: only the early return stops the rule from minting a second
        abstract out of the preamble.
        """
        text = (
            f"{_prose(sentences=6)}\n"
            f"Abstract\n{_prose(sentences=6)}\n"
            f"1. Introduction\n{_body()}\n2. Methods\n{_body()}\n"
        )
        doc = segmenter.segment(text)
        assert len(doc.get_sections_by_type(SectionType.ABSTRACT)) == 1

    def test_a_document_with_no_headings_is_untouched(self, segmenter):
        doc = segmenter.segment(_prose())
        assert doc.detected_structure is False
        assert len(doc.sections) == 1
        assert doc.sections[0].section_type == SectionType.UNKNOWN


# =============================================================================
# AC-6 — Guard 1: publisher furniture
# =============================================================================


class TestFurnitureGuard:
    """Without this guard, the rule builds 2,058–2,649-character "abstracts"
    out of title pages on SIX of the eight papers — and because they fall
    inside SEG-3's 500–4,000 band, AC-16 would not catch them. The guard makes
    the rule order-independent and safe for future label-less papers, not just
    for this corpus.
    """

    @pytest.mark.parametrize(
        "furniture",
        [
            "contact the author at someone@example.edu for details here.",
            "available online at https://example.org/paper for readers.",
            "the identifier is doi.org/10.1038/s41597 for this record.",
            "Contents lists available at ScienceDirect for this journal.",
            "Received 3 January; accepted 4 February after peer review.",
            "see www.nature.com/scientificdata for the full description.",
        ],
    )
    def test_a_span_containing_furniture_is_rejected(self, segmenter, furniture):
        text = (
            f"{_prose()}\n"
            f"{furniture.ljust(90, '.')}\n"
            f"Background & Summary\n{_body()}\nMethods\n{_body()}\n"
        )
        doc = segmenter.segment(text)
        assert doc.get_sections_by_type(SectionType.ABSTRACT) == []

    def test_the_real_lead_paragraph_contains_no_furniture(self):
        """The separation the guard relies on: the genuine abstract hits none
        of these terms, every rejected title page hits three or four."""
        assert not _TITLE_PAGE_FURNITURE.search(_prose())

    def test_rejection_is_logged_at_debug(self, segmenter, caplog):
        text = (
            f"{_prose()}\n"
            f"{'reach us at a@b.edu for the dataset'.ljust(90, '.')}\n"
            f"Background & Summary\n{_body()}\nMethods\n{_body()}\n"
        )
        with caplog.at_level(logging.DEBUG, logger=LOGGER):
            segmenter.segment(text)
        assert any(
            "positional abstract rejected" in r.getMessage()
            for r in caplog.records
        )


# =============================================================================
# AC-7 — Guard 2: does it read as prose?
# =============================================================================


class TestProseGuard:
    """Not redundant with the denylist, and not for the reason first assumed.
    A title-page span contains the paper's actual abstract prose, so it reads
    as prose and only the denylist can reject it. Conversely a pure metadata
    block fails the prose test decisively — cskg2's author/journal block scores
    0 sentences and 13.7% digits — which is what covers a long author line
    absorbed into the run, something the denylist would miss.
    """

    def test_a_digit_dense_block_is_rejected(self, segmenter):
        line = ("1234567890 " * 9).strip().ljust(95, "5")
        text = f"{line}\n{line}\n{line}\nBackground & Summary\n{_body()}\nMethods\n{_body()}\n"
        doc = segmenter.segment(text)
        assert doc.get_sections_by_type(SectionType.ABSTRACT) == []

    def test_a_span_with_too_few_sentences_is_rejected(self, segmenter):
        line = ("knowledge graph entity relation extraction pipeline " * 2)[:95]
        text = f"{line}\n{line}\n{line}\nBackground & Summary\n{_body()}\nMethods\n{_body()}\n"
        assert line.count(".") == 0
        doc = segmenter.segment(text)
        assert doc.get_sections_by_type(SectionType.ABSTRACT) == []

    def test_the_constants_are_where_the_measurement_put_them(self):
        """cskg2's real lead paragraph: 7 sentences, 0.8% digits. Its
        author/journal block: 0 sentences, 13.7% digits. The thresholds sit
        between the two with room on both sides."""
        assert _MIN_SENTENCES == 2
        assert 0.008 < _MAX_DIGIT_DENSITY < 0.137


# =============================================================================
# Prose width is relative to the document, not absolute
# =============================================================================


class TestRelativeProseWidth:
    """Line width is a property of column layout: the median body-line width is
    106 on cskg2 (single column) but 59 on fact_completion, 60 on empire and 69
    on hypothesis_generation (all two-column). An absolute threshold of 70
    would work on cskg2 and silently find nothing on an entire class of paper.
    """

    def test_the_rule_works_on_a_narrow_two_column_layout(self, segmenter):
        text = (
            f"{_prose(sentences=10, width=60)}\n"
            f"I. INTRODUCTION\n{_body()}\nII. RELATED WORK\n{_body()}\n"
        )
        doc = segmenter.segment(text)
        assert len(doc.get_sections_by_type(SectionType.ABSTRACT)) == 1

    def test_the_factor_is_a_fraction_of_the_median(self):
        assert 0 < _PROSE_WIDTH_FACTOR < 1


# =============================================================================
# The rule must not mint an abstract out of nothing
# =============================================================================


class TestDegenerateInput:
    def test_a_run_shorter_than_min_section_words_is_rejected(self, segmenter):
        text = (
            "short prose line that is long enough to be prose width "
            "here. and more.\n"
            f"1. Introduction\n{_body()}\n2. Methods\n{_body()}\n"
        )
        doc = segmenter.segment(text)
        assert doc.get_sections_by_type(SectionType.ABSTRACT) == []

    def test_a_heading_on_the_very_first_line_yields_no_abstract(self, segmenter):
        doc = segmenter.segment(f"1. Introduction\n{_body()}\n2. Methods\n{_body()}\n")
        assert doc.get_sections_by_type(SectionType.ABSTRACT) == []

    def test_empty_input_is_safe(self, segmenter):
        assert segmenter.segment("").sections == []
