"""SEG-3: run-in and letter-spaced abstracts.

Spec: ``llm/features/seg3-run-in-abstracts.md``.

``SectionType.ABSTRACT`` had exactly two patterns and both required the keyword
alone on its own line. Publishers do not typeset abstracts that way: across the
eight ground-truth papers there are five distinct conventions and the pattern
matched one of them, so seven papers reached the entity extractors with no
abstract at all — the single densest section of each paper, and one of the four
types the keep-list keeps.

Acceptance criteria AC-1 .. AC-8 and AC-14 live here. The corpus-level claims
(AC-9 .. AC-13, AC-16) are pinned by
``test_segmenter_frozen_corpus.py`` against the committed fixtures.
"""

import logging

import pytest
from agentic_kg.extraction.section_segmenter import (
    _LETTER_SPACED,
    SectionSegmenter,
    SectionType,
    _match_run_in,
)


@pytest.fixture
def segmenter() -> SectionSegmenter:
    return SectionSegmenter()


def _body(words: int = 40) -> str:
    """Filler long enough to clear ``min_section_words``."""
    return " ".join(["content"] * words)


# =============================================================================
# AC-1 — letter-spaced labels (Elsevier: 3 of the 8 papers)
# =============================================================================


class TestLetterSpacedAbstract:
    @pytest.mark.parametrize(
        "line", ["A B S T R A C T", "a b s t r a c t", "A B S T R A C T   "],
    )
    def test_letter_spaced_abstract_classifies(self, segmenter, line):
        assert segmenter._classify_heading(line) == SectionType.ABSTRACT

    def test_letter_spaced_abstract_yields_a_section(self, segmenter):
        doc = segmenter.segment(f"A B S T R A C T\n{_body()}\n")
        assert [s.section_type for s in doc.sections] == [SectionType.ABSTRACT]


# =============================================================================
# AC-7 — de-spacing is scoped to fully letter-spaced lines
# =============================================================================


class TestLetterSpacedScope:
    """Collapsing whitespace universally would turn ``Related Work`` into
    ``RelatedWork`` and break that pattern. The rule applies only when every
    token in the line is a single letter."""

    @pytest.mark.parametrize(
        "line", ["A B S T R A C T", "A R T I C L E  I N F O", "I N T R O"],
    )
    def test_matches_fully_letter_spaced_lines(self, line):
        assert _LETTER_SPACED.match(line)

    @pytest.mark.parametrize(
        "line",
        [
            "Related Work",
            "Data Records",
            "A B",          # two letters: below the >=3 floor
            "A",            # degenerate
            "A B C Work",   # a real word among the letters
            "",
        ],
    )
    def test_does_not_match_anything_else(self, line):
        assert not _LETTER_SPACED.match(line)

    def test_article_info_collapses_to_an_unmatched_token(self, segmenter):
        """Elsevier letter-spaces exactly two headings in these papers. The
        other one must collapse to something matching no pattern."""
        assert (
            segmenter._classify_heading("A R T I C L E  I N F O")
            == SectionType.UNKNOWN
        )

    def test_ordinary_multiword_headings_are_untouched(self, segmenter):
        assert segmenter._classify_heading("Related Work") == (
            SectionType.RELATED_WORK
        )

    @pytest.mark.parametrize("line", ["A B", "A", "I V"])
    def test_degenerate_spaced_lines_stay_unknown(self, segmenter, line):
        assert segmenter._classify_heading(line) == SectionType.UNKNOWN

    def test_the_three_letter_floor_is_exact(self):
        """The `{2,}` quantifier means "two letter-space pairs then a letter"
        = three letters minimum. The docstring called that deliberate but
        nothing asserted it, so `{2,}` -> `{3,}` survived mutation testing in
        the PR #69 review. Both sides of the boundary, pinned."""
        assert _LETTER_SPACED.match("A B C")     # 3 letters: matched
        assert not _LETTER_SPACED.match("A B")   # 2 letters: not


# =============================================================================
# AC-2, AC-3 — run-in labels
# =============================================================================


class TestRunInMatcher:
    @pytest.mark.parametrize(
        "line,label",
        [
            ("Abstract. In recent years, we saw the emergence", "Abstract."),
            ("Abstract—[Background.] Empirical research", "Abstract—"),
            ("Abstract– dash variant follows here", "Abstract–"),
            ("Abstract- hyphen variant follows here", "Abstract-"),
            ("Abstract: colon variant follows here", "Abstract:"),
            ("ABSTRACT In the last few years, we have witnessed", "ABSTRACT"),
        ],
    )
    def test_real_run_in_forms_match_and_report_the_label_end(self, line, label):
        result = _match_run_in(line)
        assert result is not None
        section_type, end = result
        assert section_type == SectionType.ABSTRACT
        assert line[:end].strip() == label

    def test_a_bare_abstract_line_is_not_a_run_in(self):
        """It already worked via ``^abstract\\s*$`` and must keep falling
        through, or the case is handled twice with different offsets."""
        assert _match_run_in("Abstract") is None
        assert _match_run_in("Abstract   ") is None


