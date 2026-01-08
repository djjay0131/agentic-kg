"""
Data models for paper acquisition.

Provides Pydantic models for representing paper metadata, authors,
citations, and download status from various sources.
"""

import re
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SourceType(str, Enum):
    """Source from which paper was acquired."""

    SEMANTIC_SCHOLAR = "semantic_scholar"
    ARXIV = "arxiv"
    OPENALEX = "openalex"
    PAYWALL = "paywall"
    CACHE = "cache"
    UNKNOWN = "unknown"


class DownloadStatus(str, Enum):
    """Status of an async download request."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_AVAILABLE = "not_available"


# Regex patterns for identifier validation
DOI_PATTERN = re.compile(r"^10\.\d{4,}/[^\s]+$")
ARXIV_NEW_PATTERN = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
ARXIV_OLD_PATTERN = re.compile(r"^[a-z-]+(\.[A-Z]{2})?/\d{7}(v\d+)?$")


def is_valid_doi(doi: str) -> bool:
    """Check if a string is a valid DOI."""
    return bool(DOI_PATTERN.match(doi))


def is_valid_arxiv_id(arxiv_id: str) -> bool:
    """Check if a string is a valid arXiv ID (old or new format)."""
    return bool(ARXIV_NEW_PATTERN.match(arxiv_id) or ARXIV_OLD_PATTERN.match(arxiv_id))


class AuthorRef(BaseModel):
    """Reference to an author with basic metadata."""

    name: str = Field(..., description="Full name of the author")
    author_id: Optional[str] = Field(
        None, description="Source-specific author ID (e.g., S2 author ID)"
    )
    orcid: Optional[str] = Field(None, description="ORCID identifier")
    affiliations: list[str] = Field(
        default_factory=list, description="Author affiliations"
    )

    def model_dump_json_safe(self) -> dict:
        """Return dict suitable for JSON serialization."""
        return self.model_dump(mode="json")


class Citation(BaseModel):
    """Reference to a cited or citing paper."""

    paper_id: str = Field(..., description="Source-specific paper ID")
    doi: Optional[str] = Field(None, description="DOI if available")
    title: Optional[str] = Field(None, description="Paper title if available")
    year: Optional[int] = Field(None, description="Publication year if available")
    is_influential: bool = Field(
        False, description="Whether citation is marked as influential"
    )

    @field_validator("doi")
    @classmethod
    def validate_doi(cls, v: Optional[str]) -> Optional[str]:
        """Validate DOI format if provided."""
        if v is not None and not is_valid_doi(v):
            # Don't raise, just clean it - APIs sometimes return malformed DOIs
            return None
        return v


class PaperMetadata(BaseModel):
    """
    Unified paper metadata from any source.

    This model represents paper information normalized from various APIs
    (Semantic Scholar, arXiv, OpenAlex) into a consistent format.
    """

    # Core identifiers
    paper_id: str = Field(..., description="Source-specific paper ID")
    doi: Optional[str] = Field(None, description="DOI (Digital Object Identifier)")
    arxiv_id: Optional[str] = Field(None, description="arXiv identifier")
    openalex_id: Optional[str] = Field(None, description="OpenAlex work ID")
    s2_id: Optional[str] = Field(None, description="Semantic Scholar paper ID")

    # Basic metadata
    title: str = Field(..., description="Paper title")
    abstract: Optional[str] = Field(None, description="Paper abstract")
    authors: list[AuthorRef] = Field(
        default_factory=list, description="List of authors"
    )

    # Publication info
    year: Optional[int] = Field(None, description="Publication year")
    venue: Optional[str] = Field(None, description="Publication venue (journal/conf)")
    publication_date: Optional[datetime] = Field(
        None, description="Full publication date if available"
    )

    # URLs and access
    url: Optional[str] = Field(None, description="Primary URL for the paper")
    pdf_url: Optional[str] = Field(None, description="Direct PDF URL if available")
    is_open_access: bool = Field(False, description="Whether paper is open access")

    # Source tracking
    source: SourceType = Field(
        SourceType.UNKNOWN, description="Source from which metadata was acquired"
    )
    retrieved_at: datetime = Field(
        default_factory=datetime.utcnow, description="When metadata was retrieved"
    )

    # Citation metrics (from Semantic Scholar)
    citation_count: Optional[int] = Field(None, description="Total citation count")
    influential_citation_count: Optional[int] = Field(
        None, description="Influential citation count (S2)"
    )

    # SPECTER2 embedding (from Semantic Scholar)
    embedding: Optional[list[float]] = Field(
        None, description="SPECTER2 paper embedding (768 dims)"
    )

    # Fields of study / topics
    fields_of_study: list[str] = Field(
        default_factory=list, description="Academic fields/topics"
    )

    @field_validator("doi")
    @classmethod
    def validate_doi(cls, v: Optional[str]) -> Optional[str]:
        """Validate DOI format if provided."""
        if v is not None and v.strip():
            # Clean common prefixes
            v = v.strip()
            for prefix in ["https://doi.org/", "http://doi.org/", "doi:"]:
                if v.lower().startswith(prefix.lower()):
                    v = v[len(prefix) :]
            if not is_valid_doi(v):
                return None
        return v if v else None

    @field_validator("arxiv_id")
    @classmethod
    def validate_arxiv_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate arXiv ID format if provided."""
        if v is not None and v.strip():
            v = v.strip()
            # Clean common prefixes
            for prefix in ["arXiv:", "arxiv:"]:
                if v.startswith(prefix):
                    v = v[len(prefix) :]
            if not is_valid_arxiv_id(v):
                return None
        return v if v else None

    def has_pdf(self) -> bool:
        """Check if a PDF URL is available."""
        return bool(self.pdf_url)

    def get_best_identifier(self) -> str:
        """Get the best available identifier (DOI preferred)."""
        if self.doi:
            return f"doi:{self.doi}"
        if self.arxiv_id:
            return f"arxiv:{self.arxiv_id}"
        if self.s2_id:
            return f"s2:{self.s2_id}"
        if self.openalex_id:
            return f"openalex:{self.openalex_id}"
        return self.paper_id

    def model_dump_json_safe(self) -> dict:
        """Return dict suitable for JSON serialization."""
        data = self.model_dump(mode="json")
        # Ensure datetime is serialized
        if self.retrieved_at:
            data["retrieved_at"] = self.retrieved_at.isoformat()
        if self.publication_date:
            data["publication_date"] = self.publication_date.isoformat()
        return data


class DownloadResult(BaseModel):
    """Result of a PDF download attempt."""

    identifier: str = Field(..., description="Paper identifier used for download")
    status: DownloadStatus = Field(..., description="Download status")
    source: SourceType = Field(..., description="Source used for download")
    file_path: Optional[str] = Field(None, description="Path to downloaded PDF")
    file_size: Optional[int] = Field(None, description="File size in bytes")
    content_hash: Optional[str] = Field(None, description="SHA-256 hash of content")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    downloaded_at: Optional[datetime] = Field(
        None, description="When download completed"
    )

    def is_success(self) -> bool:
        """Check if download was successful."""
        return self.status == DownloadStatus.COMPLETED and self.file_path is not None
