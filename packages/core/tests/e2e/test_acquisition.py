"""
E2E tests for data acquisition against live APIs.

Tests Semantic Scholar and arXiv clients with real paper IDs.
"""

from __future__ import annotations

import pytest

from agentic_kg.data_acquisition.arxiv import ArxivClient, construct_pdf_url
from agentic_kg.data_acquisition.semantic_scholar import SemanticScholarClient

# Known paper IDs for testing
# "Attention Is All You Need" - the Transformer paper
TRANSFORMER_SS_ID = "204e3073870fae3d05bcbc2f6a8e263d9b72e776"
TRANSFORMER_ARXIV_ID = "1706.03762"


@pytest.mark.e2e
@pytest.mark.slow
class TestSemanticScholarE2E:
    """E2E tests for Semantic Scholar API."""

    @pytest.fixture
    async def client(self):
        """Create client for tests."""
        client = SemanticScholarClient()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_get_paper_by_id(self, client: SemanticScholarClient):
        """Test getting a paper by Semantic Scholar ID."""
        paper = await client.get_paper(TRANSFORMER_SS_ID)

        assert paper is not None
        assert paper["paperId"] == TRANSFORMER_SS_ID
        assert "Attention" in paper["title"]
        assert paper["year"] == 2017
        assert len(paper["authors"]) > 0

    @pytest.mark.asyncio
    async def test_get_paper_by_arxiv_id(self, client: SemanticScholarClient):
        """Test getting a paper by arXiv ID."""
        paper = await client.get_paper_by_arxiv(TRANSFORMER_ARXIV_ID)

        assert paper is not None
        assert "Attention" in paper["title"]
        assert paper["year"] == 2017

    @pytest.mark.asyncio
    async def test_search_papers(self, client: SemanticScholarClient):
        """Test paper search."""
        results = await client.search_papers(
            query="transformer attention mechanism",
            limit=5,
            fields_of_study=["Computer Science"],
        )

        assert "data" in results
        assert len(results["data"]) > 0
        assert results["total"] > 0

        # Check first result has expected fields
        paper = results["data"][0]
        assert "paperId" in paper
        assert "title" in paper

    @pytest.mark.asyncio
    async def test_get_paper_citations(self, client: SemanticScholarClient):
        """Test getting paper citations."""
        result = await client.get_paper_citations(TRANSFORMER_SS_ID, limit=10)

        # Transformer paper is highly cited
        assert "data" in result or isinstance(result, list)
        citations = result.get("data", result) if isinstance(result, dict) else result
        assert len(citations) > 0

    @pytest.mark.asyncio
    async def test_get_author(self, client: SemanticScholarClient):
        """Test getting author info."""
        # First get author ID from paper
        paper = await client.get_paper(TRANSFORMER_SS_ID)
        author_id = paper["authors"][0]["authorId"]

        author = await client.get_author(author_id)

        assert author is not None
        assert "name" in author
        assert author["paperCount"] > 0


@pytest.mark.e2e
@pytest.mark.slow
class TestArxivE2E:
    """E2E tests for arXiv API."""

    @pytest.fixture
    async def client(self):
        """Create client for tests."""
        client = ArxivClient()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_get_paper_by_id(self, client: ArxivClient):
        """Test getting a paper by arXiv ID."""
        paper = await client.get_paper(TRANSFORMER_ARXIV_ID)

        assert paper is not None
        # arXiv returns versioned IDs (e.g., 1706.03762v7)
        assert paper["id"].startswith(TRANSFORMER_ARXIV_ID)
        assert "Attention" in paper["title"]
        assert len(paper["authors"]) > 0
        assert paper["pdf_url"] is not None

    @pytest.mark.asyncio
    async def test_search_papers(self, client: ArxivClient):
        """Test paper search."""
        results = await client.search_papers(
            query="all:transformer attention",
            limit=5,
            categories=["cs.CL", "cs.LG"],
        )

        assert "data" in results
        assert len(results["data"]) > 0
        assert results["total"] > 0

    @pytest.mark.asyncio
    async def test_get_multiple_papers(self, client: ArxivClient):
        """Test getting multiple papers by ID."""
        # A few well-known NLP papers
        arxiv_ids = [
            "1706.03762",  # Attention Is All You Need
            "1810.04805",  # BERT
        ]

        papers = await client.get_papers_by_ids(arxiv_ids)

        assert len(papers) == 2
        # arXiv returns versioned IDs (e.g., 1706.03762v7), so check startswith
        assert all(
            any(p["id"].startswith(base_id) for base_id in arxiv_ids)
            for p in papers
        )

    @pytest.mark.asyncio
    async def test_pdf_url_construction(self, client: ArxivClient):
        """Test that PDF URLs are valid."""
        import httpx

        paper = await client.get_paper(TRANSFORMER_ARXIV_ID)
        pdf_url = paper["pdf_url"]

        # HEAD request to verify URL is valid
        async with httpx.AsyncClient() as http:
            response = await http.head(pdf_url, follow_redirects=True)
            assert response.status_code == 200
            assert "pdf" in response.headers.get("content-type", "").lower()


@pytest.mark.e2e
@pytest.mark.slow
class TestCrossSourceCorrelation:
    """Tests that verify data consistency across sources."""

    @pytest.mark.asyncio
    async def test_same_paper_both_sources(self):
        """Test that the same paper can be found in both sources."""
        async with SemanticScholarClient() as ss_client:
            async with ArxivClient() as arxiv_client:
                ss_paper = await ss_client.get_paper_by_arxiv(TRANSFORMER_ARXIV_ID)
                arxiv_paper = await arxiv_client.get_paper(TRANSFORMER_ARXIV_ID)

                # Both should have similar titles
                assert "Attention" in ss_paper["title"]
                assert "Attention" in arxiv_paper["title"]

                # Both should have authors
                assert len(ss_paper["authors"]) > 0
                assert len(arxiv_paper["authors"]) > 0
