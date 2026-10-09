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
from pathlib import Path

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

    # ONE alternative per case, deliberately. The first version of this
    # parametrize double-covered: "Contents lists available at ScienceDirect"
    # hit two terms and "Received 3 January; accepted 4 February" hit two more,
    # so four of the eight alternatives were never independently asserted and
    # a "remove the redundant-looking term" refactor passed CI. These
    # alternatives are what stand between the corpus and fabricated
    # 2,058-2,649-character abstracts on six papers.
    @pytest.mark.parametrize(
        "term,furniture",
        [
            ("https?://", "available online at https://example.org/x for you."),
            ("doi.org", "the identifier is doi.org/10.1038/s41597 for this."),
            ("Contents lists available",
             "Contents lists available at this publisher for the journal."),
            ("ScienceDirect", "hosted on ScienceDirect for the readership."),
            ("accepted", "the paper was accepted in February after review."),
            ("received", "the paper was received in January by the editor."),
            ("www.", "see www.nature.com/scientificdata for a description."),
        ],
    )
    def test_a_span_containing_furniture_is_rejected(
        self, segmenter, term, furniture,
    ):
        del term  # names the alternative under test; asserted via the id
        text = (
            f"{_prose()}\n"
            f"{furniture.ljust(90, '.')}\n"
            f"Background & Summary\n{_body()}\nMethods\n{_body()}\n"
        )
        doc = segmenter.segment(text)
        assert doc.get_sections_by_type(SectionType.ABSTRACT) == []

    def test_an_at_sign_alone_is_enough(self, segmenter):
        """The bare `@` alternative, which the rewritten cases above avoid so
        each one isolates a single term."""
        text = (
            f"{_prose()}\n"
            f"{'write to a@b for the dataset access details'.ljust(90, '.')}\n"
            f"Background & Summary\n{_body()}\nMethods\n{_body()}\n"
        )
        assert segmenter.segment(text).get_sections_by_type(
            SectionType.ABSTRACT,
        ) == []

    @pytest.mark.parametrize(
        "term",
        ["@", "https?://", r"doi\.org", "Contents lists available",
         "ScienceDirect", r"\baccepted\b", r"\breceived\b", r"www\."],
    )
    def test_every_alternative_is_still_in_the_denylist(self, term):
        """Pins the LIST, so a term cannot be dropped without a test noticing
        even if some future case happens to double-cover it."""
        assert term in _TITLE_PAGE_FURNITURE.pattern

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

    def test_the_factor_is_pinned_to_its_measured_value(self):
        """The weakest assertion in this file used to be ``0 < f < 1``, on the
        constant most sensitive to layout — a live 0.60–0.75 drift window that
        mutation testing walked straight through. Bounded now the way
        ``_MIN_SENTENCES`` and ``_MAX_DIGIT_DENSITY`` are."""
        assert _PROSE_WIDTH_FACTOR == 0.75

    def test_lowering_the_factor_would_swallow_a_metadata_line(self, segmenter):
        """Behavioural pin, not just a literal. A LOWER factor admits NARROWER
        lines, so the run walks further back and absorbs the author/journal
        block. With a median body width of ~100, a 70-character metadata line
        sits below 0.75x (75) and above 0.60x (60): at the shipped value it
        breaks the run, at 0.60 it would not.
        """
        metadata = (
            "Danilo Dessi, Francesco Osborne, Enrico Motta and Angelo Salatino"
        )
        assert 60 < len(metadata) < 75, len(metadata)
        text = (
            f"{metadata}\n"
            f"{_prose(sentences=8, width=100)}\n"
            f"Background & Summary\n{_body()}\nMethods\n{_body()}\n"
        )
        abstract = segmenter.segment(text).get_sections_by_type(
            SectionType.ABSTRACT,
        )
        assert len(abstract) == 1
        assert "Danilo" not in abstract[0].content

    def test_the_factor_still_admits_the_documented_layouts(self):
        """The other side of the bound: cskg2's median body width is 106 and
        its abstract lines are ~85-106, so 0.75 x 106 = 79.5 keeps them."""
        assert _PROSE_WIDTH_FACTOR * 106 < 85


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

