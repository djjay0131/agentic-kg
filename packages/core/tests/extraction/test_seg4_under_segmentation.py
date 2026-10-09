"""SEG-4 PR-2: the under-segmentation detector.

Spec: ``llm/features/seg4-journal-vocabulary-under-segmentation.md``,
Decision 5. Acceptance criteria AC-8 .. AC-13.

An unrecognized heading is not a *missed* section — it is absorbed into its
predecessor, silently, and the result is one enormous mislabelled span rather
than an obviously broken one. The detector warns; it deliberately does not
guess where the missing boundary was.

Measured share of body words held by the largest section, which is what makes a
threshold viable: the swallowed spans run 41.3%–100%, the largest healthy
section is ~31.4%. The detector sits at 40%, in the gap.

Observability only: no behaviour changes, nothing is dropped or re-typed.
"""

import logging

import pytest
from agentic_kg.extraction.section_segmenter import (
    _MIN_BODY_SECTIONS_FOR_CHECK,
    _MIN_BODY_WORDS_FOR_CHECK,
    _UNDER_SEGMENTATION_SHARE,
    SectionSegmenter,
)

LOGGER = "agentic_kg.extraction.section_segmenter"


@pytest.fixture
def segmenter() -> SectionSegmenter:
    return SectionSegmenter()


def _words(n: int) -> str:
    return " ".join(["content"] * n)


def _all_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and "under-segmentation" in r.getMessage()
    ]


def _warnings(caplog) -> list[str]:
    """Share-based warnings only -- the N>=3 signal."""
    return [m for m in _all_warnings(caplog) if "% of body words" in m]


def _count_warnings(caplog) -> list[str]:
    """Section-count warnings only -- the N<=2 signal."""
    return [m for m in _all_warnings(caplog) if "recognized body section" in m]


# =============================================================================
# AC-8 — it fires on a swallowed span
# =============================================================================


