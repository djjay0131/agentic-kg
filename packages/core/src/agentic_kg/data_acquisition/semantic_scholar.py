"""
Semantic Scholar API client.

Provides access to the Semantic Scholar Academic Graph API for
paper metadata, citations, and author information.
"""
from __future__ import annotations


import logging
from typing import Any

from agentic_kg.data_acquisition.base import BaseAPIClient
from agentic_kg.data_acquisition.cache import (
    CacheType,
    ResponseCache,
    generate_cache_key,
    get_response_cache,
)
from agentic_kg.data_acquisition.config import (
    SemanticScholarConfig,
    get_data_acquisition_config,
)
from agentic_kg.data_acquisition.exceptions import NotFoundError, RateLimitError
from agentic_kg.data_acquisition.rate_limiter import (
    TokenBucketRateLimiter,
    get_rate_limiter_registry,
)
from agentic_kg.data_acquisition.resilience import (
    CircuitBreaker,
    get_circuit_breaker_registry,
    retry_with_backoff,
)

logger = logging.getLogger(__name__)

# Default fields to request from API
DEFAULT_PAPER_FIELDS = [
    "paperId",
    "externalIds",
    "title",
    "abstract",
    "year",
    "venue",
    "authors",
    "citationCount",
    "referenceCount",
    "fieldsOfStudy",
    "publicationTypes",
    "isOpenAccess",
    "openAccessPdf",
    "publicationDate",
]

DEFAULT_AUTHOR_FIELDS = [
    "authorId",
    "externalIds",
    "name",
    "affiliations",
    "paperCount",
    "citationCount",
    "hIndex",
]


