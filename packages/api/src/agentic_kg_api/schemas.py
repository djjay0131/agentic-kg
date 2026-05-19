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
    status: str
    confidence: Optional[float] = None
    created_at: Optional[datetime] = None


class ProblemDetail(BaseModel):
    """Full problem detail."""

    id: str
    statement: str
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
    total_topics: int = 0
    problems_by_status: dict[str, int] = Field(default_factory=dict)
    problems_by_topic: dict[str, int] = Field(default_factory=dict)


# =============================================================================
# Graph Schemas
# =============================================================================


class GraphNode(BaseModel):
    """Node in the knowledge graph visualization."""

    id: str
    label: str
    type: str  # "problem", "paper", "topic"
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


# =============================================================================
# Review Queue Schemas
# =============================================================================


class SuggestedConceptResponse(BaseModel):
    """A suggested concept for review."""

    concept_id: str
    canonical_statement: str
    similarity_score: float
    final_score: float
    agent_reasoning: Optional[str] = None
    mention_count: int = 0


class AgentContextResponse(BaseModel):
    """Agent context from matching workflow."""

    escalation_reason: str
    evaluator_decision: Optional[str] = None
    evaluator_confidence: Optional[float] = None
    maker_arguments: list[str] = Field(default_factory=list)
    hater_arguments: list[str] = Field(default_factory=list)
    arbiter_decision: Optional[str] = None
    rounds_attempted: int = 0
    final_confidence: float = 0.0


class PendingReviewSummary(BaseModel):
    """Summary of a pending review for list views."""

    id: str
    trace_id: str
    mention_id: str
    mention_statement: str
    paper_doi: str
    priority: int
    status: str
    assigned_to: Optional[str] = None
    created_at: datetime
    sla_deadline: datetime


class PendingReviewDetail(BaseModel):
    """Full detail of a pending review including agent context."""

    id: str
    trace_id: str
    mention_id: str
    mention_statement: str
    paper_doi: str
    paper_title: Optional[str] = None
    suggested_concepts: list[SuggestedConceptResponse] = Field(default_factory=list)
    agent_context: AgentContextResponse
    priority: int
    status: str
    assigned_to: Optional[str] = None
    assigned_at: Optional[datetime] = None
    created_at: datetime
    sla_deadline: datetime
    resolution: Optional[str] = None
    resolved_concept_id: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None


class PendingReviewListResponse(BaseModel):
    """Paginated list of pending reviews."""

    reviews: list[PendingReviewSummary]
    total: int
    limit: int


class ReviewResolutionRequest(BaseModel):
    """Request body for resolving a review."""

    resolution: str = Field(..., description="One of: linked, created_new, blacklisted")
    concept_id: Optional[str] = Field(
        default=None, description="Required if resolution is 'linked'"
    )
    notes: Optional[str] = Field(default=None, description="Optional resolution notes")


# =============================================================================
# Ingestion Schemas
# =============================================================================


class IngestRequest(BaseModel):
    """Request body for paper ingestion."""

    query: str = Field(..., description="Search query for paper discovery")
    limit: int = Field(default=20, ge=1, le=100, description="Max papers to fetch")
    sources: Optional[list[str]] = Field(
        default=None, description="API sources (semantic_scholar, arxiv, openalex)"
    )
    dry_run: bool = Field(default=False, description="Search only, don't extract or integrate")
    enable_agent_workflow: bool = Field(
        default=True, description="Route MEDIUM/LOW matches through agents"
    )
    min_extraction_confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Minimum problem confidence to integrate"
    )


class SanityCheckResponse(BaseModel):
    """Result of a single graph sanity check."""

    name: str
    passed: bool
    count: int
    description: str


class IngestStatusResponse(BaseModel):
    """Response for ingestion status (both queued and complete)."""

    trace_id: str
    status: str = Field(description="queued|running|completed|failed|dry_run")
    query: str = ""

    # Counts (populated as phases complete)
    papers_found: int = 0
    papers_imported: int = 0
    papers_extracted: int = 0
    papers_skipped_no_pdf: int = 0
    total_problems: int = 0
    concepts_created: int = 0
    concepts_linked: int = 0

    # Dry run
    dry_run_papers: list[dict] = Field(default_factory=list)

    # Errors
    extraction_errors: dict[str, str] = Field(default_factory=dict)
    error: Optional[str] = None

    # Sanity checks
    sanity_checks: list[SanityCheckResponse] = Field(default_factory=list)


# =============================================================================
# Topic Schemas (E-1)
# =============================================================================


class TopicSummary(BaseModel):
    """Flat topic summary for list views."""

    id: str
    name: str
    level: str
    parent_id: Optional[str] = None
    source: str
    description: Optional[str] = None
    problem_count: int = 0
    paper_count: int = 0


class TopicTreeNode(BaseModel):
    """Topic with nested children for hierarchical responses."""

    id: str
    name: str
    level: str
    parent_id: Optional[str] = None
    source: str
    description: Optional[str] = None
    problem_count: int = 0
    paper_count: int = 0
    children: list["TopicTreeNode"] = Field(default_factory=list)


TopicTreeNode.model_rebuild()


class TopicListResponse(BaseModel):
    """Response for GET /api/topics."""

    topics: list[TopicSummary] = Field(default_factory=list)
    total: int = 0


class TopicTreeResponse(BaseModel):
    """Response for GET /api/topics?tree=true."""

    roots: list[TopicTreeNode] = Field(default_factory=list)


class TopicDetail(TopicSummary):
    """Topic detail including immediate parent and children."""

    parent: Optional[TopicSummary] = None
    children: list[TopicSummary] = Field(default_factory=list)


class TopicProblemsResponse(BaseModel):
    """Response for GET /api/topics/{id}/problems."""

    topic_id: str
    problems: list[ProblemSummary] = Field(default_factory=list)
    total: int = 0
    include_subtopics: bool = True


class TopicSearchResultItem(BaseModel):
    """A single similarity-search hit."""

    topic: TopicSummary
    score: float


class TopicSearchResponse(BaseModel):
    """Response for GET /api/topics/search."""

    query: str
    results: list[TopicSearchResultItem] = Field(default_factory=list)


class TopicAssignRequest(BaseModel):
    """Request body for POST /api/topics/{id}/assign."""

    entity_id: str = Field(
        ..., description="Problem id, ProblemMention/Concept id, or Paper DOI"
    )
    entity_label: str = Field(
        ...,
        description=(
            "Source label: one of Problem, ProblemMention, "
            "ProblemConcept, Paper"
        ),
    )


class TopicAssignResponse(BaseModel):
    """Response for POST /api/topics/{id}/assign."""

    topic_id: str
    entity_id: str
    entity_label: str
    created: bool = Field(
        description="True if the edge was created, False if it already existed"
    )

