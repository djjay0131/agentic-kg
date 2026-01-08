"""
Unified Paper Acquisition Layer.

Provides a single interface for acquiring papers from multiple sources:
- Semantic Scholar (metadata, citations, embeddings)
- arXiv (open access papers and preprints)
- OpenAlex (open access discovery)

The acquisition layer handles:
- Automatic identifier type detection (DOI, arXiv ID, etc.)
- Source priority resolution
- PDF retrieval with caching
- Provenance tracking
"""

import logging
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from agentic_kg.config import get_config
from agentic_kg.data_acquisition.arxiv import (
    ArxivClient,
    ArxivNotFoundError,
    get_arxiv_client,
    parse_arxiv_id,
)
from agentic_kg.data_acquisition.models import (
    DownloadResult,
    DownloadStatus,
    PaperMetadata,
    SourceType,
    is_valid_arxiv_id,
    is_valid_doi,
)
from agentic_kg.data_acquisition.openalex import (
    OpenAlexClient,
    OpenAlexNotFoundError,
    get_openalex_client,
)
from agentic_kg.data_acquisition.semantic_scholar import (
    NotFoundError as S2NotFoundError,
    SemanticScholarClient,
    get_semantic_scholar_client,
)

logger = logging.getLogger(__name__)


class IdentifierType(str, Enum):
    """Type of paper identifier."""

    DOI = "doi"
    ARXIV = "arxiv"
    S2_ID = "s2"
    OPENALEX_ID = "openalex"
    URL = "url"
    UNKNOWN = "unknown"


# Regex patterns for identifier detection
DOI_PATTERN = re.compile(r"^10\.\d{4,}/[^\s]+$")
ARXIV_NEW_PATTERN = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
ARXIV_OLD_PATTERN = re.compile(r"^[a-z-]+(\.[A-Z]{2})?/\d{7}(v\d+)?$")
S2_ID_PATTERN = re.compile(r"^[0-9a-f]{40}$")
OPENALEX_PATTERN = re.compile(r"^W\d+$")


def detect_identifier_type(identifier: str) -> IdentifierType:
    """
    Detect the type of a paper identifier.

    Args:
        identifier: Paper identifier string

    Returns:
        Detected identifier type
    """
    identifier = identifier.strip()

    # Check for explicit prefixes
    lower_id = identifier.lower()
    if lower_id.startswith("doi:"):
        return IdentifierType.DOI
    if lower_id.startswith("arxiv:"):
        return IdentifierType.ARXIV
    if lower_id.startswith("s2:"):
        return IdentifierType.S2_ID
    if lower_id.startswith("openalex:"):
        return IdentifierType.OPENALEX_ID

    # Clean common URL prefixes
    clean_id = identifier
    for prefix in [
        "https://doi.org/",
        "http://doi.org/",
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "https://openalex.org/",
    ]:
        if clean_id.lower().startswith(prefix.lower()):
            clean_id = clean_id[len(prefix) :]
            break

    # Check patterns
    if DOI_PATTERN.match(clean_id):
        return IdentifierType.DOI
    if ARXIV_NEW_PATTERN.match(clean_id) or ARXIV_OLD_PATTERN.match(clean_id):
        return IdentifierType.ARXIV
    if S2_ID_PATTERN.match(clean_id):
        return IdentifierType.S2_ID
    if OPENALEX_PATTERN.match(clean_id):
        return IdentifierType.OPENALEX_ID

    # Check for URL patterns
    if identifier.startswith("http://") or identifier.startswith("https://"):
        return IdentifierType.URL

    return IdentifierType.UNKNOWN


