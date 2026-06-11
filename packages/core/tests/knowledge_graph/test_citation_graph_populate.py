"""Unit tests for ``populate_citations`` (E-5 Unit 4).

Mocks the Semantic Scholar client and the repository, so no Neo4j or
OpenAI is needed. Integration coverage with live testcontainers + the
real `create_or_promote_paper_stub` lives in `test_citation_graph.py`
and `test_e5_done_demo.py`.

Covers:
- Reference list with mixed DOI / no-DOI entries (Spec AC-6)
- Source paper resolves via DOI when s2 id not provided
- Failure modes: s2 lookup error, references endpoint error, stub create
  failure, link failure (AC-11)
- Counts are populated correctly
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from agentic_kg.knowledge_graph.citation_graph import (
    CitationPopulationResult,
    populate_citations,
)
from agentic_kg.knowledge_graph.repository import NotFoundError


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.create_or_promote_paper_stub.return_value = (MagicMock(), True)
    repo.link_paper_cites_paper.return_value = True
    return repo


@pytest.fixture
def mock_s2_client():
    client = MagicMock()
    client.get_paper_by_doi = AsyncMock(return_value={"paperId": "S2_ABC"})
    client.get_paper_references = AsyncMock(
        return_value={"data": []},
    )
    return client


def _ref_entry(doi: str | None, title: str = "Ref title", year: int | None = 2023):
    ext_ids = {"DOI": doi} if doi else {}
    return {"citedPaper": {"externalIds": ext_ids, "title": title, "year": year}}


# =============================================================================
# Happy path
# =============================================================================


class TestPopulateCitationsHappyPath:
    @pytest.mark.asyncio
    async def test_creates_stubs_and_edges_for_each_doi_reference(
        self, mock_repo, mock_s2_client,
    ):
        mock_s2_client.get_paper_references.return_value = {
            "data": [
                _ref_entry("10.1/A", title="Ref A"),
                _ref_entry("10.1/B", title="Ref B", year=2022),
            ]
        }

        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )

        assert isinstance(result, CitationPopulationResult)
        assert mock_repo.create_or_promote_paper_stub.call_count == 2
        assert mock_repo.link_paper_cites_paper.call_count == 2
        # Both stubs reported as created (mock default).
        assert result.stubs_created == 2
        assert result.edges_created == 2
        assert result.skipped_no_doi == 0
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_skips_references_without_doi(
        self, mock_repo, mock_s2_client,
    ):
        mock_s2_client.get_paper_references.return_value = {
            "data": [
                _ref_entry("10.1/A"),
                _ref_entry(None, title="No DOI ref"),
                _ref_entry(None),
                _ref_entry("10.1/B"),
            ]
        }

        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )

        assert result.stubs_created == 2
        assert result.edges_created == 2
        assert result.skipped_no_doi == 2

    @pytest.mark.asyncio
    async def test_existing_stub_does_not_recount(
        self, mock_repo, mock_s2_client,
    ):
        """When create_or_promote returns (paper, False), the count of
        stubs_created stays at 0 — only newly-inserted stubs are counted."""
        mock_repo.create_or_promote_paper_stub.return_value = (MagicMock(), False)
        mock_s2_client.get_paper_references.return_value = {
            "data": [_ref_entry("10.1/A")]
        }

        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )

        assert result.stubs_created == 0
        assert result.edges_created == 1  # the link still attempts and succeeds


# =============================================================================
# S2 ID resolution
# =============================================================================


class TestS2IdResolution:
    @pytest.mark.asyncio
    async def test_looks_up_s2_id_when_not_provided(
        self, mock_repo, mock_s2_client,
    ):
        mock_s2_client.get_paper_references.return_value = {"data": []}
        await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
        )
        mock_s2_client.get_paper_by_doi.assert_awaited_once_with("10.1/SOURCE")

    @pytest.mark.asyncio
    async def test_skips_when_s2_id_lookup_fails(
        self, mock_repo, mock_s2_client,
    ):
        mock_s2_client.get_paper_by_doi.side_effect = RuntimeError("S2 down")

        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
        )

        assert result.skipped_no_s2_id is True
        assert result.stubs_created == 0
        assert result.edges_created == 0
        mock_s2_client.get_paper_references.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_s2_id_unresolvable(
        self, mock_repo, mock_s2_client,
    ):
        """Lookup succeeds but paper has no paperId — skip silently."""
        mock_s2_client.get_paper_by_doi.return_value = {}

        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
        )

        assert result.skipped_no_s2_id is True
        mock_s2_client.get_paper_references.assert_not_called()


# =============================================================================
# Failure modes — AC-11 (must not raise)
# =============================================================================


class TestFailureModes:
    @pytest.mark.asyncio
    async def test_get_paper_references_failure_does_not_raise(
        self, mock_repo, mock_s2_client,
    ):
        mock_s2_client.get_paper_references.side_effect = RuntimeError("timeout")

        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )

        assert result.fetch_failed is True
        assert result.stubs_created == 0
        assert result.edges_created == 0

    @pytest.mark.asyncio
    async def test_stub_create_failure_continues_other_references(
        self, mock_repo, mock_s2_client,
    ):
        mock_s2_client.get_paper_references.return_value = {
            "data": [
                _ref_entry("10.1/A"),
                _ref_entry("10.1/B"),
            ]
        }
        mock_repo.create_or_promote_paper_stub.side_effect = [
            RuntimeError("disk full"),
            (MagicMock(), True),
        ]

        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )

        # First reference failed at stub creation; second succeeded.
        assert result.stubs_created == 1
        assert result.edges_created == 1
        assert len(result.errors) == 1
        assert "stub_failed" in result.errors[0]

    @pytest.mark.asyncio
    async def test_link_failure_continues_other_references(
        self, mock_repo, mock_s2_client,
    ):
        mock_s2_client.get_paper_references.return_value = {
            "data": [
                _ref_entry("10.1/A"),
                _ref_entry("10.1/B"),
            ]
        }
        # Stubs succeed; first link raises NotFoundError.
        mock_repo.link_paper_cites_paper.side_effect = [
            NotFoundError("source vanished"),
            True,
        ]

        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )

        assert result.stubs_created == 2
        assert result.edges_created == 1
        assert len(result.errors) == 1
        assert "link_failed" in result.errors[0]

    @pytest.mark.asyncio
    async def test_empty_references_returns_zero_counts(
        self, mock_repo, mock_s2_client,
    ):
        mock_s2_client.get_paper_references.return_value = {"data": []}

        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )

        assert result.stubs_created == 0
        assert result.edges_created == 0
        mock_repo.create_or_promote_paper_stub.assert_not_called()


# =============================================================================
# Tolerant input parsing
# =============================================================================


class TestTolerantInputParsing:
    @pytest.mark.asyncio
    async def test_handles_none_response(self, mock_repo, mock_s2_client):
        mock_s2_client.get_paper_references.return_value = None
        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )
        assert result.stubs_created == 0

    @pytest.mark.asyncio
    async def test_handles_none_data_in_response(self, mock_repo, mock_s2_client):
        mock_s2_client.get_paper_references.return_value = {"data": None}
        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )
        assert result.stubs_created == 0

    @pytest.mark.asyncio
    async def test_handles_none_ref_entry(self, mock_repo, mock_s2_client):
        mock_s2_client.get_paper_references.return_value = {
            "data": [None, _ref_entry("10.1/A")],
        }
        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )
        # None entry has no citedPaper → no DOI → skipped.
        assert result.skipped_no_doi == 1
        assert result.stubs_created == 1

    @pytest.mark.asyncio
    async def test_extract_doi_with_empty_string_value(
        self, mock_repo, mock_s2_client,
    ):
        """``"DOI": ""`` should be treated as no-DOI."""
        mock_s2_client.get_paper_references.return_value = {
            "data": [_ref_entry("")],
        }
        result = await populate_citations(
            repo=mock_repo,
            s2_client=mock_s2_client,
            paper_doi="10.1/SOURCE",
            paper_s2_id="S2_SRC",
        )
        assert result.skipped_no_doi == 1
