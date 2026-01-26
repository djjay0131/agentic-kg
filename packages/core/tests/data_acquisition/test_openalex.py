"""
Unit tests for OpenAlex API client.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentic_kg.data_acquisition.openalex import (
    OpenAlexClient,
    get_openalex_client,
    normalize_openalex_id,
    reconstruct_abstract,
    reset_openalex_client,
)
from agentic_kg.data_acquisition.config import OpenAlexConfig
from agentic_kg.data_acquisition.exceptions import APIError, NotFoundError
from agentic_kg.data_acquisition.cache import ResponseCache
from agentic_kg.data_acquisition.rate_limiter import TokenBucketRateLimiter
from agentic_kg.data_acquisition.resilience import CircuitBreaker


class TestNormalizeOpenAlexId:
    """Tests for OpenAlex ID normalization."""

    def test_normalize_basic_id(self):
        """Test normalizing basic OpenAlex ID."""
        assert normalize_openalex_id("W2741809807") == "W2741809807"

    def test_normalize_lowercase_id(self):
        """Test normalizing lowercase ID."""
        assert normalize_openalex_id("w2741809807") == "w2741809807"

    def test_normalize_url(self):
        """Test extracting ID from OpenAlex URL."""
        assert normalize_openalex_id("https://openalex.org/W2741809807") == "W2741809807"

    def test_normalize_unknown_format(self):
        """Test that unknown format returns input unchanged."""
        result = normalize_openalex_id("unknown-format")
        assert result == "unknown-format"


class TestReconstructAbstract:
    """Tests for abstract reconstruction from inverted index."""

    def test_reconstruct_simple_abstract(self):
        """Test reconstructing a simple abstract."""
        inverted_index = {
            "This": [0],
            "is": [1],
            "a": [2],
            "test": [3],
        }

        result = reconstruct_abstract(inverted_index)

        assert result == "This is a test"

    def test_reconstruct_with_repeated_words(self):
        """Test reconstructing abstract with repeated words."""
        inverted_index = {
            "The": [0, 4],
            "cat": [1],
            "sat": [2],
            "on": [3],
            "mat": [5],
        }

        result = reconstruct_abstract(inverted_index)

        assert result == "The cat sat on The mat"

    def test_reconstruct_empty_index(self):
        """Test that empty index returns None."""
        assert reconstruct_abstract({}) is None
        assert reconstruct_abstract(None) is None

    def test_reconstruct_empty_positions(self):
        """Test handling empty position lists."""
        inverted_index = {
            "word": [],
        }

        result = reconstruct_abstract(inverted_index)

        assert result is None

    def test_reconstruct_sparse_positions(self):
        """Test handling gaps in positions."""
        inverted_index = {
            "First": [0],
            "Last": [5],
        }

        result = reconstruct_abstract(inverted_index)

        # Should skip missing positions
        assert "First" in result
        assert "Last" in result


class TestOpenAlexClient:
    """Tests for OpenAlexClient class."""

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
        return OpenAlexClient(
            cache=mock_cache,
            rate_limiter=mock_rate_limiter,
            circuit_breaker=mock_circuit_breaker,
        )

    def test_source_constant(self, client):
        """Test source constant is set."""
        assert client.SOURCE == "openalex"

    def test_client_initialization(self):
        """Test client initializes with default config."""
        client = OpenAlexClient()

        assert client.config is not None
        assert "api.openalex.org" in client.config.base_url

    def test_client_initialization_custom_config(self):
        """Test client initializes with custom config."""
        config = OpenAlexConfig(
            email="test@example.com",
            timeout=120.0,
        )
        client = OpenAlexClient(config=config)

        assert client.config.email == "test@example.com"
        assert client.config.timeout == 120.0

    @pytest.mark.asyncio
    async def test_context_manager(self, client):
        """Test async context manager."""
        async with client as c:
            assert c is client

    @pytest.mark.asyncio
    async def test_get_work_cache_hit(
        self, client, mock_cache, mock_rate_limiter
    ):
        """Test that cached responses are returned."""
        cached_data = {"id": "W123", "title": "Cached Work"}
        mock_cache.get.return_value = cached_data

        result = await client.get_work("W123")

        assert result == cached_data
        mock_rate_limiter.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_work_by_openalex_id(self, client):
        """Test get_work with OpenAlex ID."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "W123", "title": "Test"}

            await client.get_work("W123")

            call_args = mock_req.call_args
            assert "/works/W123" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_work_by_url(self, client):
        """Test get_work with full OpenAlex URL."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "W123", "title": "Test"}

            await client.get_work("https://openalex.org/W123")

            call_args = mock_req.call_args
            assert "/works/W123" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_work_by_doi(self, client):
        """Test get_work with DOI."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "W123", "doi": "10.1234/test"}

            await client.get_work("10.1234/test")

            call_args = mock_req.call_args
            assert "doi.org" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_work_enriches_abstract(self, client):
        """Test that abstract is reconstructed from inverted index."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "id": "W123",
                "abstract_inverted_index": {"Hello": [0], "world": [1]},
            }

            result = await client.get_work("W123")

            assert result["abstract"] == "Hello world"

    @pytest.mark.asyncio
    async def test_get_work_by_doi_helper(self, client):
        """Test get_work_by_doi helper method."""
        with patch.object(client, "get_work", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"id": "W123"}

            await client.get_work_by_doi("https://doi.org/10.1234/test")

            # Should strip URL prefix
            mock_get.assert_called_with("10.1234/test", use_cache=True)

    @pytest.mark.asyncio
    async def test_search_works(self, client):
        """Test search_works method."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "results": [{"id": "W123", "title": "Test"}],
                "meta": {"count": 100, "per_page": 25, "page": 1},
            }

            result = await client.search_works("machine learning", per_page=25)

            assert "data" in result
            assert result["total"] == 100
            assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_search_works_with_query(self, client):
        """Test search_works with text query."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"results": [], "meta": {"count": 0}}

            await client.search_works(query="deep learning")

            call_args = mock_req.call_args
            params = call_args[1]["params"]
            assert params["search"] == "deep learning"

    @pytest.mark.asyncio
    async def test_search_works_with_filters(self, client):
        """Test search_works with filter parameters."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"results": [], "meta": {"count": 0}}

            await client.search_works(
                filter_params={
                    "publication_year": 2023,
                    "is_oa": True,
                    "type": ["article", "preprint"],
                }
            )

            call_args = mock_req.call_args
            params = call_args[1]["params"]
            assert "filter" in params
            assert "publication_year:2023" in params["filter"]
            assert "is_oa:true" in params["filter"]

    @pytest.mark.asyncio
    async def test_search_works_with_sort(self, client):
        """Test search_works with sort parameter."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"results": [], "meta": {"count": 0}}

            await client.search_works(sort="cited_by_count:desc")

            call_args = mock_req.call_args
            params = call_args[1]["params"]
            assert params["sort"] == "cited_by_count:desc"

    @pytest.mark.asyncio
    async def test_search_works_per_page_cap(self, client):
        """Test search_works caps per_page at 200."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"results": [], "meta": {"count": 0}}

            await client.search_works(per_page=500)

            call_args = mock_req.call_args
            params = call_args[1]["params"]
            assert params["per-page"] == 200

    @pytest.mark.asyncio
    async def test_search_works_enriches_results(self, client):
        """Test that search results have abstracts reconstructed."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "results": [
                    {"id": "W123", "abstract_inverted_index": {"Test": [0]}}
                ],
                "meta": {"count": 1},
            }

            result = await client.search_works("test")

            assert result["data"][0]["abstract"] == "Test"

    @pytest.mark.asyncio
    async def test_get_author(self, client):
        """Test get_author method."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "A123", "display_name": "John Doe"}

            result = await client.get_author("A123")

            assert result["id"] == "A123"
            call_args = mock_req.call_args
            assert "/authors/A123" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_author_normalizes_id(self, client):
        """Test that author ID is normalized."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "A123"}

            await client.get_author("https://openalex.org/A123")

            call_args = mock_req.call_args
            assert "/authors/A123" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_author_works(self, client):
        """Test get_author_works method."""
        with patch.object(client, "search_works", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = {"data": [], "total": 0}

            await client.get_author_works("A123", per_page=50, page=2)

            mock_search.assert_called_once()
            call_kwargs = mock_search.call_args[1]
            assert call_kwargs["filter_params"]["authorships.author.id"] == "A123"
            assert call_kwargs["per_page"] == 50
            assert call_kwargs["page"] == 2

    @pytest.mark.asyncio
    async def test_get_random_works(self, client):
        """Test get_random_works method."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "results": [{"id": "W1"}, {"id": "W2"}],
            }

            result = await client.get_random_works(count=2, seed=42)

            assert len(result) == 2
            call_args = mock_req.call_args
            params = call_args[1]["params"]
            assert params["sample"] == 2
            assert params["seed"] == 42

    @pytest.mark.asyncio
    async def test_get_random_works_caps_count(self, client):
        """Test get_random_works caps count at 200."""
        with patch.object(client, "_make_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"results": []}

            await client.get_random_works(count=500)

            call_args = mock_req.call_args
            params = call_args[1]["params"]
            assert params["per-page"] == 200

    def test_enrich_work_with_abstract(self, client):
        """Test _enrich_work reconstructs abstract."""
        work = {
            "id": "W123",
            "abstract_inverted_index": {"Hello": [0], "world": [1]},
        }

        result = client._enrich_work(work)

        assert result["abstract"] == "Hello world"

    def test_enrich_work_without_abstract(self, client):
        """Test _enrich_work handles missing abstract."""
        work = {"id": "W123", "title": "No abstract"}

        result = client._enrich_work(work)

        assert "abstract" not in result or result.get("abstract") is None

    @pytest.mark.asyncio
    async def test_close(self, client):
        """Test closing the HTTP client."""
        mock_http_client = MagicMock()
        mock_http_client.is_closed = False
        mock_http_client.aclose = AsyncMock()
        client._client = mock_http_client

        await client.close()

        mock_http_client.aclose.assert_called_once()
        assert client._client is None


class TestGetOpenAlexClient:
    """Tests for singleton access."""

    def test_returns_client_instance(self):
        """Test that get_openalex_client returns a client."""
        client = get_openalex_client()

        assert isinstance(client, OpenAlexClient)

    def test_returns_same_instance(self):
        """Test that get_openalex_client returns singleton."""
        client1 = get_openalex_client()
        client2 = get_openalex_client()

        assert client1 is client2

    def test_reset_clears_singleton(self):
        """Test that reset clears the singleton."""
        client1 = get_openalex_client()
        reset_openalex_client()
        client2 = get_openalex_client()

        assert client1 is not client2
