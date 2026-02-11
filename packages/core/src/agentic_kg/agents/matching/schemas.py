"""
Matching agent output schemas.

Pydantic models for structured outputs from agents that handle
problem mention-to-concept matching decisions.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    """Get current UTC time."""
    return datetime.now(timezone.utc)


# =============================================================================
# Enums for Matching Workflow
# =============================================================================


class EvaluatorDecision(str, Enum):
    """Possible decisions from the EvaluatorAgent."""

    APPROVE = "approve"  # Link mention to concept
    REJECT = "reject"  # Do not link, create new concept
    ESCALATE = "escalate"  # Needs multi-agent consensus


class ArbiterDecision(str, Enum):
    """Possible decisions from the ArbiterAgent."""

    LINK = "link"  # Link mention to concept
    CREATE_NEW = "create_new"  # Create new concept
    RETRY = "retry"  # Need another round of Maker/Hater debate


class EscalationReason(str, Enum):
    """Reasons for escalating to human review queue."""

    EVALUATOR_UNCERTAIN = "evaluator_uncertain"  # Evaluator couldn't decide
    CONSENSUS_FAILED = "consensus_failed"  # Maker/Hater couldn't agree
    ARBITER_LOW_CONFIDENCE = "arbiter_low_confidence"  # Arbiter uncertain
    MAX_ROUNDS_EXCEEDED = "max_rounds_exceeded"  # Hit 3-round limit


class ReviewResolution(str, Enum):
    """How a human reviewer resolved the match."""

    LINKED = "linked"  # Linked to existing concept
    CREATED_NEW = "created_new"  # Created new concept
    BLACKLISTED = "blacklisted"  # Permanently blocked


# =============================================================================
# EvaluatorAgent Schemas
# =============================================================================


class EvaluatorResult(BaseModel):
    """Output of the EvaluatorAgent for MEDIUM confidence matches."""

    decision: EvaluatorDecision = Field(
        ..., description="APPROVE, REJECT, or ESCALATE"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Agent confidence in this decision (0-1)"
    )
    reasoning: str = Field(
        ..., min_length=10, description="Detailed reasoning for the decision"
    )
    key_factors: list[str] = Field(
        default_factory=list,
        description="Key factors that influenced the decision",
    )
    similarity_assessment: str = Field(
        default="",
        description="Assessment of semantic similarity between mention and concept",
    )
    domain_match: bool = Field(
        default=True, description="Whether domains are compatible"
    )

    @field_validator("key_factors")
    @classmethod
    def validate_key_factors(cls, v: list[str]) -> list[str]:
        """Ensure at least one key factor is provided."""
        if not v:
            return ["No specific factors identified"]
        return v


# =============================================================================
# MakerAgent Schemas (argues FOR linking)
# =============================================================================


class Argument(BaseModel):
    """A single argument with supporting evidence."""

    claim: str = Field(..., min_length=5, description="The argument claim")
    evidence: str = Field(..., description="Supporting evidence for the claim")
    strength: float = Field(
        ge=0.0, le=1.0, default=0.5, description="How strong this argument is"
    )


class MakerResult(BaseModel):
    """Output of the MakerAgent (argues FOR linking)."""

    arguments: list[Argument] = Field(
        ..., min_length=1, max_length=5, description="Arguments for linking"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence in linking (0-1)"
    )
    strongest_argument: str = Field(
        ..., description="Summary of the strongest argument for linking"
    )
    semantic_similarity_evidence: str = Field(
        default="", description="Evidence of semantic similarity"
    )
    domain_alignment_evidence: str = Field(
        default="", description="Evidence of domain alignment"
    )

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, v: list[Argument]) -> list[Argument]:
        """Ensure arguments have reasonable structure."""
        if len(v) < 1:
            raise ValueError("MakerAgent must provide at least 1 argument")
        return v


# =============================================================================
# HaterAgent Schemas (argues AGAINST linking)
# =============================================================================


class HaterResult(BaseModel):
    """Output of the HaterAgent (argues AGAINST linking)."""

    arguments: list[Argument] = Field(
        ..., min_length=1, max_length=5, description="Arguments against linking"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence against linking (0-1)"
    )
    strongest_argument: str = Field(
        ..., description="Summary of the strongest argument against linking"
    )
    semantic_difference_evidence: str = Field(
        default="", description="Evidence of semantic differences"
    )
    domain_mismatch_evidence: str = Field(
        default="", description="Evidence of domain mismatch"
    )

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, v: list[Argument]) -> list[Argument]:
        """Ensure arguments have reasonable structure."""
        if len(v) < 1:
            raise ValueError("HaterAgent must provide at least 1 argument")
        return v


# =============================================================================
# ArbiterAgent Schemas (decides after debate)
# =============================================================================


class ArbiterResult(BaseModel):
    """Output of the ArbiterAgent (decides after Maker/Hater debate)."""

    decision: ArbiterDecision = Field(
        ..., description="LINK, CREATE_NEW, or RETRY"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this decision (0-1)"
    )
    reasoning: str = Field(
        ..., min_length=20, description="Explanation of the decision"
    )
    maker_weight: float = Field(
        ge=0.0, le=1.0, description="Weight given to Maker arguments (0-1)"
    )
    hater_weight: float = Field(
        ge=0.0, le=1.0, description="Weight given to Hater arguments (0-1)"
    )
    decisive_factor: str = Field(
        ..., description="The most important factor in reaching this decision"
    )
    false_negative_risk: str = Field(
        default="",
        description="Assessment of risk of missing a true duplicate",
    )

    @field_validator("decision")
    @classmethod
    def validate_confidence_threshold(
        cls, v: ArbiterDecision, info
    ) -> ArbiterDecision:
        """RETRY should be returned when confidence is below threshold."""
        # Note: Can't access 'confidence' from validator on another field
        # This validation happens in the agent logic instead
        return v


# =============================================================================
# Suggested Concept for Human Review
# =============================================================================


class SuggestedConcept(BaseModel):
    """A concept suggested for matching, with agent reasoning."""

    concept_id: str = Field(..., description="ID of the suggested concept")
    canonical_statement: str = Field(
        ..., description="The concept's canonical statement"
    )
    similarity_score: float = Field(
        ge=0.0, le=1.0, description="Vector similarity score"
    )
    final_score: float = Field(
        ge=0.0, le=1.0, description="Final score after boosts"
    )
    reasoning: str = Field(
        default="", description="Agent reasoning for this suggestion"
    )
    domain: Optional[str] = Field(default=None, description="Concept domain")
    mention_count: int = Field(
        default=0, description="Number of mentions linked to this concept"
    )


# =============================================================================
# Agent Context for Review Queue
# =============================================================================


class AgentContext(BaseModel):
    """Context from agent processing, stored with pending reviews."""

    escalation_reason: EscalationReason = Field(
        ..., description="Why this was escalated to human review"
    )
    evaluator_result: Optional[EvaluatorResult] = Field(
        default=None, description="Result from EvaluatorAgent if run"
    )
    maker_results: list[MakerResult] = Field(
        default_factory=list, description="Results from MakerAgent rounds"
    )
    hater_results: list[HaterResult] = Field(
        default_factory=list, description="Results from HaterAgent rounds"
    )
    arbiter_results: list[ArbiterResult] = Field(
        default_factory=list, description="Results from ArbiterAgent rounds"
    )
    rounds_attempted: int = Field(
        default=0, description="Number of consensus rounds attempted"
    )
    final_confidence: float = Field(
        ge=0.0, le=1.0, default=0.0, description="Final confidence before escalation"
    )


# =============================================================================
# Workflow State Summary
# =============================================================================


class MatchingWorkflowSummary(BaseModel):
    """Summary of a matching workflow for debugging/audit."""

    trace_id: str = Field(..., description="Unique trace ID for this workflow")
    mention_id: str = Field(..., description="ID of the mention being matched")
    mention_statement: str = Field(..., description="The mention's statement")
    paper_doi: Optional[str] = Field(default=None, description="Source paper DOI")

    initial_confidence: str = Field(
        ..., description="Initial confidence level (HIGH/MEDIUM/LOW)"
    )
    top_candidate_id: Optional[str] = Field(
        default=None, description="Top candidate concept ID"
    )
    top_candidate_score: float = Field(
        default=0.0, description="Top candidate similarity score"
    )

    final_decision: Optional[str] = Field(
        default=None, description="Final decision (linked/created_new/escalated)"
    )
    final_concept_id: Optional[str] = Field(
        default=None, description="Concept ID if linked"
    )

    agents_invoked: list[str] = Field(
        default_factory=list, description="List of agents that processed this"
    )
    total_rounds: int = Field(default=0, description="Total consensus rounds")
    total_duration_ms: int = Field(
        default=0, description="Total processing time in milliseconds"
    )

    escalated_to_human: bool = Field(
        default=False, description="Whether escalated to human review"
    )
    escalation_reason: Optional[EscalationReason] = Field(
        default=None, description="Reason for escalation if applicable"
    )

    created_at: datetime = Field(default_factory=_utc_now)
    completed_at: Optional[datetime] = Field(default=None)