# =============================================================================
# The risk surface this corpus CANNOT contain
# =============================================================================


class TestReconstructedPdfPreamble:
    """``paper_cskg2.txt`` begins at character 0 with the abstract, because
    ``segment_ground_truth.py`` cut the title page away. So the positional
    rule's entire risk surface — running backwards into publisher furniture,
    measured at 12,157 characters uncapped and 2,058–2,649 characters across
    six papers — **cannot exist in the committed corpus**. Any rule returning
    "the prefix before the first heading" scores 1,210 there.

    This class puts the title page back, reconstructed from the line listing
    the SEG-4 spec recorded off the real PDF:

        0 | ( 76) Scientific Data | (2025) 12:964 | https://doi.org/...
        1 | ( 29) www.nature.com/scientificdata
        2 | ( 34) CS-KG 2.0: A Large-scale Knowledge
        3 | ( 25) Graph of Computer Science
        4 | ( 86) Danilo Dessi 1, Francesco Osborne 2,3, ...
        5 | ( 15) & Enrico Motta2              <-- breaks the run
        6 | (106) The rapid evolution of AI ...   <-- run starts

    **This is a reconstruction, not a measurement of the PDF.** The PDFs are
    gitignored and this work never ran against them. What it does establish is
    that the mechanism behaves as documented when a title page IS present —
    which the committed corpus alone cannot show either way.

    Note line 4 is 86 characters, comfortably ABOVE 0.75 x 108, so it does
    *not* break the run. **The entire mechanism rests on line 5 being short.**

    **What happens when it is not, stated up front because an earlier draft of
    this docstring got it wrong.** The reassuring answer would be that the run
    reaches lines 0-1, Guard 1 trips on `https://` / `doi.org` / `www.`, and
    the failure mode is therefore a MISSING abstract rather than a wrong one.
    **That is false, and measuring it is how I found out.** With line 5
    prose-width the run takes lines 4 and 5 and stops at line 3; neither
    carries a denylisted term, and the span is dominated by real abstract prose
    so both guards pass. The result is an abstract with two lines of author
    metadata glued to the front — subtly wrong, not absent.

    So the guards bound the BLAST RADIUS -- they do stop a 12,157-character
    title-page span, pinned by the "stop the run at real furniture" test below
    -- but they do NOT guarantee a clean leading edge. On
    cskg2's real layout line 5 is 15 characters and this does not arise; on a
    future label-less paper whose author block wraps wide, it would. Both
    behaviours are pinned below — see
    ``test_a_prose_width_metadata_line_IS_absorbed_known_limitation``.
    """

    PREAMBLE = [
        'Scientific Data | (2025) 12:964 | https://doi.org/10.1038/s41597-025-05200-8',
        "www.nature.com/scientificdata",
        "CS-KG 2.0: A Large-scale Knowledge",
        "Graph of Computer Science",
        'Danilo Dessi 1, Francesco Osborne 2,3, Enrico Motta 2, Angelo Salatino 2,3 and others4',
        "& Enrico Motta2",
    ]

    @pytest.fixture
    def reconstructed(self) -> str:
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures" / "ground_truth_chain" / "paper_cskg2.txt"
        ).read_text(encoding="utf-8")
        return "\n".join(self.PREAMBLE) + "\n" + fixture

    def test_the_documented_line_widths_are_what_the_spec_recorded(self):
        """If the reconstruction drifts from the recorded widths it stops
        testing the thing it claims to test."""
        assert [len(line) for line in self.PREAMBLE] == [76, 29, 34, 25, 86, 15]
        # Line 4 is ABOVE the prose-width floor for this document (0.75 x 108
        # = 81), so it does NOT break the run. The whole mechanism rests on
        # line 5 being short.

    def test_the_abstract_survives_a_title_page(self, segmenter, reconstructed):
        doc = segmenter.segment(reconstructed)
        abstracts = doc.get_sections_by_type(SectionType.ABSTRACT)
        assert len(abstracts) == 1
        assert abstracts[0].content.startswith("The rapid evolution of AI")

    def test_it_returns_the_same_span_as_without_the_title_page(
        self, segmenter, reconstructed,
    ):
        """The load-bearing assertion. The committed corpus gives 1,210 chars
        trivially; the point is that adding a real title page in front changes
        nothing."""
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures" / "ground_truth_chain" / "paper_cskg2.txt"
        ).read_text(encoding="utf-8")
        bare = segmenter.segment(fixture).get_sections_by_type(
            SectionType.ABSTRACT,
        )[0]
        with_page = segmenter.segment(reconstructed).get_sections_by_type(
            SectionType.ABSTRACT,
        )[0]
        assert with_page.content == bare.content
        assert len(with_page.content) == 1210

    def test_no_furniture_reaches_the_span(self, segmenter, reconstructed):
        abstract = segmenter.segment(reconstructed).get_sections_by_type(
            SectionType.ABSTRACT,
        )[0]
        for term in ("doi.org", "www.", "Scientific Data |", "Enrico Motta2"):
            assert term not in abstract.content

    def test_a_prose_width_metadata_line_IS_absorbed_known_limitation(
        self, segmenter,
    ):
        """A limitation, pinned rather than papered over.

        The first draft of this test asserted that a prose-width line 5 would
        push the run back into the journal header, Guard 1 would reject, and
        the failure mode would therefore be a MISSING abstract rather than a
        wrong one. **That is false**, and measuring it is how I found out.

        With line 5 prose-width the run takes lines 4 and 5 and stops at line 3
        (25 chars). Neither line carries a denylisted term, and the span is
        dominated by real abstract prose so the digit-density and sentence
        tests both pass. The result is an abstract with two lines of author
        metadata glued to the front — subtly wrong, not absent.

        The two guards bound the BLAST RADIUS (they stop a 12,157-character
        title-page span) but they do not guarantee a clean leading edge. On the
        real cskg2 layout line 5 is 15 characters and this does not arise; on a
        future label-less paper whose author block wraps wide, it would. Owner:
        whoever extends the positional rule beyond this corpus.
        """
        preamble = list(self.PREAMBLE)
        preamble[5] = (
            "and Enrico Motta 2, with additional affiliations listed below "
            "for all of the contributing authors"
        )
        assert len(preamble[5]) > 81
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures" / "ground_truth_chain" / "paper_cskg2.txt"
        ).read_text(encoding="utf-8")
        abstracts = segmenter.segment(
            "\n".join(preamble) + "\n" + fixture,
        ).get_sections_by_type(SectionType.ABSTRACT)

        assert len(abstracts) == 1
        assert abstracts[0].content.startswith("Danilo Dessi")
        # Bounded, though: the journal header and its URLs never get in.
        for term in ("doi.org", "www.", "Scientific Data |"):
            assert term not in abstracts[0].content

    def test_the_guards_still_stop_the_run_at_real_furniture(self, segmenter):
        """The blast-radius bound the guards DO provide: make every preamble
        line prose-width and the run reaches line 0, whose URL trips Guard 1 —
        no fabricated 12,157-character abstract."""
        preamble = [
            line if len(line) > 81 else line.ljust(90, "o")
            for line in self.PREAMBLE
        ]
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures" / "ground_truth_chain" / "paper_cskg2.txt"
        ).read_text(encoding="utf-8")
        doc = segmenter.segment("\n".join(preamble) + "\n" + fixture)
        assert doc.get_sections_by_type(SectionType.ABSTRACT) == []
