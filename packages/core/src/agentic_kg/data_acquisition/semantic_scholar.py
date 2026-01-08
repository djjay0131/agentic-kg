"""
Semantic Scholar API client.

Provides access to paper metadata, citations, references, and SPECTER2 embeddings
via the Semantic Scholar Academic Graph API.

API Documentation: https://api.semanticscholar.org/api-docs/graph
"""

import logging
import time
from datetime import datetime
from typing import Optional

import httpx

from agentic_kg.config import SemanticScholarConfig, get_config
from agentic_kg.data_acquisition.models import (
    AuthorRef,
    Citation,
    PaperMetadata,
    SourceType,
)

logger = logging.getLogger(__name__)


class SemanticScholarError(Exception):
    """Base exception for Semantic Scholar API errors."""

    pass


class RateLimitError(SemanticScholarError):
    """Raised when rate limit is exceeded."""

    def __init__(self, retry_after: Optional[int] = None):
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit exceeded. Retry after {retry_after}s"
            if retry_after
            else "Rate limit exceeded"
        )


class NotFoundError(SemanticScholarError):
    """Raised when a paper is not found."""

    pass


# Default fields to request from the API
DEFAULT_PAPER_FIELDS = [
    "paperId",
    "externalIds",
    "title",
    "abstract",
    "year",
    "venue",
    "publicationDate",
    "authors",
    "citationCount",
    "influentialCitationCount",
    "isOpenAccess",
    "openAccessPdf",
    "fieldsOfStudy",
]

CITATION_FIELDS = [
    "paperId",
    "externalIds",
    "title",
    "year",
    "isInfluential",
]

EMBEDDING_FIELDS = ["embedding"]


