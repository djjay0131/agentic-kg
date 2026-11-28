"""
LangGraph workflow definition.

Defines the research workflow as a StateGraph with agent nodes
and human-in-the-loop checkpoint interrupts.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Literal, get_args, get_type_hints

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agentic_kg.agents.continuation import ContinuationAgent
from agentic_kg.agents.evaluation import EvaluationAgent
from agentic_kg.agents.ranking import RankingAgent
from agentic_kg.agents.schemas import WorkflowStatus
from agentic_kg.agents.state import ResearchState
from agentic_kg.agents.synthesis import SynthesisAgent

logger = logging.getLogger(__name__)


# The router return values are declared once as ``Literal`` types. Each
# conditional edge's path map is validated against its router's declaration at
# graph-build time (``_checked_path_map``), so a map that cannot route one of
# the router's possible returns fails loudly instead of raising ``KeyError`` on
# the first live run that takes that branch. This is the bug that took staging
# down after #164: the ranking edge mapped "select_problem" while its router
# returned "continue".
AgentRoute = Literal["continue", "end"]
SelectRoute = Literal["continuation", "end"]
ApproveRoute = Literal["evaluation", "end"]
ReviewRoute = Literal["synthesis", "end"]

RouteTarget = dict[str, Any]
WorkflowEdge = tuple[str, Callable[[ResearchState], str], RouteTarget]


def _agent_failed(state: ResearchState) -> bool:
    """Whether a node recorded a terminal failure."""
    return state.get("status") == WorkflowStatus.FAILED.value


def _should_continue_after_agent(state: ResearchState) -> AgentRoute:
    """Stop the graph as soon as an agent node fails."""
    return "end" if _agent_failed(state) else "continue"


def _should_continue_after_select(state: ResearchState) -> SelectRoute:
    """Route after problem selection checkpoint."""
    if state.get("selected_problem_id"):
        return "continuation"
    return "end"


def _should_continue_after_approve(state: ResearchState) -> ApproveRoute:
    """Route after proposal approval checkpoint."""
    if state.get("proposal_approved"):
        return "evaluation"
    return "end"


def _should_continue_after_review(state: ResearchState) -> ReviewRoute:
    """Route after evaluation review checkpoint."""
    if state.get("evaluation_approved"):
        return "synthesis"
    return "end"


def _checked_path_map(
    router: Callable[[ResearchState], str],
    targets: RouteTarget,
) -> RouteTarget:
    """Return ``targets`` after checking it covers every declared router return.

    The router's return annotation is the source of truth. A missing key would
    raise ``KeyError`` at run time (the staging failure); an extra key is dead
    code. Both are build-time errors now.
    """
    annotation = get_type_hints(router).get("return")
    declared = set(get_args(annotation)) if annotation is not None else set()
    if not declared:
        raise ValueError(
            f"conditional edge router {router.__name__} must declare its "
            "possible returns as a Literal type"
        )
    mapped = set(targets)
    if declared != mapped:
        raise ValueError(
            f"conditional edge router {router.__name__} declares returns "
            f"{sorted(declared)} but its path map covers {sorted(mapped)}"
        )
    return dict(targets)


def workflow_edges() -> list[WorkflowEdge]:
    """Every conditional edge as ``(source, router, path_map)``.

    ``build_workflow`` installs exactly these edges; tests assert each map
    covers its router's declared returns so a mismatch cannot compile silently.
    """
    return [
        (
            "ranking",
            _should_continue_after_agent,
            {"continue": "select_problem", "end": END},
        ),
        (
            "select_problem",
            _should_continue_after_select,
            {"continuation": "continuation", "end": END},
        ),
        (
            "continuation",
            _should_continue_after_agent,
            {"continue": "approve_proposal", "end": END},
        ),
        (
            "approve_proposal",
            _should_continue_after_approve,
            {"evaluation": "evaluation", "end": END},
        ),
        (
            "evaluation",
            _should_continue_after_agent,
            {"continue": "review_evaluation", "end": END},
        ),
        (
            "review_evaluation",
            _should_continue_after_review,
            {"synthesis": "synthesis", "end": END},
        ),
    ]


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
        if result.get("status") == WorkflowStatus.FAILED.value:
            return result
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
    for source, router, targets in workflow_edges():
        graph.add_conditional_edges(source, router, _checked_path_map(router, targets))

    graph.add_edge("synthesis", END)

    # Compile with interrupts at HITL checkpoint nodes
    if checkpointer is None:
        checkpointer = MemorySaver()

    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["select_problem", "approve_proposal", "review_evaluation"],
    )

    return compiled
