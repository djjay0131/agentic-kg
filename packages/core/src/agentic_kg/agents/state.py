"""
Research workflow state for LangGraph.

Defines the shared state that flows through the agent workflow graph.
Each agent node reads from and writes to this state.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

from agentic_kg.agents.schemas import (
    ContinuationProposal,
    EvaluationResult,
    HumanCheckpoint,
    RankedProblem,
    SynthesisReport,
    WorkflowStatus,
)


class ResearchState(TypedDict, total=False):
    """
    Shared state for the research workflow graph.

    LangGraph passes this state between nodes. Each agent reads its
    inputs and writes its outputs to this dict.
    """

    # --- Workflow metadata ---
    run_id: str
    status: str  # WorkflowStatus value
    current_step: str
    created_at: str  # ISO format
    updated_at: str

    # --- Input / filters ---
    domain_filter: Optional[str]
    status_filter: Optional[str]
    max_problems: int
    min_confidence: float

    # --- Ranking Agent output ---
    ranked_problems: list[dict]  # Serialized RankedProblem dicts
    total_candidates: int

    # --- Human checkpoint: select problem ---
    selected_problem_id: Optional[str]
    selected_problem_statement: Optional[str]

    # --- Continuation Agent output ---
    proposal: Optional[dict]  # Serialized ContinuationProposal
    proposal_approved: bool

    # --- Evaluation Agent output ---
    evaluation_result: Optional[dict]  # Serialized EvaluationResult
    evaluation_approved: bool

    # --- Synthesis Agent output ---
    synthesis_report: Optional[dict]  # Serialized SynthesisReport

    # --- Audit trail ---
    messages: list[dict]  # Log of agent actions
    human_checkpoints: list[dict]  # Serialized HumanCheckpoint records
    errors: list[str]


def create_initial_state(
    domain_filter: Optional[str] = None,
    status_filter: Optional[str] = None,
    max_problems: int = 20,
    min_confidence: float = 0.3,
) -> ResearchState:
    """Create a fresh workflow state with defaults."""
    now = datetime.now(timezone.utc).isoformat()
    return ResearchState(
        run_id=str(uuid.uuid4()),
        status=WorkflowStatus.PENDING.value,
        current_step="",
        created_at=now,
        updated_at=now,
        domain_filter=domain_filter,
        status_filter=status_filter,
        max_problems=max_problems,
        min_confidence=min_confidence,
        ranked_problems=[],
        total_candidates=0,
        selected_problem_id=None,
        selected_problem_statement=None,
        proposal=None,
        proposal_approved=False,
        evaluation_result=None,
        evaluation_approved=False,
        synthesis_report=None,
        messages=[],
        human_checkpoints=[],
        errors=[],
    )


def add_message(state: ResearchState, agent: str, content: str) -> ResearchState:
    """Add an audit message to the state."""
    messages = list(state.get("messages", []))
    messages.append(
        {
            "agent": agent,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    return {**state, "messages": messages, "updated_at": datetime.now(timezone.utc).isoformat()}


def add_checkpoint(state: ResearchState, checkpoint: HumanCheckpoint) -> ResearchState:
    """Record a human checkpoint decision."""
    checkpoints = list(state.get("human_checkpoints", []))
    checkpoints.append(checkpoint.model_dump(mode="json"))
    return {**state, "human_checkpoints": checkpoints}


def add_error(state: ResearchState, error: str) -> ResearchState:
    """Record an error."""
    errors = list(state.get("errors", []))
    errors.append(error)
    return {**state, "errors": errors, "status": WorkflowStatus.FAILED.value}
