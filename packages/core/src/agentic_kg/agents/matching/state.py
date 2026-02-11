"""
Matching workflow state for LangGraph.

Defines the shared state that flows through the matching agent workflow graph.
Used for MEDIUM and LOW confidence problem mention matching decisions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, TypedDict

from agentic_kg.agents.matching.schemas import (
    ArbiterResult,
    EscalationReason,
    EvaluatorResult,
    HaterResult,
    MakerResult,
    SuggestedConcept,
)


class MatchingWorkflowState(TypedDict, total=False):
    """
    Shared state for the matching workflow graph.

    LangGraph passes this state between nodes. Each agent reads its
    inputs and writes its outputs to this dict.
    """

    # --- Workflow metadata ---
    trace_id: str  # Unique trace ID for audit trail
    run_id: str  # Workflow run ID
    status: str  # pending, running, completed, failed, escalated
    current_step: str  # Current workflow step name
    created_at: str  # ISO format
    updated_at: str

    # --- Input: Mention to be matched ---
    mention_id: str
    mention_statement: str
    mention_embedding: list[float]  # 1536-dim embedding
    mention_domain: Optional[str]
    paper_doi: Optional[str]
    paper_title: Optional[str]

    # --- Input: Candidate concept ---
    candidate_concept_id: Optional[str]
    candidate_statement: Optional[str]
    candidate_domain: Optional[str]
    candidate_embedding: Optional[list[float]]
    candidate_mention_count: int
    similarity_score: float  # Vector similarity
    final_score: float  # After citation boost

    # --- Confidence classification ---
    initial_confidence: str  # HIGH, MEDIUM, LOW, REJECTED
    confidence_threshold_high: float  # 0.95
    confidence_threshold_medium: float  # 0.80
    confidence_threshold_low: float  # 0.50

    # --- EvaluatorAgent output (MEDIUM confidence) ---
    evaluator_result: Optional[dict]  # Serialized EvaluatorResult
    evaluator_decision: Optional[str]  # approve, reject, escalate

    # --- Maker/Hater/Arbiter outputs (LOW confidence) ---
    current_round: int  # 1, 2, or 3
    max_rounds: int  # Default 3
    maker_results: list[dict]  # Serialized MakerResult per round
    hater_results: list[dict]  # Serialized HaterResult per round
    arbiter_results: list[dict]  # Serialized ArbiterResult per round
    consensus_reached: bool

    # --- Final decision ---
    final_decision: Optional[str]  # linked, created_new, escalated
    final_concept_id: Optional[str]  # Concept ID if linked
    final_confidence: float
    decision_reasoning: str

    # --- Escalation to human queue ---
    escalated: bool
    escalation_reason: Optional[str]  # EscalationReason value
    suggested_concepts: list[dict]  # Serialized SuggestedConcept list

    # --- Performance tracking ---
    start_time_ms: int
    end_time_ms: int
    total_duration_ms: int

    # --- Audit trail ---
    messages: list[dict]  # Log of agent actions
    errors: list[str]


def create_matching_state(
    mention_id: str,
    mention_statement: str,
    mention_embedding: list[float],
    candidate_concept_id: Optional[str] = None,
    candidate_statement: Optional[str] = None,
    similarity_score: float = 0.0,
    paper_doi: Optional[str] = None,
    mention_domain: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> MatchingWorkflowState:
    """Create a fresh matching workflow state with defaults."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_ms = int(now.timestamp() * 1000)

    return MatchingWorkflowState(
        # Workflow metadata
        trace_id=trace_id or f"match-{uuid.uuid4().hex[:12]}",
        run_id=str(uuid.uuid4()),
        status="pending",
        current_step="",
        created_at=now_iso,
        updated_at=now_iso,
        # Mention input
        mention_id=mention_id,
        mention_statement=mention_statement,
        mention_embedding=mention_embedding,
        mention_domain=mention_domain,
        paper_doi=paper_doi,
        paper_title=None,
        # Candidate concept
        candidate_concept_id=candidate_concept_id,
        candidate_statement=candidate_statement,
        candidate_domain=None,
        candidate_embedding=None,
        candidate_mention_count=0,
        similarity_score=similarity_score,
        final_score=similarity_score,
        # Confidence
        initial_confidence="",
        confidence_threshold_high=0.95,
        confidence_threshold_medium=0.80,
        confidence_threshold_low=0.50,
        # Evaluator
        evaluator_result=None,
        evaluator_decision=None,
        # Consensus
        current_round=0,
        max_rounds=3,
        maker_results=[],
        hater_results=[],
        arbiter_results=[],
        consensus_reached=False,
        # Final decision
        final_decision=None,
        final_concept_id=None,
        final_confidence=0.0,
        decision_reasoning="",
        # Escalation
        escalated=False,
        escalation_reason=None,
        suggested_concepts=[],
        # Performance
        start_time_ms=now_ms,
        end_time_ms=0,
        total_duration_ms=0,
        # Audit
        messages=[],
        errors=[],
    )


def add_matching_message(
    state: MatchingWorkflowState, agent: str, content: str
) -> MatchingWorkflowState:
    """Add an audit message to the matching workflow state."""
    messages = list(state.get("messages", []))
    messages.append(
        {
            "agent": agent,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    return {
        **state,
        "messages": messages,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def add_matching_error(
    state: MatchingWorkflowState, error: str
) -> MatchingWorkflowState:
    """Record an error in the matching workflow."""
    errors = list(state.get("errors", []))
    errors.append(error)
    return {
        **state,
        "errors": errors,
        "status": "failed",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def complete_matching_workflow(
    state: MatchingWorkflowState,
    decision: str,
    concept_id: Optional[str] = None,
    reasoning: str = "",
    confidence: float = 0.0,
) -> MatchingWorkflowState:
    """Mark the matching workflow as complete with a decision."""
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    start_ms = state.get("start_time_ms", now_ms)

    return {
        **state,
        "status": "completed",
        "final_decision": decision,
        "final_concept_id": concept_id,
        "final_confidence": confidence,
        "decision_reasoning": reasoning,
        "end_time_ms": now_ms,
        "total_duration_ms": now_ms - start_ms,
        "updated_at": now.isoformat(),
    }


def escalate_to_human(
    state: MatchingWorkflowState,
    reason: EscalationReason,
    suggested_concepts: list[SuggestedConcept],
) -> MatchingWorkflowState:
    """Escalate the matching workflow to human review queue."""
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    start_ms = state.get("start_time_ms", now_ms)

    return {
        **state,
        "status": "escalated",
        "escalated": True,
        "escalation_reason": reason.value,
        "suggested_concepts": [c.model_dump(mode="json") for c in suggested_concepts],
        "final_decision": "escalated",
        "end_time_ms": now_ms,
        "total_duration_ms": now_ms - start_ms,
        "updated_at": now.isoformat(),
    }