class TestDetectorFires:
    def test_one_section_holding_most_of_the_body_warns(self, segmenter, caplog):
        text = (
            f"1. Introduction\n{_words(900)}\n"
            f"2. Related work\n{_words(100)}\n"
            f"3. Methodology\n{_words(100)}\n"
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert len(_warnings(caplog)) == 1

    def test_the_warning_names_the_section_its_type_and_its_share(
        self, segmenter, caplog,
    ):
        text = (
            f"1. Introduction\n{_words(900)}\n"
            f"2. Related work\n{_words(100)}\n"
            f"3. Methodology\n{_words(100)}\n"
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        message = _warnings(caplog)[0]
        assert "1. Introduction" in message
        assert "introduction" in message
        assert "%" in message

    def test_the_warning_names_the_next_recognized_heading(
        self, segmenter, caplog,
    ):
        """The actionable half: the missing heading lies between the swallowing
        span and the next one that *was* recognized. The message does not
        attempt to name a root cause — attribution would be a guess, and the
        two facts together let a human read it in seconds."""
        text = (
            f"1. Introduction\n{_words(900)}\n"
            f"4. Evaluation\n{_words(100)}\n"
            f"5. Results\n{_words(100)}\n"
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert "4. Evaluation" in _warnings(caplog)[0]

    def test_a_trailing_swallowing_span_reports_end_of_document(
        self, segmenter, caplog,
    ):
        text = (
            f"1. Introduction\n{_words(100)}\n"
            f"2. Related work\n{_words(100)}\n"
            f"3. Methodology\n{_words(900)}\n"
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert "(end of document)" in _warnings(caplog)[0]


# =============================================================================
# AC-9, AC-10 — it stays quiet where it should
# =============================================================================


class TestDetectorIsQuiet:
    def test_a_healthy_paper_produces_no_warning(self, segmenter, caplog):
        """The largest healthy section in the corpus is ~31.4% of body words.
        A detector that fires there is noise, and noise gets muted."""
        text = "".join(
            f"{h}\n{_words(300)}\n"
            for h in (
                "1. Introduction", "2. Related work",
                "3. Methodology", "4. Evaluation",
            )
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert _warnings(caplog) == []

    def test_a_short_document_is_silent(self, segmenter, caplog):
        """Below ``_MIN_BODY_WORDS_FOR_CHECK`` the share is meaningless, and
        every hand-written test fixture in this repo would otherwise warn.
        Neither signal fires: the word floor runs before the section floor."""
        text = f"1. Introduction\n{_words(60)}\n2. Related work\n{_words(25)}\n"
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            doc = segmenter.segment(text)
        assert sum(s.word_count for s in doc.sections) < _MIN_BODY_WORDS_FOR_CHECK
        assert _all_warnings(caplog) == []

    def test_a_large_references_section_does_not_trip_it(self, segmenter, caplog):
        """``references`` reaches 31.8% of TOTAL words on ``fact_completion``
        and is legitimately large, so the denominator excludes the tail types
        and they are never themselves reported."""
        text = (
            f"1. Introduction\n{_words(300)}\n"
            f"2. Related work\n{_words(300)}\n"
            f"3. Methodology\n{_words(300)}\n"
            f"References\n{_words(2000)}\n"
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert _warnings(caplog) == []


# =============================================================================
# AC-11 — the threshold sits in the measured gap
# =============================================================================


class TestThreshold:
    def test_the_threshold_is_between_healthy_and_swallowed(self):
        """Swallowed spans measure 41.3%–100%; the largest healthy section
        ~31.4%. If someone moves this constant, the margin has to be argued
        again."""
        assert 0.32 < _UNDER_SEGMENTATION_SHARE < 0.41

    @pytest.mark.parametrize("share,expected", [(0.45, 1), (0.30, 0)])
    def test_behaviour_either_side_of_the_threshold(
        self, segmenter, caplog, share, expected,
    ):
        total = 2000
        big = int(total * share)
        rest = total - big
        text = (
            f"1. Introduction\n{_words(big)}\n"
            f"2. Related work\n{_words(rest // 2)}\n"
            f"3. Methodology\n{_words(rest - rest // 2)}\n"
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert len(_warnings(caplog)) == expected


# =============================================================================
# AC-12, AC-13 — observability only
# =============================================================================


class TestNoBehaviourChange:
    def test_the_returned_document_is_unchanged_by_the_detector(
        self, segmenter, caplog,
    ):
        text = (
            f"1. Introduction\n{_words(900)}\n"
            f"2. Related work\n{_words(100)}\n"
            f"3. Methodology\n{_words(100)}\n"
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            warned = segmenter.segment(text)
        with caplog.at_level(logging.CRITICAL, logger=LOGGER):
            quiet = segmenter.segment(text)
        assert [(s.section_type, s.title, s.content) for s in warned.sections] == [
            (s.section_type, s.title, s.content) for s in quiet.sections
        ]

    def test_an_unstructured_document_does_not_raise(self, segmenter):
        """``detected_structure`` is False here; the detector must not run on
        the single synthetic UNKNOWN section and call it under-segmentation."""
        doc = segmenter.segment(_words(900))
        assert doc.detected_structure is False
        assert len(doc.sections) == 1

    def test_empty_input_does_not_raise(self, segmenter):
        assert segmenter.segment("").sections == []


# =============================================================================
# A deviation from the spec: a threshold below 1/N carries no information
# =============================================================================


class TestSmallSectionCountGetsItsOwnSignal:
    """With N body sections the smallest possible maximum share is 1/N, so at
    N <= 2 the smallest max share is 50% — already above the 40% threshold.
    A share-based warning would fire on EVERY two-section document regardless
    of how well it was segmented.

    This is not hypothetical. On the committed gold corpus the keep-list leaves
    only 2–4 sections per paper, and ``kg_construction_survey`` has exactly two
    (gold says abstract + introduction, the honest answer for a 94-page survey
    with no methods or experiments). It warned at 83.5% purely because of this
    arithmetic.

    **Going silent would be the wrong fix**, and the adversarial review of
    PR #69 caught it: N <= 2 on a large document is itself the worst case this
    detector exists for — ``cskg2`` at base had exactly ONE body section
    holding 100% of the paper, which is cause (4) exactly. So the small-N case
    reports the section COUNT: a different fact, stated as a different fact,
    with no share because the share is arithmetically forced.
    """

    def test_a_two_section_document_gets_no_share_warning(
        self, segmenter, caplog,
    ):
        text = f"1. Introduction\n{_words(900)}\n2. Related work\n{_words(100)}\n"
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            doc = segmenter.segment(text)
        assert len(doc.sections) == 2
        assert sum(s.word_count for s in doc.sections) >= _MIN_BODY_WORDS_FOR_CHECK
        assert _warnings(caplog) == []

    def test_a_two_section_document_does_get_a_count_warning(
        self, segmenter, caplog,
    ):
        text = f"1. Introduction\n{_words(900)}\n2. Related work\n{_words(100)}\n"
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert len(_count_warnings(caplog)) == 1
        message = _count_warnings(caplog)[0]
        assert "only 2 recognized body section" in message
        assert "1. Introduction" in message
        # No share: at N<=2 it carries no information and printing it invites
        # exactly the misreading this branch exists to avoid.
        assert "% of body words" not in message

    def test_the_single_section_worst_case_is_not_silenced(
        self, segmenter, caplog,
    ):
        """The hole the review demonstrated: one `introduction` holding 100% of
        the body, with only a tail `references` beside it, emitted nothing."""
        text = (
            f"1. Introduction\n{_words(2000)}\n"
            f"References\n{_words(600)}\n"
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert len(_count_warnings(caplog)) == 1
        assert "only 1 recognized body section" in _count_warnings(caplog)[0]

    def test_a_three_section_document_uses_the_share_signal(
        self, segmenter, caplog,
    ):
        """The floor is a switch between two signals, not a mute button."""
        text = (
            f"1. Introduction\n{_words(900)}\n"
            f"2. Related work\n{_words(100)}\n"
            f"3. Methodology\n{_words(100)}\n"
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert len(_warnings(caplog)) == 1
        assert _count_warnings(caplog) == []

    def test_a_healthy_three_section_document_says_nothing_at_all(
        self, segmenter, caplog,
    ):
        text = "".join(
            f"{h}\n{_words(300)}\n"
            for h in ("1. Introduction", "3. Methodology", "4. Evaluation")
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert _all_warnings(caplog) == []

    def test_a_short_two_section_document_stays_silent(self, segmenter, caplog):
        """The word floor still runs first, so hand-written fixtures and short
        documents emit neither signal."""
        text = f"1. Introduction\n{_words(60)}\n2. Related work\n{_words(25)}\n"
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            segmenter.segment(text)
        assert _all_warnings(caplog) == []

    def test_the_floor_is_the_smallest_N_where_the_threshold_can_fail(self):
        """1/3 = 33.3% < 40%, so at N = 3 a well-segmented document can stay
        silent. At N = 2, 1/2 = 50% > 40% and it cannot."""
        assert 1 / _MIN_BODY_SECTIONS_FOR_CHECK < _UNDER_SEGMENTATION_SHARE
        assert 1 / (_MIN_BODY_SECTIONS_FOR_CHECK - 1) > _UNDER_SEGMENTATION_SHARE
