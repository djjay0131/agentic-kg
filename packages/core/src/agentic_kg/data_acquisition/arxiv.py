"""
arXiv API client.

Provides access to the arXiv API for preprint metadata and PDF URLs.
"""

import logging
import re
from typing import Any
from xml.etree import ElementTree

import feedparser
import httpx

from agentic_kg.data_acquisition.cache import (
    CacheType,
    ResponseCache,
    generate_cache_key,
    get_response_cache,
)
from agentic_kg.data_acquisition.config import (
    ArxivConfig,
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

# arXiv Atom namespaces
ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"

# Pattern for extracting arXiv ID from URL or identifier
ARXIV_ID_PATTERN = re.compile(
    r"(?:arxiv[:\.])?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)",
    re.IGNORECASE,
)


def normalize_arxiv_id(identifier: str) -> str:
    """
    Normalize an arXiv identifier.

    Args:
        identifier: arXiv ID in various formats

    Returns:
        Normalized arXiv ID (e.g., "2106.01345" or "hep-th/9901001")

    Examples:
        normalize_arxiv_id("arxiv:2106.01345") -> "2106.01345"
        normalize_arxiv_id("2106.01345v2") -> "2106.01345v2"
        normalize_arxiv_id("https://arxiv.org/abs/2106.01345") -> "2106.01345"
    """
    match = ARXIV_ID_PATTERN.search(identifier)
    if match:
        return match.group(1)
    return identifier


def construct_pdf_url(arxiv_id: str, config: ArxivConfig | None = None) -> str:
    """
    Construct PDF download URL from arXiv ID.

    Args:
        arxiv_id: Normalized arXiv ID
        config: arXiv configuration

    Returns:
        PDF URL
    """
    config = config or ArxivConfig()
    # Remove version suffix for base URL
    base_id = re.sub(r"v\d+$", "", arxiv_id)
    return f"{config.pdf_base_url}/{base_id}.pdf"


def construct_abs_url(arxiv_id: str, config: ArxivConfig | None = None) -> str:
    """
    Construct abstract page URL from arXiv ID.

    Args:
        arxiv_id: Normalized arXiv ID
        config: arXiv configuration

    Returns:
        Abstract page URL
    """
    config = config or ArxivConfig()
    return f"{config.abs_base_url}/{arxiv_id}"


class ArxivClient:
    """
    Client for the arXiv API.

    Features:
    - Paper lookup by arXiv ID
    - Paper search with pagination
    - Category filtering
    - PDF URL construction
    - Rate limiting, caching, and circuit breaker

    Note: arXiv API uses Atom feeds, not JSON.
    """

    SOURCE = "arxiv"

    def __init__(
        self,
        config: ArxivConfig | None = None,
        cache: ResponseCache | None = None,
        rate_limiter: TokenBucketRateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        """
        Initialize arXiv client.

        Args:
            config: API configuration
            cache: Response cache (uses singleton if not provided)
            rate_limiter: Rate limiter (uses registry if not provided)
            circuit_breaker: Circuit breaker (uses registry if not provided)
        """
        self.config = config or get_data_acquisition_config().arxiv

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
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "ArxivClient":
        """Async context manager entry."""
        await self._get_client()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _make_request(
        self,
        params: dict[str, Any],
        cache_key: str | None = None,
        cache_type: CacheType = CacheType.PAPER,
    ) -> dict[str, Any]:
        """
        Make an API request with rate limiting, caching, and circuit breaker.

        Args:
            params: Query parameters
            cache_key: Cache key (if caching desired)
            cache_type: Type of cache to use

        Returns:
            Parsed response data
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
                response = await client.get(self.config.base_url, params=params)

                if response.status_code != 200:
                    raise APIError(
                        message=f"Request failed with status {response.status_code}",
                        source=self.SOURCE,
                        status_code=response.status_code,
                        response_body=response.text[:500] if response.text else None,
                    )

                return self._parse_response(response.text)

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

        except Exception as e:
            await self._circuit_breaker.record_failure()
            raise

    def _parse_response(self, xml_text: str) -> dict[str, Any]:
        """
        Parse arXiv Atom feed response.

        Args:
            xml_text: Raw XML response

        Returns:
            Parsed response with entries
        """
        # Use feedparser for robust Atom parsing
        feed = feedparser.parse(xml_text)

        if feed.bozo and not feed.entries:
            raise APIError(
                message=f"Failed to parse Atom feed: {feed.bozo_exception}",
                source=self.SOURCE,
            )

        # Extract total results from opensearch namespace
        total_results = int(
            feed.feed.get("opensearch_totalresults", len(feed.entries))
        )
        start_index = int(feed.feed.get("opensearch_startindex", 0))
        items_per_page = int(
            feed.feed.get("opensearch_itemsperpage", len(feed.entries))
        )

        # Parse entries
        entries = []
        for entry in feed.entries:
            parsed = self._parse_entry(entry)
            if parsed:
                entries.append(parsed)

        return {
            "total_results": total_results,
            "start_index": start_index,
            "items_per_page": items_per_page,
            "entries": entries,
        }

    def _parse_entry(self, entry: Any) -> dict[str, Any] | None:
        """
        Parse a single Atom entry into a paper dict.

        Args:
            entry: feedparser entry object

        Returns:
            Parsed paper data
        """
        try:
            # Extract arXiv ID from entry ID
            entry_id = entry.get("id", "")
            arxiv_id = normalize_arxiv_id(entry_id)

            # Parse authors
            authors = []
            for author in entry.get("authors", []):
                author_data = {
                    "name": author.get("name", ""),
                }
                # Check for affiliation in arxiv namespace
                if hasattr(author, "arxiv_affiliation"):
                    author_data["affiliation"] = author.arxiv_affiliation
                authors.append(author_data)

            # Parse categories
            categories = []
            primary_category = None
            for tag in entry.get("tags", []):
                term = tag.get("term", "")
                if term:
                    categories.append(term)
                    if primary_category is None:
                        primary_category = term

            # Check for arxiv:primary_category
            if hasattr(entry, "arxiv_primary_category"):
                primary_category = entry.arxiv_primary_category.get("term", primary_category)

            # Extract DOI if available
            doi = None
            if hasattr(entry, "arxiv_doi"):
                doi = entry.arxiv_doi

            # Extract comment (often contains page count, etc.)
            comment = None
            if hasattr(entry, "arxiv_comment"):
                comment = entry.arxiv_comment

            # Extract journal reference if available
            journal_ref = None
            if hasattr(entry, "arxiv_journal_ref"):
                journal_ref = entry.arxiv_journal_ref

            return {
                "id": arxiv_id,
                "title": entry.get("title", "").replace("\n", " ").strip(),
                "summary": entry.get("summary", "").strip(),
                "authors": authors,
                "published": entry.get("published", ""),
                "updated": entry.get("updated", ""),
                "categories": categories,
                "primary_category": primary_category,
                "doi": doi,
                "comment": comment,
                "journal_ref": journal_ref,
                "pdf_url": construct_pdf_url(arxiv_id, self.config),
                "abs_url": construct_abs_url(arxiv_id, self.config),
                # Links
                "links": [
                    {"href": link.get("href", ""), "type": link.get("type", "")}
                    for link in entry.get("links", [])
                ],
            }

        except Exception as e:
            logger.warning("Failed to parse arXiv entry: %s", str(e))
            return None

    async def get_paper(
        self,
        identifier: str,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Get paper by arXiv ID.

        Args:
            identifier: arXiv ID (e.g., "2106.01345", "arxiv:2106.01345")
            use_cache: Whether to use cache

        Returns:
            Paper data from API

        Raises:
            NotFoundError: If paper not found
        """
        arxiv_id = normalize_arxiv_id(identifier)

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(self.SOURCE, "get_paper", arxiv_id=arxiv_id)

        params = {
            "id_list": arxiv_id,
            "max_results": 1,
        }

        result = await self._make_request(params, cache_key=cache_key)

        if not result["entries"]:
            raise NotFoundError(
                resource_type="paper",
                identifier=arxiv_id,
                source=self.SOURCE,
            )

        return result["entries"][0]

    async def search_papers(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        sort_by: str = "relevance",
        sort_order: str = "descending",
        categories: list[str] | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """
        Search for papers.

        Args:
            query: Search query (supports arXiv query syntax)
            limit: Maximum results (max 30000 total offset + limit)
            offset: Offset for pagination
            sort_by: Sort field ("relevance", "lastUpdatedDate", "submittedDate")
            sort_order: Sort order ("ascending", "descending")
            categories: Filter by arXiv categories (e.g., ["cs.AI", "cs.LG"])
            use_cache: Whether to use cache

        Returns:
            Search results with entries and pagination info

        Note:
            arXiv query syntax supports:
            - ti:term - title search
            - au:name - author search
            - abs:term - abstract search
            - cat:category - category filter
            - all:term - search all fields
            - AND, OR, ANDNOT for combining
        """
        # Build search query
        search_query = query

        # Add category filter if specified
        if categories:
            cat_query = " OR ".join(f"cat:{cat}" for cat in categories)
            search_query = f"({search_query}) AND ({cat_query})"

        params: dict[str, Any] = {
            "search_query": search_query,
            "start": offset,
            "max_results": min(limit, 100),  # Be conservative
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }

        cache_key = None
        if use_cache:
            cache_key = generate_cache_key(self.SOURCE, "search_papers", **params)

        result = await self._make_request(
            params, cache_key=cache_key, cache_type=CacheType.SEARCH
        )

        return {
            "data": result["entries"],
            "total": result["total_results"],
            "offset": result["start_index"],
            "limit": len(result["entries"]),
        }

    async def get_papers_by_ids(
        self,
        arxiv_ids: list[str],
        use_cache: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Get multiple papers by arXiv IDs.

        Args:
            arxiv_ids: List of arXiv IDs
            use_cache: Whether to use cache

        Returns:
            List of paper data
        """
        # Normalize IDs
        normalized_ids = [normalize_arxiv_id(id) for id in arxiv_ids]

        # arXiv API accepts comma-separated ID list
        params = {
            "id_list": ",".join(normalized_ids),
            "max_results": len(normalized_ids),
        }

        result = await self._make_request(params)

        # Cache individual papers
        if use_cache:
            for paper in result["entries"]:
                if paper and "id" in paper:
                    cache_key = generate_cache_key(
                        self.SOURCE, "get_paper", arxiv_id=paper["id"]
                    )
                    self._cache.set(cache_key, paper, CacheType.PAPER)

        return result["entries"]


# Singleton instance
_client: ArxivClient | None = None


def get_arxiv_client() -> ArxivClient:
    """Get the arXiv client singleton."""
    global _client
    if _client is None:
        _client = ArxivClient()
    return _client


def reset_arxiv_client() -> None:
    """Reset the client singleton (useful for testing)."""
    global _client
    _client = None


__all__ = [
    "ArxivClient",
    "get_arxiv_client",
    "reset_arxiv_client",
    "normalize_arxiv_id",
    "construct_pdf_url",
    "construct_abs_url",
]
