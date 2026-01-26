"""
Unit tests for multi-source paper aggregator.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentic_kg.data_acquisition.aggregator import (
    AggregatedResult,
    PaperAggregator,
    SearchResult,
    detect_identifier_type,
    get_paper_aggregator,
    reset_paper_aggregator,
)
from agentic_kg.data_acquisition.normalizer import NormalizedPaper
from agentic_kg.data_acquisition.exceptions import NotFoundError


class TestDetectIdentifierType:
    """Tests for identifier type detection."""

    def test_detect_doi_pattern(self):
        """Test detecting DOI by pattern."""
        assert detect_identifier_type("10.1038/nature12373") == "doi"
        assert detect_identifier_type("10.18653/v1/N18-1202") == "doi"
        assert detect_identifier_type("10.1000/xyz123") == "doi"

    def test_detect_doi_prefix(self):
        """Test detecting DOI by prefix."""
        assert detect_identifier_type("doi:10.1038/nature12373") == "doi"
        assert detect_identifier_type("DOI:10.1038/nature12373") == "doi"
        assert detect_identifier_type("https://doi.org/10.1038/nature12373") == "doi"

    def test_detect_arxiv_pattern(self):
        """Test detecting arXiv ID by pattern."""
        assert detect_identifier_type("2106.01345") == "arxiv"
        assert detect_identifier_type("2106.01345v2") == "arxiv"
        assert detect_identifier_type("hep-th/9901001") == "arxiv"

    def test_detect_arxiv_prefix(self):
        """Test detecting arXiv ID by prefix."""
        assert detect_identifier_type("arxiv:2106.01345") == "arxiv"
        assert detect_identifier_type("ARXIV:2106.01345") == "arxiv"
        assert detect_identifier_type("https://arxiv.org/abs/2106.01345") == "arxiv"

    def test_detect_openalex_id(self):
        """Test detecting OpenAlex ID."""
        assert detect_identifier_type("W2741809807") == "openalex"
        assert detect_identifier_type("w2741809807") == "openalex"
        assert detect_identifier_type("https://openalex.org/W2741809807") == "openalex"

    def test_detect_semantic_scholar_id(self):
        """Test detecting Semantic Scholar ID (40-char hex)."""
        assert detect_identifier_type("649def34f8be52c8b66281af98ae884c09aef38b") == "semantic_scholar"

    def test_detect_unknown(self):
        """Test that unknown identifiers return None."""
        assert detect_identifier_type("unknown-identifier") is None
        assert detect_identifier_type("12345") is None
        assert detect_identifier_type("") is None

    def test_strips_whitespace(self):
        """Test that whitespace is stripped."""
        assert detect_identifier_type("  10.1038/nature12373  ") == "doi"
        assert detect_identifier_type("\t2106.01345\n") == "arxiv"


class TestAggregatedResult:
    """Tests for AggregatedResult dataclass."""

    def test_create_with_defaults(self):
        """Test creating result with default values."""
        paper = NormalizedPaper(title="Test", source="test")
        result = AggregatedResult(paper=paper)

        assert result.paper is paper
        assert result.sources == []
        assert result.errors == {}

    def test_create_full_result(self):
        """Test creating result with all fields."""
        paper = NormalizedPaper(title="Test", source="test")
        result = AggregatedResult(
            paper=paper,
            sources=["semantic_scholar", "openalex"],
            errors={"arxiv": "Not found"},
        )

        assert result.paper is paper
        assert len(result.sources) == 2
        assert result.errors["arxiv"] == "Not found"


class TestSearchResult:
    """Tests for SearchResult dataclass."""

    def test_create_with_defaults(self):
        """Test creating search result with defaults."""
        result = SearchResult(papers=[])

        assert result.papers == []
        assert result.total_by_source == {}
        assert result.errors == {}

    def test_create_full_result(self):
        """Test creating search result with all fields."""
        papers = [NormalizedPaper(title="Test", source="test")]
        result = SearchResult(
            papers=papers,
            total_by_source={"semantic_scholar": 100, "openalex": 50},
            errors={"arxiv": "Rate limited"},
        )

        assert len(result.papers) == 1
        assert result.total_by_source["semantic_scholar"] == 100
        assert "arxiv" in result.errors


class TestPaperAggregator:
    """Tests for PaperAggregator class."""

    @pytest.fixture
    def mock_ss_client(self):
        """Create mock Semantic Scholar client."""
        client = MagicMock()
        client.get_paper = AsyncMock()
        client.search_papers = AsyncMock()
        return client

    @pytest.fixture
    def mock_arxiv_client(self):
        """Create mock arXiv client."""
        client = MagicMock()
        client.get_paper = AsyncMock()
        client.search_papers = AsyncMock()
        return client

    @pytest.fixture
    def mock_openalex_client(self):
        """Create mock OpenAlex client."""
        client = MagicMock()
        client.get_work = AsyncMock()
        client.search_works = AsyncMock()
        return client

    @pytest.fixture
    def aggregator(self, mock_ss_client, mock_arxiv_client, mock_openalex_client):
        """Create aggregator with mock clients."""
        return PaperAggregator(
            semantic_scholar_client=mock_ss_client,
            arxiv_client=mock_arxiv_client,
            openalex_client=mock_openalex_client,
        )

    @pytest.mark.asyncio
    async def test_get_paper_by_doi(
        self,
        aggregator,
        mock_ss_client,
        mock_openalex_client,
        sample_semantic_scholar_paper,
    ):
        """Test fetching paper by DOI."""
        mock_ss_client.get_paper.return_value = sample_semantic_scholar_paper
        mock_openalex_client.get_work.side_effect = NotFoundError(
            resource_type="work", identifier="test", source="openalex"
        )

        result = await aggregator.get_paper("10.18653/v1/N18-1202")

        assert result.paper is not None
        assert result.paper.title == "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
        assert "semantic_scholar" in result.sources

    @pytest.mark.asyncio
    async def test_get_paper_by_arxiv(
        self,
        aggregator,
        mock_arxiv_client,
        mock_ss_client,
        sample_arxiv_paper,
    ):
        """Test fetching paper by arXiv ID."""
        mock_arxiv_client.get_paper.return_value = sample_arxiv_paper
        mock_ss_client.get_paper.side_effect = NotFoundError(
            resource_type="paper", identifier="test", source="semantic_scholar"
        )

        result = await aggregator.get_paper("2106.01345")

        assert result.paper is not None
        assert "arxiv" in result.sources

    @pytest.mark.asyncio
    async def test_get_paper_not_found(
        self,
        aggregator,
        mock_ss_client,
        mock_openalex_client,
    ):
        """Test NotFoundError when paper not in any source."""
        mock_ss_client.get_paper.side_effect = NotFoundError(
            resource_type="paper", identifier="test", source="semantic_scholar"
        )
        mock_openalex_client.get_work.side_effect = NotFoundError(
            resource_type="work", identifier="test", source="openalex"
        )

        with pytest.raises(NotFoundError):
            await aggregator.get_paper("10.1234/nonexistent")

    @pytest.mark.asyncio
    async def test_get_paper_merges_results(
        self,
        aggregator,
        mock_ss_client,
        mock_openalex_client,
        sample_semantic_scholar_paper,
        sample_openalex_work,
    ):
        """Test that results from multiple sources are merged."""
        mock_ss_client.get_paper.return_value = sample_semantic_scholar_paper
        sample_openalex_work["abstract"] = "We introduce BERT"
        mock_openalex_client.get_work.return_value = sample_openalex_work

        result = await aggregator.get_paper("10.18653/v1/N18-1202", merge=True)

        assert result.paper is not None
        assert len(result.sources) >= 1
        # External IDs should be merged
        assert "semantic_scholar" in result.paper.external_ids or "openalex" in result.paper.external_ids

    @pytest.mark.asyncio
    async def test_get_paper_no_merge(
        self,
        aggregator,
        mock_ss_client,
        mock_openalex_client,
        sample_semantic_scholar_paper,
        sample_openalex_work,
    ):
        """Test that results are not merged when merge=False."""
        mock_ss_client.get_paper.return_value = sample_semantic_scholar_paper
        sample_openalex_work["abstract"] = "We introduce BERT"
        mock_openalex_client.get_work.return_value = sample_openalex_work

        result = await aggregator.get_paper("10.18653/v1/N18-1202", merge=False)

        # Should only return first result
        assert len(result.sources) == 1

    @pytest.mark.asyncio
    async def test_get_paper_records_errors(
        self,
        aggregator,
        mock_ss_client,
        mock_openalex_client,
        sample_semantic_scholar_paper,
    ):
        """Test that non-NotFound errors are recorded."""
        mock_ss_client.get_paper.return_value = sample_semantic_scholar_paper
        mock_openalex_client.get_work.side_effect = Exception("API Error")

        result = await aggregator.get_paper("10.18653/v1/N18-1202")

        assert result.paper is not None
        assert "openalex" in result.errors

    @pytest.mark.asyncio
    async def test_get_paper_specific_sources(
        self,
        aggregator,
        mock_ss_client,
        mock_openalex_client,
        mock_arxiv_client,
        sample_semantic_scholar_paper,
    ):
        """Test specifying which sources to query."""
        mock_ss_client.get_paper.return_value = sample_semantic_scholar_paper

        result = await aggregator.get_paper(
            "10.18653/v1/N18-1202",
            sources=["semantic_scholar"],
        )

        assert result.paper is not None
        # Only SS should be called
        mock_ss_client.get_paper.assert_called_once()
        mock_openalex_client.get_work.assert_not_called()
        mock_arxiv_client.get_paper.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_papers(
        self,
        aggregator,
        mock_ss_client,
        sample_semantic_scholar_paper,
    ):
        """Test searching papers."""
        mock_ss_client.search_papers.return_value = {
            "data": [sample_semantic_scholar_paper],
            "total": 100,
        }

        result = await aggregator.search_papers(
            "BERT transformers",
            sources=["semantic_scholar"],
            limit=10,
        )

        assert isinstance(result, SearchResult)
        assert len(result.papers) == 1
        assert result.total_by_source["semantic_scholar"] == 100

    @pytest.mark.asyncio
    async def test_search_papers_multiple_sources(
        self,
        aggregator,
        mock_ss_client,
        mock_openalex_client,
        sample_semantic_scholar_paper,
        sample_openalex_work,
    ):
        """Test searching papers from multiple sources."""
        mock_ss_client.search_papers.return_value = {
            "data": [sample_semantic_scholar_paper],
            "total": 100,
        }
        sample_openalex_work["abstract"] = "We introduce BERT"
        mock_openalex_client.search_works.return_value = {
            "data": [sample_openalex_work],
            "total": 50,
        }

        result = await aggregator.search_papers(
            "BERT",
            sources=["semantic_scholar", "openalex"],
        )

        assert result.total_by_source["semantic_scholar"] == 100
        assert result.total_by_source["openalex"] == 50

    @pytest.mark.asyncio
    async def test_search_papers_deduplication(
        self,
        aggregator,
        mock_ss_client,
        mock_openalex_client,
    ):
        """Test that search results are deduplicated by DOI."""
        # Same paper from both sources (same DOI)
        ss_paper = {
            "paperId": "abc123",
            "externalIds": {"DOI": "10.1234/same"},
            "title": "Same Paper",
        }
        oa_work = {
            "id": "W123",
            "doi": "https://doi.org/10.1234/same",
            "title": "Same Paper",
        }

        mock_ss_client.search_papers.return_value = {"data": [ss_paper], "total": 1}
        mock_openalex_client.search_works.return_value = {"data": [oa_work], "total": 1}

        result = await aggregator.search_papers(
            "test",
            sources=["semantic_scholar", "openalex"],
            deduplicate=True,
        )

        # Should only have one paper after deduplication
        assert len(result.papers) == 1

    @pytest.mark.asyncio
    async def test_search_papers_no_deduplication(
        self,
        aggregator,
        mock_ss_client,
        mock_openalex_client,
    ):
        """Test search without deduplication."""
        ss_paper = {
            "paperId": "abc123",
            "externalIds": {"DOI": "10.1234/same"},
            "title": "Same Paper",
        }
        oa_work = {
            "id": "W123",
            "doi": "https://doi.org/10.1234/same",
            "title": "Same Paper",
        }

        mock_ss_client.search_papers.return_value = {"data": [ss_paper], "total": 1}
        mock_openalex_client.search_works.return_value = {"data": [oa_work], "total": 1}

        result = await aggregator.search_papers(
            "test",
            sources=["semantic_scholar", "openalex"],
            deduplicate=False,
        )

        # Should have both papers without deduplication
        assert len(result.papers) == 2

    @pytest.mark.asyncio
    async def test_get_paper_by_doi_method(
        self,
        aggregator,
        mock_ss_client,
        sample_semantic_scholar_paper,
    ):
        """Test convenience method for DOI lookup."""
        mock_ss_client.get_paper.return_value = sample_semantic_scholar_paper

        result = await aggregator.get_paper_by_doi("10.18653/v1/N18-1202")

        assert result.paper is not None

    @pytest.mark.asyncio
    async def test_get_paper_by_arxiv_method(
        self,
        aggregator,
        mock_arxiv_client,
        sample_arxiv_paper,
    ):
        """Test convenience method for arXiv lookup."""
        mock_arxiv_client.get_paper.return_value = sample_arxiv_paper

        result = await aggregator.get_paper_by_arxiv("2106.01345")

        assert result.paper is not None

    def test_lazy_client_initialization(self):
        """Test that clients are lazily initialized."""
        aggregator = PaperAggregator()

        # Access property to trigger lazy init
        assert aggregator.semantic_scholar is not None
        assert aggregator.arxiv is not None
        assert aggregator.openalex is not None


class TestGetPaperAggregator:
    """Tests for singleton access."""

    def test_returns_aggregator_instance(self):
        """Test that get_paper_aggregator returns an aggregator."""
        aggregator = get_paper_aggregator()

        assert isinstance(aggregator, PaperAggregator)

    def test_returns_same_instance(self):
        """Test that get_paper_aggregator returns singleton."""
        aggregator1 = get_paper_aggregator()
        aggregator2 = get_paper_aggregator()

        assert aggregator1 is aggregator2

    def test_reset_clears_singleton(self):
        """Test that reset clears the singleton."""
        aggregator1 = get_paper_aggregator()
        reset_paper_aggregator()
        aggregator2 = get_paper_aggregator()

        assert aggregator1 is not aggregator2
