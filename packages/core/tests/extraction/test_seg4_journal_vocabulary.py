"""SEG-4 PR-1: Nature *Scientific Data* vocabulary.

Spec: ``llm/features/seg4-journal-vocabulary-under-segmentation.md``,
Decisions 1 and 2. Acceptance criteria AC-1 .. AC-3.

Section spans run from one recognized heading to the next, so a heading the
pattern table does not know is not a *missed* section — it is **absorbed into
its predecessor**, silently. ``cskg2`` is Nature *Scientific Data*, whose
top-level headings are ``Background & Summary``, ``Methods``, ``Data Records``,
``Technical Validation`` and ``Usage Notes``. Only ``Methods`` was in
``SECTION_PATTERNS``, so it absorbed 100% of the recognized body while the
paper's real introduction sat in no section at all.
"""

import pytest
from agentic_kg.extraction.section_segmenter import SectionSegmenter, SectionType


@pytest.fixture
def segmenter() -> SectionSegmenter:
    return SectionSegmenter()


def _body(words: int = 40) -> str:
    return " ".join(["content"] * words)


# =============================================================================
# AC-1 — the four headings classify
# =============================================================================


class TestNatureVocabulary:
    @pytest.mark.parametrize(
        "heading,expected",
        [
            ("Background & Summary", SectionType.INTRODUCTION),
            ("Background and Summary", SectionType.INTRODUCTION),
            ("background & summary", SectionType.INTRODUCTION),
            ("Technical Validation", SectionType.EXPERIMENTS),
            ("Data Records", SectionType.RESULTS),
            ("Usage Notes", SectionType.DISCUSSION),
        ],
    )
    def test_heading_classifies(self, segmenter, heading, expected):
        assert segmenter._classify_heading(heading) == expected

    @pytest.mark.parametrize(
        "heading",
        [
            "2. Background & Summary",
            "II. Technical Validation",
            "3 Data Records",
        ],
    )
    def test_numbering_is_inherited_from_seg1(self, segmenter, heading):
        """SEG-1's central win was stripping the enumerator once in
        ``_classify_heading``, so new vocabulary supports "IV." and "4." for
        free — and cannot forget to."""
        assert segmenter._classify_heading(heading) != SectionType.UNKNOWN


# =============================================================================
# AC-2 — the mapping is the one the keep-list rewards honestly
# =============================================================================


class TestMappingRationale:
    def test_background_and_summary_is_the_introduction_not_background(
        self, segmenter,
    ):
        """It *is* that journal's introduction. Mapping it to ``background``
        reads the name literally and costs -15,932 chars, because
        ``background`` is not in the keep-list — and it hides the citation
        chain's spine concept, ``scientific knowledge graph``."""
        assert segmenter._classify_heading("Background & Summary") == (
            SectionType.INTRODUCTION
        )
        assert segmenter._classify_heading("Background") == (
            SectionType.BACKGROUND
        )

    def test_data_records_is_results_not_methods(self, segmenter):
        """Mapping it to ``methods`` measures +6,138 chars, but that is a false
        label chosen to game the keep-list. If the keep-list is too narrow —
        and SEG-7's data says it is — that is SEG-7's decision to make openly,
        not something to smuggle in via a mis-mapping here."""
        assert segmenter._classify_heading("Data Records") == SectionType.RESULTS


# =============================================================================
# AC-3 — no new false positives
# =============================================================================


class TestNoOverreach:
    @pytest.mark.parametrize(
        "line",
        [
            "Background information about knowledge graphs is summarized",
            "Data Records are stored in a relational database",
            "Usage Notes for the API are in the appendix",
            "Technical Validation of the approach required two annotators",
            "Summary",
        ],
    )
    def test_prose_beginning_with_the_vocabulary_is_not_a_heading(
        self, segmenter, line,
    ):
        """The patterns stay ``$``-anchored, so a sentence opening with the
        same words is not promoted to a heading."""
        if line == "Summary":
            # "Summary" alone is a pre-existing ABSTRACT pattern, untouched.
            assert segmenter._classify_heading(line) == SectionType.ABSTRACT
            return
        assert segmenter._classify_heading(line) == SectionType.UNKNOWN


# =============================================================================
# Document level: the swallow stops
# =============================================================================


class TestNatureDocumentIsSegmented:
    def test_methods_no_longer_absorbs_the_rest_of_the_paper(self, segmenter):
        text = (
            f"Background & Summary\n{_body()}\n"
            f"Methods\n{_body()}\n"
            f"Data Records\n{_body()}\n"
            f"Technical Validation\n{_body()}\n"
            f"Usage Notes\n{_body()}\n"
        )
        assert [s.section_type for s in segmenter.segment(text).sections] == [
            SectionType.INTRODUCTION,
            SectionType.METHODS,
            SectionType.RESULTS,
            SectionType.EXPERIMENTS,
            SectionType.DISCUSSION,
        ]

    def test_before_the_fix_methods_would_have_held_everything(self, segmenter):
        """The regression this locks: with only ``Methods`` recognized, one
        span holds the whole body and the introduction is dropped entirely."""
        doc = segmenter.segment(
            f"Background & Summary\n{_body()}\n"
            f"Methods\n{_body()}\n"
            f"Data Records\n{_body(200)}\n",
        )
        methods = doc.get_sections_by_type(SectionType.METHODS)
        assert len(methods) == 1
        assert "content" in methods[0].content
        # The methods span must stop at Data Records, not run to the end.
        assert len(methods[0].content.split()) < 100