# =============================================================================
# AC-6 — Decision 4 lock: whitespace is a delimiter only after ALL CAPS
# =============================================================================


class TestRunInFalsePositives:
    """"Abstract syntax tree" and "abstract representation" are ordinary prose
    in computer science. A false abstract heading mid-paper is not a harmless
    mislabel: it opens a new span and therefore TRUNCATES whatever section
    contained the line."""

    @pytest.mark.parametrize(
        "line",
        [
            "Abstract syntax trees are used to represent programs",
            "Abstract representations of knowledge graphs are common",
            "Abstracts of the papers were reviewed by two annotators",
            "ABSTRACTS",
            "Abstraction is the key idea behind this layer",
        ],
    )
    def test_prose_beginning_with_abstract_is_not_a_heading(
        self, segmenter, line,
    ):
        assert _match_run_in(line) is None
        assert segmenter._classify_heading(line) == SectionType.UNKNOWN

    def test_a_false_abstract_heading_would_truncate_its_parent(
        self, segmenter,
    ):
        """The reason the case constraint exists, asserted as behaviour rather
        than as a classification."""
        text = (
            "1. Introduction\n"
            f"{_body()}\n"
            "Abstract syntax trees are used to represent programs\n"
            f"{_body()}\n"
        )
        doc = segmenter.segment(text)
        assert [s.section_type for s in doc.sections] == [
            SectionType.INTRODUCTION,
        ]


# =============================================================================
# AC-5, AC-11 — the run-in remainder is kept, character-exactly
# =============================================================================


class TestRunInRemainder:
    """``_extract_sections`` starts a section's content at the end of the
    heading LINE, so classifying a run-in line as a heading would throw away
    the body text sharing it — 60 chars on cskg, 87 on fact_completion, 48 on
    empire — and the kept abstract would then begin mid-sentence."""

    @pytest.mark.parametrize(
        "line,title,starts_with",
        [
            (
                "Abstract. In recent years, we saw the emergence",
                "Abstract.",
                "In recent years, we saw the",
            ),
            (
                "Abstract—[Background.] Empirical research in requirements",
                "Abstract—",
                "[Background.] Empirical research",
            ),
            (
                "ABSTRACT In the last few years, we have witnessed",
                "ABSTRACT",
                "In the last few years, we have",
            ),
        ],
    )
    def test_content_starts_immediately_after_the_label(
        self, segmenter, line, title, starts_with,
    ):
        doc = segmenter.segment(f"{line}\n{_body()}\n")
        abstract = doc.get_sections_by_type(SectionType.ABSTRACT)
        assert len(abstract) == 1
        assert abstract[0].title == title
        assert abstract[0].content.startswith(starts_with)

    def test_an_indented_run_in_label_gets_the_right_offset(self, segmenter):
        """``current_pos + indent + label_end``: an off-by-one here silently
        shifts every abstract by a character and no proxy assertion catches it."""
        doc = segmenter.segment(f"    ABSTRACT In the last few years\n{_body()}\n")
        abstract = doc.get_sections_by_type(SectionType.ABSTRACT)
        assert abstract[0].content.startswith("In the last few years")


# =============================================================================
# AC-4 — the standalone form is unchanged
# =============================================================================


class TestStandaloneAbstractUnchanged:
    @pytest.mark.parametrize("line", ["Abstract", "ABSTRACT", "Summary"])
    def test_standalone_labels_still_classify(self, segmenter, line):
        assert segmenter._classify_heading(line) == SectionType.ABSTRACT

    def test_standalone_title_is_the_whole_line(self, segmenter):
        doc = segmenter.segment(f"Abstract\n{_body()}\n")
        assert doc.sections[0].title == "Abstract"
        assert doc.sections[0].content.startswith("content")


# =============================================================================
# AC-8 — Decision 5 lock: the length guard tests the label, not the line
# =============================================================================


