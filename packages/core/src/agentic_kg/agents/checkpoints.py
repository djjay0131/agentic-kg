"""
Human-in-the-loop checkpoint management.

Handles saving/loading workflow state and injecting human decisions
at interrupt points in the LangGraph workflow.
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_kg.agents.schemas import (
    CheckpointDecision,
    CheckpointType,
    HumanCheckpoint,
)
from agentic_kg.agents.state import ResearchState, add_checkpoint

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manages human checkpoint decisions for workflow interrupts."""

    @staticmethod
    def apply_decision(
        state: ResearchState,
        checkpoint_type: CheckpointType,
        decision: CheckpointDecision,
        feedback: str = "",
        edited_data: dict[str, Any] | None = None,
    ) -> ResearchState:
        """
        Apply a human decision to the workflow state.

        Args:
            state: Current workflow state (at an interrupt point).
            checkpoint_type: Which checkpoint this decision is for.
            decision: approve, reject, or edit.
            feedback: Optional human feedback text.
            edited_data: Modified data if decision is 'edit'.

        Returns:
            Updated state with the decision applied.
        """
        checkpoint = HumanCheckpoint(
            checkpoint_type=checkpoint_type,
            data=_extract_checkpoint_data(state, checkpoint_type),
            decision=decision,
            feedback=feedback,
            edited_data=edited_data,
        )

        state = add_checkpoint(state, checkpoint)

        if checkpoint_type == CheckpointType.SELECT_PROBLEM:
            return _apply_problem_selection(state, decision, edited_data)
        elif checkpoint_type == CheckpointType.APPROVE_PROPOSAL:
            return _apply_proposal_decision(state, decision, edited_data)
        elif checkpoint_type == CheckpointType.REVIEW_EVALUATION:
            return _apply_evaluation_review(state, decision, edited_data)
        else:
            logger.warning(f"Unknown checkpoint type: {checkpoint_type}")
            return state


def _extract_checkpoint_data(
    state: ResearchState, checkpoint_type: CheckpointType
) -> dict[str, Any]:
    """Extract relevant data to record in the checkpoint."""
    if checkpoint_type == CheckpointType.SELECT_PROBLEM:
        return {"ranked_problems": state.get("ranked_problems", [])}
    elif checkpoint_type == CheckpointType.APPROVE_PROPOSAL:
        return {"proposal": state.get("proposal")}
    elif checkpoint_type == CheckpointType.REVIEW_EVALUATION:
        return {"evaluation_result": state.get("evaluation_result")}
    return {}


def _apply_problem_selection(
    state: ResearchState,
    decision: CheckpointDecision,
    edited_data: dict[str, Any] | None,
) -> ResearchState:
    """Apply problem selection decision."""
    if decision == CheckpointDecision.REJECT:
        return {**state, "selected_problem_id": None}

    if edited_data and "problem_id" in edited_data:
        problem_id = edited_data["problem_id"]
        statement = edited_data.get("statement", "")
    else:
        # Default: select the top-ranked problem
        ranked = state.get("ranked_problems", [])
        if ranked:
            problem_id = ranked[0].get("problem_id")
            statement = ranked[0].get("rationale", "")
        else:
            return {**state, "selected_problem_id": None}

    return {
        **state,
        "selected_problem_id": problem_id,
        "selected_problem_statement": statement,
    }


def _apply_proposal_decision(
    state: ResearchState,
    decision: CheckpointDecision,
    edited_data: dict[str, Any] | None,
) -> ResearchState:
    """Apply proposal approval decision."""
    if decision == CheckpointDecision.REJECT:
        return {**state, "proposal_approved": False}

    if decision == CheckpointDecision.EDIT and edited_data:
        # Merge edits into the existing proposal
        proposal = dict(state.get("proposal") or {})
        proposal.update(edited_data)
        return {**state, "proposal": proposal, "proposal_approved": True}

    return {**state, "proposal_approved": True}


def _apply_evaluation_review(
    state: ResearchState,
    decision: CheckpointDecision,
    edited_data: dict[str, Any] | None,
) -> ResearchState:
    """Apply evaluation review decision."""
    if decision == CheckpointDecision.REJECT:
        return {**state, "evaluation_approved": False}

    return {**state, "evaluation_approved": True}
