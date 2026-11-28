"""
Workflow runner and session management.

Manages workflow lifecycle: start, resume after checkpoints,
query status, and list active/completed workflows.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from langgraph.checkpoint.memory import MemorySaver

from agentic_kg.agents.checkpoints import CheckpointManager
from agentic_kg.agents.config import AgentConfig, get_agent_config
from agentic_kg.agents.continuation import ContinuationAgent
from agentic_kg.agents.dispatch import (
    DispatchOutcome,
    WorkflowDispatcher,
    is_permission_denied,
)
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
from agentic_kg.agents.state import ResearchState, add_message, create_initial_state
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


#: How long a running execution owns the run before another process may close
#: it. The worker extends this every heartbeat while it is alive, so a lease
#: only expires after a crash, a killed Job execution or a lost instance.
DEFAULT_LEASE_SECONDS = 180.0
DEFAULT_HEARTBEAT_SECONDS = 15.0
#: How long a decision that was handed to a background Job may sit without the
#: worker reporting that it started. Dispatch only proves Cloud Run *accepted*
#: the execution; a rejected entrypoint, a missing secret or a malformed args
#: override leaves the run with no worker at all. This bound turns that into a
#: named failure instead of an eternal gate with a consumed decision.
DEFAULT_DISPATCH_WATCHDOG_SECONDS = 120.0
LEASE_SECONDS_ENV = "WORKFLOW_LEASE_SECONDS"
HEARTBEAT_SECONDS_ENV = "WORKFLOW_HEARTBEAT_SECONDS"
DISPATCH_WATCHDOG_SECONDS_ENV = "WORKFLOW_DISPATCH_WATCHDOG_SECONDS"


def _positive_seconds(env_name: str, default: float) -> float:
    raw = os.getenv(env_name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be a number; got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{env_name} must be positive; got {value}")
    return value


def _lease_seconds() -> float:
    return _positive_seconds(LEASE_SECONDS_ENV, DEFAULT_LEASE_SECONDS)


def _heartbeat_seconds() -> float:
    return _positive_seconds(HEARTBEAT_SECONDS_ENV, DEFAULT_HEARTBEAT_SECONDS)


def _dispatch_watchdog_seconds() -> float:
    return _positive_seconds(
        DISPATCH_WATCHDOG_SECONDS_ENV, DEFAULT_DISPATCH_WATCHDOG_SECONDS
    )


def _parse_timestamp(raw: object) -> datetime | None:
    """Parse a persisted ISO timestamp, returning ``None`` for a missing/bad one."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


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
        dispatcher: WorkflowDispatcher | None = None,
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
        self._dispatcher = dispatcher
        self._namespace = os.getenv("KGCS_CANONICAL_NAMESPACE", "canon")

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
            if namespace:
                self._namespace = namespace
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

    def _execution_metadata(
        self,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        """The settings a background Job needs, carried in the run metadata.

        The Job image already has Neo4j, LLM keys and the migration extra, but
        it does not necessarily have the API's graph-source or experiment
        sandbox variables. Persisting them with the run means the Job needs
        only an ``args`` override, never env overrides (and therefore no
        Terraform change to the Job template).
        """
        config = getattr(self, "_config", None)
        source = getattr(self, "_source", "") or (
            getattr(config, "graph_source", "") if config is not None else ""
        )
        sandbox = asdict(config.sandbox) if config is not None else {}
        execution = {
            "graph_source": source,
            "namespace": getattr(self, "_namespace", "canon"),
            "sandbox": sandbox,
            "params": dict(params),
        }
        # ``execution`` is the shape the worker reads; the same keys are kept at
        # the top level because ``advance_workflow`` and older readers use
        # ``params`` there directly. Writing both means an operator inspecting
        # a run node sees the settings the Job will use.
        return {"execution": execution, **execution}

    def create_workflow(
        self,
        topic_filter: str | None = None,
        status_filter: str | None = None,
        max_problems: int = 20,
        min_confidence: float = 0.3,
        candidate_problem_ids: list[str] | None = None,
        tag: str | None = None,
    ) -> str:
        """Create a run and persist its metadata without running any agent.

        The API starts a workflow by creating the row and dispatching the Job;
        the historical in-request path calls :meth:`advance_workflow` right
        after. Keeping creation separate is what lets ``POST /workflows``
        return before ranking makes its paid LLM calls.
        """
        state = create_initial_state(
            topic_filter=topic_filter,
            status_filter=status_filter,
            max_problems=max_problems,
            min_confidence=min_confidence,
            candidate_problem_ids=candidate_problem_ids,
        )
        run_id = state["run_id"]
        params = {
            "topic_filter": topic_filter,
            "status_filter": status_filter,
            "max_problems": max_problems,
            "min_confidence": min_confidence,
            "candidate_problem_ids": list(candidate_problem_ids or []),
        }
        now = state["created_at"]
        self._workflows[run_id] = {
            "run_id": run_id,
            "status": WorkflowStatus.RUNNING.value,
            "current_step": "ranking",
            "created_at": now,
            "updated_at": now,
            "step_started_at": now,
            "total_steps": 7,
            "completed_steps": 0,
            "error": "",
            "tag": str(tag or ""),
            # Dispatch/execution evidence. Empty until a step is handed off.
            "execution_mode": "",
            "dispatch_phase": "",
            "dispatch_result": "",
            "job_execution": "",
            "dispatch_error": "",
            "dispatched_at": "",
            "worker_started_at": "",
            "heartbeat_at": "",
            "lease_until": "",
            **self._execution_metadata(params),
        }
        # Persist before the first agent call so a crash leaves a visible row.
        self._persist_metadata(run_id)
        logger.info("created workflow %s", run_id)
        return run_id

    async def start_workflow(
        self,
        topic_filter: str | None = None,
        status_filter: str | None = None,
        max_problems: int = 20,
        min_confidence: float = 0.3,
        candidate_problem_ids: list[str] | None = None,
        tag: str | None = None,
    ) -> str:
        """
        Start a new research workflow and run it to its first interrupt.

        ``candidate_problem_ids`` narrows the ranking step to a cycle's
        selected candidates, so a cycle can offer one problem per workflow
        instead of starting three identical full rankings.

        This is the historical in-request entry point (the cycle and offline
        callers use it). The API uses :meth:`create_workflow` plus
        :meth:`dispatch` so the request can return immediately.
        """
        run_id = self.create_workflow(
            topic_filter=topic_filter,
            status_filter=status_filter,
            max_problems=max_problems,
            min_confidence=min_confidence,
            candidate_problem_ids=candidate_problem_ids,
            tag=tag,
        )
        await self.advance_workflow(run_id)
        return run_id

    async def start_workflow_async(
        self,
        topic_filter: str | None = None,
        status_filter: str | None = None,
        max_problems: int = 20,
        min_confidence: float = 0.3,
        candidate_problem_ids: list[str] | None = None,
        tag: str | None = None,
    ) -> tuple[str, bool]:
        """Create a run, dispatch it, and return ``(run_id, dispatched)``.

        ``dispatched`` is ``False`` when no background execution was started;
        in that case the run was advanced in-request, exactly as before.
        """
        run_id = self.create_workflow(
            topic_filter=topic_filter,
            status_filter=status_filter,
            max_problems=max_problems,
            min_confidence=min_confidence,
            candidate_problem_ids=candidate_problem_ids,
            tag=tag,
        )
        dispatched = await self.dispatch(run_id, phase="start")
        if not dispatched:
            await self.advance_workflow(run_id)
        return run_id, dispatched

    async def dispatch(self, run_id: str, *, phase: str) -> bool:
        """Try to hand ``run_id`` to a background Job; never raise in ``auto``.

        Every outcome is persisted on the run metadata (and, when a checkpoint
        exists, appended to the activity log) so a run that never advances can
        always name its dispatch result. A dispatched run deliberately does
        **not** get a ``heartbeat_at`` here: only the worker can prove it
        started. That is what lets the watchdog distinguish "accepted but never
        ran" from "running slowly".
        """
        dispatcher = getattr(self, "_dispatcher", None)
        now = datetime.now(timezone.utc)
        if dispatcher is None:
            outcome = DispatchOutcome(
                dispatched=False,
                result="inline",
                error="no workflow Job dispatcher configured",
            )
        else:
            try:
                raw = dispatcher.dispatch(run_id, phase=phase)
            except Exception as exc:  # noqa: BLE001 - fallback is the safety net
                logger.warning(
                    "workflow %s dispatch failed (%s); falling back to in-request "
                    "execution",
                    run_id,
                    exc,
                )
                outcome = DispatchOutcome(
                    dispatched=False,
                    result="refused" if is_permission_denied(exc) else "failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            else:
                # A custom dispatcher may still return a bool (the historical
                # contract); normalise it rather than rejecting it.
                outcome = (
                    raw
                    if isinstance(raw, DispatchOutcome)
                    else DispatchOutcome(dispatched=bool(raw))
                )

        meta = self._merge_metadata(run_id)
        meta["dispatch_phase"] = phase
        meta["dispatch_result"] = outcome.result
        meta["job_execution"] = outcome.execution
        meta["dispatch_error"] = outcome.error
        meta["dispatched_at"] = now.isoformat()
        meta["updated_at"] = now.isoformat()
        if outcome.dispatched:
            meta["execution_mode"] = "job"
            # Dispatch is not a worker heartbeat.
            meta["heartbeat_at"] = ""
            meta["worker_started_at"] = ""
            meta["lease_until"] = (
                now + timedelta(seconds=_lease_seconds())
            ).isoformat()
        else:
            meta["execution_mode"] = "in_request"
            meta["lease_until"] = ""
            meta["worker_started_at"] = ""
        self._persist_metadata(run_id)

        if outcome.dispatched:
            await self._record_message(
                run_id,
                f"Dispatched step to Job {outcome.execution or '<unknown execution>'}",
            )
        else:
            reason = outcome.error or outcome.result
            await self._record_message(
                run_id,
                f"Job dispatch {outcome.result} ({reason}): running in request",
            )
        return outcome.dispatched

    async def _record_message(self, run_id: str, content: str) -> None:
        """Append a dispatcher line to the run's activity log (best effort).

        The metadata already carries the dispatch result; this only makes it
        visible in the same ``messages`` list the agents write to. A run with no
        checkpoint yet (a freshly created start) has nothing to attach to and is
        skipped; its dispatch result still lives in the metadata.
        """
        thread_config = {"configurable": {"thread_id": run_id}}
        try:
            snapshot = await self._graph.aget_state(thread_config)
            state = dict(snapshot.values)
            if not state:
                return
            state = add_message(state, "dispatcher", content)
            await self._graph.aupdate_state(thread_config, state)
        except Exception:  # noqa: BLE001 - a log line must never fail a dispatch
            logger.warning("could not record dispatch message for %s", run_id)

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

    def _merge_metadata(self, run_id: str) -> dict:
        """Merge the in-process and durable copies into one authoritative dict.

        A dispatched run is owned by a **different process** (the Cloud Run
        Job), so the durable copy carries the worker's heartbeat, status and
        current step. Keeping the in-process copy from creation would make the
        API watchdog believe a live worker never started, and would let a later
        decision clobber the worker's progress. For a Job-owned run the store
        therefore wins; otherwise the local process is the writer and its copy
        wins.
        """
        local = self._workflows.get(run_id)
        durable = self._load_metadata(run_id)
        if local is None:
            merged = dict(durable or {"run_id": run_id})
        elif durable is None:
            merged = dict(local)
        else:
            mode = str(local.get("execution_mode") or durable.get("execution_mode") or "")
            if mode == "job":
                merged = {**local, **durable}
            else:
                merged = {**durable, **local}
            merged.setdefault("run_id", run_id)
        self._workflows[run_id] = merged
        return merged

    def _metadata(self, run_id: str) -> dict:
        """The current metadata for a run (see :meth:`_merge_metadata`)."""
        return self._merge_metadata(run_id)

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

    @staticmethod
    def _next_step(checkpoint_type: CheckpointType) -> str:
        """The agent step that runs after a gate decision."""
        return {
            CheckpointType.SELECT_PROBLEM: "continuation",
            CheckpointType.APPROVE_PROPOSAL: "evaluation",
            CheckpointType.REVIEW_EVALUATION: "synthesis",
        }[checkpoint_type]

    async def apply_checkpoint_decision(
        self,
        run_id: str,
        checkpoint_type: CheckpointType,
        decision: CheckpointDecision,
        feedback: str = "",
        edited_data: dict[str, Any] | None = None,
    ) -> ResearchState:
        """Persist a gate decision without running the following agent.

        This is the durable half of a resume: the human decision is recorded in
        the LangGraph checkpoint before the endpoint answers, so the Job (or
        the fallback in-request path) only has to continue the graph.
        """
        self._require_workflow(run_id)
        thread_config = {"configurable": {"thread_id": run_id}}
        current_state = await self._graph.aget_state(thread_config)
        state = dict(current_state.values)
        state = CheckpointManager.apply_decision(
            state,
            checkpoint_type=checkpoint_type,
            decision=decision,
            feedback=feedback,
            edited_data=edited_data,
        )
        await self._graph.aupdate_state(thread_config, state)

        now = datetime.now(timezone.utc).isoformat()
        next_step = self._next_step(checkpoint_type)
        meta = self._merge_metadata(run_id)
        meta["current_step"] = next_step
        meta["step_started_at"] = now
        meta["updated_at"] = now
        # A new decision starts a new step: clear the previous dispatch evidence
        # so a stale "dispatched" never describes this decision.
        meta["execution_mode"] = ""
        meta["dispatch_result"] = ""
        meta["job_execution"] = ""
        meta["dispatch_error"] = ""
        meta["dispatched_at"] = ""
        meta["worker_started_at"] = ""
        meta["heartbeat_at"] = ""
        if decision == CheckpointDecision.REJECT:
            meta["status"] = WorkflowStatus.REJECTED.value
            meta["lease_until"] = ""
        else:
            meta["status"] = WorkflowStatus.RUNNING.value
        self._persist_metadata(run_id)

        presented = dict(state)
        presented["current_step"] = next_step
        presented["status"] = meta["status"]
        presented["updated_at"] = now
        # A rejection still lets the graph run (to close the thread) but the
        # routing function sends it to END; present it as rejected meanwhile.
        if decision == CheckpointDecision.REJECT:
            presented["status"] = WorkflowStatus.REJECTED.value
        return presented

    async def submit_checkpoint(
        self,
        run_id: str,
        checkpoint_type: CheckpointType,
        decision: CheckpointDecision,
        feedback: str = "",
        edited_data: dict[str, Any] | None = None,
    ) -> tuple[ResearchState, bool]:
        """Apply a decision, then dispatch or run the next step.

        Returns ``(state, dispatched)``. When ``dispatched`` is true the
        following agent has not run yet; the caller should return immediately
        and let the worker update the run.
        """
        state = await self.apply_checkpoint_decision(
            run_id,
            checkpoint_type,
            decision,
            feedback,
            edited_data,
        )
        # A rejection needs the graph to run once to close the thread, but it
        # does no paid work; run that in-request even in Job mode.
        dispatched = False
        if decision != CheckpointDecision.REJECT:
            dispatched = await self.dispatch(run_id, phase="resume")
        if not dispatched:
            await self.advance_workflow(run_id)
            return await self.get_state(run_id), False
        return state, True

    async def resume_workflow(
        self,
        run_id: str,
        checkpoint_type: CheckpointType,
        decision: CheckpointDecision,
        feedback: str = "",
        edited_data: dict[str, Any] | None = None,
    ) -> ResearchState:
        """
        Resume a workflow after a human checkpoint decision and wait for it.

        This is the historical in-request entry point (the cycle and offline
        callers use it). The API uses :meth:`submit_checkpoint` instead.
        """
        state, _ = await self.submit_checkpoint(
            run_id,
            checkpoint_type,
            decision,
            feedback,
            edited_data,
        )
        return state

    def _touch_running(self, run_id: str, *, current_step: str | None = None) -> None:
        """Mark a run running and extend its lease now."""
        now = datetime.now(timezone.utc)
        meta = self._merge_metadata(run_id)
        meta["status"] = WorkflowStatus.RUNNING.value
        # The first heartbeat in a process is also the proof that a worker
        # actually started (dispatch itself must not claim that).
        if not meta.get("worker_started_at"):
            meta["worker_started_at"] = now.isoformat()
        if current_step:
            meta["current_step"] = current_step
            meta["step_started_at"] = now.isoformat()
        meta["heartbeat_at"] = now.isoformat()
        meta["lease_until"] = (
            now + timedelta(seconds=_lease_seconds())
        ).isoformat()
        meta["updated_at"] = now.isoformat()
        self._persist_metadata(run_id)

    def record_worker_start(self, run_id: str, *, execution: str = "") -> None:
        """Write the worker-started heartbeat before any agent work.

        A Cloud Run Job calls this as its first action. It is what distinguishes
        "the platform accepted the execution but the entrypoint never loaded the
        run" from "the worker is alive and working"; the watchdog fails the
        former by name after the configured bound.
        """
        self._touch_running(run_id)
        if execution:
            current = self._workflows[run_id]
            current["job_execution"] = str(execution)
            self._persist_metadata(run_id)
        logger.info("workflow %s worker started (%s)", run_id, execution or "in-request")

    def _clear_lease(self, run_id: str) -> None:
        meta = self._workflows.get(run_id)
        if meta is None:
            return
        meta["lease_until"] = ""
        self._persist_metadata(run_id)

    async def _heartbeat_until(self, run_id: str, done: asyncio.Event) -> None:
        """Extend the run's lease while the worker is alive."""
        interval = _heartbeat_seconds()
        while not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.to_thread(self._touch_running, run_id)
            except Exception:  # noqa: BLE001 - a heartbeat is best effort
                logger.warning("could not heartbeat workflow %s", run_id)

    async def advance_workflow(self, run_id: str) -> ResearchState:
        """Continue a run to its next gate or terminal state.

        This is the worker entry point for both a newly created run (start) and
        a decision that has already been applied to the checkpoint (resume).
        It updates the heartbeat/lease while it runs, so another process can
        distinguish a slow execution from a dead one.
        """
        self._require_workflow(run_id)
        thread_config = {"configurable": {"thread_id": run_id}}
        meta = self._merge_metadata(run_id)
        if meta.get("status") in _TERMINAL_STATUSES and not meta.get("params"):
            return await self.get_state(run_id)

        current_step = str(meta.get("current_step") or "ranking")
        self._touch_running(run_id, current_step=current_step)

        snapshot = await self._graph.aget_state(thread_config)
        values = dict(snapshot.values)
        invoke_input: ResearchState | None
        if values:
            invoke_input = None
        else:
            params = dict(meta.get("params") or {})
            state = create_initial_state(
                topic_filter=params.get("topic_filter"),
                status_filter=params.get("status_filter"),
                max_problems=int(params.get("max_problems", 20)),
                min_confidence=float(params.get("min_confidence", 0.3)),
                candidate_problem_ids=params.get("candidate_problem_ids") or None,
            )
            # Keep the id the API returned before the Job started.
            state["run_id"] = run_id
            state["trace_id"] = run_id
            state["created_at"] = str(meta.get("created_at") or state["created_at"])
            state["updated_at"] = state["created_at"]
            state["status"] = WorkflowStatus.RUNNING.value
            state["current_step"] = "ranking"
            invoke_input = state

        done = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat_until(run_id, done))
        failure: Exception | None = None
        try:
            await self._graph.ainvoke(invoke_input, config=thread_config)
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed run
            failure = exc
        finally:
            # Stop heartbeats before writing the final status, or a late beat
            # could overwrite ``waiting_checkpoint``/``failed`` with ``running``.
            done.set()
            try:
                await heartbeat
            except Exception:  # noqa: BLE001 - never let cleanup mask a result
                logger.warning("heartbeat task for %s ended badly", run_id)
        if failure is not None:
            logger.error(
                "workflow %s failed while advancing",
                run_id,
                exc_info=(
                    type(failure),
                    failure,
                    failure.__traceback__,
                ),
            )
            await self._mark_failed(
                run_id,
                thread_config,
                f"{type(failure).__name__}: {failure}",
            )
        else:
            await self._sync_metadata(run_id, thread_config)
        self._clear_lease(run_id)
        return await self.get_state(run_id)

    async def expire_stale_leases(self) -> list[str]:
        """Close runs that can no longer be making progress.

        Two independent bounds are enforced here, both on the read/list path:

        * **A dispatched step whose worker never started.** Dispatch only proves
          Cloud Run accepted the request. If no worker heartbeat arrives within
          ``WORKFLOW_DISPATCH_WATCHDOG_SECONDS`` (default 120 s), the run is
          failed with the dispatch result, Job execution name and error text in
          the reason. This is the case that used to look like an eternal
          ``waiting_checkpoint`` with a consumed decision.
        * **A worker that started and then stopped heartbeating.** A Job
          execution that is killed (platform timeout, OOM, deletion) cannot
          write its own failure, so the lease is the only evidence it is gone.
        """
        await self.expire_stalled_dispatches()
        now = datetime.now(timezone.utc)
        expired: list[str] = []
        run_ids = set(self._workflows)
        for meta in self._list_metadata():
            if meta.get("run_id"):
                run_ids.add(str(meta["run_id"]))
        for run_id in run_ids:
            meta = self._metadata(run_id)
            if meta.get("status") != WorkflowStatus.RUNNING.value:
                continue
            lease = str(meta.get("lease_until") or "")
            if not lease:
                continue
            deadline = _parse_timestamp(lease)
            if deadline is None or deadline > now:
                continue
            await self._mark_failed(
                run_id,
                {"configurable": {"thread_id": run_id}},
                "background execution lease expired: the worker stopped "
                "heartbeating (crashed, killed or out of time)",
            )
            expired.append(run_id)
        if expired:
            logger.warning("closed %d expired workflow run(s): %s", len(expired), expired)
        return expired

    async def expire_stalled_dispatches(self) -> list[str]:
        """Fail runs whose decision was applied but no worker ever started.

        Uses ``worker_started_at`` (written by the Job as its first action) and
        ``heartbeat_at``. Dispatch writes neither, so an accepted-but-dead
        execution is visible instead of being mistaken for a slow worker.
        """
        now = datetime.now(timezone.utc)
        threshold = _dispatch_watchdog_seconds()
        stalled: list[str] = []
        run_ids = set(self._workflows)
        for meta in self._list_metadata():
            if meta.get("run_id"):
                run_ids.add(str(meta["run_id"]))
        for run_id in run_ids:
            meta = self._metadata(run_id)
            if meta.get("status") != WorkflowStatus.RUNNING.value:
                continue
            reference = (
                _parse_timestamp(meta.get("dispatched_at"))
                or _parse_timestamp(meta.get("step_started_at"))
                or _parse_timestamp(meta.get("updated_at"))
            )
            if reference is None:
                continue
            heartbeat = _parse_timestamp(meta.get("heartbeat_at"))
            worker_started = _parse_timestamp(meta.get("worker_started_at"))
            # The most recent proof of life. A worker that started extends the
            # heartbeat; before it starts, the dispatch/decision time is the
            # only clock.
            last_alive = heartbeat or worker_started or reference
            age = (now - last_alive).total_seconds()
            if age <= threshold:
                continue
            execution = str(meta.get("job_execution") or "")
            dispatch_result = str(meta.get("dispatch_result") or "unknown")
            dispatch_error = str(meta.get("dispatch_error") or "").strip()
            if worker_started is None:
                reason = (
                    f"decision was applied but no worker started within "
                    f"{int(threshold)}s (dispatch_result={dispatch_result}"
                    f"{f', job_execution={execution}' if execution else ''}"
                    f"{f', dispatch_error={dispatch_error}' if dispatch_error else ''})"
                )
            else:
                reason = (
                    f"worker stopped heartbeating for more than {int(threshold)}s "
                    f"(last heartbeat {last_alive.isoformat()}, "
                    f"dispatch_result={dispatch_result}"
                    f"{f', job_execution={execution}' if execution else ''})"
                )
            await self._mark_failed(
                run_id,
                {"configurable": {"thread_id": run_id}},
                reason,
            )
            stalled.append(run_id)
        if stalled:
            logger.warning(
                "closed %d stalled workflow run(s): %s", len(stalled), stalled
            )
        return stalled

    async def get_state(self, run_id: str) -> ResearchState:
        """Get the current state of a workflow.

        Raises:
            KeyError: If ``run_id`` is not a tracked workflow.
        """
        self._require_workflow(run_id)
        await self.expire_stale_leases()
        thread_config = {"configurable": {"thread_id": run_id}}
        snapshot = await self._graph.aget_state(thread_config)
        values = dict(snapshot.values)
        meta = self._metadata(run_id)
        if not values:
            # A run that failed before its first checkpoint has metadata only.
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
        state = self._present_state(values, snapshot)
        # Metadata is authoritative while a step is in flight (or terminal).
        # The graph still points at the gate whose decision was just written,
        # so presenting only the checkpoint made a dispatched step look like it
        # was still waiting at the old gate.
        meta_status = str(meta.get("status") or "")
        if meta_status and (
            meta_status == WorkflowStatus.RUNNING.value
            or meta_status in _TERMINAL_STATUSES
        ):
            state["status"] = meta_status
            if meta.get("current_step"):
                state["current_step"] = str(meta["current_step"])
        return state

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

        Durable metadata is the base. This process's in-memory copy overlays it
        for runs this process owns (in-request work), but a run handed to a
        background Job is owned by the Job process: its durable copy carries
        the live heartbeat and must not be masked by this process's stale
        creation-time row. A fresh runner over the same store therefore lists
        runs it never started.
        """
        runs: dict[str, dict] = {}
        for meta in self._list_metadata():
            run_id = meta.get("run_id")
            if run_id:
                runs[str(run_id)] = dict(meta)
        for run_id, local in self._workflows.items():
            durable = runs.get(run_id)
            if durable is None:
                runs[run_id] = dict(local)
                continue
            mode = str(
                durable.get("execution_mode") or local.get("execution_mode") or ""
            )
            runs[run_id] = (
                {**local, **durable} if mode == "job" else {**durable, **local}
            )
        return sorted(
            runs.values(),
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )

    def get_last_error(self, run_id: str) -> str:
        """Return the recorded failure message for ``run_id``, or ``""``."""
        return str(self._metadata(run_id).get("error") or "")

    def get_run_metadata(self, run_id: str) -> dict:
        """Return the persisted list/detail metadata for ``run_id``."""
        return dict(self._metadata(run_id))

    async def cancel_workflow(self, run_id: str) -> None:
        """Cancel a running workflow.

        Raises:
            KeyError: If ``run_id`` is not a tracked workflow.
        """
        self._require_workflow(run_id)
        meta = self._merge_metadata(run_id)
        meta.setdefault(
            "created_at", datetime.now(timezone.utc).isoformat()
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
        meta = self._merge_metadata(run_id)
        meta.setdefault(
            "created_at",
            state.get("created_at")
            or datetime.now(timezone.utc).isoformat(),
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
        meta = self._merge_metadata(run_id)
        meta.setdefault(
            "created_at", datetime.now(timezone.utc).isoformat()
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
            lease_until="",
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
        instead of being shown as running forever. A run whose lease expired
        (its background execution died) is closed first.
        """
        await self.expire_stale_leases()
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