def clean_identifier(identifier: str, id_type: IdentifierType) -> str:
    """
    Clean an identifier by removing prefixes.

    Args:
        identifier: Raw identifier
        id_type: Type of identifier

    Returns:
        Cleaned identifier
    """
    identifier = identifier.strip()

    # Remove explicit prefixes
    lower_id = identifier.lower()
    prefix_map = {
        IdentifierType.DOI: ["doi:", "https://doi.org/", "http://doi.org/"],
        IdentifierType.ARXIV: [
            "arxiv:",
            "https://arxiv.org/abs/",
            "http://arxiv.org/abs/",
        ],
        IdentifierType.S2_ID: ["s2:"],
        IdentifierType.OPENALEX_ID: ["openalex:", "https://openalex.org/"],
    }

    prefixes = prefix_map.get(id_type, [])
    for prefix in prefixes:
        if lower_id.startswith(prefix.lower()):
            return identifier[len(prefix) :]

    return identifier


class PaperAcquisitionLayer:
    """
    Unified interface for paper acquisition from multiple sources.

    Provides methods for:
    - Retrieving paper metadata from any supported source
    - Downloading PDFs with automatic source selection
    - Checking paper availability
    - Tracking provenance of acquired papers

    Example:
        acquisition = PaperAcquisitionLayer()

        # Get paper by any identifier
        paper = await acquisition.get_paper_metadata("10.1038/nature12373")
        paper = await acquisition.get_paper_metadata("2301.12345")

        # Download PDF
        result = await acquisition.get_pdf("2301.12345", Path("./papers"))
    """

    def __init__(
        self,
        s2_client: Optional[SemanticScholarClient] = None,
        arxiv_client: Optional[ArxivClient] = None,
        openalex_client: Optional[OpenAlexClient] = None,
        cache_dir: Optional[Path] = None,
    ):
        """
        Initialize the acquisition layer.

        Args:
            s2_client: Semantic Scholar client (uses singleton if not provided)
            arxiv_client: arXiv client (uses singleton if not provided)
            openalex_client: OpenAlex client (uses singleton if not provided)
            cache_dir: Directory for caching PDFs
        """
        self._s2_client = s2_client
        self._arxiv_client = arxiv_client
        self._openalex_client = openalex_client

        config = get_config()
        self._cache_dir = cache_dir or Path(config.data_acquisition.cache.cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def s2_client(self) -> SemanticScholarClient:
        """Get Semantic Scholar client."""
        if self._s2_client is None:
            self._s2_client = get_semantic_scholar_client()
        return self._s2_client

    @property
    def arxiv_client(self) -> ArxivClient:
        """Get arXiv client."""
        if self._arxiv_client is None:
            self._arxiv_client = get_arxiv_client()
        return self._arxiv_client

    @property
    def openalex_client(self) -> OpenAlexClient:
        """Get OpenAlex client."""
        if self._openalex_client is None:
            self._openalex_client = get_openalex_client()
        return self._openalex_client

    def get_identifier_type(self, identifier: str) -> IdentifierType:
        """
        Get the type of a paper identifier.

        Args:
            identifier: Paper identifier

        Returns:
            Identifier type
        """
        return detect_identifier_type(identifier)

    def get_paper_metadata(
        self,
        identifier: str,
        include_embedding: bool = False,
    ) -> Optional[PaperMetadata]:
        """
        Get paper metadata from the best available source.

        Resolution order:
        1. For DOIs: Semantic Scholar > OpenAlex
        2. For arXiv IDs: arXiv > Semantic Scholar
        3. For S2 IDs: Semantic Scholar only
        4. For OpenAlex IDs: OpenAlex only

        Args:
            identifier: Paper identifier (DOI, arXiv ID, etc.)
            include_embedding: Whether to fetch SPECTER2 embedding

        Returns:
            Paper metadata or None if not found
        """
        id_type = detect_identifier_type(identifier)
        clean_id = clean_identifier(identifier, id_type)

        metadata: Optional[PaperMetadata] = None

        if id_type == IdentifierType.DOI:
            # Try Semantic Scholar first (better metadata)
            metadata = self._get_from_s2_by_doi(clean_id, include_embedding)
            if metadata is None:
                metadata = self._get_from_openalex_by_doi(clean_id)

        elif id_type == IdentifierType.ARXIV:
            # Try arXiv first (authoritative for preprints)
            metadata = self._get_from_arxiv(clean_id)
            if metadata is None:
                metadata = self._get_from_s2_by_arxiv(clean_id, include_embedding)

        elif id_type == IdentifierType.S2_ID:
            metadata = self._get_from_s2(clean_id, include_embedding)

        elif id_type == IdentifierType.OPENALEX_ID:
            metadata = self._get_from_openalex(clean_id)

        elif id_type == IdentifierType.URL:
            # Try to extract identifier from URL
            metadata = self._get_from_url(identifier, include_embedding)

        else:
            # Unknown type - try all sources
            metadata = self._get_from_any_source(clean_id, include_embedding)

        return metadata

    def _get_from_s2(
        self, paper_id: str, include_embedding: bool
    ) -> Optional[PaperMetadata]:
        """Get from Semantic Scholar by S2 ID."""
        try:
            paper = self.s2_client.get_paper(paper_id)
            if include_embedding and not paper.embedding:
                embedding = self.s2_client.get_embedding(paper_id)
                if embedding:
                    paper.embedding = embedding
            return paper
        except S2NotFoundError:
            return None
        except Exception as e:
            logger.warning(f"S2 lookup failed for {paper_id}: {e}")
            return None

    def _get_from_s2_by_doi(
        self, doi: str, include_embedding: bool
    ) -> Optional[PaperMetadata]:
        """Get from Semantic Scholar by DOI."""
        try:
            paper = self.s2_client.get_paper_by_doi(doi)
            if include_embedding and not paper.embedding:
                embedding = self.s2_client.get_embedding(paper.paper_id)
                if embedding:
                    paper.embedding = embedding
            return paper
        except S2NotFoundError:
            return None
        except Exception as e:
            logger.warning(f"S2 DOI lookup failed for {doi}: {e}")
            return None

    def _get_from_s2_by_arxiv(
        self, arxiv_id: str, include_embedding: bool
    ) -> Optional[PaperMetadata]:
        """Get from Semantic Scholar by arXiv ID."""
        try:
            paper = self.s2_client.get_paper_by_arxiv_id(arxiv_id)
            if include_embedding and not paper.embedding:
                embedding = self.s2_client.get_embedding(paper.paper_id)
                if embedding:
                    paper.embedding = embedding
            return paper
        except S2NotFoundError:
            return None
        except Exception as e:
            logger.warning(f"S2 arXiv lookup failed for {arxiv_id}: {e}")
            return None

    def _get_from_arxiv(self, arxiv_id: str) -> Optional[PaperMetadata]:
        """Get from arXiv."""
        try:
            return self.arxiv_client.get_metadata(arxiv_id)
        except ArxivNotFoundError:
            return None
        except Exception as e:
            logger.warning(f"arXiv lookup failed for {arxiv_id}: {e}")
            return None

    def _get_from_openalex(self, openalex_id: str) -> Optional[PaperMetadata]:
        """Get from OpenAlex by ID."""
        try:
            return self.openalex_client.get_work(openalex_id)
        except OpenAlexNotFoundError:
            return None
        except Exception as e:
            logger.warning(f"OpenAlex lookup failed for {openalex_id}: {e}")
            return None

    def _get_from_openalex_by_doi(self, doi: str) -> Optional[PaperMetadata]:
        """Get from OpenAlex by DOI."""
        try:
            return self.openalex_client.get_work_by_doi(doi)
        except OpenAlexNotFoundError:
            return None
        except Exception as e:
            logger.warning(f"OpenAlex DOI lookup failed for {doi}: {e}")
            return None

    def _get_from_url(
        self, url: str, include_embedding: bool
    ) -> Optional[PaperMetadata]:
        """Try to get metadata from a URL by extracting identifier."""
        # Try to extract DOI
        if "doi.org" in url:
            # Extract DOI from URL
            match = re.search(r"doi\.org/(10\.[^/\s]+/[^\s]+)", url)
            if match:
                return self._get_from_s2_by_doi(match.group(1), include_embedding)

        # Try to extract arXiv ID
        if "arxiv.org" in url:
            match = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s/]+)", url)
            if match:
                arxiv_id = match.group(1).replace(".pdf", "")
                return self._get_from_arxiv(arxiv_id)

        # Try to extract OpenAlex ID
        if "openalex.org" in url:
            match = re.search(r"openalex\.org/(W\d+)", url)
            if match:
                return self._get_from_openalex(match.group(1))

        return None

    def _get_from_any_source(
        self, identifier: str, include_embedding: bool
    ) -> Optional[PaperMetadata]:
        """Try all sources for an unknown identifier."""
        # Try as DOI
        if is_valid_doi(identifier):
            result = self._get_from_s2_by_doi(identifier, include_embedding)
            if result:
                return result
            return self._get_from_openalex_by_doi(identifier)

        # Try as arXiv ID
        if is_valid_arxiv_id(identifier):
            result = self._get_from_arxiv(identifier)
            if result:
                return result
            return self._get_from_s2_by_arxiv(identifier, include_embedding)

        # Try as S2 ID
        if S2_ID_PATTERN.match(identifier):
            return self._get_from_s2(identifier, include_embedding)

        # Try as OpenAlex ID
        if OPENALEX_PATTERN.match(identifier):
            return self._get_from_openalex(identifier)

        return None

    def is_available(self, identifier: str) -> bool:
        """
        Check if a paper is available from any source.

        Args:
            identifier: Paper identifier

        Returns:
            True if paper exists in any source
        """
        return self.get_paper_metadata(identifier) is not None

    def get_pdf_url(self, identifier: str) -> Optional[str]:
        """
        Get PDF URL for a paper without downloading.

        Args:
            identifier: Paper identifier

        Returns:
            PDF URL if available, None otherwise
        """
        id_type = detect_identifier_type(identifier)
        clean_id = clean_identifier(identifier, id_type)

        # For arXiv, we can generate the URL directly
        if id_type == IdentifierType.ARXIV:
            return self.arxiv_client.get_pdf_url(clean_id)

        # For other types, get metadata and check pdf_url
        metadata = self.get_paper_metadata(identifier)
        if metadata and metadata.pdf_url:
            return metadata.pdf_url

        return None

    def get_pdf(
        self,
        identifier: str,
        output_dir: Optional[Path] = None,
        filename: Optional[str] = None,
    ) -> DownloadResult:
        """
        Download PDF for a paper.

        Source priority:
        1. arXiv (for arXiv papers)
        2. Open access URL from metadata
        3. (Future: Paywall service)

        Args:
            identifier: Paper identifier
            output_dir: Directory to save PDF (uses cache if not provided)
            filename: Custom filename (auto-generated if not provided)

        Returns:
            Download result with status and file path
        """
        id_type = detect_identifier_type(identifier)
        clean_id = clean_identifier(identifier, id_type)
        output_dir = output_dir or self._cache_dir

        # For arXiv papers, download directly from arXiv
        if id_type == IdentifierType.ARXIV:
            return self._download_from_arxiv(clean_id, output_dir, filename)

        # Get metadata to find PDF URL
        metadata = self.get_paper_metadata(identifier)
        if metadata is None:
            return DownloadResult(
                identifier=identifier,
                status=DownloadStatus.NOT_AVAILABLE,
                source=SourceType.UNKNOWN,
                error_message="Paper not found",
            )

        # If paper has arXiv ID, download from arXiv
        if metadata.arxiv_id:
            return self._download_from_arxiv(
                metadata.arxiv_id, output_dir, filename
            )

        # Try open access URL
        if metadata.pdf_url:
            return self._download_from_url(
                metadata.pdf_url,
                identifier,
                metadata.source,
                output_dir,
                filename,
            )

        # No PDF available
        return DownloadResult(
            identifier=identifier,
            status=DownloadStatus.NOT_AVAILABLE,
            source=metadata.source,
            error_message="No PDF URL available",
        )

    def _download_from_arxiv(
        self,
        arxiv_id: str,
        output_dir: Path,
        filename: Optional[str],
    ) -> DownloadResult:
        """Download PDF from arXiv."""
        try:
            path = self.arxiv_client.download_pdf(arxiv_id, output_dir, filename)
            return DownloadResult(
                identifier=f"arxiv:{arxiv_id}",
                status=DownloadStatus.COMPLETED,
                source=SourceType.ARXIV,
                file_path=str(path),
                file_size=path.stat().st_size,
                downloaded_at=datetime.utcnow(),
            )
        except ArxivNotFoundError:
            return DownloadResult(
                identifier=f"arxiv:{arxiv_id}",
                status=DownloadStatus.NOT_AVAILABLE,
                source=SourceType.ARXIV,
                error_message="PDF not found on arXiv",
            )
        except Exception as e:
            return DownloadResult(
                identifier=f"arxiv:{arxiv_id}",
                status=DownloadStatus.FAILED,
                source=SourceType.ARXIV,
                error_message=str(e),
            )

    def _download_from_url(
        self,
        url: str,
        identifier: str,
        source: SourceType,
        output_dir: Path,
        filename: Optional[str],
    ) -> DownloadResult:
        """Download PDF from a generic URL."""
        import hashlib
        import httpx

        if filename is None:
            # Generate filename from identifier
            safe_id = re.sub(r"[^\w\-.]", "_", identifier)
            filename = f"{safe_id}.pdf"

        output_path = output_dir / filename
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with httpx.Client(follow_redirects=True, timeout=60.0) as client:
                response = client.get(url)
                response.raise_for_status()

                content = response.content
                with open(output_path, "wb") as f:
                    f.write(content)

                content_hash = hashlib.sha256(content).hexdigest()

                return DownloadResult(
                    identifier=identifier,
                    status=DownloadStatus.COMPLETED,
                    source=source,
                    file_path=str(output_path),
                    file_size=len(content),
                    content_hash=content_hash,
                    downloaded_at=datetime.utcnow(),
                )

        except httpx.HTTPStatusError as e:
            return DownloadResult(
                identifier=identifier,
                status=DownloadStatus.FAILED,
                source=source,
                error_message=f"HTTP error {e.response.status_code}",
            )
        except Exception as e:
            return DownloadResult(
                identifier=identifier,
                status=DownloadStatus.FAILED,
                source=source,
                error_message=str(e),
            )

    def search(
        self,
        query: str,
        source: Optional[SourceType] = None,
        limit: int = 10,
    ) -> list[PaperMetadata]:
        """
        Search for papers across sources.

        Args:
            query: Search query
            source: Specific source to search (searches all if None)
            limit: Maximum results

        Returns:
            List of matching papers
        """
        results: list[PaperMetadata] = []

        if source is None or source == SourceType.SEMANTIC_SCHOLAR:
            try:
                s2_results = self.s2_client.search_papers(query, limit=limit)
                results.extend(s2_results)
            except Exception as e:
                logger.warning(f"S2 search failed: {e}")

        if source is None or source == SourceType.OPENALEX:
            try:
                oa_results = self.openalex_client.search_works(query, per_page=limit)
                results.extend(oa_results)
            except Exception as e:
                logger.warning(f"OpenAlex search failed: {e}")

        if source is None or source == SourceType.ARXIV:
            try:
                arxiv_results = self.arxiv_client.search(query, max_results=limit)
                results.extend(arxiv_results)
            except Exception as e:
                logger.warning(f"arXiv search failed: {e}")

        # Deduplicate by DOI
        seen_dois: set[str] = set()
        unique_results: list[PaperMetadata] = []
        for paper in results:
            if paper.doi:
                if paper.doi not in seen_dois:
                    seen_dois.add(paper.doi)
                    unique_results.append(paper)
            else:
                unique_results.append(paper)

        return unique_results[:limit]


# Singleton instance
_acquisition_layer: Optional[PaperAcquisitionLayer] = None


def get_acquisition_layer() -> PaperAcquisitionLayer:
    """Get the paper acquisition layer singleton."""
    global _acquisition_layer
    if _acquisition_layer is None:
        _acquisition_layer = PaperAcquisitionLayer()
    return _acquisition_layer


def reset_acquisition_layer() -> None:
    """Reset the acquisition layer singleton."""
    global _acquisition_layer
    _acquisition_layer = None
