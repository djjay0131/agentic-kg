"""
Pydantic models for the Knowledge Graph entities.

Defines Problem, Paper, Author, and supporting models for the research
knowledge graph with validation and JSON serialization for Neo4j storage.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


# =============================================================================
# Enums
# =============================================================================


class ProblemStatus(str, Enum):
    """Status of a research problem."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    DEPRECATED = "deprecated"


class ConstraintType(str, Enum):
    """Type of constraint on a research problem."""

    COMPUTATIONAL = "computational"
    DATA = "data"
    METHODOLOGICAL = "methodological"
    THEORETICAL = "theoretical"


class RelationType(str, Enum):
    """Types of relationships between problems."""

    EXTENDS = "EXTENDS"
    CONTRADICTS = "CONTRADICTS"
    DEPENDS_ON = "DEPENDS_ON"
    REFRAMES = "REFRAMES"


class ContradictionType(str, Enum):
    """Types of contradictions between problems."""

    EMPIRICAL = "empirical"
    THEORETICAL = "theoretical"
    METHODOLOGICAL = "methodological"


class DependencyType(str, Enum):
    """Types of dependencies between problems."""

    PREREQUISITE = "prerequisite"
    DATA_DEPENDENCY = "data_dependency"
    METHODOLOGICAL = "methodological"


# =============================================================================
# Supporting Models
# =============================================================================


class Assumption(BaseModel):
    """An assumption underlying a research problem."""

    text: str = Field(..., min_length=1, description="The assumption statement")
    implicit: bool = Field(default=False, description="Whether explicitly stated or inferred")
    confidence: float = Field(ge=0, le=1, default=0.8, description="Confidence score")


class Constraint(BaseModel):
    """A constraint on a research problem."""

    text: str = Field(..., min_length=1, description="The constraint description")
    type: ConstraintType = Field(..., description="Type of constraint")
    confidence: float = Field(ge=0, le=1, default=0.8, description="Confidence score")


class Dataset(BaseModel):
    """A dataset associated with a research problem."""

    name: str = Field(..., min_length=1, description="Dataset name")
    url: Optional[str] = Field(default=None, description="Link to dataset")
    available: bool = Field(default=True, description="Whether publicly available")
    size: Optional[str] = Field(default=None, description="Dataset size description")


class Metric(BaseModel):
    """An evaluation metric for a research problem."""

    name: str = Field(..., min_length=1, description="Metric name (e.g., 'F1-score', 'BLEU')")
    description: Optional[str] = Field(default=None, description="Metric description")
    baseline_value: Optional[float] = Field(default=None, description="Current best/baseline")


class Baseline(BaseModel):
    """A baseline method for a research problem."""

    name: str = Field(..., min_length=1, description="Baseline method name")
    paper_doi: Optional[str] = Field(default=None, description="DOI of baseline paper")
    performance: dict = Field(default_factory=dict, description="Metric-value pairs")


class Evidence(BaseModel):
    """Evidence linking a problem to its source paper."""

    source_doi: str = Field(..., description="DOI of source paper")
    source_title: str = Field(..., min_length=1, description="Paper title")
    section: str = Field(..., description="Section where extracted")
    quoted_text: str = Field(..., min_length=1, description="Original text from paper")
    char_offset_start: Optional[int] = Field(default=None, ge=0)
    char_offset_end: Optional[int] = Field(default=None, ge=0)

    @field_validator("source_doi")
    @classmethod
    def validate_doi(cls, v: str) -> str:
        """Validate DOI format (basic check)."""
        if not v.startswith("10."):
            raise ValueError("DOI must start with '10.'")
        return v


class ExtractionMetadata(BaseModel):
    """Metadata about how a problem was extracted."""

    extracted_at: datetime = Field(default_factory=_utc_now)
    extractor_version: str = Field(default="1.0.0")
    extraction_model: str = Field(..., description="Model used (e.g., 'gpt-4', 'claude-3')")
    confidence_score: float = Field(ge=0, le=1, description="Extraction confidence")
    human_reviewed: bool = Field(default=False)
    reviewed_by: Optional[str] = Field(default=None)
    reviewed_at: Optional[datetime] = Field(default=None)


# =============================================================================
# Core Entity Models
# =============================================================================


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
    evidence: Evidence = Field(..., description="Source evidence from paper")
    extraction_metadata: ExtractionMetadata = Field(..., description="Extraction details")

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


# =============================================================================
# Relation Models
# =============================================================================


class ProblemRelation(BaseModel):
    """Base model for relations between problems."""

    from_problem_id: str = Field(..., description="Source problem ID")
    to_problem_id: str = Field(..., description="Target problem ID")
    relation_type: RelationType = Field(..., description="Type of relation")
    confidence: float = Field(ge=0, le=1, default=0.8, description="Confidence score")
    evidence_doi: Optional[str] = Field(default=None, description="Supporting paper DOI")
    created_at: datetime = Field(default_factory=_utc_now)


class ExtendsRelation(ProblemRelation):
    """Problem B extends/builds on Problem A."""

    relation_type: RelationType = Field(default=RelationType.EXTENDS)
    inferred_by: Optional[str] = Field(default=None, description="Model that inferred relation")


class ContradictsRelation(ProblemRelation):
    """Problem B presents conflicting findings to Problem A."""

    relation_type: RelationType = Field(default=RelationType.CONTRADICTS)
    contradiction_type: ContradictionType = Field(..., description="Type of contradiction")


class DependsOnRelation(ProblemRelation):
    """Problem B requires solution to Problem A first."""

    relation_type: RelationType = Field(default=RelationType.DEPENDS_ON)
    dependency_type: DependencyType = Field(..., description="Type of dependency")


class ReframesRelation(ProblemRelation):
    """Problem B redefines the problem space of A."""

    relation_type: RelationType = Field(default=RelationType.REFRAMES)


class ExtractedFromRelation(BaseModel):
    """Links a Problem to its source Paper."""

    problem_id: str = Field(..., description="Problem ID")
    paper_doi: str = Field(..., description="Paper DOI")
    section: str = Field(..., description="Section where extracted")
    extraction_date: datetime = Field(default_factory=_utc_now)


class AuthoredByRelation(BaseModel):
    """Links a Paper to its Author."""

    paper_doi: str = Field(..., description="Paper DOI")
    author_id: str = Field(..., description="Author ID")
    author_position: int = Field(..., ge=1, description="Author position (1=first)")