class TestLengthGuard:
    """``fact_completion``'s run-in line is 96 characters of the allowed 100.
    Line wrapping is a property of the PDF and the extractor version, so a
    re-extraction could silently un-fix that paper with no error."""

    def test_a_long_run_in_abstract_line_still_classifies(self, segmenter):
        line = "ABSTRACT " + "word " * 30
        assert len(line.strip()) > segmenter.max_heading_length
        doc = segmenter.segment(f"{line}\n{_body()}\n")
        assert doc.get_sections_by_type(SectionType.ABSTRACT)

    def test_a_long_body_sentence_is_still_skipped(self, segmenter):
        """Intent, not a sensitive guard.

        Measured (mutation M5): deleting ``max_heading_length`` entirely leaves
        this assertion green, because every entry in ``SECTION_PATTERNS`` is
        ``$``-anchored and a body sentence therefore classifies as UNKNOWN with
        or without the guard. Recorded rather than dressed up: after SEG-3 the
        length guard is *nearly* inert. The one case it still decides is
        below.
        """
        long_line = "We evaluate our approach on seven " + "dataset " * 20
        assert len(long_line.strip()) > segmenter.max_heading_length
        doc = segmenter.segment(
            f"1. Introduction\n{_body()}\n{long_line}\n{_body()}\n",
        )
        assert [s.section_type for s in doc.sections] == [
            SectionType.INTRODUCTION,
        ]

    def test_the_guard_still_rejects_an_over_long_pattern_match(
        self, segmenter,
    ):
        """The reachable case, and the one that makes M5 red.

        ``Related`` + 100 spaces + ``Work`` classifies as ``related_work`` --
        the patterns allow ``\\s+`` between the words -- but a 111-character
        line with 100 spaces in the middle is PDF column-join noise, not a
        heading. Only the length guard separates the two.
        """
        line = "Related" + " " * 100 + "Work"
        assert segmenter._classify_heading(line) == SectionType.RELATED_WORK
        assert len(line) > segmenter.max_heading_length
        doc = segmenter.segment(f"{line}\n{_body()}\n")
        assert not doc.get_sections_by_type(SectionType.RELATED_WORK)

    def test_a_long_non_run_in_heading_line_is_still_skipped(self, segmenter):
        long_related = "Related Work " + "x" * 120
        doc = segmenter.segment(f"{long_related}\n{_body()}\n")
        assert not doc.get_sections_by_type(SectionType.RELATED_WORK)


# =============================================================================
# AC-14 — SEG-1's DEBUG rejection stream must survive the restructuring
# =============================================================================


class TestRejectionStreamSurvives:
    def test_unmatched_candidates_are_still_logged(self, segmenter, caplog):
        """What the segmenter REJECTS is the only record of a SEG-4/5-class
        miss. ``_find_headings`` is restructured by SEG-3, so this is pinned."""
        with caplog.at_level(
            logging.DEBUG, logger="agentic_kg.extraction.section_segmenter",
        ):
            segmenter.segment(
                "A R T I C L E  I N F O\n"
                f"{_body()}\n"
                "The Computer Science Knowledge Graph\n"
                f"{_body()}\n",
            )
        rejected = [
            r.getMessage()
            for r in caplog.records
            if "unmatched candidate heading" in r.getMessage()
        ]
        assert any("A R T I C L E" in m for m in rejected)
        assert any("Computer Science Knowledge Graph" in m for m in rejected)

    def test_a_run_in_heading_is_not_logged_as_unmatched(self, segmenter, caplog):
        with caplog.at_level(
            logging.DEBUG, logger="agentic_kg.extraction.section_segmenter",
        ):
            segmenter.segment(f"ABSTRACT In the last few years\n{_body()}\n")
        assert not [
            r for r in caplog.records
            if "unmatched candidate heading" in r.getMessage()
            and "ABSTRACT" in r.getMessage()
        ]


# =============================================================================
# The five conventions, end to end
# =============================================================================


class TestAllFiveConventions:
    """One assertion per publisher convention in the ground-truth set, at the
    document level rather than through ``_classify_heading`` — AC-15's point is
    that a classification test passes while the document-level offset is wrong.
    """

    @pytest.mark.parametrize(
        "slug,first_line,expected_start",
        [
            ("kg_construction_survey", "Abstract", "content"),
            ("llm_ontology_gen", "A B S T R A C T", "content"),
            (
                "cskg",
                "Abstract. In recent years, we saw the emergence",
                "In recent years",
            ),
            (
                "fact_completion",
                "ABSTRACT In the last few years, we have witnessed",
                "In the last few years",
            ),
            (
                "empire",
                "Abstract—[Background.] Empirical research in requirements",
                "[Background.] Empirical",
            ),
        ],
    )
    def test_convention_yields_one_bounded_abstract(
        self, segmenter, slug, first_line, expected_start,
    ):
        doc = segmenter.segment(
            f"{first_line}\n{_body()}\n1. Introduction\n{_body()}\n",
        )
        abstracts = doc.get_sections_by_type(SectionType.ABSTRACT)
        assert len(abstracts) == 1, slug
        assert abstracts[0].content.startswith(expected_start), slug
        # AC-16: an abstract is BOUNDED, and something follows it. The harness
        # scores MORE chars as an improvement, so an abstract that swallowed
        # the next section would be reported as a success.
        assert doc.sections[1].section_type == SectionType.INTRODUCTION, slug
