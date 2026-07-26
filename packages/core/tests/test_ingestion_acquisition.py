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
    MIN_USABLE_CHARS,
    _acquire_full_text,
    _classify_pdf_failure,
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
