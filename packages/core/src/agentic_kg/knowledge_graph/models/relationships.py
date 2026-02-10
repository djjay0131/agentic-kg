"""
Relationship models for the Knowledge Graph.

Defines relationships between entities: problem relations, extraction relations,
authorship, and mention-to-concept links.
"""

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .enums import (
    ContradictionType,
    DependencyType,
    MatchMethod,
    RelationType,
)


def _utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime."""
    return datetime.now(timezone.utc)


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


class InstanceOfRelation(BaseModel):
    """Links a ProblemMention to its canonical ProblemConcept."""

    mention_id: str = Field(..., description="ProblemMention ID")
    concept_id: str = Field(..., description="ProblemConcept ID")
    confidence: float = Field(..., ge=0, le=1, description="Match confidence score")
    match_method: MatchMethod = Field(..., description="How match was determined")
    matched_at: datetime = Field(default_factory=_utc_now, description="When matched")
    matched_by: Optional[str] = Field(default=None, description="Agent or user who matched")
