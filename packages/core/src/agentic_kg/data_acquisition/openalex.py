"""
OpenAlex API client.

Provides access to the OpenAlex API for scholarly metadata including
works, authors, venues, and concepts.
"""
from __future__ import annotations


import logging
import re
from typing import Any

import httpx

from agentic_kg.data_acquisition.cache import (
    CacheType,
    ResponseCache,
    generate_cache_key,
    get_response_cache,
)
from agentic_kg.data_acquisition.config import (
    OpenAlexConfig,
    get_data_acquisition_config,
)
from agentic_kg.data_acquisition.exceptions import APIError, NotFoundError
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

# Pattern for OpenAlex IDs
OPENALEX_ID_PATTERN = re.compile(r"^(?:https://openalex\.org/)?(W\d+)$", re.IGNORECASE)
DOI_PATTERN = re.compile(r"^(?:https?://doi\.org/)?(.+)$")


def normalize_openalex_id(identifier: str) -> str:
    """
    Normalize an OpenAlex identifier.

    Args:
        identifier: OpenAlex ID in various formats

    Returns:
        Normalized OpenAlex ID (e.g., "W2741809807")

    Examples:
        normalize_openalex_id("W2741809807") -> "W2741809807"
        normalize_openalex_id("https://openalex.org/W2741809807") -> "W2741809807"
    """
    match = OPENALEX_ID_PATTERN.match(identifier)
    if match:
        return match.group(1)
    return identifier


def reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """
    Reconstruct abstract from OpenAlex inverted index format.

    OpenAlex stores abstracts as an inverted index where:
    - Keys are words
    - Values are lists of positions where the word appears

    Args:
        inverted_index: The abstract_inverted_index from OpenAlex

    Returns:
        Reconstructed abstract text, or None if no index
    """
    if not inverted_index:
        return None

    # Find the total length
    max_pos = -1
    for positions in inverted_index.values():
        if positions:
            max_pos = max(max_pos, max(positions))

    if max_pos < 0:
        return None

    # Build the abstract
    words: list[str | None] = [None] * (max_pos + 1)
    for word, positions in inverted_index.items():
        for pos in positions:
            words[pos] = word

    # Join words, handling any missing positions
    result = []
    for word in words:
        if word is not None:
            result.append(word)

    return " ".join(result) if result else None


