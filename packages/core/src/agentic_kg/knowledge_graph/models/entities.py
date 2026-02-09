"""
Entity models for the Knowledge Graph.

Defines the main node types: Problem, ProblemMention, ProblemConcept,
MatchCandidate, Paper, and Author.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .enums import (
    MatchConfidence,
    MatchMethod,
    ProblemStatus,
    ReviewStatus,
)
from .supporting import (
    Assumption,
    Baseline,
    Constraint,
    Dataset,
    Evidence,
    ExtractionMetadata,
    Metric,
)


def _utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


class Problem(BaseModel):
    """
    A research problem as a first-class entity in the knowledge graph.

    Problems are the central nodes in our graph, representing open questions,
    challenges, and research directions extracted from scientific papers.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier")
    statement: str = Field(..., min_length=20, description="The research problem statement")
    domain: Optional[str] = Field(default=None, description="Research domain/field")
    status: ProblemStatus = Field(default=ProblemStatus.OPEN, description="Problem status")

    # Structured attributes
    assumptions: list[Assumption] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    baselines: list[Baseline] = Field(default_factory=list)

    # Provenance
    evidence: Optional[Evidence] = Field(default=None, description="Source evidence from paper")
    extraction_metadata: Optional[ExtractionMetadata] = Field(default=None, description="Extraction details")

    @model_validator(mode='after')
    def validate_resolved_status(self) -> 'Problem':
        """Require evidence with DOI when problem status is RESOLVED or DEPRECATED."""
        if self.status in [ProblemStatus.RESOLVED, ProblemStatus.DEPRECATED]:
            if not self.evidence:
                raise ValueError(
                    f"Status '{self.status.value}' requires evidence field with reference to supporting paper"
                )
            if not self.evidence.source_doi:
                raise ValueError(
                    f"Status '{self.status.value}' requires evidence.source_doi pointing to the paper that resolves/deprecates this problem"
                )
        return self

    # Semantic search
    embedding: Optional[list[float]] = Field(
        default=None, description="Problem statement embedding (1536 dims)"
    )

    # Timestamps and versioning
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    version: int = Field(default=1, ge=1, description="Version number")

    def to_neo4j_properties(self) -> dict:
        """Convert to Neo4j node properties (JSON-serializable)."""
        data = self.model_dump(exclude={"embedding"})
        # Convert nested objects to JSON strings for Neo4j
        data["assumptions"] = [a.model_dump() for a in self.assumptions]
        data["constraints"] = [c.model_dump() for c in self.constraints]
        data["datasets"] = [d.model_dump() for d in self.datasets]
        data["metrics"] = [m.model_dump() for m in self.metrics]
        data["baselines"] = [b.model_dump() for b in self.baselines]
        data["evidence"] = self.evidence.model_dump()
        data["extraction_metadata"] = self.extraction_metadata.model_dump()
        # Convert datetime to ISO strings
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        extracted_at = self.extraction_metadata.extracted_at.isoformat()
        data["extraction_metadata"]["extracted_at"] = extracted_at
        if self.extraction_metadata.reviewed_at:
            reviewed_at = self.extraction_metadata.reviewed_at.isoformat()
            data["extraction_metadata"]["reviewed_at"] = reviewed_at
        return data


