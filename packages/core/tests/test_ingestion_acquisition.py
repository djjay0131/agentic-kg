"""
Unit tests for SM-1 full-text acquisition (content-acquisition-resilience).

Covers the extracted `_acquire_full_text` helper and its classifiers: candidate
source ordering (published first, arXiv fallback), fail-loud with categorized
reasons, and the no-abstract-fallback contract.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.data_acquisition.normalizer import NormalizedPaper
from agentic_kg.ingestion import (
    _EXTRACTOR_WANTED_SECTIONS,
    MIN_USABLE_CHARS,
    _acquire_full_text,
    _classify_pdf_failure,
    _found_section_types,
    _missing_wanted_sections,
    _paper_has_footprint,
    _proc_error,
)


def _paper(pdf_url=None, arxiv=None):
    ext = {"arxiv": arxiv} if arxiv else {}
    return NormalizedPaper(
        title="T", source="semantic_scholar", doi="10.x/y",
        pdf_url=pdf_url, external_ids=ext,
    )


def _seg_with_text(text):
    section = MagicMock()
    section.section_type = MagicMock()
    section.section_type.value = "abstract"
    section.content = text
    seg = MagicMock()
    seg.sections = [section]
    return seg


def _stage(success, error=None):
    s = MagicMock()
    s.success = success
    s.error = error
    return s


def _ok_proc(text="x" * (MIN_USABLE_CHARS + 50)):
    p = MagicMock()
    p.success = True
    p.segmented_document = _seg_with_text(text)
    p.stages = [_stage(True)]
    return p


def _failed_proc(error="Error downloading PDF: Server disconnected"):
    p = MagicMock()
    p.success = False
    p.segmented_document = None
    p.stages = [_stage(False, error)]
    return p


class TestClassifyPdfFailure:
    def test_404_message_is_failed_404(self):
        assert _classify_pdf_failure("HTTP error downloading PDF: 404") == "failed_404"

    def test_connection_message_is_failed_blocked(self):
        msg = "Error downloading PDF: Server disconnected"
        assert _classify_pdf_failure(msg) == "failed_blocked"

    def test_empty_message_defaults_to_blocked(self):
        assert _classify_pdf_failure("") == "failed_blocked"


class TestProcError:
    def test_returns_first_failed_stage_error(self):
        proc = MagicMock()
        proc.stages = [_stage(True), _stage(False, "boom"), _stage(False, "later")]
        assert _proc_error(proc) == "boom"

    def test_returns_empty_when_all_stages_ok(self):
        proc = MagicMock()
        proc.stages = [_stage(True)]
        assert _proc_error(proc) == ""


class TestAcquireFullText:
    @pytest.mark.asyncio
    async def test_first_candidate_success_returns_full_text(self):
        """Published source yields usable text → success, no fallback needed."""
        pipeline = MagicMock()
        pipeline.process_pdf_url = AsyncMock(return_value=_ok_proc())
        paper = _paper(pdf_url="https://pub.example/paper.pdf")

        outcome = await _acquire_full_text(pipeline, paper)

        assert outcome.reason is None
        assert outcome.proc is not None
        assert len(outcome.section_text) >= MIN_USABLE_CHARS
        pipeline.process_pdf_url.assert_awaited_once()
        assert pipeline.process_pdf_url.await_args.kwargs["url"] == "https://pub.example/paper.pdf"

    @pytest.mark.asyncio
    async def test_published_blocked_falls_back_to_arxiv(self):
        """Published URL fails → arXiv fallback is tried and succeeds."""
        pipeline = MagicMock()
        pipeline.process_pdf_url = AsyncMock(
            side_effect=[_failed_proc("Error downloading PDF: Server disconnected"), _ok_proc()]
        )
        paper = _paper(pdf_url="https://ojs.aaai.org/paper.pdf", arxiv="2309.01431")

        outcome = await _acquire_full_text(pipeline, paper)

        assert outcome.reason is None
        assert pipeline.process_pdf_url.await_count == 2
        # published attempted first, arXiv second
        urls = [c.kwargs["url"] for c in pipeline.process_pdf_url.await_args_list]
        assert urls == ["https://ojs.aaai.org/paper.pdf", "https://arxiv.org/pdf/2309.01431"]

    @pytest.mark.asyncio
    async def test_all_candidates_blocked_returns_failed_blocked(self):
        """Every candidate fails with a connection error → failed_blocked, no text."""
        pipeline = MagicMock()
        pipeline.process_pdf_url = AsyncMock(return_value=_failed_proc())
        paper = _paper(pdf_url="https://pub/paper.pdf", arxiv="2309.01431")

        outcome = await _acquire_full_text(pipeline, paper)

        assert outcome.proc is None
        assert outcome.section_text == ""
        assert outcome.reason == "failed_blocked"

    @pytest.mark.asyncio
    async def test_404_on_only_candidate_returns_failed_404(self):
        pipeline = MagicMock()
        pipeline.process_pdf_url = AsyncMock(
            return_value=_failed_proc("HTTP error downloading PDF: 404")
        )
        paper = _paper(pdf_url="https://pub/dead.pdf")

        outcome = await _acquire_full_text(pipeline, paper)

        assert outcome.reason == "failed_404"

    @pytest.mark.asyncio
    async def test_text_at_threshold_is_accepted(self):
        """Boundary: exactly MIN_USABLE_CHARS of text is usable (>= not >)."""
        pipeline = MagicMock()
        pipeline.process_pdf_url = AsyncMock(return_value=_ok_proc(text="a" * MIN_USABLE_CHARS))
        paper = _paper(pdf_url="https://pub/paper.pdf")

        outcome = await _acquire_full_text(pipeline, paper)

        assert outcome.reason is None

    @pytest.mark.asyncio
    async def test_text_one_below_threshold_is_thin(self):
        """Boundary: one char below MIN_USABLE_CHARS is failed_thin."""
        pipeline = MagicMock()
        pipeline.process_pdf_url = AsyncMock(
            return_value=_ok_proc(text="a" * (MIN_USABLE_CHARS - 1))
        )
        paper = _paper(pdf_url="https://pub/paper.pdf")

        outcome = await _acquire_full_text(pipeline, paper)

        assert outcome.reason == "failed_thin"

    @pytest.mark.asyncio
    async def test_thin_text_is_failed_thin_no_fallback_to_abstract(self):
        """A PDF that extracts too little text → failed_thin (never abstract)."""
        pipeline = MagicMock()
        pipeline.process_pdf_url = AsyncMock(return_value=_ok_proc(text="tiny"))
        paper = _paper(pdf_url="https://pub/scanned.pdf")

        outcome = await _acquire_full_text(pipeline, paper)

        assert outcome.reason == "failed_thin"
        assert outcome.section_text == ""


# =============================================================================
# SEG-1 AC-13 — partial segmentation is reported, not just thin segmentation
# See llm/features/seg1-roman-numeral-headings.md
# =============================================================================


def _seg_with_types(*type_values):
    """A SegmentedDocument-alike whose sections carry the given type values."""
    sections = []
    for value in type_values:
        section = MagicMock()
        section.section_type = MagicMock()
        section.section_type.value = value
        section.content = f"{value} body text"
        sections.append(section)
    seg = MagicMock()
    seg.sections = sections
    return seg


class TestMissingWantedSections:
    """The pure helper behind the AC-13 warning."""

    def test_all_four_present_reports_nothing(self):
        seg = _seg_with_types(
            "abstract", "introduction", "methods", "experiments"
        )

        assert _missing_wanted_sections(seg) == []

    def test_reports_in_canonical_prompt_order(self):
        """Order matches _EXTRACTOR_WANTED_SECTIONS so the log reads the way
        a human reads a paper, not in set-iteration order."""
        seg = _seg_with_types("introduction")

        assert _missing_wanted_sections(seg) == [
            "abstract",
            "methods",
            "experiments",
        ]

    def test_cskg_shape_reports_abstract_and_methods(self):
        """The real still-silent case: cskg clears MIN_USABLE_CHARS at 8,917
        chars with neither an abstract nor a methods section."""
        seg = _seg_with_types(
            "introduction", "related_work", "experiments", "conclusion",
            "references",
        )

        assert _missing_wanted_sections(seg) == ["abstract", "methods"]

    def test_discarded_types_do_not_count_as_found(self):
        seg = _seg_with_types("references", "acknowledgments")

        assert _missing_wanted_sections(seg) == list(
            _EXTRACTOR_WANTED_SECTIONS
        )

    def test_type_matching_is_case_insensitive(self):
        seg = _seg_with_types(
            "ABSTRACT", "Introduction", "METHODS", "Experiments"
        )

        assert _missing_wanted_sections(seg) == []

    def test_none_document_reports_everything_missing(self):
        assert _missing_wanted_sections(None) == list(
            _EXTRACTOR_WANTED_SECTIONS
        )

    def test_empty_sections_reports_everything_missing(self):
        seg = MagicMock()
        seg.sections = []

        assert _missing_wanted_sections(seg) == list(
            _EXTRACTOR_WANTED_SECTIONS
        )

    def test_section_type_as_plain_string_is_handled(self):
        """Defensive: a pinned object whose section_type is a bare str rather
        than a SectionType enum."""
        section = MagicMock()
        section.section_type = "abstract"
        section.content = "body"
        seg = MagicMock()
        seg.sections = [section]

        assert "abstract" not in _missing_wanted_sections(seg)


class TestFoundSectionTypes:
    """Companion helper — what the warning reports as *found*."""

    def test_returns_lowercased_type_values(self):
        seg = _seg_with_types("Abstract", "INTRODUCTION")

        assert _found_section_types(seg) == {"abstract", "introduction"}

    def test_none_document_is_empty_set(self):
        assert _found_section_types(None) == set()

    def test_empty_sections_is_empty_set(self):
        seg = MagicMock()
        seg.sections = []

        assert _found_section_types(seg) == set()

    def test_falsy_type_value_is_skipped(self):
        """A section with no resolvable type contributes nothing rather than
        polluting the set with an empty string."""
        section = MagicMock()
        section.section_type = None
        section.content = "body"
        seg = MagicMock()
        seg.sections = [section]

        assert _found_section_types(seg) == set()

    def test_empty_content_section_counts_as_not_found(self):
        """Adversarial review (Phase 5): a typed section with no content is
        skipped by _build_extractor_section_text, so counting it as "found"
        would suppress the warning on a document that contributes nothing.
        """
        section = MagicMock()
        section.section_type = MagicMock()
        section.section_type.value = "abstract"
        section.content = "   "
        seg = MagicMock()
        seg.sections = [section]

        assert "abstract" in _missing_wanted_sections(seg)
        assert _found_section_types(seg) == set()

    def test_agrees_with_the_extractor_text_builder(self):
        """The two must not disagree about what the extractors will see: any
        type reported as found has to contribute characters."""
        from agentic_kg.ingestion import _build_extractor_section_text

        section = MagicMock()
        section.section_type = MagicMock()
        section.section_type.value = "methods"
        section.content = ""
        empty = MagicMock()
        empty.sections = [section]

        assert _build_extractor_section_text(empty) == ""
        assert _found_section_types(empty) == set()
        assert _missing_wanted_sections(empty) == list(
            _EXTRACTOR_WANTED_SECTIONS
        )


class TestPaperHasFootprint:
    """Pre-existing purge-gate helper, uncovered until SEG-1's verify gate.

    It decides whether the AC-13 purge-then-rewrite step runs at all: a
    metadata-only paper has nothing to purge.
    """

    @staticmethod
    def _repo(row):
        session = MagicMock()
        session.run.return_value.single.return_value = row
        repo = MagicMock()
        repo.session.return_value.__enter__.return_value = session
        return repo, session

    def test_true_when_mentions_exist(self):
        repo, _ = self._repo({"n": 3})

        assert _paper_has_footprint(repo, "10.1/a") is True

    def test_false_when_count_is_zero(self):
        repo, _ = self._repo({"n": 0})

        assert _paper_has_footprint(repo, "10.1/a") is False

    def test_false_when_paper_absent(self):
        """`.single()` returns None when the MATCH yields no row."""
        repo, _ = self._repo(None)

        assert _paper_has_footprint(repo, "10.1/missing") is False

    def test_queries_by_the_given_doi(self):
        repo, session = self._repo({"n": 1})

        _paper_has_footprint(repo, "10.1/specific")

        assert session.run.call_args.kwargs["doi"] == "10.1/specific"
