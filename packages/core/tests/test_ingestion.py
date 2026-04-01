"""
Unit tests for the ingestion orchestration module.

Tests the end-to-end paper ingestion workflow:
search → import → extract → integrate → sanity checks.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone

from agentic_kg.ingestion import (
    IngestionResult,
    SanityCheck,
    ingest_papers,
    run_sanity_checks,
    _notify,
)


# =============================================================================
# Model Tests
# =============================================================================


class TestSanityCheck:
    """Tests for SanityCheck model."""

    def test_passing_check(self):
        check = SanityCheck(name="test", passed=True, count=0, description="All good")
        assert check.passed is True
        assert check.count == 0

    def test_failing_check(self):
        check = SanityCheck(name="test", passed=False, count=5, description="5 violations")
        assert check.passed is False
        assert check.count == 5


class TestIngestionResult:
    """Tests for IngestionResult model."""

    def test_default_values(self):
        result = IngestionResult(trace_id="test-001", query="test query")
        assert result.status == "pending"
        assert result.papers_found == 0
        assert result.papers_imported == 0
        assert result.papers_extracted == 0
        assert result.papers_skipped_no_pdf == 0
        assert result.total_problems == 0
        assert result.concepts_created == 0
        assert result.concepts_linked == 0
        assert result.sanity_checks == []
        assert result.dry_run_papers == []
        assert result.extraction_errors == {}
        assert result.error is None

    def test_with_values(self):
        result = IngestionResult(
            trace_id="test-001",
            query="graph retrieval",
            status="completed",
            papers_found=20,
            papers_imported=18,
            papers_extracted=14,
            total_problems=47,
        )
        assert result.papers_found == 20
        assert result.total_problems == 47


class TestNotify:
    """Tests for _notify helper."""

    def test_calls_callback(self):
        cb = MagicMock()
        _notify(cb, "phase1", "10.1234/test", {"count": 5})
        cb.assert_called_once_with("phase1", "10.1234/test", {"count": 5})

    def test_none_callback_no_error(self):
        _notify(None, "phase1", "10.1234/test")

    def test_callback_exception_suppressed(self):
        cb = MagicMock(side_effect=RuntimeError("boom"))
        _notify(cb, "phase1", None)  # Should not raise


# =============================================================================
# Helpers
# =============================================================================


def _make_normalized_paper(doi="10.1234/test", title="Test Paper", pdf_url="https://example.com/paper.pdf"):
    """Create a mock NormalizedPaper."""
    p = MagicMock()
    p.doi = doi
    p.title = title
    p.pdf_url = pdf_url
    p.authors = [MagicMock(name="Author A")]
    p.authors[0].name = "Author A"
    return p


def _make_search_result(papers):
    """Create a mock SearchResult."""
    sr = MagicMock()
    sr.papers = papers
    return sr


def _make_batch_import_result(created=0, updated=0):
    """Create a mock BatchImportResult."""
    r = MagicMock()
    r.created = created
    r.updated = updated
    r.to_dict.return_value = {"created": created, "updated": updated}
    return r


def _make_processing_result(success=True, problem_count=3, confidence=0.8):
    """Create a mock PaperProcessingResult."""
    r = MagicMock()
    r.success = success
    r.problem_count = problem_count
    r.paper_title = "Test Paper"
    problems = []
    for i in range(problem_count):
        p = MagicMock()
        p.confidence = confidence
        p.statement = f"Problem {i}"
        problems.append(p)
    r.get_high_confidence_problems.return_value = problems
    r.get_problems.return_value = problems
    return r


def _make_integration_result(mentions_created=3, new_concepts=2, linked=1):
    """Create a mock IntegrationResultV2."""
    r = MagicMock()
    r.mentions_created = mentions_created
    r.mentions_new_concepts = new_concepts
    r.mentions_linked = linked
    r.errors = []
    return r


# =============================================================================
# ingest_papers Tests
# =============================================================================


class TestIngestPapers:
    """Tests for the ingest_papers orchestration function."""

    @pytest.mark.asyncio
    async def test_dry_run_returns_papers_without_writing(self):
        """Dry run searches but does not import, extract, or integrate."""
        papers = [_make_normalized_paper(), _make_normalized_paper(doi="10.1234/test2")]
        search_result = _make_search_result(papers)

        with patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg:
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)

            result = await ingest_papers("test query", limit=10, dry_run=True)

        assert result.status == "dry_run"
        assert result.papers_found == 2
        assert len(result.dry_run_papers) == 2
        assert result.dry_run_papers[0]["doi"] == "10.1234/test"
        assert result.papers_imported == 0
        assert result.papers_extracted == 0

    @pytest.mark.asyncio
    async def test_empty_search_results(self):
        """Empty search returns completed with zero counts."""
        search_result = _make_search_result([])

        with (
            patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
            patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
            patch("agentic_kg.ingestion.get_pipeline") as mock_pipe,
            patch("agentic_kg.ingestion.KGIntegratorV2"),
            patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
        ):
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)
            mock_imp.return_value.batch_import = AsyncMock(
                return_value=_make_batch_import_result()
            )

            result = await ingest_papers("obscure query", limit=10)

        assert result.status == "completed"
        assert result.papers_found == 0
        assert result.papers_extracted == 0

    @pytest.mark.asyncio
    async def test_full_pipeline_success(self):
        """Full pipeline: search → import → extract → integrate → sanity checks."""
        papers = [
            _make_normalized_paper(doi="10.1/a", title="Paper A", pdf_url="https://a.pdf"),
            _make_normalized_paper(doi="10.1/b", title="Paper B", pdf_url="https://b.pdf"),
        ]
        search_result = _make_search_result(papers)
        import_result = _make_batch_import_result(created=2)
        proc_result = _make_processing_result(success=True, problem_count=3)
        int_result = _make_integration_result(mentions_created=3, new_concepts=2, linked=1)
        sanity = [SanityCheck(name="test", passed=True, count=0, description="ok")]

        with (
            patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
            patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
            patch("agentic_kg.ingestion.get_pipeline") as mock_pipe,
            patch("agentic_kg.ingestion.KGIntegratorV2") as mock_intg,
            patch("agentic_kg.ingestion.run_sanity_checks", return_value=sanity),
        ):
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)
            mock_imp.return_value.batch_import = AsyncMock(return_value=import_result)
            mock_pipe.return_value.process_pdf_url = AsyncMock(return_value=proc_result)
            mock_intg.return_value.integrate_extracted_problems.return_value = int_result

            result = await ingest_papers("graph-based retrieval", limit=20)

        assert result.status == "completed"
        assert result.papers_found == 2
        assert result.papers_imported == 2
        assert result.papers_extracted == 2
        assert result.total_problems == 6  # 3 per paper * 2 papers
        assert result.concepts_created == 4  # 2 per paper * 2
        assert result.concepts_linked == 2  # 1 per paper * 2
        assert len(result.sanity_checks) == 1

    @pytest.mark.asyncio
    async def test_papers_without_pdf_url_skipped(self):
        """Papers without pdf_url are imported but not extracted."""
        papers = [
            _make_normalized_paper(doi="10.1/a", pdf_url="https://a.pdf"),
            _make_normalized_paper(doi="10.1/b", pdf_url=None),  # No PDF
        ]
        search_result = _make_search_result(papers)
        import_result = _make_batch_import_result(created=2)
        proc_result = _make_processing_result(success=True, problem_count=2)
        int_result = _make_integration_result(mentions_created=2, new_concepts=1, linked=1)

        with (
            patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
            patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
            patch("agentic_kg.ingestion.get_pipeline") as mock_pipe,
            patch("agentic_kg.ingestion.KGIntegratorV2") as mock_intg,
            patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
        ):
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)
            mock_imp.return_value.batch_import = AsyncMock(return_value=import_result)
            mock_pipe.return_value.process_pdf_url = AsyncMock(return_value=proc_result)
            mock_intg.return_value.integrate_extracted_problems.return_value = int_result

            result = await ingest_papers("test", limit=10)

        assert result.papers_skipped_no_pdf == 1
        assert result.papers_extracted == 1
        # Pipeline should only be called for paper with PDF
        mock_pipe.return_value.process_pdf_url.assert_called_once()

    @pytest.mark.asyncio
    async def test_papers_without_doi_skipped_for_import(self):
        """Papers without DOI are not passed to batch_import."""
        papers = [
            _make_normalized_paper(doi=None, pdf_url="https://a.pdf"),
            _make_normalized_paper(doi="10.1/b", pdf_url="https://b.pdf"),
        ]
        search_result = _make_search_result(papers)
        import_result = _make_batch_import_result(created=1)
        proc_result = _make_processing_result(success=True, problem_count=2)
        int_result = _make_integration_result(mentions_created=2, new_concepts=1, linked=1)

        with (
            patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
            patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
            patch("agentic_kg.ingestion.get_pipeline") as mock_pipe,
            patch("agentic_kg.ingestion.KGIntegratorV2") as mock_intg,
            patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
        ):
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)
            mock_imp.return_value.batch_import = AsyncMock(return_value=import_result)
            mock_pipe.return_value.process_pdf_url = AsyncMock(return_value=proc_result)
            mock_intg.return_value.integrate_extracted_problems.return_value = int_result

            result = await ingest_papers("test", limit=10)

        # Only doi "10.1/b" should be passed to batch_import
        mock_imp.return_value.batch_import.assert_called_once_with(
            ["10.1/b"], create_authors=True
        )

    @pytest.mark.asyncio
    async def test_extraction_failure_continues_other_papers(self):
        """If extraction fails for one paper, others still process."""
        papers = [
            _make_normalized_paper(doi="10.1/a", title="A", pdf_url="https://a.pdf"),
            _make_normalized_paper(doi="10.1/b", title="B", pdf_url="https://b.pdf"),
        ]
        search_result = _make_search_result(papers)
        import_result = _make_batch_import_result(created=2)
        proc_result = _make_processing_result(success=True, problem_count=2)
        int_result = _make_integration_result(mentions_created=2, new_concepts=1, linked=1)

        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("PDF download failed")
            return proc_result

        with (
            patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
            patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
            patch("agentic_kg.ingestion.get_pipeline") as mock_pipe,
            patch("agentic_kg.ingestion.KGIntegratorV2") as mock_intg,
            patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
        ):
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)
            mock_imp.return_value.batch_import = AsyncMock(return_value=import_result)
            mock_pipe.return_value.process_pdf_url = AsyncMock(side_effect=side_effect)
            mock_intg.return_value.integrate_extracted_problems.return_value = int_result

            result = await ingest_papers("test", limit=10)

        assert result.papers_extracted == 1
        assert "10.1/a" in result.extraction_errors
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_extraction_no_problems_not_integrated(self):
        """Papers with 0 extracted problems are not integrated."""
        papers = [_make_normalized_paper(doi="10.1/a", pdf_url="https://a.pdf")]
        search_result = _make_search_result(papers)
        import_result = _make_batch_import_result(created=1)
        proc_result = _make_processing_result(success=True, problem_count=0)

        with (
            patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
            patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
            patch("agentic_kg.ingestion.get_pipeline") as mock_pipe,
            patch("agentic_kg.ingestion.KGIntegratorV2") as mock_intg,
            patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
        ):
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)
            mock_imp.return_value.batch_import = AsyncMock(return_value=import_result)
            mock_pipe.return_value.process_pdf_url = AsyncMock(return_value=proc_result)

            result = await ingest_papers("test", limit=10)

        assert result.papers_extracted == 0
        mock_intg.return_value.integrate_extracted_problems.assert_not_called()

    @pytest.mark.asyncio
    async def test_integration_failure_recorded(self):
        """Integration failure for one paper is recorded, others continue."""
        papers = [_make_normalized_paper(doi="10.1/a", pdf_url="https://a.pdf")]
        search_result = _make_search_result(papers)
        import_result = _make_batch_import_result(created=1)
        proc_result = _make_processing_result(success=True, problem_count=3)

        with (
            patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
            patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
            patch("agentic_kg.ingestion.get_pipeline") as mock_pipe,
            patch("agentic_kg.ingestion.KGIntegratorV2") as mock_intg,
            patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
        ):
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)
            mock_imp.return_value.batch_import = AsyncMock(return_value=import_result)
            mock_pipe.return_value.process_pdf_url = AsyncMock(return_value=proc_result)
            mock_intg.return_value.integrate_extracted_problems.side_effect = RuntimeError(
                "Neo4j down"
            )

            result = await ingest_papers("test", limit=10)

        assert "10.1/a" in result.extraction_errors
        assert "Integration failed" in result.extraction_errors["10.1/a"]
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_search_failure_returns_failed(self):
        """If the search itself fails, result status is 'failed'."""
        with patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg:
            mock_agg.return_value.search_papers = AsyncMock(
                side_effect=RuntimeError("API unreachable")
            )

            result = await ingest_papers("test", limit=10)

        assert result.status == "failed"
        assert "API unreachable" in result.error

    @pytest.mark.asyncio
    async def test_progress_callback_called(self):
        """Progress callback is called at each phase."""
        papers = [_make_normalized_paper(doi="10.1/a", pdf_url="https://a.pdf")]
        search_result = _make_search_result(papers)
        import_result = _make_batch_import_result(created=1)
        proc_result = _make_processing_result(success=True, problem_count=2)
        int_result = _make_integration_result(mentions_created=2, new_concepts=1, linked=1)
        progress_cb = MagicMock()

        with (
            patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
            patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
            patch("agentic_kg.ingestion.get_pipeline") as mock_pipe,
            patch("agentic_kg.ingestion.KGIntegratorV2") as mock_intg,
            patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
        ):
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)
            mock_imp.return_value.batch_import = AsyncMock(return_value=import_result)
            mock_pipe.return_value.process_pdf_url = AsyncMock(return_value=proc_result)
            mock_intg.return_value.integrate_extracted_problems.return_value = int_result

            await ingest_papers("test", limit=10, on_progress=progress_cb)

        phases_called = [c[0][0] for c in progress_cb.call_args_list]
        assert "search_complete" in phases_called
        assert "metadata_imported" in phases_called
        assert "extracted" in phases_called
        assert "integrated" in phases_called

    @pytest.mark.asyncio
    async def test_sources_parameter_passed_through(self):
        """Sources parameter is forwarded to aggregator.search_papers."""
        search_result = _make_search_result([])

        with (
            patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
            patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
            patch("agentic_kg.ingestion.get_pipeline"),
            patch("agentic_kg.ingestion.KGIntegratorV2"),
            patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
        ):
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)
            mock_imp.return_value.batch_import = AsyncMock(
                return_value=_make_batch_import_result()
            )

            await ingest_papers("test", limit=5, sources=["arxiv"])

        mock_agg.return_value.search_papers.assert_called_once_with(
            "test", sources=["arxiv"], limit=5
        )

    @pytest.mark.asyncio
    async def test_low_confidence_problems_filtered(self):
        """Problems below min_extraction_confidence are not integrated."""
        papers = [_make_normalized_paper(doi="10.1/a", pdf_url="https://a.pdf")]
        search_result = _make_search_result(papers)
        import_result = _make_batch_import_result(created=1)
        proc_result = _make_processing_result(success=True, problem_count=3, confidence=0.8)
        # get_high_confidence_problems returns empty when threshold is high
        proc_result.get_high_confidence_problems.return_value = []

        with (
            patch("agentic_kg.ingestion.get_paper_aggregator") as mock_agg,
            patch("agentic_kg.ingestion.get_paper_importer") as mock_imp,
            patch("agentic_kg.ingestion.get_pipeline") as mock_pipe,
            patch("agentic_kg.ingestion.KGIntegratorV2") as mock_intg,
            patch("agentic_kg.ingestion.run_sanity_checks", return_value=[]),
        ):
            mock_agg.return_value.search_papers = AsyncMock(return_value=search_result)
            mock_imp.return_value.batch_import = AsyncMock(return_value=import_result)
            mock_pipe.return_value.process_pdf_url = AsyncMock(return_value=proc_result)

            result = await ingest_papers("test", min_extraction_confidence=0.95)

        mock_intg.return_value.integrate_extracted_problems.assert_not_called()
        assert result.total_problems == 0


# =============================================================================
# run_sanity_checks Tests
# =============================================================================


class TestRunSanityChecks:
    """Tests for run_sanity_checks function."""

    def test_all_checks_pass(self):
        """All sanity checks pass on a healthy graph."""
        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        # All counts return 0 (no violations), except graph_populated
        run_results = iter([
            MagicMock(single=MagicMock(return_value={"cnt": 0})),  # mentions_have_instance_of
            MagicMock(single=MagicMock(return_value={"cnt": 0})),  # mentions_linked_to_paper
            MagicMock(single=MagicMock(return_value={"cnt": 0})),  # papers_have_authors
            MagicMock(single=MagicMock(return_value={"cnt": 0})),  # no_orphan_concepts
            MagicMock(single=MagicMock(return_value={"cnt": 50})),  # node count
            MagicMock(single=MagicMock(return_value={"cnt": 75})),  # edge count
        ])
        mock_session.run.side_effect = lambda q: next(run_results)

        checks = run_sanity_checks(repository=mock_repo)

        assert len(checks) == 5
        assert all(c.passed for c in checks)
        assert checks[4].description == "50 nodes, 75 edges"

    def test_orphan_mentions_detected(self):
        """Detects ProblemMentions without INSTANCE_OF edge."""
        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        run_results = iter([
            MagicMock(single=MagicMock(return_value={"cnt": 3})),  # 3 orphan mentions
            MagicMock(single=MagicMock(return_value={"cnt": 0})),
            MagicMock(single=MagicMock(return_value={"cnt": 0})),
            MagicMock(single=MagicMock(return_value={"cnt": 0})),
            MagicMock(single=MagicMock(return_value={"cnt": 10})),
            MagicMock(single=MagicMock(return_value={"cnt": 15})),
        ])
        mock_session.run.side_effect = lambda q: next(run_results)

        checks = run_sanity_checks(repository=mock_repo)

        assert checks[0].name == "mentions_have_instance_of"
        assert checks[0].passed is False
        assert checks[0].count == 3

    def test_empty_graph_detected(self):
        """Detects when graph has no nodes."""
        mock_session = MagicMock()
        mock_repo = MagicMock()
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        run_results = iter([
            MagicMock(single=MagicMock(return_value={"cnt": 0})),
            MagicMock(single=MagicMock(return_value={"cnt": 0})),
            MagicMock(single=MagicMock(return_value={"cnt": 0})),
            MagicMock(single=MagicMock(return_value={"cnt": 0})),
            MagicMock(single=MagicMock(return_value={"cnt": 0})),  # 0 nodes
            MagicMock(single=MagicMock(return_value={"cnt": 0})),  # 0 edges
        ])
        mock_session.run.side_effect = lambda q: next(run_results)

        checks = run_sanity_checks(repository=mock_repo)

        assert checks[4].name == "graph_populated"
        assert checks[4].passed is False

    def test_neo4j_connection_failure(self):
        """Returns connectivity failure check on exception."""
        mock_repo = MagicMock()
        mock_repo.session.side_effect = RuntimeError("Connection refused")

        checks = run_sanity_checks(repository=mock_repo)

        assert len(checks) == 1
        assert checks[0].name == "connectivity"
        assert checks[0].passed is False
        assert "Connection refused" in checks[0].description