class ProblemMention(BaseModel):
    """
    A paper-specific mention of a research problem.

    ProblemMentions preserve the original problem statement as it appears
    in a specific paper, maintaining provenance and paper-specific context.
    Each mention is matched to a canonical ProblemConcept.
    """

    # Identity
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier")
    statement: str = Field(..., min_length=20, description="Problem as stated in this paper")
    paper_doi: str = Field(..., description="Source paper DOI")
    section: str = Field(..., description="Section where problem was mentioned")

    # Rich metadata (same structure as original Problem)
    domain: Optional[str] = Field(default=None, description="Research domain/field")
    assumptions: list[Assumption] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    baselines: list[Baseline] = Field(default_factory=list)

    # Extraction provenance
    quoted_text: str = Field(..., min_length=1, description="Original text from paper")
    extraction_metadata: Optional[ExtractionMetadata] = Field(
        default=None, description="Extraction details"
    )
    embedding: Optional[list[float]] = Field(
        default=None, description="Statement embedding (1536 dims)"
    )

    # Concept linking
    concept_id: Optional[str] = Field(default=None, description="Linked ProblemConcept ID")
    match_confidence: Optional[MatchConfidence] = Field(
        default=None, description="Match confidence level"
    )
    match_score: Optional[float] = Field(
        default=None, ge=0, le=1, description="Similarity score (0-1)"
    )
    match_method: Optional[MatchMethod] = Field(default=None, description="How match was made")

    # Review tracking
    review_status: ReviewStatus = Field(default=ReviewStatus.PENDING, description="Review status")
    reviewed_by: Optional[str] = Field(default=None, description="User ID of reviewer")
    reviewed_at: Optional[datetime] = Field(default=None, description="Review timestamp")
    agent_consensus: Optional[dict] = Field(
        default=None, description="Maker/hater debate results"
    )

    # Timestamps
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("paper_doi")
    @classmethod
    def validate_doi(cls, v: str) -> str:
        """Validate DOI format."""
        if not v.startswith("10."):
            raise ValueError("DOI must start with '10.'")
        return v

    def to_neo4j_properties(self) -> dict:
        """Convert to Neo4j node properties (JSON-serializable)."""
        data = self.model_dump(exclude={"embedding"})
        # Convert nested objects to JSON
        data["assumptions"] = [a.model_dump() for a in self.assumptions]
        data["constraints"] = [c.model_dump() for c in self.constraints]
        data["datasets"] = [d.model_dump() for d in self.datasets]
        data["metrics"] = [m.model_dump() for m in self.metrics]
        data["baselines"] = [b.model_dump() for b in self.baselines]
        if self.extraction_metadata:
            data["extraction_metadata"] = self.extraction_metadata.model_dump()
            extracted_at = self.extraction_metadata.extracted_at.isoformat()
            data["extraction_metadata"]["extracted_at"] = extracted_at
            if self.extraction_metadata.reviewed_at:
                reviewed_at = self.extraction_metadata.reviewed_at.isoformat()
                data["extraction_metadata"]["reviewed_at"] = reviewed_at
        # Convert datetime to ISO strings
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        if self.reviewed_at:
            data["reviewed_at"] = self.reviewed_at.isoformat()
        # Convert enums to strings
        if self.match_confidence:
            data["match_confidence"] = self.match_confidence.value
        if self.match_method:
            data["match_method"] = self.match_method.value
        data["review_status"] = self.review_status.value
        return data


class ProblemConcept(BaseModel):
    """
    Canonical representation of a research problem.

    ProblemConcepts represent the underlying research problem that may be
    mentioned across multiple papers. The canonical statement is synthesized
    from all mentions, and metadata is aggregated with provenance.
    """

    # Identity
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier")
    canonical_statement: str = Field(
        ..., min_length=20, description="AI-synthesized canonical problem statement"
    )
    domain: str = Field(..., description="Research domain/field")
    status: ProblemStatus = Field(default=ProblemStatus.OPEN, description="Problem status")

    # Aggregated metadata
    assumptions: list[Assumption] = Field(default_factory=list, description="Union of all mentions")
    constraints: list[Constraint] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)

    # Baselines with validation
    verified_baselines: list[Baseline] = Field(
        default_factory=list, description="Reproducible baselines"
    )
    claimed_baselines: list[Baseline] = Field(
        default_factory=list, description="Unverified baselines"
    )

    # Synthesis metadata
    synthesis_method: str = Field(default="llm_synthesis", description="How statement was created")
    synthesis_model: Optional[str] = Field(default=None, description="Model used (e.g., 'gpt-4')")
    synthesized_at: Optional[datetime] = Field(default=None, description="When synthesized")
    synthesized_by: Optional[str] = Field(default=None, description="Agent or human user")
    human_edited: bool = Field(
        default=False, description="Whether human has edited canonical statement"
    )

    # Aggregation stats
    mention_count: int = Field(default=0, ge=0, description="Number of mentions")
    paper_count: int = Field(default=0, ge=0, description="Number of unique papers")
    first_mentioned_year: Optional[int] = Field(
        default=None, ge=1900, le=2100, description="Earliest mention year"
    )
    last_mentioned_year: Optional[int] = Field(
        default=None, ge=1900, le=2100, description="Most recent mention year"
    )

    # Semantic search
    embedding: Optional[list[float]] = Field(
        default=None, description="Canonical statement embedding (1536 dims)"
    )

    # Versioning
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    version: int = Field(default=1, ge=1, description="Version number")

    @model_validator(mode="after")
    def validate_year_order(self) -> "ProblemConcept":
        """Ensure first_mentioned_year <= last_mentioned_year."""
        if (
            self.first_mentioned_year is not None
            and self.last_mentioned_year is not None
            and self.first_mentioned_year > self.last_mentioned_year
        ):
            raise ValueError("first_mentioned_year must be <= last_mentioned_year")
        return self

    def to_neo4j_properties(self) -> dict:
        """Convert to Neo4j node properties (JSON-serializable)."""
        data = self.model_dump(exclude={"embedding"})
        # Convert nested objects to JSON
        data["assumptions"] = [a.model_dump() for a in self.assumptions]
        data["constraints"] = [c.model_dump() for c in self.constraints]
        data["datasets"] = [d.model_dump() for d in self.datasets]
        data["metrics"] = [m.model_dump() for m in self.metrics]
        data["verified_baselines"] = [b.model_dump() for b in self.verified_baselines]
        data["claimed_baselines"] = [b.model_dump() for b in self.claimed_baselines]
        # Convert datetime to ISO strings
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        if self.synthesized_at:
            data["synthesized_at"] = self.synthesized_at.isoformat()
        # Convert enum to string
        data["status"] = self.status.value
        return data


