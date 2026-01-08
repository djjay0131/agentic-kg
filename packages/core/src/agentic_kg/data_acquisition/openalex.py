"""
OpenAlex API client.

Provides access to paper metadata, open access URLs, and author information
via the OpenAlex API.

API Documentation: https://docs.openalex.org/
"""

import logging
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from agentic_kg.config import OpenAlexConfig, get_config
from agentic_kg.data_acquisition.models import (
    AuthorRef,
    PaperMetadata,
    SourceType,
)

logger = logging.getLogger(__name__)


class OpenAlexError(Exception):
    """Base exception for OpenAlex API errors."""

    pass


class OpenAlexNotFoundError(OpenAlexError):
    """Raised when a work is not found."""

    pass


class OpenAlexRateLimitError(OpenAlexError):
    """Raised when rate limit is exceeded."""

    pass


class OpenAlexClient:
    """
    Client for the OpenAlex API.

    Provides methods for retrieving paper metadata, finding open access URLs,
    and searching academic works.

    Example:
        client = OpenAlexClient()

        # Get work by DOI
        work = client.get_work_by_doi("10.1038/nature12373")

        # Search for works
        results = client.search_works("machine learning")

        # Get open access PDF URL
        pdf_url = client.get_open_access_url(work)
    """

    def __init__(self, config: Optional[OpenAlexConfig] = None):
        """
        Initialize the OpenAlex client.

        Args:
            config: Configuration for the client. Uses global config if not provided.
        """
        self._config = config or get_config().data_acquisition.openalex
        self._client: Optional[httpx.Client] = None
        self._last_request_time: float = 0.0

    @property
    def client(self) -> httpx.Client:
        """Lazy-load the HTTP client."""
        if self._client is None:
            headers = {
                "Accept": "application/json",
                "User-Agent": "agentic-kg/1.0 (research tool)",
            }
            # Add polite pool email for higher rate limits
            if self._config.email:
                headers["mailto"] = self._config.email

            self._client = httpx.Client(
                base_url=self._config.base_url,
                headers=headers,
                timeout=self._config.timeout,
            )
        return self._client

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "OpenAlexClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _wait_for_rate_limit(self) -> None:
        """Wait if necessary to respect rate limits."""
        min_interval = 1.0 / self._config.rate_limit
        elapsed = time.time() - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    def _make_request(
        self,
        path: str,
        params: Optional[dict] = None,
    ) -> dict:
        """
        Make an API request with rate limiting and retry logic.

        Args:
            path: API path
            params: Query parameters

        Returns:
            JSON response as dict

        Raises:
            OpenAlexError: On API errors
        """
        last_error: Optional[Exception] = None

        # Add email to params for polite pool
        if params is None:
            params = {}
        if self._config.email and "mailto" not in params:
            params["mailto"] = self._config.email

        for attempt in range(self._config.max_retries):
            self._wait_for_rate_limit()

            try:
                self._last_request_time = time.time()
                response = self.client.get(path, params=params)

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 404:
                    raise OpenAlexNotFoundError(f"Work not found: {path}")

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "60")
                    retry_seconds = int(retry_after)
                    if attempt < self._config.max_retries - 1:
                        logger.warning(
                            f"Rate limited, waiting {retry_seconds}s before retry"
                        )
                        time.sleep(retry_seconds)
                        continue
                    raise OpenAlexRateLimitError("Rate limit exceeded")

                response.raise_for_status()

            except httpx.HTTPStatusError as e:
                last_error = OpenAlexError(
                    f"HTTP error {e.response.status_code}: {e.response.text}"
                )
                if attempt < self._config.max_retries - 1:
                    delay = self._config.retry_delay * (2**attempt)
                    logger.warning(f"Request failed, retrying in {delay}s: {e}")
                    time.sleep(delay)
                    continue
                raise last_error from e

            except httpx.RequestError as e:
                last_error = OpenAlexError(f"Request error: {e}")
                if attempt < self._config.max_retries - 1:
                    delay = self._config.retry_delay * (2**attempt)
                    logger.warning(f"Request failed, retrying in {delay}s: {e}")
                    time.sleep(delay)
                    continue
                raise last_error from e

        raise last_error or OpenAlexError("Request failed after retries")

    def _parse_work(self, data: dict) -> PaperMetadata:
        """Parse OpenAlex work data into PaperMetadata."""
        # Extract IDs
        openalex_id = data.get("id", "").replace("https://openalex.org/", "")
        doi = data.get("doi", "")
        if doi:
            doi = doi.replace("https://doi.org/", "")

        # Parse authors
        authors = []
        for authorship in data.get("authorships") or []:
            author_data = authorship.get("author") or {}
            name = author_data.get("display_name", "Unknown")

            # Get affiliations
            affiliations = []
            for inst in authorship.get("institutions") or []:
                inst_name = inst.get("display_name")
                if inst_name:
                    affiliations.append(inst_name)

            # Get ORCID
            orcid = author_data.get("orcid")
            if orcid:
                orcid = orcid.replace("https://orcid.org/", "")

            authors.append(
                AuthorRef(
                    name=name,
                    author_id=author_data.get("id", "").replace(
                        "https://openalex.org/", ""
                    ),
                    orcid=orcid,
                    affiliations=affiliations,
                )
            )

        # Parse publication date
        pub_date = None
        pub_date_str = data.get("publication_date")
        if pub_date_str:
            try:
                pub_date = datetime.fromisoformat(pub_date_str)
            except ValueError:
                pass

        # Get open access info
        is_open_access = data.get("open_access", {}).get("is_oa", False)
        pdf_url = data.get("open_access", {}).get("oa_url")

        # Get best open access URL
        if not pdf_url:
            pdf_url = self._extract_best_oa_url(data)

        # Get venue
        venue = None
        primary_location = data.get("primary_location") or {}
        source = primary_location.get("source") or {}
        if source:
            venue = source.get("display_name")

        # Get fields of study (concepts)
        fields = []
        for concept in data.get("concepts") or []:
            if concept.get("level", 0) <= 1:  # Top-level concepts only
                display_name = concept.get("display_name")
                if display_name:
                    fields.append(display_name)

        return PaperMetadata(
            paper_id=openalex_id,
            openalex_id=openalex_id,
            doi=doi if doi else None,
            title=data.get("title", ""),
            abstract=self._reconstruct_abstract(data.get("abstract_inverted_index")),
            authors=authors,
            year=data.get("publication_year"),
            venue=venue,
            publication_date=pub_date,
            url=data.get("id"),
            pdf_url=pdf_url,
            is_open_access=is_open_access,
            source=SourceType.OPENALEX,
            citation_count=data.get("cited_by_count"),
            fields_of_study=fields,
        )

    def _reconstruct_abstract(
        self, inverted_index: Optional[dict[str, list[int]]]
    ) -> Optional[str]:
        """Reconstruct abstract from inverted index format."""
        if not inverted_index:
            return None

        # Build position -> word mapping
        positions: dict[int, str] = {}
        for word, indices in inverted_index.items():
            for pos in indices:
                positions[pos] = word

        # Reconstruct in order
        if not positions:
            return None

        max_pos = max(positions.keys())
        words = [positions.get(i, "") for i in range(max_pos + 1)]
        return " ".join(words)

    def _extract_best_oa_url(self, data: dict) -> Optional[str]:
        """Extract the best open access URL from work data."""
        # Try primary location first
        primary_location = data.get("primary_location") or {}
        pdf_url = primary_location.get("pdf_url")
        if pdf_url:
            return pdf_url

        # Check other locations
        for location in data.get("locations") or []:
            pdf_url = location.get("pdf_url")
            if pdf_url:
                return pdf_url

        # Try best_oa_location
        best_oa = data.get("best_oa_location") or {}
        pdf_url = best_oa.get("pdf_url")
        if pdf_url:
            return pdf_url

        return None

    def get_work(self, openalex_id: str) -> PaperMetadata:
        """
        Get a work by OpenAlex ID.

        Args:
            openalex_id: OpenAlex work ID (e.g., "W2741809807")

        Returns:
            Paper metadata

        Raises:
            OpenAlexNotFoundError: If work not found
        """
        # Clean ID
        clean_id = openalex_id.replace("https://openalex.org/", "")
        data = self._make_request(f"/works/{clean_id}")
        return self._parse_work(data)

    def get_work_by_doi(self, doi: str) -> PaperMetadata:
        """
        Get a work by DOI.

        Args:
            doi: DOI (e.g., "10.1038/nature12373")

        Returns:
            Paper metadata

        Raises:
            OpenAlexNotFoundError: If work not found
        """
        # Clean DOI
        clean_doi = doi
        for prefix in ["https://doi.org/", "http://doi.org/", "doi:"]:
            if clean_doi.lower().startswith(prefix.lower()):
                clean_doi = clean_doi[len(prefix) :]

        data = self._make_request(f"/works/https://doi.org/{clean_doi}")
        return self._parse_work(data)

    def search_works(
        self,
        query: str,
        filters: Optional[dict[str, Any]] = None,
        page: int = 1,
        per_page: int = 25,
        sort: Optional[str] = None,
    ) -> list[PaperMetadata]:
        """
        Search for works.

        Args:
            query: Search query (searches title and abstract)
            filters: OpenAlex filter dict (e.g., {"publication_year": 2023})
            page: Page number (1-indexed)
            per_page: Results per page (max 200)
            sort: Sort order (e.g., "cited_by_count:desc", "publication_date:desc")

        Returns:
            List of matching works
        """
        params: dict[str, Any] = {
            "search": query,
            "page": page,
            "per_page": min(per_page, 200),
        }

        if filters:
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, list):
                    filter_parts.append(f"{key}:{'|'.join(str(v) for v in value)}")
                else:
                    filter_parts.append(f"{key}:{value}")
            params["filter"] = ",".join(filter_parts)

        if sort:
            params["sort"] = sort

        data = self._make_request("/works", params)

        works = []
        for item in data.get("results") or []:
            try:
                works.append(self._parse_work(item))
            except Exception as e:
                logger.warning(f"Failed to parse work: {e}")

        return works

    def get_works_by_author(
        self,
        author_id: str,
        page: int = 1,
        per_page: int = 25,
    ) -> list[PaperMetadata]:
        """
        Get works by a specific author.

        Args:
            author_id: OpenAlex author ID
            page: Page number
            per_page: Results per page

        Returns:
            List of works by the author
        """
        clean_id = author_id.replace("https://openalex.org/", "")
        params = {
            "filter": f"author.id:{clean_id}",
            "page": page,
            "per_page": min(per_page, 200),
        }

        data = self._make_request("/works", params)

        works = []
        for item in data.get("results") or []:
            try:
                works.append(self._parse_work(item))
            except Exception as e:
                logger.warning(f"Failed to parse work: {e}")

        return works

    def get_open_access_url(self, work: PaperMetadata) -> Optional[str]:
        """
        Get the open access PDF URL for a work.

        Args:
            work: Paper metadata (must have openalex_id)

        Returns:
            PDF URL if available, None otherwise
        """
        if work.pdf_url:
            return work.pdf_url

        if not work.openalex_id:
            return None

        # Fetch fresh data to check for OA
        try:
            fresh = self.get_work(work.openalex_id)
            return fresh.pdf_url
        except OpenAlexNotFoundError:
            return None

    def get_author(self, author_id: str) -> dict:
        """
        Get author information.

        Args:
            author_id: OpenAlex author ID

        Returns:
            Author data dict
        """
        clean_id = author_id.replace("https://openalex.org/", "")
        return self._make_request(f"/authors/{clean_id}")

    def get_institution(self, institution_id: str) -> dict:
        """
        Get institution information.

        Args:
            institution_id: OpenAlex institution ID

        Returns:
            Institution data dict
        """
        clean_id = institution_id.replace("https://openalex.org/", "")
        return self._make_request(f"/institutions/{clean_id}")


# Singleton client
_client: Optional[OpenAlexClient] = None


def get_openalex_client() -> OpenAlexClient:
    """Get the OpenAlex client singleton."""
    global _client
    if _client is None:
        _client = OpenAlexClient()
    return _client


def reset_openalex_client() -> None:
    """Reset the client singleton."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
