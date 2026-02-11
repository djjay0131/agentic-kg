"""
LangGraph workflow for problem mention-to-concept matching.

Orchestrates the matching agents:
- MEDIUM confidence → EvaluatorAgent → link/create_new/escalate
- LOW confidence → Maker/Hater/Arbiter consensus → link/create_new/human_review
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Literal, Optional

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from agentic_kg.agents.matching.schemas import (
    ArbiterDecision,
    EscalationReason,
    EvaluatorDecision,
    SuggestedConcept,
)
from agentic_kg.agents.matching.state import (
    MatchingWorkflowState,
    add_matching_message,
    complete_matching_workflow,
    escalate_to_human,
)

if TYPE_CHECKING:
    from agentic_kg.agents.matching.evaluator import EvaluatorAgent
    from agentic_kg.agents.matching.maker import MakerAgent
    from agentic_kg.agents.matching.hater import HaterAgent
    from agentic_kg.agents.matching.arbiter import ArbiterAgent

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

MAX_CONSENSUS_ROUNDS = 3


# =============================================================================
# Node Functions
# =============================================================================


def create_evaluator_node(evaluator: EvaluatorAgent) -> Callable:
    """Create the evaluator node function."""

    async def evaluator_node(state: MatchingWorkflowState) -> dict:
        """Run the EvaluatorAgent for MEDIUM confidence matches."""
        trace_id = state.get("trace_id", "unknown")
        logger.info(f"[Workflow] {trace_id}: Running EvaluatorAgent")

        updated_state = await evaluator.run(state)

        return {
            "evaluator_result": updated_state.get("evaluator_result"),
            "evaluator_decision": updated_state.get("evaluator_decision"),
            "current_step": "evaluator_complete",
            "messages": updated_state.get("messages", []),
        }

    return evaluator_node


def create_maker_node(maker: MakerAgent) -> Callable:
    """Create the maker node function."""

    async def maker_node(state: MatchingWorkflowState) -> dict:
        """Run the MakerAgent to argue FOR linking."""
        trace_id = state.get("trace_id", "unknown")
        round_num = state.get("current_round", 0) + 1
        logger.info(f"[Workflow] {trace_id}: Running MakerAgent (round {round_num})")

        # Increment round at start of consensus
        updated_state = {**state, "current_round": round_num}
        updated_state = await maker.run(updated_state)

        return {
            "maker_results": updated_state.get("maker_results", []),
            "current_round": round_num,
            "current_step": "maker_complete",
            "messages": updated_state.get("messages", []),
        }

    return maker_node


def create_hater_node(hater: HaterAgent) -> Callable:
    """Create the hater node function."""

    async def hater_node(state: MatchingWorkflowState) -> dict:
        """Run the HaterAgent to argue AGAINST linking."""
        trace_id = state.get("trace_id", "unknown")
        round_num = state.get("current_round", 1)
        logger.info(f"[Workflow] {trace_id}: Running HaterAgent (round {round_num})")

        updated_state = await hater.run(state)

        return {
            "hater_results": updated_state.get("hater_results", []),
            "current_step": "hater_complete",
            "messages": updated_state.get("messages", []),
        }

    return hater_node


def create_arbiter_node(arbiter: ArbiterAgent) -> Callable:
    """Create the arbiter node function."""

    async def arbiter_node(state: MatchingWorkflowState) -> dict:
        """Run the ArbiterAgent to make a decision after debate."""
        trace_id = state.get("trace_id", "unknown")
        round_num = state.get("current_round", 1)
        logger.info(f"[Workflow] {trace_id}: Running ArbiterAgent (round {round_num})")

        updated_state = await arbiter.run(state)

        return {
            "arbiter_results": updated_state.get("arbiter_results", []),
            "consensus_reached": updated_state.get("consensus_reached", False),
            "final_confidence": updated_state.get("final_confidence", 0.0),
            "current_step": "arbiter_complete",
            "messages": updated_state.get("messages", []),
        }

    return arbiter_node


def create_link_node() -> Callable:
    """Create the link node function (marks decision as LINK)."""

    async def link_node(state: MatchingWorkflowState) -> dict:
        """Mark the workflow decision as LINK."""
        trace_id = state.get("trace_id", "unknown")
        concept_id = state.get("candidate_concept_id")

        logger.info(f"[Workflow] {trace_id}: Decision=LINK to concept {concept_id}")

        return complete_matching_workflow(
            state,
            decision="linked",
            concept_id=concept_id,
            reasoning="Match approved by agent workflow",
            confidence=state.get("final_confidence", 0.0),
        )

    return link_node


def create_new_node() -> Callable:
    """Create the create_new node function (marks decision as CREATE_NEW)."""

    async def create_new_node(state: MatchingWorkflowState) -> dict:
        """Mark the workflow decision as CREATE_NEW."""
        trace_id = state.get("trace_id", "unknown")

        logger.info(f"[Workflow] {trace_id}: Decision=CREATE_NEW concept")

        return complete_matching_workflow(
            state,
            decision="created_new",
            concept_id=None,
            reasoning="Agent workflow determined distinct problem",
            confidence=state.get("final_confidence", 0.0),
        )

    return create_new_node


def create_human_review_node() -> Callable:
    """Create the human review node function (marks for escalation)."""

    async def human_review_node(state: MatchingWorkflowState) -> dict:
        """Mark the workflow for human review escalation."""
        trace_id = state.get("trace_id", "unknown")
        round_num = state.get("current_round", 0)

        # Determine escalation reason
        if round_num >= MAX_CONSENSUS_ROUNDS:
            reason = EscalationReason.MAX_ROUNDS_EXCEEDED
        elif state.get("evaluator_decision") == "escalate":
            reason = EscalationReason.EVALUATOR_UNCERTAIN
        else:
            reason = EscalationReason.CONSENSUS_FAILED

        logger.info(
            f"[Workflow] {trace_id}: Decision=HUMAN_REVIEW (reason={reason.value})"
        )

        # Build suggested concepts list
        suggested = []
        candidate_id = state.get("candidate_concept_id")
        candidate_statement = state.get("candidate_statement")
        if candidate_id and candidate_statement:
            suggested.append(
                SuggestedConcept(
                    concept_id=candidate_id,
                    canonical_statement=candidate_statement,
                    similarity_score=state.get("similarity_score", 0.0),
                    final_score=state.get("final_score", 0.0),
                    reasoning="Top candidate from ConceptMatcher",
                    domain=state.get("candidate_domain"),
                    mention_count=state.get("candidate_mention_count", 0),
                )
            )

        return escalate_to_human(state, reason, suggested)

    return human_review_node


# =============================================================================
# Routing Functions
# =============================================================================


def route_by_confidence(state: MatchingWorkflowState) -> str:
    """Route based on initial confidence level."""
    confidence = state.get("initial_confidence", "").lower()

    if confidence == "medium":
        return "evaluator"
    elif confidence == "low":
        return "maker"
    else:
        # HIGH or unknown - should not reach workflow
        return "end"


def route_evaluator_decision(state: MatchingWorkflowState) -> str:
    """Route based on EvaluatorAgent decision."""
    evaluator_decision = state.get("evaluator_decision", "escalate")

    if evaluator_decision == "approve":
        return "link"
    elif evaluator_decision == "reject":
        return "create_new"
    else:
        # escalate to Maker/Hater/Arbiter consensus
        return "maker"


def route_arbiter_decision(state: MatchingWorkflowState) -> str:
    """Route based on ArbiterAgent decision and round count."""
    arbiter_results = state.get("arbiter_results", [])
    round_num = state.get("current_round", 0)
    max_rounds = state.get("max_rounds", MAX_CONSENSUS_ROUNDS)

    if not arbiter_results:
        # No arbiter result yet - shouldn't happen
        return "human_review"

    latest_result = arbiter_results[-1]
    decision = latest_result.get("decision", "retry")
    confidence = latest_result.get("confidence", 0.0)

    # Check for final decision
    if decision == "link":
        return "link"
    elif decision == "create_new":
        return "create_new"
    elif decision == "retry":
        if round_num >= max_rounds:
            # Max rounds exceeded - escalate to human
            return "human_review"
        else:
            # Another round of debate
            return "maker"
    else:
        # Unknown decision - escalate
        return "human_review"


# =============================================================================
# Workflow Builder
# =============================================================================


def build_matching_workflow(
    evaluator: EvaluatorAgent,
    maker: MakerAgent,
    hater: HaterAgent,
    arbiter: ArbiterAgent,
    checkpointer: Optional[Any] = None,
) -> StateGraph:
    """
    Build the concept matching workflow graph.

    Args:
        evaluator: EvaluatorAgent for MEDIUM confidence.
        maker: MakerAgent for consensus debate.
        hater: HaterAgent for consensus debate.
        arbiter: ArbiterAgent for consensus decision.
        checkpointer: LangGraph checkpointer (default: MemorySaver).

    Returns:
        Compiled StateGraph ready for invocation.

    Workflow:
        MEDIUM confidence:
            entry → evaluator → approve → link → END
                              → reject → create_new → END
                              → escalate → maker → hater → arbiter → ...

        LOW confidence:
            entry → maker → hater → arbiter → link → END
                                            → create_new → END
                                            → retry → maker (max 3 rounds)
                                            → human_review → END
    """
    workflow = StateGraph(MatchingWorkflowState)

    # Create node functions with injected agents
    evaluator_node = create_evaluator_node(evaluator)
    maker_node = create_maker_node(maker)
    hater_node = create_hater_node(hater)
    arbiter_node = create_arbiter_node(arbiter)
    link_node = create_link_node()
    new_node = create_new_node()
    human_node = create_human_review_node()

    # Add nodes
    workflow.add_node("evaluator", evaluator_node)
    workflow.add_node("maker", maker_node)
    workflow.add_node("hater", hater_node)
    workflow.add_node("arbiter", arbiter_node)
    workflow.add_node("link", link_node)
    workflow.add_node("create_new", new_node)
    workflow.add_node("human_review", human_node)

    # Entry routing based on confidence
    workflow.set_conditional_entry_point(
        route_by_confidence,
        {
            "evaluator": "evaluator",
            "maker": "maker",
            "end": END,
        },
    )

    # Evaluator routing
    workflow.add_conditional_edges(
        "evaluator",
        route_evaluator_decision,
        {
            "link": "link",
            "create_new": "create_new",
            "maker": "maker",
        },
    )

    # Consensus chain: Maker → Hater → Arbiter
    workflow.add_edge("maker", "hater")
    workflow.add_edge("hater", "arbiter")

    # Arbiter routing
    workflow.add_conditional_edges(
        "arbiter",
        route_arbiter_decision,
        {
            "link": "link",
            "create_new": "create_new",
            "maker": "maker",  # retry
            "human_review": "human_review",
        },
    )

    # Terminal nodes
    workflow.add_edge("link", END)
    workflow.add_edge("create_new", END)
    workflow.add_edge("human_review", END)

    # Compile with checkpointing
    compiled = workflow.compile(checkpointer=checkpointer or MemorySaver())

    return compiled


# =============================================================================
# Workflow Singleton
# =============================================================================

_workflow_instance: Optional[StateGraph] = None


def get_matching_workflow(
    evaluator: Optional[EvaluatorAgent] = None,
    maker: Optional[MakerAgent] = None,
    hater: Optional[HaterAgent] = None,
    arbiter: Optional[ArbiterAgent] = None,
    checkpointer: Optional[Any] = None,
) -> StateGraph:
    """
    Get or create the matching workflow singleton.

    First call must provide all agents. Subsequent calls can omit them.
    """
    global _workflow_instance

    if _workflow_instance is None:
        if not all([evaluator, maker, hater, arbiter]):
            raise ValueError(
                "First call to get_matching_workflow must provide all agents"
            )
        _workflow_instance = build_matching_workflow(
            evaluator=evaluator,
            maker=maker,
            hater=hater,
            arbiter=arbiter,
            checkpointer=checkpointer,
        )

    return _workflow_instance


def reset_matching_workflow() -> None:
    """Reset the workflow singleton (for testing)."""
    global _workflow_instance
    _workflow_instance = None


# =============================================================================
# Workflow Invocation Helper
# =============================================================================


async def process_medium_low_confidence(
    state: MatchingWorkflowState,
    workflow: Optional[StateGraph] = None,
) -> MatchingWorkflowState:
    """
    Process a MEDIUM or LOW confidence match through the agent workflow.

    Args:
        state: Initial workflow state with mention and candidate info.
        workflow: Optional workflow instance (uses singleton if not provided).

    Returns:
        Final workflow state with decision.
    """
    trace_id = state.get("trace_id", "unknown")

    if workflow is None:
        workflow = get_matching_workflow()

    logger.info(
        f"[Workflow] {trace_id}: Starting workflow "
        f"(confidence={state.get('initial_confidence')})"
    )

    # Invoke workflow with trace ID as thread ID for checkpointing
    result = await workflow.ainvoke(
        state,
        config={"configurable": {"thread_id": trace_id}},
    )

    logger.info(
        f"[Workflow] {trace_id}: Workflow complete "
        f"(decision={result.get('final_decision')}, "
        f"duration={result.get('total_duration_ms', 0)}ms)"
    )

    return result
