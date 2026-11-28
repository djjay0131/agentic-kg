"""
Workflow runner and session management.

Manages workflow lifecycle: start, resume after checkpoints,
query status, and list active/completed workflows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

from langgraph.checkpoint.memory import MemorySaver

from agentic_kg.agents.checkpoints import CheckpointManager
from agentic_kg.agents.config import AgentConfig, get_agent_config
from agentic_kg.agents.continuation import ContinuationAgent
from agentic_kg.agents.evaluation import EvaluationAgent
from agentic_kg.agents.graph_port import (
    SOURCE_CANONICAL,
    GraphPort,
    build_graph_port,
)
from agentic_kg.agents.provenance import AgentOutputStore, InMemoryAgentOutputStore
from agentic_kg.agents.ranking import RankingAgent
from agentic_kg.agents.sandbox import ExecutionBackend, create_execution_backend
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

#: Nodes the graph interrupts before; the run is waiting for a human at one.
_GATE_STEPS = frozenset(
    {"select_problem", "approve_proposal", "review_evaluation"}
)
#: A run in one of these states is over; a pending gate is not a live wait.
_TERMINAL_STATUSES = frozenset(
    {
        WorkflowStatus.COMPLETED.value,
        WorkflowStatus.FAILED.value,
        WorkflowStatus.CANCELLED.value,
        WorkflowStatus.REJECTED.value,
    }
)


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
        graph_source: str | None = None,
        canonical_reader: Any | None = None,
        canonical_sink: Any | None = None,
        output_store: AgentOutputStore | None = None,
        graph_port: GraphPort | None = None,
        sandbox: ExecutionBackend | None = None,
    ) -> None:
        self._llm = llm_client
        self._repo = repository
        self._search = search_service
        self._relations = relation_service
        self._config = config or get_agent_config()
        self._checkpointer = checkpointer or MemorySaver()
        self._workflows: dict[str, dict] = {}  # run_id -> metadata
        self._source = graph_source or self._config.graph_source
        self._reader = canonical_reader
        self._output_store = output_store or InMemoryAgentOutputStore()
        self._sandbox = sandbox

        # ADR-0010: one port per runner, shared by the agents that read it.
        # A caller may inject the port directly (the compiled-graph e2e does);
        # otherwise it is built from the configured source.
        self._port = graph_port or build_graph_port(
            self._source,
            repository=self._repo,
            search_service=self._search,
            relation_service=self._relations,
            reader=self._reader,
        )

        sink = canonical_sink
        namespace = None
        if self._source == SOURCE_CANONICAL:
            if sink is None:
                from agentic_kg.knowledge_graph.review_items import (
                    ReviewItemRepository,
                )

                sink = ReviewItemRepository(self._repo)
            namespace = getattr(sink, "namespace", None)
            # A canonical run needs the opt-in extra for candidate payloads.
            from agentic_kg.migration.imports import require_kg_contracts

            require_kg_contracts()

        common = dict(
            llm_client=self._llm,
            repository=self._repo,
            search_service=self._search,
            relation_service=self._relations,
            graph_port=self._port,
            output_store=self._output_store,
            source=self._source,
        )

        # Build agents. Each gets its per-call completion budget; the client is
        # shared, so a budget must travel with the call, not the client.
        self._ranking = RankingAgent(
            **common,
            model=self._config.ranking.model,
            max_tokens=self._config.ranking.max_tokens,
        )
        self._continuation = ContinuationAgent(
            **common,
            model=self._config.continuation.model,
            max_tokens=self._config.continuation.max_tokens,
        )
        self._evaluation = EvaluationAgent(
            **common,
            model=self._config.evaluation.model,
            max_tokens=self._config.evaluation.max_tokens,
            sandbox=self._sandbox
            or create_execution_backend(config=self._config.sandbox),
        )
        self._synthesis = SynthesisAgent(
            **common,
            model=self._config.synthesis.model,
            max_tokens=self._config.synthesis.max_tokens,
            canonical_sink=sink,
            namespace=namespace,
        )

        # Compile workflow graph
        self._graph = build_workflow(
            ranking_agent=self._ranking,
            continuation_agent=self._continuation,
            evaluation_agent=self._evaluation,
            synthesis_agent=self._synthesis,
            checkpointer=self._checkpointer,
        )

    async def rank_candidates(
        self,
        topic_filter: str | None = None,
        status_filter: str | None = None,
        max_problems: int = 20,
        min_confidence: float = 0.3,
    ) -> list[dict]:
        """Run only the ranking step, for a cycle deciding what to start.

        This is the same agent and the same candidate query a workflow uses, so
        the cycle and the workflow cannot disagree about what "top-ranked"
        means. It performs no write and opens no workflow thread.
        """
        state = create_initial_state(
            topic_filter=topic_filter,
            status_filter=status_filter,
            max_problems=max_problems,
            min_confidence=min_confidence,
        )
        result = await self._ranking.run(state)
        return list(result.get("ranked_problems", []))

    async def start_workflow(
        self,
        topic_filter: str | None = None,
        status_filter: str | None = None,
        max_problems: int = 20,
        min_confidence: float = 0.3,
        candidate_problem_ids: list[str] | None = None,
    ) -> str:
        """
        Start a new research workflow.

        ``candidate_problem_ids`` narrows the ranking step to a cycle's
        selected candidates, so a cycle can offer one problem per workflow
        instead of starting three identical full rankings.

        Returns:
            run_id for tracking the workflow session.
        """
        state = create_initial_state(
            topic_filter=topic_filter,
            status_filter=status_filter,
            max_problems=max_problems,
            min_confidence=min_confidence,
            candidate_problem_ids=candidate_problem_ids,
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
            "total_steps": 7,
            "completed_steps": 0,
            "error": "",
        }
        # Persist before the first agent call so a crash leaves a visible row.
        self._persist_metadata(run_id)

        # Run until first interrupt. An agent node records its own failure in
        # the state; an unexpected graph error is recorded here so the run can
        # never stay ``running`` after it stopped.
        try:
            await self._graph.ainvoke(state, config=thread_config)
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed run
            logger.exception("workflow %s failed", run_id)
            await self._mark_failed(
                run_id,
                thread_config,
                f"{type(exc).__name__}: {exc}",
            )
        else:
            await self._sync_metadata(run_id, thread_config)

        return run_id

    # -- run metadata persistence ------------------------------------------
    #
    # The list/detail metadata used to live only in ``self._workflows``, so a
    # restart (or a second Cloud Run instance) listed no workflows even though
    # the durable checkpoints were still there. It is now written beside the
    # checkpoints through the same store, which is what lets any instance list
    # and resume a run another instance started.

    def _run_store(self) -> Any | None:
        """The durable saver if it can persist run metadata, else ``None``."""
        saver = getattr(self, "_checkpointer", None)
        if callable(getattr(saver, "save_run", None)) and callable(
            getattr(saver, "list_runs", None)
        ):
            return saver
        return None

    def _persist_metadata(self, run_id: str) -> None:
        store = self._run_store()
        meta = self._workflows.get(run_id)
        if store is None or meta is None:
            return
        try:
            store.save_run(run_id, dict(meta))
        except Exception:  # noqa: BLE001 - metadata write must not break a run
            logger.warning("could not persist metadata for %s", run_id)

    def _load_metadata(self, run_id: str) -> dict | None:
        store = self._run_store()
        if store is None:
            return None
        try:
            return store.load_run(run_id)
        except Exception:  # noqa: BLE001 - a store read is best effort
            logger.warning("could not load metadata for %s", run_id)
            return None

    def _list_metadata(self) -> list[dict]:
        store = self._run_store()
        if store is None:
            return []
        try:
            return list(store.list_runs())
        except Exception:  # noqa: BLE001 - a store listing is best effort
            logger.warning("could not list durable run metadata")
            return []

    def _metadata(self, run_id: str) -> dict:
        """In-process metadata first, then the durable copy."""
        return self._workflows.get(run_id) or self._load_metadata(run_id) or {}

    @staticmethod
    def _present_state(values: Any, snapshot: Any) -> ResearchState:
        """Present the pending gate as the current step.

        LangGraph interrupts *before* a passthrough gate node, so the raw
        checkpoint still names the agent that just ran. A reader asking "where
        is this run?" needs the gate it is waiting at, and ``waiting_checkpoint``
        rather than the agent's ``running``. The raw checkpoint is untouched;
        the next agent resumes from it normally.
        """
        state = dict(values)
        next_nodes = tuple(getattr(snapshot, "next", ()) or ())
        if (
            next_nodes
            and next_nodes[0] in _GATE_STEPS
            and state.get("status") not in _TERMINAL_STATUSES
        ):
            state["current_step"] = next_nodes[0]
            state["status"] = WorkflowStatus.WAITING_CHECKPOINT.value
        return state

    def has_workflow(self, run_id: str) -> bool:
        """Return whether ``run_id`` names a known workflow.

        A workflow is known if this process started it, **or** if the
        checkpointer has a durable checkpoint for it. The second case is what
        lets a different process (the API after a restart, or a scheduled
        cycle) find and resume a workflow that stopped at a gate days ago.

        ``MemorySaver.aget_state`` returns an empty snapshot for an unknown
        thread instead of raising, so callers that need to distinguish "not
        found" from "present" must consult the tracked set or the checkpoint
        store explicitly.
        """
        if run_id in self._workflows:
            return True
        checker = getattr(getattr(self, "_checkpointer", None), "has_thread", None)
        if checker is not None:
            try:
                if checker(run_id):
                    return True
            except Exception:  # noqa: BLE001 - a store failure must not look like "found"
                logger.warning("could not check durable checkpoint for %s", run_id)
        # A run that failed before its first checkpoint has metadata only.
        return self._load_metadata(run_id) is not None

    def _require_workflow(self, run_id: str) -> None:
        """Raise ``KeyError`` if ``run_id`` is not a tracked workflow.

        The API maps ``KeyError`` to HTTP 404; anything else is a genuine
        500. Without this guard an unknown id resolves to an empty state and
        the API answered 200 (issue #76).
        """
        if not self.has_workflow(run_id):
            raise KeyError(f"Workflow {run_id} not found")

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

        Raises:
            KeyError: If ``run_id`` is not a tracked workflow.
        """
        self._require_workflow(run_id)
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
        try:
            await self._graph.ainvoke(None, config=thread_config)
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed run
            logger.exception("workflow %s failed while resuming", run_id)
            await self._mark_failed(
                run_id,
                thread_config,
                f"{type(exc).__name__}: {exc}",
            )
        else:
            await self._sync_metadata(run_id, thread_config)

        return await self.get_state(run_id)

    async def get_state(self, run_id: str) -> ResearchState:
        """Get the current state of a workflow.

        Raises:
            KeyError: If ``run_id`` is not a tracked workflow.
        """
        self._require_workflow(run_id)
        thread_config = {"configurable": {"thread_id": run_id}}
        snapshot = await self._graph.aget_state(thread_config)
        values = dict(snapshot.values)
        if not values:
            # A run that failed before its first checkpoint has metadata only.
            meta = self._metadata(run_id)
            if meta:
                error = str(meta.get("error") or "")
                return {
                    "run_id": run_id,
                    "status": meta.get("status", ""),
                    "current_step": meta.get("current_step", ""),
                    "created_at": meta.get("created_at", ""),
                    "updated_at": meta.get("updated_at", ""),
                    "errors": [error] if error else [],
                }
        return self._present_state(values, snapshot)

    async def get_status(self, run_id: str) -> WorkflowSummary:
        """Get a summary of workflow status.

        ``created_at``/``updated_at`` come from the in-process metadata when
        present and from the durable metadata when this process did not start
        the workflow (a restart or a different Cloud Run instance).
        """
        self._require_workflow(run_id)
        meta = self._metadata(run_id)

        state = await self.get_state(run_id)
        # Metadata is the fallback when the graph has no checkpoint (for
        # example a run that failed before its first write).
        status = state.get("status") or meta.get("status") or "pending"
        return WorkflowSummary(
            run_id=run_id,
            status=WorkflowStatus(status),
            current_step=state.get("current_step") or meta.get("current_step", ""),
            created_at=meta.get("created_at") or state.get("created_at"),
            updated_at=state.get("updated_at") or meta.get("updated_at"),
            total_steps=7,
            completed_steps=self._count_completed_steps(state),
        )

    def list_workflows(self) -> list[dict]:
        """List workflows started by any process, newest first.

        Durable metadata is the base; this process's in-memory copy overlays
        it so a run started here is never stale in the listing. A fresh runner
        over the same store therefore lists runs it never started.
        """
        runs: dict[str, dict] = {}
        for meta in self._list_metadata():
            run_id = meta.get("run_id")
            if run_id:
                runs[str(run_id)] = dict(meta)
        runs.update(self._workflows)
        return sorted(
            runs.values(),
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )

    def get_last_error(self, run_id: str) -> str:
        """Return the recorded failure message for ``run_id``, or ``""``."""
        return str(self._metadata(run_id).get("error") or "")

    async def cancel_workflow(self, run_id: str) -> None:
        """Cancel a running workflow.

        Raises:
            KeyError: If ``run_id`` is not a tracked workflow.
        """
        self._require_workflow(run_id)
        meta = self._workflows.setdefault(
            run_id,
            self._load_metadata(run_id)
            or {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat()},
        )
        meta["status"] = WorkflowStatus.CANCELLED.value
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._persist_metadata(run_id)

    async def _sync_metadata(self, run_id: str, thread_config: dict) -> None:
        """Sync and persist metadata from the graph state.

        A run started by another instance has no in-memory entry; it is loaded
        from the durable copy (or created) so the update is not dropped.
        """
        state: dict[str, Any] = {}
        try:
            snapshot = await self._graph.aget_state(thread_config)
            state = self._present_state(snapshot.values, snapshot)
        except Exception:  # noqa: BLE001 - metadata must not break a run
            logger.warning("could not read checkpoint state for %s", run_id)
        meta = self._workflows.setdefault(
            run_id,
            self._load_metadata(run_id)
            or {
                "run_id": run_id,
                "created_at": state.get("created_at")
                or datetime.now(timezone.utc).isoformat(),
            },
        )
        if state.get("status"):
            meta["status"] = state["status"]
        if state.get("current_step"):
            meta["current_step"] = state["current_step"]
        errors = state.get("errors") or []
        if errors:
            meta["error"] = str(errors[-1])
        meta["total_steps"] = 7
        meta["completed_steps"] = self._count_completed_steps(state)
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._persist_metadata(run_id)

    async def _mark_failed(
        self,
        run_id: str,
        thread_config: dict,
        message: str,
    ) -> None:
        """Record a terminal failure in both metadata and the checkpoint."""
        meta = self._workflows.setdefault(
            run_id,
            self._load_metadata(run_id)
            or {
                "run_id": run_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        state: dict[str, Any] = {}
        try:
            snapshot = await self._graph.aget_state(thread_config)
            state = dict(snapshot.values)
        except Exception:  # noqa: BLE001 - metadata still carries the failure
            logger.warning("could not read checkpoint state for %s", run_id)

        step = state.get("current_step") or meta.get("current_step") or ""
        errors = [str(error) for error in (state.get("errors") or [])]
        if message not in errors:
            errors.append(message)
        now = datetime.now(timezone.utc).isoformat()
        state.update(
            run_id=state.get("run_id", run_id),
            status=WorkflowStatus.FAILED.value,
            current_step=step,
            errors=errors,
            updated_at=now,
        )
        meta.update(
            status=WorkflowStatus.FAILED.value,
            current_step=step,
            error=message,
            updated_at=now,
        )
        try:
            await self._graph.aupdate_state(thread_config, state)
        except Exception:  # noqa: BLE001 - no checkpoint may exist yet
            logger.warning(
                "could not persist failed state for %s; metadata only", run_id
            )
        self._persist_metadata(run_id)

    async def reconcile_stale_runs(self) -> list[str]:
        """Mark runs left ``running`` by a dead process as failed.

        A run is resumable only while its checkpoint has a next node (a human
        gate, or the node that was about to execute). A ``running`` row with no
        resumable checkpoint cannot make progress, so it is closed as failed
        instead of being shown as running forever.
        """
        run_ids: set[str] = set(self._workflows)
        for meta in self._list_metadata():
            if meta.get("run_id"):
                run_ids.add(str(meta["run_id"]))
        lister = getattr(self._checkpointer, "list_thread_ids", None)
        if lister is not None:
            try:
                run_ids.update(lister())
            except Exception:  # noqa: BLE001 - a store listing is best effort
                logger.warning("could not list durable checkpoint threads")

        stale: list[str] = []
        for run_id in run_ids:
            thread_config = {"configurable": {"thread_id": run_id}}
            try:
                snapshot = await self._graph.aget_state(thread_config)
            except Exception:  # noqa: BLE001 - one bad thread must not stop startup
                logger.warning("could not inspect workflow %s", run_id)
                continue
            state = dict(snapshot.values)
            status = state.get("status") or self._workflows.get(run_id, {}).get(
                "status"
            )
            if status != WorkflowStatus.RUNNING.value:
                continue
            if getattr(snapshot, "next", ()):
                # A gate (or the next node) can still resume this run.
                continue
            message = (
                "stale: the process that started this run exited and no "
                "resumable checkpoint exists"
            )
            await self._mark_failed(run_id, thread_config, message)
            stale.append(run_id)
        if stale:
            logger.warning("closed %d stale workflow run(s): %s", len(stale), stale)
        return stale

    @staticmethod
    def _count_completed_steps(state: Mapping[str, Any]) -> int:
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