class SemanticScholarClient:
    """
    Client for the Semantic Scholar Academic Graph API.

    Provides methods for searching papers, retrieving metadata,
    and accessing citations, references, and SPECTER2 embeddings.

    Example:
        client = SemanticScholarClient()

        # Search for papers
        results = client.search_papers("attention mechanism transformer")

        # Get paper by ID
        paper = client.get_paper("649def34f8be52c8b66281af98ae884c09aef38b")

        # Get paper by DOI
        paper = client.get_paper_by_doi("10.48550/arXiv.1706.03762")
    """

    def __init__(self, config: Optional[SemanticScholarConfig] = None):
        """
        Initialize the Semantic Scholar client.

        Args:
            config: Configuration for the client. Uses global config if not provided.
        """
        self._config = config or get_config().data_acquisition.semantic_scholar
        self._client: Optional[httpx.Client] = None
        self._last_request_time: float = 0.0

    @property
    def client(self) -> httpx.Client:
        """Lazy-load the HTTP client."""
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self._config.api_key:
                headers["x-api-key"] = self._config.api_key

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

    def __enter__(self) -> "SemanticScholarClient":
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
        method: str,
        path: str,
        params: Optional[dict] = None,
    ) -> dict:
        """
        Make an API request with rate limiting and retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path
            params: Query parameters

        Returns:
            JSON response as dict

        Raises:
            SemanticScholarError: On API errors
            RateLimitError: When rate limited
            NotFoundError: When resource not found
        """
        last_error: Optional[Exception] = None

        for attempt in range(self._config.max_retries):
            self._wait_for_rate_limit()

            try:
                self._last_request_time = time.time()
                response = self.client.request(method, path, params=params)

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 404:
                    raise NotFoundError(f"Resource not found: {path}")

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    retry_seconds = int(retry_after) if retry_after else 60
                    if attempt < self._config.max_retries - 1:
                        logger.warning(
                            f"Rate limited, waiting {retry_seconds}s before retry"
                        )
                        time.sleep(retry_seconds)
                        continue
                    raise RateLimitError(retry_seconds)

                # Other errors
                response.raise_for_status()

            except httpx.HTTPStatusError as e:
                last_error = SemanticScholarError(
                    f"HTTP error {e.response.status_code}: {e.response.text}"
                )
                if attempt < self._config.max_retries - 1:
                    delay = self._config.retry_delay * (2**attempt)
                    logger.warning(f"Request failed, retrying in {delay}s: {e}")
                    time.sleep(delay)
                    continue
                raise last_error from e

            except httpx.RequestError as e:
                last_error = SemanticScholarError(f"Request error: {e}")
                if attempt < self._config.max_retries - 1:
                    delay = self._config.retry_delay * (2**attempt)
                    logger.warning(f"Request failed, retrying in {delay}s: {e}")
                    time.sleep(delay)
                    continue
                raise last_error from e

        raise last_error or SemanticScholarError("Request failed after retries")

    def _parse_paper(self, data: dict) -> PaperMetadata:
        """Parse API response into PaperMetadata model."""
        external_ids = data.get("externalIds") or {}

        # Parse authors
        authors = []
        for author_data in data.get("authors") or []:
            authors.append(
                AuthorRef(
                    name=author_data.get("name", "Unknown"),
                    author_id=author_data.get("authorId"),
                )
            )

        # Parse publication date
        pub_date = None
        if data.get("publicationDate"):
            try:
                pub_date = datetime.fromisoformat(data["publicationDate"])
            except ValueError:
                pass

        # Get PDF URL
        pdf_url = None
        open_access_pdf = data.get("openAccessPdf")
        if open_access_pdf and isinstance(open_access_pdf, dict):
            pdf_url = open_access_pdf.get("url")

        return PaperMetadata(
            paper_id=data.get("paperId", ""),
            doi=external_ids.get("DOI"),
            arxiv_id=external_ids.get("ArXiv"),
            s2_id=data.get("paperId"),
            title=data.get("title", ""),
            abstract=data.get("abstract"),
            authors=authors,
            year=data.get("year"),
            venue=data.get("venue"),
            publication_date=pub_date,
            url=f"https://www.semanticscholar.org/paper/{data.get('paperId', '')}",
            pdf_url=pdf_url,
            is_open_access=data.get("isOpenAccess", False),
            source=SourceType.SEMANTIC_SCHOLAR,
            citation_count=data.get("citationCount"),
            influential_citation_count=data.get("influentialCitationCount"),
            embedding=data.get("embedding", {}).get("vector") if data.get("embedding") else None,
            fields_of_study=data.get("fieldsOfStudy") or [],
        )

    def _parse_citation(self, data: dict) -> Citation:
        """Parse citation data from API response."""
        citing_paper = data.get("citingPaper") or data.get("citedPaper") or data
        external_ids = citing_paper.get("externalIds") or {}

        return Citation(
            paper_id=citing_paper.get("paperId", ""),
            doi=external_ids.get("DOI"),
            title=citing_paper.get("title"),
            year=citing_paper.get("year"),
            is_influential=data.get("isInfluential", False),
        )

    # =========================================================================
    # Search Methods
    # =========================================================================

    def search_papers(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        fields: Optional[list[str]] = None,
        year: Optional[str] = None,
        open_access_pdf: Optional[bool] = None,
        fields_of_study: Optional[list[str]] = None,
    ) -> list[PaperMetadata]:
        """
        Search for papers by query string.

        Args:
            query: Search query
            limit: Maximum number of results (max 100)
            offset: Offset for pagination
            fields: Fields to include in response
            year: Filter by year (e.g., "2020", "2019-2021", "2020-")
            open_access_pdf: Filter to only open access papers
            fields_of_study: Filter by fields of study

        Returns:
            List of matching papers
        """
        params = {
            "query": query,
            "limit": min(limit, 100),
            "offset": offset,
            "fields": ",".join(fields or DEFAULT_PAPER_FIELDS),
        }

        if year:
            params["year"] = year
        if open_access_pdf is not None:
            params["openAccessPdf"] = str(open_access_pdf).lower()
        if fields_of_study:
            params["fieldsOfStudy"] = ",".join(fields_of_study)

        data = self._make_request("GET", "/paper/search", params)
        papers = []
        for item in data.get("data") or []:
            try:
                papers.append(self._parse_paper(item))
            except Exception as e:
                logger.warning(f"Failed to parse paper: {e}")
                continue

        return papers

    def search_papers_bulk(
        self,
        query: str,
        limit: int = 100,
        fields: Optional[list[str]] = None,
    ) -> list[PaperMetadata]:
        """
        Search for papers using bulk endpoint (higher limits).

        Args:
            query: Search query
            limit: Maximum number of results (max 1000)
            fields: Fields to include in response

        Returns:
            List of matching papers
        """
        params = {
            "query": query,
            "limit": min(limit, 1000),
            "fields": ",".join(fields or DEFAULT_PAPER_FIELDS),
        }

        data = self._make_request("GET", "/paper/search/bulk", params)
        papers = []
        for item in data.get("data") or []:
            try:
                papers.append(self._parse_paper(item))
            except Exception as e:
                logger.warning(f"Failed to parse paper: {e}")
                continue

        return papers

    # =========================================================================
    # Paper Retrieval Methods
    # =========================================================================

    def get_paper(
        self,
        paper_id: str,
        fields: Optional[list[str]] = None,
    ) -> PaperMetadata:
        """
        Get a paper by Semantic Scholar paper ID.

        Args:
            paper_id: Semantic Scholar paper ID
            fields: Fields to include in response

        Returns:
            Paper metadata

        Raises:
            NotFoundError: If paper not found
        """
        params = {"fields": ",".join(fields or DEFAULT_PAPER_FIELDS)}
        data = self._make_request("GET", f"/paper/{paper_id}", params)
        return self._parse_paper(data)

    def get_paper_by_doi(
        self,
        doi: str,
        fields: Optional[list[str]] = None,
    ) -> PaperMetadata:
        """
        Get a paper by DOI.

        Args:
            doi: DOI (e.g., "10.48550/arXiv.1706.03762")
            fields: Fields to include in response

        Returns:
            Paper metadata

        Raises:
            NotFoundError: If paper not found
        """
        return self.get_paper(f"DOI:{doi}", fields)

    def get_paper_by_arxiv_id(
        self,
        arxiv_id: str,
        fields: Optional[list[str]] = None,
    ) -> PaperMetadata:
        """
        Get a paper by arXiv ID.

        Args:
            arxiv_id: arXiv ID (e.g., "1706.03762")
            fields: Fields to include in response

        Returns:
            Paper metadata

        Raises:
            NotFoundError: If paper not found
        """
        # Clean the arXiv ID
        clean_id = arxiv_id
        for prefix in ["arXiv:", "arxiv:"]:
            if clean_id.startswith(prefix):
                clean_id = clean_id[len(prefix):]

        return self.get_paper(f"ARXIV:{clean_id}", fields)

    def get_papers_batch(
        self,
        paper_ids: list[str],
        fields: Optional[list[str]] = None,
    ) -> list[PaperMetadata]:
        """
        Get multiple papers by ID in a single request.

        Args:
            paper_ids: List of paper IDs (S2 IDs, DOIs, or arXiv IDs)
            fields: Fields to include in response

        Returns:
            List of paper metadata (may be fewer than requested if some not found)
        """
        if not paper_ids:
            return []

        params = {"fields": ",".join(fields or DEFAULT_PAPER_FIELDS)}

        # POST request with IDs in body
        self._wait_for_rate_limit()
        self._last_request_time = time.time()

        response = self.client.post(
            "/paper/batch",
            params=params,
            json={"ids": paper_ids[:500]},  # Max 500 per request
        )

        if response.status_code != 200:
            raise SemanticScholarError(
                f"Batch request failed: {response.status_code} {response.text}"
            )

        papers = []
        for item in response.json() or []:
            if item:  # Skip null entries (not found)
                try:
                    papers.append(self._parse_paper(item))
                except Exception as e:
                    logger.warning(f"Failed to parse paper: {e}")

        return papers

    # =========================================================================
    # Citation and Reference Methods
    # =========================================================================

    def get_references(
        self,
        paper_id: str,
        limit: int = 100,
        offset: int = 0,
        fields: Optional[list[str]] = None,
    ) -> list[Citation]:
        """
        Get papers referenced by a paper.

        Args:
            paper_id: Paper ID
            limit: Maximum number of results
            offset: Offset for pagination
            fields: Fields to include for cited papers

        Returns:
            List of referenced papers
        """
        params = {
            "limit": min(limit, 1000),
            "offset": offset,
            "fields": ",".join(fields or CITATION_FIELDS),
        }

        data = self._make_request("GET", f"/paper/{paper_id}/references", params)
        citations = []
        for item in data.get("data") or []:
            try:
                citations.append(self._parse_citation(item))
            except Exception as e:
                logger.warning(f"Failed to parse reference: {e}")

        return citations

    def get_citations(
        self,
        paper_id: str,
        limit: int = 100,
        offset: int = 0,
        fields: Optional[list[str]] = None,
    ) -> list[Citation]:
        """
        Get papers that cite a paper.

        Args:
            paper_id: Paper ID
            limit: Maximum number of results
            offset: Offset for pagination
            fields: Fields to include for citing papers

        Returns:
            List of citing papers
        """
        params = {
            "limit": min(limit, 1000),
            "offset": offset,
            "fields": ",".join(fields or CITATION_FIELDS),
        }

        data = self._make_request("GET", f"/paper/{paper_id}/citations", params)
        citations = []
        for item in data.get("data") or []:
            try:
                citations.append(self._parse_citation(item))
            except Exception as e:
                logger.warning(f"Failed to parse citation: {e}")

        return citations

    # =========================================================================
    # Embedding Methods
    # =========================================================================

    def get_embedding(
        self,
        paper_id: str,
    ) -> Optional[list[float]]:
        """
        Get SPECTER2 embedding for a paper.

        Args:
            paper_id: Paper ID

        Returns:
            768-dimensional SPECTER2 embedding vector, or None if not available
        """
        params = {"fields": "embedding"}

        try:
            data = self._make_request("GET", f"/paper/{paper_id}", params)
            embedding = data.get("embedding")
            if embedding and isinstance(embedding, dict):
                return embedding.get("vector")
            return None
        except NotFoundError:
            return None

    def get_embeddings_batch(
        self,
        paper_ids: list[str],
    ) -> dict[str, Optional[list[float]]]:
        """
        Get SPECTER2 embeddings for multiple papers.

        Args:
            paper_ids: List of paper IDs

        Returns:
            Dict mapping paper ID to embedding (None if not available)
        """
        if not paper_ids:
            return {}

        params = {"fields": "paperId,embedding"}

        self._wait_for_rate_limit()
        self._last_request_time = time.time()

        response = self.client.post(
            "/paper/batch",
            params=params,
            json={"ids": paper_ids[:500]},
        )

        if response.status_code != 200:
            raise SemanticScholarError(
                f"Batch request failed: {response.status_code}"
            )

        result = {}
        for i, item in enumerate(response.json() or []):
            pid = paper_ids[i] if i < len(paper_ids) else None
            if item and pid:
                embedding = item.get("embedding")
                if embedding and isinstance(embedding, dict):
                    result[pid] = embedding.get("vector")
                else:
                    result[pid] = None
            elif pid:
                result[pid] = None

        return result


# Singleton client
_client: Optional[SemanticScholarClient] = None


def get_semantic_scholar_client() -> SemanticScholarClient:
    """Get the Semantic Scholar client singleton."""
    global _client
    if _client is None:
        _client = SemanticScholarClient()
    return _client


def reset_semantic_scholar_client() -> None:
    """Reset the client singleton."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