class MatchCandidate(BaseModel):
    """
    A candidate concept match for a problem mention.

    Used during similarity search to represent potential matches
    with scores and reasoning.
    """

    concept_id: str = Field(..., description="Candidate ProblemConcept ID")
    concept_statement: str = Field(..., description="Canonical statement")
    similarity_score: float = Field(..., ge=0, le=1, description="Cosine similarity (0-1)")
    confidence: MatchConfidence = Field(..., description="Confidence classification")
    reasoning: Optional[str] = Field(default=None, description="Why this is a good match")

    # Additional match factors
    citation_boost: float = Field(
        default=0.0, ge=0, le=0.2, description="Boost from citation relationship"
    )
    domain_match: bool = Field(default=False, description="Whether domains match")
    metadata_overlap: dict = Field(
        default_factory=dict, description="Overlapping datasets/metrics/etc"
    )

    @property
    def final_score(self) -> float:
        """Calculate final score with all boosts applied."""
        return min(1.0, self.similarity_score + self.citation_boost)


class Paper(BaseModel):
    """
    A scientific paper in the knowledge graph.

    Papers are source nodes that problems are extracted from.
    """

    doi: str = Field(..., description="DOI (primary key)")
    title: str = Field(..., min_length=10, description="Paper title")
    authors: list[str] = Field(default_factory=list, description="Author names")
    venue: Optional[str] = Field(default=None, description="Publication venue")
    year: int = Field(..., ge=1900, le=2100, description="Publication year")
    abstract: Optional[str] = Field(default=None, description="Paper abstract")

    # External identifiers
    arxiv_id: Optional[str] = Field(default=None, description="arXiv identifier")
    openalex_id: Optional[str] = Field(default=None, description="OpenAlex identifier")
    semantic_scholar_id: Optional[str] = Field(default=None, description="Semantic Scholar ID")

    # Content
    pdf_url: Optional[str] = Field(default=None, description="URL to PDF")
    full_text: Optional[str] = Field(default=None, description="Full text content")

    # Metadata
    ingested_at: datetime = Field(default_factory=_utc_now)

    @field_validator("doi")
    @classmethod
    def validate_doi(cls, v: str) -> str:
        """Validate DOI format."""
        if not v.startswith("10."):
            raise ValueError("DOI must start with '10.'")
        return v

    def to_neo4j_properties(self) -> dict:
        """Convert to Neo4j node properties."""
        data = self.model_dump(exclude={"full_text"})  # Exclude large text
        data["ingested_at"] = self.ingested_at.isoformat()
        return data


class Author(BaseModel):
    """
    An author in the knowledge graph.

    Authors are linked to papers via AUTHORED_BY relationships.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier")
    name: str = Field(..., min_length=1, description="Author name")
    affiliations: list[str] = Field(default_factory=list, description="Institutional affiliations")
    orcid: Optional[str] = Field(default=None, description="ORCID identifier")
    semantic_scholar_id: Optional[str] = Field(
        default=None, description="Semantic Scholar author ID"
    )

    @field_validator("orcid")
    @classmethod
    def validate_orcid(cls, v: Optional[str]) -> Optional[str]:
        """Validate ORCID format if provided."""
        if v is not None and not v.startswith("0000-"):
            raise ValueError("ORCID must start with '0000-'")
        return v

    def to_neo4j_properties(self) -> dict:
        """Convert to Neo4j node properties."""
        return self.model_dump()
