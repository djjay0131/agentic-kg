"""
arXiv API client.

Provides access to arXiv paper metadata and PDF downloads via the arXiv API.

API Documentation: https://info.arxiv.org/help/api/
"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx

from agentic_kg.config import ArxivConfig, get_config
from agentic_kg.data_acquisition.models import (
    AuthorRef,
    PaperMetadata,
    SourceType,
)

logger = logging.getLogger(__name__)

# XML namespaces used by arXiv API
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

# Regex patterns for arXiv ID formats
ARXIV_NEW_PATTERN = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
ARXIV_OLD_PATTERN = re.compile(r"^([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")


class ArxivError(Exception):
    """Base exception for arXiv API errors."""

    pass


class ArxivNotFoundError(ArxivError):
    """Raised when a paper is not found on arXiv."""

    pass


class ArxivRateLimitError(ArxivError):
    """Raised when rate limit is exceeded."""

    pass


def parse_arxiv_id(identifier: str) -> tuple[str, Optional[str]]:
    """
    Parse an arXiv identifier into base ID and version.

    Args:
        identifier: arXiv ID (with or without version)

    Returns:
        Tuple of (base_id, version) where version may be None

    Examples:
        >>> parse_arxiv_id("2301.12345")
        ("2301.12345", None)
        >>> parse_arxiv_id("2301.12345v2")
        ("2301.12345", "v2")
        >>> parse_arxiv_id("cs.AI/0501001v1")
        ("cs.AI/0501001", "v1")
    """
    # Clean common prefixes
    clean_id = identifier.strip()
    for prefix in ["arXiv:", "arxiv:", "https://arxiv.org/abs/", "http://arxiv.org/abs/"]:
        if clean_id.lower().startswith(prefix.lower()):
            clean_id = clean_id[len(prefix) :]

    # Try new format first
    match = ARXIV_NEW_PATTERN.match(clean_id)
    if match:
        return match.group(1), match.group(2)

    # Try old format
    match = ARXIV_OLD_PATTERN.match(clean_id)
    if match:
        return match.group(1), match.group(2)

    # Return as-is if no pattern matches
    return clean_id, None


def normalize_arxiv_id(identifier: str, include_version: bool = False) -> str:
    """
    Normalize an arXiv ID to standard format.

    Args:
        identifier: arXiv ID (with or without version, with or without prefix)
        include_version: Whether to include version in output

    Returns:
        Normalized arXiv ID
    """
    base_id, version = parse_arxiv_id(identifier)
    if include_version and version:
        return f"{base_id}{version}"
    return base_id


class ArxivClient:
    """
    Client for the arXiv API.

    Provides methods for retrieving paper metadata and downloading PDFs
    from arXiv.

    Example:
        client = ArxivClient()

        # Get metadata
        paper = client.get_metadata("2301.12345")

        # Download PDF
        pdf_path = client.download_pdf("2301.12345", Path("./papers"))
    """

    def __init__(self, config: Optional[ArxivConfig] = None):
        """
        Initialize the arXiv client.

        Args:
            config: Configuration for the client. Uses global config if not provided.
        """
        self._config = config or get_config().data_acquisition.arxiv
        self._client: Optional[httpx.Client] = None
        self._last_request_time: float = 0.0

    @property
    def client(self) -> httpx.Client:
        """Lazy-load the HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._config.timeout,
                follow_redirects=True,
            )
        return self._client

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "ArxivClient":
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
        url: str,
        stream: bool = False,
    ) -> httpx.Response:
        """
        Make an HTTP request with rate limiting and retry logic.

        Args:
            url: Full URL to request
            stream: Whether to stream the response

        Returns:
            HTTP response

        Raises:
            ArxivError: On request errors
        """
        last_error: Optional[Exception] = None

        for attempt in range(self._config.max_retries):
            self._wait_for_rate_limit()

            try:
                self._last_request_time = time.time()

                if stream:
                    # For streaming, don't auto-close response
                    response = self.client.stream("GET", url)
                    return response.__enter__()
                else:
                    response = self.client.get(url)

                if response.status_code == 200:
                    return response

                if response.status_code == 404:
                    raise ArxivNotFoundError(f"Paper not found: {url}")

                if response.status_code == 503:
                    # arXiv returns 503 when overloaded
                    retry_after = response.headers.get("Retry-After", "30")
                    retry_seconds = int(retry_after)
                    if attempt < self._config.max_retries - 1:
                        logger.warning(
                            f"arXiv overloaded, waiting {retry_seconds}s before retry"
                        )
                        time.sleep(retry_seconds)
                        continue
                    raise ArxivRateLimitError(f"arXiv overloaded after {attempt + 1} attempts")

                response.raise_for_status()

            except httpx.HTTPStatusError as e:
                last_error = ArxivError(f"HTTP error {e.response.status_code}")
                if attempt < self._config.max_retries - 1:
                    delay = self._config.retry_delay * (2**attempt)
                    logger.warning(f"Request failed, retrying in {delay}s: {e}")
                    time.sleep(delay)
                    continue
                raise last_error from e

            except httpx.RequestError as e:
                last_error = ArxivError(f"Request error: {e}")
                if attempt < self._config.max_retries - 1:
                    delay = self._config.retry_delay * (2**attempt)
                    logger.warning(f"Request failed, retrying in {delay}s: {e}")
                    time.sleep(delay)
                    continue
                raise last_error from e

        raise last_error or ArxivError("Request failed after retries")

    def _parse_entry(self, entry: ET.Element) -> PaperMetadata:
        """Parse an Atom entry into PaperMetadata."""
        # Extract arXiv ID from entry ID URL
        entry_id = entry.findtext(f"{ATOM_NS}id", "")
        arxiv_id = entry_id.replace("http://arxiv.org/abs/", "")

        # Parse authors
        authors = []
        for author in entry.findall(f"{ATOM_NS}author"):
            name = author.findtext(f"{ATOM_NS}name", "Unknown")
            affiliations = [
                aff.text
                for aff in author.findall(f"{ARXIV_NS}affiliation")
                if aff.text
            ]
            authors.append(AuthorRef(name=name, affiliations=affiliations))

        # Parse publication date
        published = entry.findtext(f"{ATOM_NS}published", "")
        pub_date = None
        if published:
            try:
                pub_date = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except ValueError:
                pass

        # Extract year from date
        year = pub_date.year if pub_date else None

        # Get PDF URL
        pdf_url = None
        for link in entry.findall(f"{ATOM_NS}link"):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
                break

        # Get categories/fields of study
        categories = [
            cat.get("term", "")
            for cat in entry.findall(f"{ATOM_NS}category")
            if cat.get("term")
        ]

        # Get DOI if available
        doi = entry.findtext(f"{ARXIV_NS}doi")

        # Get journal reference
        journal_ref = entry.findtext(f"{ARXIV_NS}journal_ref")

        return PaperMetadata(
            paper_id=arxiv_id,
            arxiv_id=normalize_arxiv_id(arxiv_id),
            doi=doi,
            title=entry.findtext(f"{ATOM_NS}title", "").strip().replace("\n", " "),
            abstract=entry.findtext(f"{ATOM_NS}summary", "").strip(),
            authors=authors,
            year=year,
            venue=journal_ref,
            publication_date=pub_date,
            url=entry_id,
            pdf_url=pdf_url,
            is_open_access=True,  # arXiv is always open access
            source=SourceType.ARXIV,
            fields_of_study=categories,
        )

    def get_metadata(
        self,
        arxiv_id: str,
        include_version: bool = False,
    ) -> PaperMetadata:
        """
        Get metadata for a paper from arXiv.

        Args:
            arxiv_id: arXiv ID (e.g., "2301.12345", "cs.AI/0501001")
            include_version: Whether to request specific version

        Returns:
            Paper metadata

        Raises:
            ArxivNotFoundError: If paper not found
        """
        normalized = normalize_arxiv_id(arxiv_id, include_version)
        url = f"{self._config.base_url}query?id_list={quote(normalized)}"

        response = self._make_request(url)
        root = ET.fromstring(response.text)

        # Check for entries
        entries = root.findall(f"{ATOM_NS}entry")
        if not entries:
            raise ArxivNotFoundError(f"Paper not found: {arxiv_id}")

        # Check if the entry has an actual result (not just an error)
        entry = entries[0]
        title = entry.findtext(f"{ATOM_NS}title", "")
        if not title or "Error" in title:
            raise ArxivNotFoundError(f"Paper not found: {arxiv_id}")

        return self._parse_entry(entry)

    def search(
        self,
        query: str,
        max_results: int = 10,
        start: int = 0,
        sort_by: str = "relevance",
        sort_order: str = "descending",
    ) -> list[PaperMetadata]:
        """
        Search for papers on arXiv.

        Args:
            query: Search query (supports arXiv search syntax)
            max_results: Maximum number of results (max 2000 per request)
            start: Starting index for pagination
            sort_by: Sort field ("relevance", "lastUpdatedDate", "submittedDate")
            sort_order: Sort order ("ascending", "descending")

        Returns:
            List of matching papers
        """
        # Build query URL
        params = [
            f"search_query={quote(query)}",
            f"start={start}",
            f"max_results={min(max_results, 2000)}",
            f"sortBy={sort_by}",
            f"sortOrder={sort_order}",
        ]
        url = f"{self._config.base_url}query?{'&'.join(params)}"

        response = self._make_request(url)
        root = ET.fromstring(response.text)

        papers = []
        for entry in root.findall(f"{ATOM_NS}entry"):
            try:
                # Skip error entries
                title = entry.findtext(f"{ATOM_NS}title", "")
                if not title or "Error" in title:
                    continue
                papers.append(self._parse_entry(entry))
            except Exception as e:
                logger.warning(f"Failed to parse arXiv entry: {e}")

        return papers

    def get_pdf_url(
        self,
        arxiv_id: str,
        include_version: bool = True,
    ) -> str:
        """
        Get direct PDF download URL for a paper.

        Args:
            arxiv_id: arXiv ID
            include_version: Whether to include version in URL

        Returns:
            PDF download URL
        """
        base_id, version = parse_arxiv_id(arxiv_id)
        if include_version and version:
            return f"https://arxiv.org/pdf/{base_id}{version}.pdf"
        return f"https://arxiv.org/pdf/{base_id}.pdf"

    def download_pdf(
        self,
        arxiv_id: str,
        output_dir: Path,
        filename: Optional[str] = None,
        include_version: bool = True,
    ) -> Path:
        """
        Download PDF from arXiv.

        Args:
            arxiv_id: arXiv ID
            output_dir: Directory to save PDF
            filename: Custom filename (default: arxiv_id.pdf)
            include_version: Whether to include version in request

        Returns:
            Path to downloaded PDF

        Raises:
            ArxivError: On download failure
        """
        url = self.get_pdf_url(arxiv_id, include_version)
        base_id, version = parse_arxiv_id(arxiv_id)

        if filename is None:
            safe_id = base_id.replace("/", "_")
            if include_version and version:
                filename = f"{safe_id}{version}.pdf"
            else:
                filename = f"{safe_id}.pdf"

        output_path = Path(output_dir) / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Download with streaming
        self._wait_for_rate_limit()
        self._last_request_time = time.time()

        try:
            with self.client.stream("GET", url) as response:
                if response.status_code == 404:
                    raise ArxivNotFoundError(f"PDF not found: {arxiv_id}")
                response.raise_for_status()

                with open(output_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)

            return output_path

        except httpx.HTTPStatusError as e:
            raise ArxivError(f"Failed to download PDF: {e}") from e
        except httpx.RequestError as e:
            raise ArxivError(f"Download error: {e}") from e

    def get_pdf_bytes(
        self,
        arxiv_id: str,
        include_version: bool = True,
    ) -> bytes:
        """
        Download PDF content as bytes.

        Args:
            arxiv_id: arXiv ID
            include_version: Whether to include version in request

        Returns:
            PDF content as bytes

        Raises:
            ArxivError: On download failure
        """
        url = self.get_pdf_url(arxiv_id, include_version)

        self._wait_for_rate_limit()
        self._last_request_time = time.time()

        try:
            response = self.client.get(url)
            if response.status_code == 404:
                raise ArxivNotFoundError(f"PDF not found: {arxiv_id}")
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as e:
            raise ArxivError(f"Failed to download PDF: {e}") from e
        except httpx.RequestError as e:
            raise ArxivError(f"Download error: {e}") from e


# Singleton client
_client: Optional[ArxivClient] = None


def get_arxiv_client() -> ArxivClient:
    """Get the arXiv client singleton."""
    global _client
    if _client is None:
        _client = ArxivClient()
    return _client


def reset_arxiv_client() -> None:
    """Reset the client singleton."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
