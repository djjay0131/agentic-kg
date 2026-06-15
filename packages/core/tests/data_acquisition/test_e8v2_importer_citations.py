"""E-8 V2 Unit 6 — PaperImporter.import_paper populate_citations wiring.

Covers AC-10 (default-on create), AC-11 (update path), AC-12 (exception
absorbed).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agentic_kg.data_acquisition.aggregator import AggregatedResult
from agentic_kg.data_acquisition.importer import (
    ImportResult,
    PaperImporter,
)
from agentic_kg.data_acquisition.normalizer import NormalizedPaper
from agentic_kg.knowledge_graph.repository import (
    NotFoundError as RepoNotFoundError,
)


def _normalized(doi: str = "10.1/abc", title: str = "A paper") -> NormalizedPaper:
    return NormalizedPaper(
        doi=doi,
        title=title,
        authors=[],
        venue=None,
        year=2024,
        abstract=None,
        external_ids={},
        pdf_url=None,
        source="openalex",
    )


@pytest.fixture
def aggregator():
    agg = MagicMock()
    agg.get_paper = AsyncMock(return_value=AggregatedResult(
        paper=_normalized(),
        sources=["openalex"],
        errors={},
    ))
    return agg


@pytest.fixture
def repository():
    repo = MagicMock()
    # Default: paper doesn't exist (creates fresh).
    repo.get_paper.side_effect = RepoNotFoundError("not in graph")
    repo.create_paper.return_value = MagicMock(doi="10.1/abc")
    repo.update_paper.return_value = MagicMock(doi="10.1/abc")
    return repo


@pytest.fixture
def importer(aggregator, repository) -> PaperImporter:
    return PaperImporter(aggregator=aggregator, repository=repository)


# =============================================================================
# AC-10 — populate_citations default-on at create
# =============================================================================


class TestPopulateCitationsDefaultOnCreate:
    @pytest.mark.asyncio
    async def test_populate_called_on_create(self, importer):
        with patch(
            "agentic_kg.knowledge_graph.citation_graph.populate_citations",
            new=AsyncMock(return_value=MagicMock(stubs_created=2)),
        ) as mock_populate:
            result = await importer.import_paper(
                "10.1/abc", s2_client=MagicMock(),
            )

        assert result.created is True
        mock_populate.assert_awaited_once()
        kwargs = mock_populate.await_args.kwargs
        assert kwargs["paper_doi"] == "10.1/abc"
        # ImportResult carries the populate result.
        assert result.citation_population is not None
        assert result.citation_population.stubs_created == 2

    @pytest.mark.asyncio
    async def test_populate_skipped_when_flag_false(self, importer):
        with patch(
            "agentic_kg.knowledge_graph.citation_graph.populate_citations",
            new=AsyncMock(),
        ) as mock_populate:
            result = await importer.import_paper(
                "10.1/abc",
                populate_citations=False,
                s2_client=MagicMock(),
            )
        assert result.created is True
        mock_populate.assert_not_called()
        assert result.citation_population is None

    @pytest.mark.asyncio
    async def test_populate_skipped_on_doi_missing(
        self, importer, aggregator,
    ):
        # Paper has no DOI → ImportResult.error → no paper persisted → no
        # populate call.
        aggregator.get_paper.return_value = AggregatedResult(
            paper=_normalized(doi=""),
            sources=["arxiv"],
            errors={},
        )
        with patch(
            "agentic_kg.knowledge_graph.citation_graph.populate_citations",
            new=AsyncMock(),
        ) as mock_populate:
            result = await importer.import_paper(
                "arxiv-id", s2_client=MagicMock(),
            )
        assert result.error is not None
        mock_populate.assert_not_called()


# =============================================================================
# AC-11 — populate_citations on update_existing=True
# =============================================================================


class TestPopulateCitationsOnUpdate:
    @pytest.mark.asyncio
    async def test_populate_called_on_update(self, importer, repository):
        # Paper exists → update_existing=True triggers update path.
        repository.get_paper.side_effect = None
        repository.get_paper.return_value = MagicMock(doi="10.1/abc")

        with patch(
            "agentic_kg.knowledge_graph.citation_graph.populate_citations",
            new=AsyncMock(return_value=MagicMock()),
        ) as mock_populate:
            result = await importer.import_paper(
                "10.1/abc",
                update_existing=True,
                s2_client=MagicMock(),
            )
        assert result.updated is True
        mock_populate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_populate_skipped_on_skip_path(self, importer, repository):
        # Paper exists, update_existing=False → ImportResult.skipped=True →
        # no populate call (operator's intent was a no-op).
        repository.get_paper.side_effect = None
        repository.get_paper.return_value = MagicMock(doi="10.1/abc")

        with patch(
            "agentic_kg.knowledge_graph.citation_graph.populate_citations",
            new=AsyncMock(),
        ) as mock_populate:
            result = await importer.import_paper(
                "10.1/abc",
                update_existing=False,
                s2_client=MagicMock(),
            )
        assert result.skipped is True
        mock_populate.assert_not_called()


# =============================================================================
# AC-12 — populate_citations exception absorbed
# =============================================================================


class TestPopulateCitationsExceptionAbsorbed:
    @pytest.mark.asyncio
    async def test_populate_unexpected_exception_does_not_propagate(
        self, importer, caplog,
    ):
        import logging

        with patch(
            "agentic_kg.knowledge_graph.citation_graph.populate_citations",
            new=AsyncMock(side_effect=RuntimeError("simulated")),
        ):
            with caplog.at_level(logging.ERROR):
                # Must not raise.
                result = await importer.import_paper(
                    "10.1/abc", s2_client=MagicMock(),
                )

        assert result.created is True
        assert result.paper is not None
        assert result.citation_population is None
        assert any(
            "populate_citations unexpectedly raised" in r.message
            and "10.1/abc" in r.message
            for r in caplog.records
        )


# =============================================================================
# Default kwarg sanity
# =============================================================================


class TestDefaultKwargs:
    def test_default_populate_citations_is_true(self):
        """Guards against future signature drift — the default must be True."""
        import inspect

        sig = inspect.signature(PaperImporter.import_paper)
        assert sig.parameters["populate_citations"].default is True

    def test_default_s2_client_is_none(self):
        import inspect

        sig = inspect.signature(PaperImporter.import_paper)
        assert sig.parameters["s2_client"].default is None

    def test_import_result_default_citation_population_none(self):
        r = ImportResult()
        assert r.citation_population is None


# =============================================================================
# Lazy S2 client construction
# =============================================================================


class TestS2ClientLazyInit:
    @pytest.mark.asyncio
    async def test_lazy_singleton_constructed_when_none_passed(
        self, importer,
    ):
        with patch(
            "agentic_kg.data_acquisition.semantic_scholar.SemanticScholarClient",
        ) as SSClient, patch(
            "agentic_kg.knowledge_graph.citation_graph.populate_citations",
            new=AsyncMock(return_value=MagicMock()),
        ) as mock_populate:
            await importer.import_paper("10.1/abc")  # no s2_client kwarg

        SSClient.assert_called_once()
        kwargs = mock_populate.await_args.kwargs
        # s2_client kwarg got the lazily-built sentinel.
        assert kwargs["s2_client"] is SSClient.return_value


# =============================================================================
# AC-18 — autouse conftest fixture is actually in effect
# =============================================================================


class TestAutouseStubFires:
    """Without explicitly patching populate_citations, the importer's
    citation hook must still NOT hit the live S2 helper — the conftest
    autouse fixture short-circuits to an empty result."""

    @pytest.mark.asyncio
    async def test_no_explicit_patch_returns_empty_citation_result(
        self, importer, _stub_populate_citations,
    ):
        result = await importer.import_paper(
            "10.1/abc", s2_client=MagicMock(),
        )

        # The autouse stub returned an empty CitationPopulationResult; the
        # importer attached it to the ImportResult.
        assert result.citation_population is not None
        assert result.citation_population.stubs_created == 0
        assert result.citation_population.edges_created == 0
        # And the stub was actually invoked.
        _stub_populate_citations.assert_awaited_once()
