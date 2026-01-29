"""
LangGraph workflow definition.

Defines the research workflow as a StateGraph with agent nodes
and human-in-the-loop checkpoint interrupts.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agentic_kg.agents.continuation import ContinuationAgent
from agentic_kg.agents.evaluation import EvaluationAgent
from agentic_kg.agents.ranking import RankingAgent
from agentic_kg.agents.schemas import WorkflowStatus
from agentic_kg.agents.state import ResearchState
from agentic_kg.agents.synthesis import SynthesisAgent

logger = logging.getLogger(__name__)


def _should_continue_after_select(state: ResearchState) -> str:
    """Route after problem selection checkpoint."""
    if state.get("selected_problem_id"):
        return "continuation"
    return "end"


def _should_continue_after_approve(state: ResearchState) -> str:
    """Route after proposal approval checkpoint."""
    if state.get("proposal_approved"):
        return "evaluation"
    return "end"


def _should_continue_after_review(state: ResearchState) -> str:
    """Route after evaluation review checkpoint."""
    if state.get("evaluation_approved"):
        return "synthesis"
    return "end"


def build_workflow(
    ranking_agent: RankingAgent,
    continuation_agent: ContinuationAgent,
    evaluation_agent: EvaluationAgent,
    synthesis_agent: SynthesisAgent,
    checkpointer: Any | None = None,
) -> Any:
    """
    Build and compile the research workflow graph.

    Graph structure:
        ranking → [HITL: select] → continuation → [HITL: approve]
        → evaluation → [HITL: review] → synthesis → END

    Args:
        ranking_agent: Ranking agent instance.
        continuation_agent: Continuation agent instance.
        evaluation_agent: Evaluation agent instance.
        synthesis_agent: Synthesis agent instance.
        checkpointer: LangGraph checkpointer for state persistence.
            Defaults to MemorySaver for development.

    Returns:
        Compiled LangGraph workflow.
    """
    graph = StateGraph(ResearchState)

    # --- Agent nodes ---
    async def rank_node(state: ResearchState) -> ResearchState:
        return await ranking_agent.run(state)

    async def continuation_node(state: ResearchState) -> ResearchState:
        return await continuation_agent.run(state)

    async def evaluation_node(state: ResearchState) -> ResearchState:
        return await evaluation_agent.run(state)

    async def synthesis_node(state: ResearchState) -> ResearchState:
        result = await synthesis_agent.run(state)
        return {**result, "status": WorkflowStatus.COMPLETED.value}

    # --- Checkpoint (passthrough) nodes ---
    def select_problem_node(state: ResearchState) -> ResearchState:
        """HITL: user selects a problem from ranked list."""
        return {**state, "current_step": "select_problem"}

    def approve_proposal_node(state: ResearchState) -> ResearchState:
        """HITL: user approves/rejects the continuation proposal."""
        return {**state, "current_step": "approve_proposal"}

    def review_evaluation_node(state: ResearchState) -> ResearchState:
        """HITL: user reviews evaluation results."""
        return {**state, "current_step": "review_evaluation"}

    # Add nodes
    graph.add_node("ranking", rank_node)
    graph.add_node("select_problem", select_problem_node)
    graph.add_node("continuation", continuation_node)
    graph.add_node("approve_proposal", approve_proposal_node)
    graph.add_node("evaluation", evaluation_node)
    graph.add_node("review_evaluation", review_evaluation_node)
    graph.add_node("synthesis", synthesis_node)

    # --- Edges ---
    graph.set_entry_point("ranking")
    graph.add_edge("ranking", "select_problem")

    graph.add_conditional_edges(
        "select_problem",
        _should_continue_after_select,
        {"continuation": "continuation", "end": END},
    )

    graph.add_edge("continuation", "approve_proposal")

    graph.add_conditional_edges(
        "approve_proposal",
        _should_continue_after_approve,
        {"evaluation": "evaluation", "end": END},
    )

    graph.add_edge("evaluation", "review_evaluation")

    graph.add_conditional_edges(
        "review_evaluation",
        _should_continue_after_review,
        {"synthesis": "synthesis", "end": END},
    )

    graph.add_edge("synthesis", END)

    # Compile with interrupts at HITL checkpoint nodes
    if checkpointer is None:
        checkpointer = MemorySaver()

    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["select_problem", "approve_proposal", "review_evaluation"],
    )

    return compiled
