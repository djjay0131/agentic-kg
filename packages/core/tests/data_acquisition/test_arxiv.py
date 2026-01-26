"""
Unit tests for arXiv API client.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentic_kg.data_acquisition.arxiv import (
    ArxivClient,
    construct_abs_url,
    construct_pdf_url,
    get_arxiv_client,
    normalize_arxiv_id,
    reset_arxiv_client,
)
from agentic_kg.data_acquisition.config import ArxivConfig
from agentic_kg.data_acquisition.exceptions import APIError, NotFoundError
from agentic_kg.data_acquisition.cache import ResponseCache
from agentic_kg.data_acquisition.rate_limiter import TokenBucketRateLimiter
from agentic_kg.data_acquisition.resilience import CircuitBreaker


class TestNormalizeArxivId:
    """Tests for arXiv ID normalization."""

    def test_normalize_basic_id(self):
        """Test normalizing basic arXiv ID."""
        assert normalize_arxiv_id("2106.01345") == "2106.01345"

    def test_normalize_id_with_version(self):
        """Test normalizing ID with version suffix."""
        assert normalize_arxiv_id("2106.01345v2") == "2106.01345v2"

    def test_normalize_with_arxiv_prefix_colon(self):
        """Test normalizing ID with arxiv: prefix."""
        assert normalize_arxiv_id("arxiv:2106.01345") == "2106.01345"
        assert normalize_arxiv_id("ARXIV:2106.01345") == "2106.01345"

    def test_normalize_with_arxiv_prefix_dot(self):
        """Test normalizing ID with arxiv. prefix."""
        assert normalize_arxiv_id("arXiv.2106.01345") == "2106.01345"

    def test_normalize_old_format_id(self):
        """Test normalizing old-style arXiv ID."""
        assert normalize_arxiv_id("hep-th/9901001") == "hep-th/9901001"
        assert normalize_arxiv_id("cond-mat/9901001v2") == "cond-mat/9901001v2"

    def test_normalize_from_url(self):
        """Test extracting ID from URL."""
        assert normalize_arxiv_id("https://arxiv.org/abs/2106.01345") == "2106.01345"

    def test_normalize_unknown_format(self):
        """Test that unknown format returns input unchanged."""
        result = normalize_arxiv_id("unknown-format")
        assert result == "unknown-format"


class TestConstructPdfUrl:
    """Tests for PDF URL construction."""

    def test_construct_basic_url(self):
        """Test constructing PDF URL from basic ID."""
        url = construct_pdf_url("2106.01345")

        assert url == "https://arxiv.org/pdf/2106.01345.pdf"

    def test_construct_url_strips_version(self):
        """Test that version suffix is stripped from PDF URL."""
        url = construct_pdf_url("2106.01345v2")

        assert url == "https://arxiv.org/pdf/2106.01345.pdf"

    def test_construct_url_with_custom_config(self):
        """Test constructing URL with custom config."""
        config = ArxivConfig(pdf_base_url="https://custom.arxiv.org/pdf")

        url = construct_pdf_url("2106.01345", config)

        assert url == "https://custom.arxiv.org/pdf/2106.01345.pdf"


class TestConstructAbsUrl:
    """Tests for abstract URL construction."""

    def test_construct_basic_url(self):
        """Test constructing abstract URL from basic ID."""
        url = construct_abs_url("2106.01345")

        assert url == "https://arxiv.org/abs/2106.01345"

    def test_construct_url_keeps_version(self):
        """Test that version suffix is kept in abstract URL."""
        url = construct_abs_url("2106.01345v2")

        assert url == "https://arxiv.org/abs/2106.01345v2"

    def test_construct_url_with_custom_config(self):
        """Test constructing URL with custom config."""
        config = ArxivConfig(abs_base_url="https://custom.arxiv.org/abs")

        url = construct_abs_url("2106.01345", config)

        assert url == "https://custom.arxiv.org/abs/2106.01345"


class TestArxivClient:
    """Tests for ArxivClient class."""

    @pytest.fixture
    def mock_cache(self):
        """Create mock cache."""
        cache = MagicMock(spec=ResponseCache)
        cache.get.return_value = None
        return cache

    @pytest.fixture
    def mock_rate_limiter(self):
        """Create mock rate limiter."""
        limiter = MagicMock(spec=TokenBucketRateLimiter)
        limiter.acquire = AsyncMock()
        return limiter

    @pytest.fixture
    def mock_circuit_breaker(self):
        """Create mock circuit breaker."""
        cb = MagicMock(spec=CircuitBreaker)
        cb.check = AsyncMock()
        cb.record_success = AsyncMock()
        cb.record_failure = AsyncMock()
        return cb

    @pytest.fixture
    def client(self, mock_cache, mock_rate_limiter, mock_circuit_breaker):
        """Create client with mock dependencies."""
        return ArxivClient(
            cache=mock_cache,
            rate_limiter=mock_rate_limiter,
            circuit_breaker=mock_circuit_breaker,
        )

    def test_source_constant(self, client):
        """Test source constant is set."""
        assert client.SOURCE == "arxiv"

    def test_client_initialization(self):
        """Test client initializes with default config."""
        client = ArxivClient()

        assert client.config is not None
        assert "export.arxiv.org" in client.config.base_url

    def test_client_initialization_custom_config(self):
        """Test client initializes with custom config."""
        config = ArxivConfig(timeout=120.0)
        client = ArxivClient(config=config)

        assert client.config.timeout == 120.0

    @pytest.mark.asyncio
    async def test_context_manager(self, client):
        """Test async context manager."""
        async with client as c:
            assert c is client

    @pytest.mark.asyncio
    async def test_get_paper_cache_hit(
        self, client, mock_cache, mock_rate_limiter
    ):
        """Test that cached responses are returned."""
        cached_data = {"entries": [{"id": "cached"}]}
        mock_cache.get.return_value = cached_data

        result = await client.get_paper("2106.01345")

        assert result["id"] == "cached"
        mock_rate_limiter.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_paper_normalizes_id(self, client):
        """Test that paper ID is normalized."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "entries": [{"id": "2106.01345", "title": "Test"}]
            }

            await client.get_paper("arxiv:2106.01345")

            call_args = mock_req.call_args
            params = call_args[0][0]
            assert params["id_list"] == "2106.01345"

    @pytest.mark.asyncio
    async def test_get_paper_not_found(self, client):
        """Test NotFoundError when paper not found."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"entries": []}

            with pytest.raises(NotFoundError) as exc_info:
                await client.get_paper("9999.99999")

            assert exc_info.value.source == "arxiv"

    @pytest.mark.asyncio
    async def test_search_papers(self, client):
        """Test search_papers method."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "entries": [{"id": "test", "title": "Test Paper"}],
                "total_results": 100,
                "start_index": 0,
            }

            result = await client.search_papers("machine learning", limit=10)

            assert "data" in result
            assert result["total"] == 100
            assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_search_papers_with_categories(self, client):
        """Test search_papers with category filter."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"entries": [], "total_results": 0, "start_index": 0}

            await client.search_papers(
                "test",
                categories=["cs.AI", "cs.LG"],
            )

            call_args = mock_req.call_args
            params = call_args[0][0]
            # Category filter should be added to query
            assert "cat:cs.AI" in params["search_query"]
            assert "cat:cs.LG" in params["search_query"]

    @pytest.mark.asyncio
    async def test_search_papers_sort_options(self, client):
        """Test search_papers with sort options."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"entries": [], "total_results": 0, "start_index": 0}

            await client.search_papers(
                "test",
                sort_by="submittedDate",
                sort_order="ascending",
            )

            call_args = mock_req.call_args
            params = call_args[0][0]
            assert params["sortBy"] == "submittedDate"
            assert params["sortOrder"] == "ascending"

    @pytest.mark.asyncio
    async def test_search_papers_limit_cap(self, client):
        """Test search_papers caps limit at 100."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"entries": [], "total_results": 0, "start_index": 0}

            await client.search_papers("test", limit=500)

            call_args = mock_req.call_args
            params = call_args[0][0]
            assert params["max_results"] == 100

    @pytest.mark.asyncio
    async def test_get_papers_by_ids(self, client, mock_cache):
        """Test get_papers_by_ids method."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "entries": [
                    {"id": "2106.01345", "title": "Paper 1"},
                    {"id": "2107.02345", "title": "Paper 2"},
                ]
            }

            result = await client.get_papers_by_ids(["2106.01345", "2107.02345"])

            assert len(result) == 2
            # Should cache individual papers
            mock_cache.set.assert_called()

    @pytest.mark.asyncio
    async def test_get_papers_by_ids_normalizes(self, client):
        """Test that paper IDs are normalized."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"entries": [{"id": "2106.01345"}]}

            await client.get_papers_by_ids(["arxiv:2106.01345"])

            call_args = mock_req.call_args
            params = call_args[0][0]
            assert params["id_list"] == "2106.01345"

    def test_parse_entry(self, client):
        """Test parsing a feedparser entry."""
        entry = MagicMock()
        entry.get.side_effect = lambda k, d=None: {
            "id": "http://arxiv.org/abs/2106.01345v1",
            "title": "Test Paper Title\n  with newlines",
            "summary": "  Test abstract.  ",
            "published": "2021-06-01T00:00:00Z",
            "updated": "2021-06-15T00:00:00Z",
        }.get(k, d)
        entry.authors = []
        entry.tags = [MagicMock(get=lambda k, d=None: "cs.AI" if k == "term" else None)]

        result = client._parse_entry(entry)

        assert result["id"] == "2106.01345v1"
        assert result["title"] == "Test Paper Title with newlines"
        assert result["summary"] == "Test abstract."
        assert "pdf_url" in result
        assert "abs_url" in result

    def test_parse_entry_with_authors(self, client):
        """Test parsing entry with authors."""
        author1 = MagicMock()
        author1.get.return_value = "John Doe"
        author1.arxiv_affiliation = "MIT"

        entry = MagicMock()
        entry.get.side_effect = lambda k, d=None: {
            "id": "2106.01345",
            "title": "Test",
            "summary": "Abstract",
            "published": "",
            "updated": "",
        }.get(k, d)
        entry.authors = [author1]
        entry.tags = []

        # Mock hasattr for affiliation check
        with patch("builtins.hasattr", return_value=True):
            result = client._parse_entry(entry)

        assert len(result["authors"]) == 1
        assert result["authors"][0]["name"] == "John Doe"

    def test_parse_entry_error_handling(self, client):
        """Test that parse errors return None."""
        entry = MagicMock()
        entry.get.side_effect = Exception("Parse error")

        result = client._parse_entry(entry)

        assert result is None

    @pytest.mark.asyncio
    async def test_close(self, client):
        """Test closing the HTTP client."""
        # Create a mock client
        mock_http_client = MagicMock()
        mock_http_client.is_closed = False
        mock_http_client.aclose = AsyncMock()
        client._client = mock_http_client

        await client.close()

        mock_http_client.aclose.assert_called_once()
        assert client._client is None


class TestGetArxivClient:
    """Tests for singleton access."""

    def test_returns_client_instance(self):
        """Test that get_arxiv_client returns a client."""
        client = get_arxiv_client()

        assert isinstance(client, ArxivClient)

    def test_returns_same_instance(self):
        """Test that get_arxiv_client returns singleton."""
        client1 = get_arxiv_client()
        client2 = get_arxiv_client()

        assert client1 is client2

    def test_reset_clears_singleton(self):
        """Test that reset clears the singleton."""
        client1 = get_arxiv_client()
        reset_arxiv_client()
        client2 = get_arxiv_client()

        assert client1 is not client2
