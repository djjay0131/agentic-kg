"""E-8 follow-up — ingest_papers wires --force-rewrite to purge_paper_extraction.

Mocked-pipeline tests confirm the wiring contract: papers with existing
extraction footprint get purged before re-extraction; PurgeBlocked
guardrail surfaces as a skip+error rather than a crash.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agentic_kg.extraction.re_ingestion import PurgeBlocked
from agentic_kg.ingestion import ingest_papers


@pytest.fixture
def patched_pipeline_chain():
    """Patch every collaborator ingest_papers touches so we can drive its
    branches with mocks. Returns the collection of patches for assertion.
    """
    with (
        patch("agentic_kg.ingestion.get_paper_aggregator") as agg_p,
        patch("agentic_kg.ingestion.get_paper_importer") as imp_p,
        patch("agentic_kg.ingestion.get_pipeline") as pipe_p,
        patch("agentic_kg.ingestion.KGIntegratorV2") as kg_p,
        patch("agentic_kg.ingestion.get_repository") as repo_p,
        patch("agentic_kg.ingestion.purge_paper_extraction") as purge_p,
        patch("agentic_kg.ingestion._paper_has_footprint") as footprint_p,
        patch("agentic_kg.ingestion.run_sanity_checks") as sanity_p,
    ):
        # Aggregator: one paper with a PDF URL.
        paper = MagicMock()
        paper.doi = "10.1/abc"
        paper.title = "Test"
        paper.pdf_url = "https://example/p.pdf"
        paper.authors = []
        search = MagicMock(papers=[paper])
        agg_p.return_value.search_papers = AsyncMock(return_value=search)

        # Importer.
        imp_p.return_value.batch_import = AsyncMock(
            return_value=MagicMock(created=0, updated=1)
        )

        # Pipeline: extract returns a result with one problem.
        proc = MagicMock()
        proc.success = True
        proc.problem_count = 1
        proc.get_high_confidence_problems.return_value = [MagicMock()]
        pipe_p.return_value.process_pdf_url = AsyncMock(return_value=proc)

        # KGIntegratorV2: integration returns mock counts.
        integration = MagicMock()
        integration.mentions_created = 1
        integration.mentions_new_concepts = 0
        integration.mentions_linked = 1
        kg_p.return_value.integrate_extracted_problems.return_value = integration

        # Repository is a passthrough mock.
        repo_p.return_value = MagicMock()

        # Sanity checks return empty list (tests don't care).
        sanity_p.return_value = []

        yield {
            "purge": purge_p,
            "footprint": footprint_p,
            "pipeline": pipe_p,
            "integrator": kg_p,
        }


class TestForceRewriteWiring:
    @pytest.mark.asyncio
    async def test_paper_with_footprint_is_purged_when_force_rewrite(
        self, patched_pipeline_chain
    ):
        patched_pipeline_chain["footprint"].return_value = True
        patched_pipeline_chain["purge"].return_value = MagicMock(
            problems_deleted=2, mentions_deleted=3
        )

        result = await ingest_papers(
            query="test", force_rewrite=True
        )
        patched_pipeline_chain["purge"].assert_called_once()
        # force_rewrite was passed through.
        kwargs = patched_pipeline_chain["purge"].call_args.kwargs
        assert kwargs["force_rewrite"] is True
        assert result.papers_purged == 1

    @pytest.mark.asyncio
    async def test_paper_without_footprint_is_not_purged(
        self, patched_pipeline_chain
    ):
        patched_pipeline_chain["footprint"].return_value = False

        await ingest_papers(query="test", force_rewrite=True)
        patched_pipeline_chain["purge"].assert_not_called()

    @pytest.mark.asyncio
    async def test_purge_blocked_records_skip_not_crash(
        self, patched_pipeline_chain
    ):
        patched_pipeline_chain["footprint"].return_value = True
        patched_pipeline_chain["purge"].side_effect = PurgeBlocked(
            paper_doi="10.1/abc",
            blocking_edges=[
                {
                    "problem_id": "p-1",
                    "relationship_type": "SOLVED_BY",
                    "other_node": "x",
                }
            ],
        )

        result = await ingest_papers(query="test", force_rewrite=False)
        # The paper was NOT extracted (purge guardrail refused).
        assert "10.1/abc" in result.extraction_errors
        assert "SOLVED_BY" in result.extraction_errors["10.1/abc"]
        # No extraction or integration call happened for the skipped paper.
        patched_pipeline_chain["pipeline"].return_value.process_pdf_url.assert_not_called()
        assert result.papers_blocked_by_guardrail == 1

    @pytest.mark.asyncio
    async def test_force_rewrite_default_false(self, patched_pipeline_chain):
        """When the caller omits force_rewrite, the flag passed downstream
        to purge_paper_extraction is False (preserving the AC-13 guardrail)."""
        patched_pipeline_chain["footprint"].return_value = True
        patched_pipeline_chain["purge"].return_value = MagicMock(
            problems_deleted=0, mentions_deleted=0
        )

        await ingest_papers(query="test")
        kwargs = patched_pipeline_chain["purge"].call_args.kwargs
        assert kwargs["force_rewrite"] is False