class OpenAlexClient:
    """
    Client for the OpenAlex API.

    Features:
    - Work (paper) lookup by ID or DOI
    - Work search with filters
    - Author information
    - Abstract reconstruction from inverted index
    - Polite pool identification
    - Rate limiting, caching, and circuit breaker
    """

    SOURCE = "openalex"

    def __init__(
        self,
        config: OpenAlexConfig | None = None,
        cache: ResponseCache | None = None,
        rate_limiter: TokenBucketRateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        """
        Initialize OpenAlex client.

        Args:
            config: API configuration
            cache: Response cache (uses singleton if not provided)
            rate_limiter: Rate limiter (uses registry if not provided)
            circuit_breaker: Circuit breaker (uses registry if not provided)
        """
        self.config = config or get_data_acquisition_config().openalex

        # Infrastructure
        self._cache = cache or get_response_cache()
        self._rate_limiter = rate_limiter or get_rate_limiter_registry().get(
            self.SOURCE, self.config.rate_limit
        )
        self._circuit_breaker = circuit_breaker or get_circuit_breaker_registry().get(
            self.SOURCE
        )

        # HTTP client
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            headers = {
                "Accept": "application/json",
                "User-Agent": self.config.user_agent,
            }
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout),
                headers=headers,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "OpenAlexClient":
        """Async context manager entry."""
        await self._get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _make_request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        cache_key: str | None = None,
        cache_type: CacheType = CacheType.PAPER,
    ) -> dict[str, Any]:
        """
        Make an API request with rate limiting, caching, and circuit breaker.

        Args:
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
            async def do_request() -> dict[str, Any]:
                client = await self._get_client()

                # Add polite pool email if configured
                request_params = dict(params) if params else {}
                if self.config.email:
                    request_params["mailto"] = self.config.email

                response = await client.get(endpoint, params=request_params)

                if response.status_code == 404:
                    raise NotFoundError(
                        resource_type="work",
                        identifier=endpoint,
                        source=self.SOURCE,
                    )

                if response.status_code != 200:
                    raise APIError(
                        message=f"Request failed with status {response.status_code}",
                        source=self.SOURCE,
                        status_code=response.status_code,
                        response_body=response.text[:500] if response.text else None,
                    )

                return response.json()

            result = await retry_with_backoff(
                do_request,
                max_retries=self.config.max_retries,
                source=self.SOURCE,
            )

            await self._circuit_breaker.record_success()

            # Cache result
            if cache_key:
                self._cache.set(cache_key, result, cache_type)

            return result

        except NotFoundError:
            raise
        except Exception as e:
            await self._circuit_breaker.record_failure()
            raise

    def _enrich_work(self, work: dict[str, Any]) -> dict[str, Any]:
        """
        Enrich work data with reconstructed abstract.

        Args:
            work: Raw work data from API

        Returns:
            Work data with reconstructed abstract
        """
        # Reconstruct abstract if available
        if "abstract_inverted_index" in work:
            work["abstract"] = reconstruct_abstract(work.get("abstract_inverted_index"))
        return work

    async def get_work(
        self,
        identifier: str,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get work by OpenAlex ID or DOI.

        Args:
            identifier: OpenAlex ID (e.g., "W2741809807") or DOI
            use_cache: Whether to use cache

        Returns:
            Work data from API

        Raises:
            NotFoundError: If work not found
        """
        # Determine if DOI or OpenAlex ID
        if identifier.startswith("W") or identifier.startswith("https://openalex.org/W"):
            # OpenAlex ID
            openalex_id = normalize_openalex_id(identifier)
            endpoint = f"/works/{openalex_id}"
            cache_params = {"openalex_id": openalex_id}
        else:
            # Assume DOI
            doi_match = DOI_PATTERN.match(identifier)
            doi = doi_match.group(1) if doi_match else identifier
            endpoint = f"/works/https://doi.org/{doi}"
            cache_params = {"doi": doi}

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(self.SOURCE, "get_work", **cache_params)

        result = await self._make_request(endpoint, cache_key=cache_key)
        return self._enrich_work(result)

    async def get_work_by_doi(
        self,
        doi: str,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get work by DOI.

        Args:
            doi: DOI (e.g., "10.1038/nature12373")
            use_cache: Whether to use cache

        Returns:
            Work data from API
        """
        # Strip doi.org prefix if present
        doi_match = DOI_PATTERN.match(doi)
        clean_doi = doi_match.group(1) if doi_match else doi
        return await self.get_work(clean_doi, use_cache=use_cache)

    async def search_works(
        self,
        query: str | None = None,
        filter_params: dict[str, Any] | None = None,
        per_page: int = 25,
        page: int = 1,
        sort: str | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Search for works.

        Args:
            query: Search query (searches across titles, abstracts, etc.)
            filter_params: Filter parameters (see OpenAlex docs for options)
            per_page: Results per page (max 200)
            page: Page number (1-indexed)
            sort: Sort field (e.g., "cited_by_count:desc", "publication_date:asc")
            use_cache: Whether to use cache

        Returns:
            Search results with 'results' and 'meta' fields

        Filter examples:
            - {"publication_year": 2023}
            - {"authorships.author.id": "A1969205032"}
            - {"concepts.id": "C41008148"}  # Computer Science
            - {"is_oa": True}
        """
        params: dict[str, Any] = {
            "per-page": min(per_page, 200),
            "page": page,
        }

        if query:
            params["search"] = query

        if filter_params:
            # Build filter string
            filters = []
            for key, value in filter_params.items():
                if isinstance(value, bool):
                    filters.append(f"{key}:{str(value).lower()}")
                elif isinstance(value, list):
                    filters.append(f"{key}:{'|'.join(str(v) for v in value)}")
                else:
                    filters.append(f"{key}:{value}")
            if filters:
                params["filter"] = ",".join(filters)

        if sort:
            params["sort"] = sort

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(self.SOURCE, "search_works", **params)

        result = await self._make_request(
            "/works", params=params, cache_key=cache_key, cache_type=CacheType.SEARCH
        )

        # Enrich each work with reconstructed abstract
        if "results" in result:
            result["results"] = [self._enrich_work(w) for w in result["results"]]

        return {
            "data": result.get("results", []),
            "meta": result.get("meta", {}),
            "total": result.get("meta", {}).get("count", 0),
            "per_page": result.get("meta", {}).get("per_page", per_page),
            "page": result.get("meta", {}).get("page", page),
        }

    async def get_author(
        self,
        author_id: str,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get author by OpenAlex ID.

        Args:
            author_id: OpenAlex author ID (e.g., "A1969205032")
            use_cache: Whether to use cache

        Returns:
            Author data from API
        """
        # Normalize ID format
        if not author_id.startswith("A"):
            if author_id.startswith("https://openalex.org/"):
                author_id = author_id.replace("https://openalex.org/", "")

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(
                self.SOURCE, "get_author", author_id=author_id
            )

        endpoint = f"/authors/{author_id}"

        return await self._make_request(
            endpoint, cache_key=cache_key, cache_type=CacheType.AUTHOR
        )

    async def get_author_works(
        self,
        author_id: str,
        per_page: int = 25,
        page: int = 1,
        sort: str = "publication_date:desc",
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get works by an author.

        Args:
            author_id: OpenAlex author ID
            per_page: Results per page (max 200)
            page: Page number
            sort: Sort field
            use_cache: Whether to use cache

        Returns:
            Author's works with pagination info
        """
        # Normalize ID format
        if not author_id.startswith("A"):
            if author_id.startswith("https://openalex.org/"):
                author_id = author_id.replace("https://openalex.org/", "")

        filter_params = {"authorships.author.id": author_id}

        return await self.search_works(
            filter_params=filter_params,
            per_page=per_page,
            page=page,
            sort=sort,
            use_cache=use_cache,
        )

    async def get_random_works(
        self,
        count: int = 10,
        seed: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get random works (useful for testing/sampling).

        Args:
            count: Number of random works
            seed: Random seed for reproducibility

        Returns:
            List of random works
        """
        params: dict[str, Any] = {
            "per-page": min(count, 200),
            "sample": count,
        }
        if seed is not None:
            params["seed"] = seed

        result = await self._make_request("/works", params=params)

        if "results" in result:
            return [self._enrich_work(w) for w in result["results"]]
        return []


# Singleton instance
_client: OpenAlexClient | None = None


def get_openalex_client() -> OpenAlexClient:
    """Get the OpenAlex client singleton."""
    global _client
    if _client is None:
        _client = OpenAlexClient()
    return _client


def reset_openalex_client() -> None:
    """Reset the client singleton (useful for testing)."""
    global _client
    _client = None


__all__ = [
    "OpenAlexClient",
    "get_openalex_client",
    "reset_openalex_client",
    "normalize_openalex_id",
    "reconstruct_abstract",
]
