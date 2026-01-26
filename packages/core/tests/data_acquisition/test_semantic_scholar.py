"""
Unit tests for Semantic Scholar API client.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from agentic_kg.data_acquisition.semantic_scholar import (
    DEFAULT_AUTHOR_FIELDS,
    DEFAULT_PAPER_FIELDS,
    SemanticScholarClient,
    get_semantic_scholar_client,
    reset_semantic_scholar_client,
)
from agentic_kg.data_acquisition.config import SemanticScholarConfig
from agentic_kg.data_acquisition.exceptions import RateLimitError
from agentic_kg.data_acquisition.cache import ResponseCache
from agentic_kg.data_acquisition.rate_limiter import TokenBucketRateLimiter
from agentic_kg.data_acquisition.resilience import CircuitBreaker


class TestSemanticScholarClient:
    """Tests for SemanticScholarClient class."""

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
        return SemanticScholarClient(
            cache=mock_cache,
            rate_limiter=mock_rate_limiter,
            circuit_breaker=mock_circuit_breaker,
        )

    def test_default_paper_fields(self):
        """Test that default paper fields are defined."""
        assert "paperId" in DEFAULT_PAPER_FIELDS
        assert "title" in DEFAULT_PAPER_FIELDS
        assert "abstract" in DEFAULT_PAPER_FIELDS
        assert "authors" in DEFAULT_PAPER_FIELDS
        assert "citationCount" in DEFAULT_PAPER_FIELDS

    def test_default_author_fields(self):
        """Test that default author fields are defined."""
        assert "authorId" in DEFAULT_AUTHOR_FIELDS
        assert "name" in DEFAULT_AUTHOR_FIELDS
        assert "affiliations" in DEFAULT_AUTHOR_FIELDS

    def test_source_constant(self, client):
        """Test source constant is set."""
        assert client.SOURCE == "semantic_scholar"

    def test_client_initialization(self):
        """Test client initializes with default config."""
        client = SemanticScholarClient()

        assert client.config is not None
        assert client.config.base_url == "https://api.semanticscholar.org/graph/v1"

    def test_client_initialization_custom_config(self):
        """Test client initializes with custom config."""
        config = SemanticScholarConfig(
            api_key="test-key",
            timeout=60.0,
        )
        client = SemanticScholarClient(config=config)

        assert client.config.api_key == "test-key"
        assert client.config.timeout == 60.0

    @pytest.mark.asyncio
    async def test_get_paper_cache_hit(
        self, client, mock_cache, mock_rate_limiter
    ):
        """Test that cached responses are returned."""
        cached_data = {"paperId": "cached", "title": "Cached Paper"}
        mock_cache.get.return_value = cached_data

        result = await client.get_paper("test-id")

        assert result == cached_data
        mock_rate_limiter.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_paper_by_doi(self, client):
        """Test get_paper_by_doi formats identifier correctly."""
        with patch.object(client, "get_paper", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"paperId": "test"}

            await client.get_paper_by_doi("10.1234/test")

            # Should add DOI: prefix
            mock_get.assert_called_once_with(
                "DOI:10.1234/test", fields=None, use_cache=True
            )

    @pytest.mark.asyncio
    async def test_get_paper_by_doi_already_prefixed(self, client):
        """Test get_paper_by_doi doesn't double-prefix."""
        with patch.object(client, "get_paper", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"paperId": "test"}

            await client.get_paper_by_doi("DOI:10.1234/test")

            mock_get.assert_called_once_with(
                "DOI:10.1234/test", fields=None, use_cache=True
            )

    @pytest.mark.asyncio
    async def test_get_paper_by_arxiv(self, client):
        """Test get_paper_by_arxiv formats identifier correctly."""
        with patch.object(client, "get_paper", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"paperId": "test"}

            await client.get_paper_by_arxiv("2106.01345")

            mock_get.assert_called_once_with(
                "ARXIV:2106.01345", fields=None, use_cache=True
            )

    @pytest.mark.asyncio
    async def test_search_papers_params(self, client):
        """Test search_papers builds parameters correctly."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"data": [], "total": 0}

            await client.search_papers(
                query="test query",
                limit=50,
                offset=10,
                year="2020-2023",
                venue="NeurIPS",
                fields_of_study=["Computer Science"],
                open_access_pdf=True,
            )

            call_args = mock_req.call_args
            params = call_args[1]["params"]

            assert params["query"] == "test query"
            assert params["limit"] == 50
            assert params["offset"] == 10
            assert params["year"] == "2020-2023"
            assert params["venue"] == "NeurIPS"
            assert params["fieldsOfStudy"] == "Computer Science"
            assert params["openAccessPdf"] == "true"

    @pytest.mark.asyncio
    async def test_search_papers_limit_cap(self, client):
        """Test search_papers caps limit at 100."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"data": [], "total": 0}

            await client.search_papers(query="test", limit=500)

            call_args = mock_req.call_args
            params = call_args[1]["params"]

            assert params["limit"] == 100

    @pytest.mark.asyncio
    async def test_get_author(self, client):
        """Test get_author endpoint."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"authorId": "123", "name": "Test Author"}

            result = await client.get_author("123")

            assert result["authorId"] == "123"
            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert "author/123" in call_args[0]

    @pytest.mark.asyncio
    async def test_get_author_papers(self, client):
        """Test get_author_papers endpoint."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"data": [], "offset": 0}

            await client.get_author_papers("123", limit=50, offset=10)

            call_args = mock_req.call_args
            assert "author/123/papers" in call_args[0]
            params = call_args[1]["params"]
            assert params["limit"] == 50
            assert params["offset"] == 10

    @pytest.mark.asyncio
    async def test_get_author_papers_limit_cap(self, client):
        """Test get_author_papers caps limit at 1000."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"data": []}

            await client.get_author_papers("123", limit=5000)

            call_args = mock_req.call_args
            params = call_args[1]["params"]
            assert params["limit"] == 1000

    @pytest.mark.asyncio
    async def test_get_paper_citations(self, client):
        """Test get_paper_citations endpoint."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"data": []}

            await client.get_paper_citations("paper123", limit=50)

            call_args = mock_req.call_args
            assert "paper/paper123/citations" in call_args[0]

    @pytest.mark.asyncio
    async def test_get_paper_references(self, client):
        """Test get_paper_references endpoint."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"data": []}

            await client.get_paper_references("paper123", limit=50)

            call_args = mock_req.call_args
            assert "paper/paper123/references" in call_args[0]

    @pytest.mark.asyncio
    async def test_bulk_get_papers(
        self, client, mock_cache, mock_rate_limiter, mock_circuit_breaker
    ):
        """Test bulk_get_papers endpoint."""
        papers = [
            {"paperId": "p1", "title": "Paper 1"},
            {"paperId": "p2", "title": "Paper 2"},
        ]

        with patch.object(client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = papers

            result = await client.bulk_get_papers(["p1", "p2"])

            assert len(result) == 2
            mock_circuit_breaker.check.assert_called_once()
            mock_rate_limiter.acquire.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_get_papers_max_500(self, client):
        """Test bulk_get_papers enforces max 500 papers."""
        paper_ids = [f"p{i}" for i in range(600)]

        with pytest.raises(ValueError) as exc_info:
            await client.bulk_get_papers(paper_ids)

        assert "500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_bulk_get_papers_caches_individual(
        self, client, mock_cache, mock_rate_limiter, mock_circuit_breaker
    ):
        """Test bulk_get_papers caches individual papers."""
        papers = [{"paperId": "p1", "title": "Paper 1"}]

        with patch.object(client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = papers

            await client.bulk_get_papers(["p1"])

            # Should cache the paper
            mock_cache.set.assert_called()

    def test_handle_response_rate_limit(self, client):
        """Test that 429 responses raise RateLimitError."""
        response = MagicMock()
        response.status_code = 429
        response.headers = {"retry-after": "30"}

        with pytest.raises(RateLimitError) as exc_info:
            client._handle_response(response)

        assert exc_info.value.retry_after == 30.0

    def test_handle_response_rate_limit_no_header(self, client):
        """Test RateLimitError without retry-after header."""
        response = MagicMock()
        response.status_code = 429
        response.headers = {}

        with pytest.raises(RateLimitError) as exc_info:
            client._handle_response(response)

        assert exc_info.value.retry_after is None


class TestGetSemanticScholarClient:
    """Tests for singleton access."""

    def test_returns_client_instance(self):
        """Test that get_semantic_scholar_client returns a client."""
        client = get_semantic_scholar_client()

        assert isinstance(client, SemanticScholarClient)

    def test_returns_same_instance(self):
        """Test that get_semantic_scholar_client returns singleton."""
        client1 = get_semantic_scholar_client()
        client2 = get_semantic_scholar_client()

        assert client1 is client2

    def test_reset_clears_singleton(self):
        """Test that reset clears the singleton."""
        client1 = get_semantic_scholar_client()
        reset_semantic_scholar_client()
        client2 = get_semantic_scholar_client()

        assert client1 is not client2