class SemanticScholarClient(BaseAPIClient):
    """
    Client for the Semantic Scholar Academic Graph API.

    Features:
    - Paper lookup by ID, DOI, arXiv ID
    - Paper search with pagination
    - Author information
    - Citation and reference retrieval
    - Bulk paper retrieval
    - Rate limiting, caching, and circuit breaker
    """

    SOURCE = "semantic_scholar"

    def __init__(
        self,
        config: SemanticScholarConfig | None = None,
        cache: ResponseCache | None = None,
        rate_limiter: TokenBucketRateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        """
        Initialize Semantic Scholar client.

        Args:
            config: API configuration
            cache: Response cache (uses singleton if not provided)
            rate_limiter: Rate limiter (uses registry if not provided)
            circuit_breaker: Circuit breaker (uses registry if not provided)
        """
        self.config = config or get_data_acquisition_config().semantic_scholar

        super().__init__(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            headers=self.config.headers,
        )

        # Infrastructure
        self._cache = cache or get_response_cache()
        self._rate_limiter = rate_limiter or get_rate_limiter_registry().get(
            self.SOURCE, self.config.rate_limit
        )
        self._circuit_breaker = circuit_breaker or get_circuit_breaker_registry().get(
            self.SOURCE
        )

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        cache_key: str | None = None,
        cache_type: CacheType = CacheType.PAPER,
    ) -> dict[str, Any]:
        """
        Make an API request with rate limiting, caching, and circuit breaker.

        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            cache_key: Cache key (if caching desired)
            cache_type: Type of cache to use

        Returns:
            API response data
        """
        # Check cache first
        if cache_key:
            cached = self._cache.get(cache_key, cache_type)
            if cached is not None:
                return cached

        # Check circuit breaker
        await self._circuit_breaker.check()

        # Acquire rate limit token
        await self._rate_limiter.acquire()

        try:
            # Make request with retry
            async def do_request() -> dict[str, Any]:
                if method == "GET":
                    return await self.get(endpoint, params=params)
                else:
                    return await self.post(endpoint, params=params)

            result = await retry_with_backoff(
                do_request,
                max_retries=self.config.max_retries,
                source=self.SOURCE,
            )

            # Record success
            await self._circuit_breaker.record_success()

            # Cache result
            if cache_key:
                self._cache.set(cache_key, result, cache_type)

            return result

        except Exception as e:
            await self._circuit_breaker.record_failure()
            raise

    def _handle_response(self, response: Any) -> dict[str, Any]:
        """Override to handle Semantic Scholar specific errors."""
        if response.status_code == 429:
            # Extract retry-after header if present
            retry_after = response.headers.get("retry-after")
            retry_seconds = float(retry_after) if retry_after else None
            raise RateLimitError(source=self.SOURCE, retry_after=retry_seconds)

        return super()._handle_response(response)

    async def get_paper(
        self,
        identifier: str,
        fields: list[str] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get paper by identifier.

        Args:
            identifier: Paper ID, DOI (with "DOI:" prefix), or arXiv ID (with "ARXIV:" prefix)
            fields: Fields to return (defaults to DEFAULT_PAPER_FIELDS)
            use_cache: Whether to use cache

        Returns:
            Paper data from API

        Examples:
            # By Semantic Scholar ID
            await client.get_paper("649def34f8be52c8b66281af98ae884c09aef38b")

            # By DOI
            await client.get_paper("DOI:10.1038/nature12373")

            # By arXiv ID
            await client.get_paper("ARXIV:2106.01345")
        """
        fields = fields or DEFAULT_PAPER_FIELDS
        fields_str = ",".join(fields)

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(
                self.SOURCE, "get_paper", identifier=identifier, fields=fields_str
            )

        endpoint = f"paper/{identifier}"
        params = {"fields": fields_str}

        return await self._make_request(
            "GET", endpoint, params=params, cache_key=cache_key, cache_type=CacheType.PAPER
        )

    async def get_paper_by_doi(
        self,
        doi: str,
        fields: list[str] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get paper by DOI.

        Args:
            doi: DOI without prefix (e.g., "10.1038/nature12373")
            fields: Fields to return
            use_cache: Whether to use cache

        Returns:
            Paper data from API
        """
        # Normalize DOI format
        if not doi.startswith("DOI:"):
            doi = f"DOI:{doi}"
        return await self.get_paper(doi, fields=fields, use_cache=use_cache)

    async def get_paper_by_arxiv(
        self,
        arxiv_id: str,
        fields: list[str] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get paper by arXiv ID.

        Args:
            arxiv_id: arXiv ID without prefix (e.g., "2106.01345")
            fields: Fields to return
            use_cache: Whether to use cache

        Returns:
            Paper data from API
        """
        # Normalize arXiv format
        if not arxiv_id.startswith("ARXIV:"):
            arxiv_id = f"ARXIV:{arxiv_id}"
        return await self.get_paper(arxiv_id, fields=fields, use_cache=use_cache)

    async def search_papers(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        fields: list[str] | None = None,
        year: str | None = None,
        venue: str | None = None,
        fields_of_study: list[str] | None = None,
        open_access_pdf: bool | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Search for papers.

        Args:
            query: Search query
            limit: Maximum results (max 100)
            offset: Offset for pagination
            fields: Fields to return
            year: Year filter (e.g., "2020", "2019-2021", "2020-")
            venue: Venue filter
            fields_of_study: Filter by fields of study
            open_access_pdf: Filter by open access availability
            use_cache: Whether to use cache

        Returns:
            Search results with 'data' and 'total' fields
        """
        fields = fields or DEFAULT_PAPER_FIELDS
        fields_str = ",".join(fields)

        params: dict[str, Any] = {
            "query": query,
            "limit": min(limit, 100),  # API max is 100
            "offset": offset,
            "fields": fields_str,
        }

        if year:
            params["year"] = year
        if venue:
            params["venue"] = venue
        if fields_of_study:
            params["fieldsOfStudy"] = ",".join(fields_of_study)
        if open_access_pdf is not None:
            params["openAccessPdf"] = str(open_access_pdf).lower()

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(self.SOURCE, "search_papers", **params)

        return await self._make_request(
            "GET", "paper/search", params=params, cache_key=cache_key, cache_type=CacheType.SEARCH
        )

    async def get_author(
        self,
        author_id: str,
        fields: list[str] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get author by ID.

        Args:
            author_id: Semantic Scholar author ID
            fields: Fields to return
            use_cache: Whether to use cache

        Returns:
            Author data from API
        """
        fields = fields or DEFAULT_AUTHOR_FIELDS
        fields_str = ",".join(fields)

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(
                self.SOURCE, "get_author", author_id=author_id, fields=fields_str
            )

        endpoint = f"author/{author_id}"
        params = {"fields": fields_str}

        return await self._make_request(
            "GET", endpoint, params=params, cache_key=cache_key, cache_type=CacheType.AUTHOR
        )

    async def get_author_papers(
        self,
        author_id: str,
        limit: int = 100,
        offset: int = 0,
        fields: list[str] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get papers by an author.

        Args:
            author_id: Semantic Scholar author ID
            limit: Maximum results (max 1000)
            offset: Offset for pagination
            fields: Paper fields to return
            use_cache: Whether to use cache

        Returns:
            Author's papers with pagination info
        """
        fields = fields or DEFAULT_PAPER_FIELDS
        fields_str = ",".join(fields)

        params: dict[str, Any] = {
            "limit": min(limit, 1000),
            "offset": offset,
            "fields": fields_str,
        }

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(
                self.SOURCE, "get_author_papers", author_id=author_id, **params
            )

        endpoint = f"author/{author_id}/papers"

        return await self._make_request(
            "GET", endpoint, params=params, cache_key=cache_key, cache_type=CacheType.PAPER
        )

    async def get_paper_citations(
        self,
        paper_id: str,
        limit: int = 100,
        offset: int = 0,
        fields: list[str] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get papers that cite a given paper.

        Args:
            paper_id: Paper identifier
            limit: Maximum results (max 1000)
            offset: Offset for pagination
            fields: Paper fields to return
            use_cache: Whether to use cache

        Returns:
            Citing papers with pagination info
        """
        fields = fields or DEFAULT_PAPER_FIELDS
        fields_str = ",".join(fields)

        params: dict[str, Any] = {
            "limit": min(limit, 1000),
            "offset": offset,
            "fields": fields_str,
        }

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(
                self.SOURCE, "get_paper_citations", paper_id=paper_id, **params
            )

        endpoint = f"paper/{paper_id}/citations"

        return await self._make_request(
            "GET", endpoint, params=params, cache_key=cache_key, cache_type=CacheType.PAPER
        )

    async def get_paper_references(
        self,
        paper_id: str,
        limit: int = 100,
        offset: int = 0,
        fields: list[str] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get papers referenced by a given paper.

        Args:
            paper_id: Paper identifier
            limit: Maximum results (max 1000)
            offset: Offset for pagination
            fields: Paper fields to return
            use_cache: Whether to use cache

        Returns:
            Referenced papers with pagination info
        """
        fields = fields or DEFAULT_PAPER_FIELDS
        fields_str = ",".join(fields)

        params: dict[str, Any] = {
            "limit": min(limit, 1000),
            "offset": offset,
            "fields": fields_str,
        }

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(
                self.SOURCE, "get_paper_references", paper_id=paper_id, **params
            )

        endpoint = f"paper/{paper_id}/references"

        return await self._make_request(
            "GET", endpoint, params=params, cache_key=cache_key, cache_type=CacheType.PAPER
        )

    async def bulk_get_papers(
        self,
        paper_ids: list[str],
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get multiple papers by ID.

        Args:
            paper_ids: List of paper identifiers (max 500)
            fields: Fields to return

        Returns:
            List of paper data (may contain None for not found papers)
        """
        if len(paper_ids) > 500:
            raise ValueError("Maximum 500 papers per bulk request")

        fields = fields or DEFAULT_PAPER_FIELDS
        fields_str = ",".join(fields)

        # Bulk endpoint uses POST
        endpoint = "paper/batch"
        params = {"fields": fields_str}

        # Check circuit breaker and rate limit
        await self._circuit_breaker.check()
        await self._rate_limiter.acquire()

        try:
            async def do_request() -> dict[str, Any]:
                return await self.post(
                    endpoint,
                    json={"ids": paper_ids},
                    params=params,
                )

            result = await retry_with_backoff(
                do_request,
                max_retries=self.config.max_retries,
                source=self.SOURCE,
            )

            await self._circuit_breaker.record_success()

            # Cache individual papers
            for paper in result:
                if paper and "paperId" in paper:
                    cache_key = generate_cache_key(
                        self.SOURCE,
                        "get_paper",
                        identifier=paper["paperId"],
                        fields=fields_str,
                    )
                    self._cache.set(cache_key, paper, CacheType.PAPER)

            return result

        except Exception as e:
            await self._circuit_breaker.record_failure()
            raise


# Singleton instance
_client: SemanticScholarClient | None = None


def get_semantic_scholar_client() -> SemanticScholarClient:
    """Get the Semantic Scholar client singleton."""
    global _client
    if _client is None:
        _client = SemanticScholarClient()
    return _client


def reset_semantic_scholar_client() -> None:
    """Reset the client singleton (useful for testing)."""
    global _client
    _client = None


__all__ = [
    "SemanticScholarClient",
    "get_semantic_scholar_client",
    "reset_semantic_scholar_client",
    "DEFAULT_PAPER_FIELDS",
    "DEFAULT_AUTHOR_FIELDS",
]
