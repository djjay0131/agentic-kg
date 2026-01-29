"""
Workflow runner and session management.

Manages workflow lifecycle: start, resume after checkpoints,
query status, and list active/completed workflows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from agentic_kg.agents.checkpoints import CheckpointManager
from agentic_kg.agents.config import AgentConfig, get_agent_config
from agentic_kg.agents.continuation import ContinuationAgent
from agentic_kg.agents.evaluation import EvaluationAgent
from agentic_kg.agents.ranking import RankingAgent
from agentic_kg.agents.sandbox import DockerSandbox
from agentic_kg.agents.schemas import (
    CheckpointDecision,
    CheckpointType,
    WorkflowStatus,
    WorkflowSummary,
)
from agentic_kg.agents.state import ResearchState, create_initial_state
from agentic_kg.agents.synthesis import SynthesisAgent
from agentic_kg.agents.workflow import build_workflow

logger = logging.getLogger(__name__)


class WorkflowRunner:
    """
    Manages research workflow sessions.

    Provides start/resume/status/list operations over compiled
    LangGraph workflows with persistent checkpointing.
    """

    def __init__(
        self,
        llm_client: Any,
        repository: Any,
        search_service: Any | None = None,
        relation_service: Any | None = None,
        config: AgentConfig | None = None,
        checkpointer: Any | None = None,
    ) -> None:
        self._llm = llm_client
        self._repo = repository
        self._search = search_service
        self._relations = relation_service
        self._config = config or get_agent_config()
        self._checkpointer = checkpointer or MemorySaver()
        self._workflows: dict[str, dict] = {}  # run_id -> metadata

        # Build agents
        self._ranking = RankingAgent(
            llm_client=self._llm,
            repository=self._repo,
            search_service=self._search,
            relation_service=self._relations,
        )
        self._continuation = ContinuationAgent(
            llm_client=self._llm,
            repository=self._repo,
            search_service=self._search,
            relation_service=self._relations,
        )
        self._evaluation = EvaluationAgent(
            llm_client=self._llm,
            repository=self._repo,
            sandbox=DockerSandbox(config=self._config.sandbox),
        )
        self._synthesis = SynthesisAgent(
            llm_client=self._llm,
            repository=self._repo,
            search_service=self._search,
            relation_service=self._relations,
        )

        # Compile workflow graph
        self._graph = build_workflow(
            ranking_agent=self._ranking,
            continuation_agent=self._continuation,
            evaluation_agent=self._evaluation,
            synthesis_agent=self._synthesis,
            checkpointer=self._checkpointer,
        )

    async def start_workflow(
        self,
        domain_filter: str | None = None,
        status_filter: str | None = None,
        max_problems: int = 20,
        min_confidence: float = 0.3,
    ) -> str:
        """
        Start a new research workflow.

        Returns:
            run_id for tracking the workflow session.
        """
        state = create_initial_state(
            domain_filter=domain_filter,
            status_filter=status_filter,
            max_problems=max_problems,
            min_confidence=min_confidence,
        )
        run_id = state["run_id"]
        thread_config = {"configurable": {"thread_id": run_id}}

        logger.info(f"Starting workflow {run_id}")
        self._workflows[run_id] = {
            "run_id": run_id,
            "status": WorkflowStatus.RUNNING.value,
            "current_step": "ranking",
            "created_at": state["created_at"],
            "updated_at": state["updated_at"],
        }

        # Run until first interrupt
        await self._graph.ainvoke(state, config=thread_config)
        self._sync_metadata(run_id, thread_config)

        return run_id

    async def resume_workflow(
        self,
        run_id: str,
        checkpoint_type: CheckpointType,
        decision: CheckpointDecision,
        feedback: str = "",
        edited_data: dict[str, Any] | None = None,
    ) -> ResearchState:
        """
        Resume a workflow after a human checkpoint decision.

        Args:
            run_id: Workflow session ID.
            checkpoint_type: Which checkpoint to resolve.
            decision: Human decision (approve/reject/edit).
            feedback: Optional feedback text.
            edited_data: Modified data if decision is edit.

        Returns:
            Updated workflow state.
        """
        thread_config = {"configurable": {"thread_id": run_id}}

        # Get current state from checkpointer
        current_state = await self._graph.aget_state(thread_config)
        state = dict(current_state.values)

        # Apply human decision
        state = CheckpointManager.apply_decision(
            state,
            checkpoint_type=checkpoint_type,
            decision=decision,
            feedback=feedback,
            edited_data=edited_data,
        )

        # Update the state in the graph and resume
        await self._graph.aupdate_state(thread_config, state)
        await self._graph.ainvoke(None, config=thread_config)

        self._sync_metadata(run_id, thread_config)
        return await self.get_state(run_id)

    async def get_state(self, run_id: str) -> ResearchState:
        """Get the current state of a workflow."""
        thread_config = {"configurable": {"thread_id": run_id}}
        snapshot = await self._graph.aget_state(thread_config)
        return dict(snapshot.values)

    async def get_status(self, run_id: str) -> WorkflowSummary:
        """Get a summary of workflow status."""
        meta = self._workflows.get(run_id)
        if not meta:
            raise KeyError(f"Workflow {run_id} not found")

        state = await self.get_state(run_id)
        return WorkflowSummary(
            run_id=run_id,
            status=WorkflowStatus(state.get("status", "pending")),
            current_step=state.get("current_step", ""),
            created_at=meta["created_at"],
            updated_at=state.get("updated_at", meta["updated_at"]),
            total_steps=7,
            completed_steps=self._count_completed_steps(state),
        )

    def list_workflows(self) -> list[dict]:
        """List all tracked workflows."""
        return list(self._workflows.values())

    async def cancel_workflow(self, run_id: str) -> None:
        """Cancel a running workflow."""
        if run_id in self._workflows:
            self._workflows[run_id]["status"] = WorkflowStatus.CANCELLED.value
            self._workflows[run_id]["updated_at"] = datetime.now(
                timezone.utc
            ).isoformat()

    def _sync_metadata(self, run_id: str, thread_config: dict) -> None:
        """Sync in-memory metadata from the graph state."""
        # We can't await here so we just update what we can
        if run_id in self._workflows:
            self._workflows[run_id]["updated_at"] = datetime.now(
                timezone.utc
            ).isoformat()

    @staticmethod
    def _count_completed_steps(state: ResearchState) -> int:
        """Count how many workflow steps have completed."""
        count = 0
        if state.get("ranked_problems"):
            count += 1
        if state.get("selected_problem_id"):
            count += 1
        if state.get("proposal"):
            count += 1
        if state.get("proposal_approved"):
            count += 1
        if state.get("evaluation_result"):
            count += 1
        if state.get("evaluation_approved"):
            count += 1
        if state.get("synthesis_report"):
            count += 1
        return count
