"""API request/response schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Problem Schemas
# =============================================================================


class EvidenceResponse(BaseModel):
    """Evidence linking a problem to a paper."""

    source_doi: Optional[str] = None
    source_title: Optional[str] = None
    section: Optional[str] = None
    quoted_text: Optional[str] = None


class ExtractionMetadataResponse(BaseModel):
    """Metadata about how a problem was extracted."""

    extraction_model: Optional[str] = None
    confidence_score: Optional[float] = None
    extractor_version: Optional[str] = None
    human_reviewed: bool = False


class ProblemSummary(BaseModel):
    """Abbreviated problem for list views."""

    id: str
    statement: str
    domain: Optional[str] = None
    status: str
    confidence: Optional[float] = None
    created_at: Optional[datetime] = None


class ProblemDetail(BaseModel):
    """Full problem detail."""

    id: str
    statement: str
    domain: Optional[str] = None
    status: str
    assumptions: list[dict] = Field(default_factory=list)
    constraints: list[dict] = Field(default_factory=list)
    datasets: list[dict] = Field(default_factory=list)
    metrics: list[dict] = Field(default_factory=list)
    baselines: list[dict] = Field(default_factory=list)
    evidence: Optional[EvidenceResponse] = None
    extraction_metadata: Optional[ExtractionMetadataResponse] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProblemUpdate(BaseModel):
    """Fields that can be updated on a problem."""

    status: Optional[str] = None
    domain: Optional[str] = None
    statement: Optional[str] = None


class ProblemListResponse(BaseModel):
    """Paginated problem list."""

    problems: list[ProblemSummary]
    total: int
    limit: int
    offset: int


# =============================================================================
# Paper Schemas
# =============================================================================


class PaperSummary(BaseModel):
    """Abbreviated paper for list views."""

    doi: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None


class PaperDetail(BaseModel):
    """Full paper detail."""

    doi: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    venue: Optional[str] = None
    abstract: Optional[str] = None
    arxiv_id: Optional[str] = None
    pdf_url: Optional[str] = None
    citation_count: Optional[int] = None


class PaperListResponse(BaseModel):
    """Paginated paper list."""

    papers: list[PaperSummary]
    total: int
    limit: int
    offset: int


# =============================================================================
# Search Schemas
# =============================================================================


class SearchRequest(BaseModel):
    """Search request body."""

    query: str = Field(..., min_length=1, description="Search query text")
    domain: Optional[str] = Field(default=None, description="Filter by domain")
    status: Optional[str] = Field(default=None, description="Filter by status")
    top_k: int = Field(default=10, ge=1, le=100, description="Max results")
    semantic_weight: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Weight for semantic vs structured"
    )


class SearchResultItem(BaseModel):
    """Single search result."""

    problem: ProblemSummary
    score: float
    match_type: str


class SearchResponse(BaseModel):
    """Search results."""

    results: list[SearchResultItem]
    query: str
    total: int


# =============================================================================
# Extraction Schemas
# =============================================================================


class ExtractRequest(BaseModel):
    """Extraction request body."""

    url: Optional[str] = Field(default=None, description="PDF URL")
    text: Optional[str] = Field(default=None, description="Raw text")
    title: Optional[str] = Field(default=None, description="Paper title")
    doi: Optional[str] = Field(default=None, description="Paper DOI")
    authors: list[str] = Field(default_factory=list, description="Paper authors")


class ExtractedProblemResponse(BaseModel):
    """Extracted problem in API response."""

    statement: str
    domain: Optional[str] = None
    confidence: float
    quoted_text: str


class ExtractResponse(BaseModel):
    """Extraction result."""

    success: bool
    paper_title: Optional[str] = None
    problems_extracted: int = 0
    relations_found: int = 0
    duration_ms: float = 0.0
    problems: list[ExtractedProblemResponse] = Field(default_factory=list)
    stages: list[dict] = Field(default_factory=list)


class BatchExtractRequest(BaseModel):
    """Batch extraction request."""

    papers: list[ExtractRequest] = Field(..., min_length=1)


class BatchExtractResponse(BaseModel):
    """Batch extraction result."""

    total: int
    succeeded: int
    failed: int
    total_problems: int
    results: list[ExtractResponse] = Field(default_factory=list)


# =============================================================================
# Health & Stats
# =============================================================================


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str = "0.1.0"
    neo4j_connected: bool = False


class StatsResponse(BaseModel):
    """System statistics."""

    total_problems: int = 0
    total_papers: int = 0
    problems_by_status: dict[str, int] = Field(default_factory=dict)
    problems_by_domain: dict[str, int] = Field(default_factory=dict)


# =============================================================================
# Graph Schemas
# =============================================================================


class GraphNode(BaseModel):
    """Node in the knowledge graph visualization."""

    id: str
    label: str
    type: str  # "problem", "paper", "domain"
    properties: dict = Field(default_factory=dict)


class GraphLink(BaseModel):
    """Edge/link in the knowledge graph visualization."""

    source: str
    target: str
    type: str  # relation type
    properties: dict = Field(default_factory=dict)


class GraphResponse(BaseModel):
    """Graph data for visualization."""

    nodes: list[GraphNode] = Field(default_factory=list)
    links: list[GraphLink] = Field(default_factory=list)
