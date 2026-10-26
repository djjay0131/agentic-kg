"""The scheduled research-progression cycle.

This is the closed loop from the proposal (§IV-E, §V-G5), one scheduling tick at
a time::

    close finished workflows -> re-rank -> select top-k -> start workflows
    -> each workflow runs to its next human gate -> record the cycle

Gates are policy, not a hard-coded prompt: :mod:`agentic_kg.agents.autonomy`
loads a versioned file and this PR ships every gate as ``human``. A human
decision (the Research page / ``POST /api/research/gates/...``) resumes the
workflow through the durable checkpoint store, so a gate can wait for days and
be decided by a different process.

Budgets are honest stops, not silent truncation. A cycle records
``stopped_by_budget`` when a per-cycle USD cap, experiment cap or wall-time cap
prevents it from starting or resuming a workflow. The cycle itself is written as
the same ``PipelineRun`` shape as the nightly job (``trigger="research_cycle"``)
so the Runs page and the log-based alert events cover it without a second
report format.

Kill switch
-----------
``RESEARCH_CYCLE_ENABLED`` (Terraform ``research_cycle_enabled``) defaults to
**false**. The owner turns it on. When it is false ``main`` logs
``research_cycle_disabled`` and exits 0 without touching the graph.

One-at-a-time
-------------
A new cycle never starts while another cycle is ``running``. A cycle that is
waiting at a human gate is ``awaiting_gate``, **not** ``running``: otherwise a
single pending gate would block the loop forever. A stale ``running`` cycle
older than the wall-time cap is closed as failed before the guard is applied.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from agentic_kg.agents.autonomy import AutonomyPolicy, load_policy
from agentic_kg.agents.schemas import (
    CheckpointDecision,
    CheckpointType,
    WorkflowStatus,
)
from agentic_kg.knowledge_graph.models.pipeline_run import (
    PipelineBudget,
    PipelineFailure,
    PipelineQueryResult,
    PipelineRun,
    PipelineTotals,
)
from agentic_kg.knowledge_graph.models.research_cycle import (
    CycleBudget,
    CycleTrigger,
    CycleWorkflow,
    GateDecision,
    ResearchCycle,
)
from agentic_kg.knowledge_graph.pipeline_runs import save_pipeline_run
from agentic_kg.knowledge_graph.research_cycles import (
    ResearchCycleNotFoundError,
    ResearchCycleRepository,
)
from agentic_kg.pipeline.report import iso, utc_now, write_report_json

logger = logging.getLogger(__name__)

ENV_ENABLED = "RESEARCH_CYCLE_ENABLED"
ENV_TOP_K = "RESEARCH_CYCLE_TOP_K"
ENV_MAX_LLM_USD = "RESEARCH_CYCLE_MAX_LLM_USD"
ENV_MAX_EXPERIMENTS = "RESEARCH_CYCLE_MAX_EXPERIMENTS"
ENV_MAX_WALL_SECONDS = "RESEARCH_CYCLE_MAX_WALL_SECONDS"
ENV_NAMESPACE = "KGCS_CANONICAL_NAMESPACE"
ENV_RUNS_DIR = "NIGHTLY_RUNS_DIR"
ENV_RUNS_BUCKET = "NIGHTLY_RUNS_BUCKET"
ENV_GIT_SHA = "NIGHTLY_GIT_SHA"
ENV_IMAGE = "NIGHTLY_IMAGE"
ENV_EST_USD_PER_WORKFLOW = "RESEARCH_CYCLE_EST_USD_PER_WORKFLOW"

DEFAULT_TOP_K = 3
DEFAULT_MAX_EXPERIMENTS = 10
DEFAULT_MAX_WALL_SECONDS = 3600
DEFAULT_EST_USD_PER_WORKFLOW = 0.05

#: Gate names in the order the workflow reaches them.
GATE_ORDER: tuple[str, ...] = (
    CheckpointType.SELECT_PROBLEM.value,
    CheckpointType.APPROVE_PROPOSAL.value,
    CheckpointType.REVIEW_EVALUATION.value,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_cycle_id(now: datetime) -> str:
    return "cycle-" + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_bool(value: str | bool | None, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class CycleConfig:
    """Everything one cycle run is allowed to do."""

    enabled: bool = False
    top_k: int = DEFAULT_TOP_K
    max_llm_usd: float = 0.0
    max_experiments: int = DEFAULT_MAX_EXPERIMENTS
    max_wall_seconds: int = DEFAULT_MAX_WALL_SECONDS
    est_usd_per_workflow: float = DEFAULT_EST_USD_PER_WORKFLOW
    namespace: str = "canon"
    trigger: CycleTrigger = "schedule"
    git_sha: str = "unknown"
    image: str = "unknown"

    @classmethod
    def from_env(cls, **overrides: Any) -> "CycleConfig":
        values: dict[str, Any] = {
            "enabled": _parse_bool(os.getenv(ENV_ENABLED), False),
            "top_k": int(os.getenv(ENV_TOP_K, str(DEFAULT_TOP_K))),
            "max_llm_usd": float(os.getenv(ENV_MAX_LLM_USD, "0")),
            "max_experiments": int(
                os.getenv(ENV_MAX_EXPERIMENTS, str(DEFAULT_MAX_EXPERIMENTS))
            ),
            "max_wall_seconds": int(
                os.getenv(ENV_MAX_WALL_SECONDS, str(DEFAULT_MAX_WALL_SECONDS))
            ),
            "est_usd_per_workflow": float(
                os.getenv(ENV_EST_USD_PER_WORKFLOW, str(DEFAULT_EST_USD_PER_WORKFLOW))
            ),
            "namespace": os.getenv(ENV_NAMESPACE, "canon"),
            "git_sha": os.getenv(ENV_GIT_SHA, "unknown"),
            "image": os.getenv(ENV_IMAGE, "unknown"),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**values)


class CycleWorkflowRunner(Protocol):
    """The slice of the workflow runner a cycle needs.

    Kept deliberately narrow so the scheduling tests can run offline with a
    fake, and so the production adapter can be the existing ``WorkflowRunner``.
    """

    async def rank_candidates(self, *, max_problems: int = 20) -> list[dict]:
        """Return ranked problems (``RankedProblem`` dicts), best first."""

    async def start_workflow(
        self,
        *,
        candidate_problem_ids: list[str],
        max_problems: int = 20,
    ) -> str:
        """Start a workflow and run it to its first gate; return its run id."""

    async def get_state(self, run_id: str) -> dict:
        """Return the current workflow state."""

    async def resume_workflow(
        self,
        run_id: str,
        *,
        checkpoint_type: CheckpointType,
        decision: CheckpointDecision,
        feedback: str = "",
        edited_data: Optional[dict[str, Any]] = None,
    ) -> dict:
        """Resume a gated workflow after a decision; return its new state."""


class WorkflowRunnerAdapter:
    """Adapt the real ``WorkflowRunner`` to :class:`CycleWorkflowRunner`."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner

    async def rank_candidates(self, *, max_problems: int = 20) -> list[dict]:
        return await self._runner.rank_candidates(max_problems=max_problems)

    async def start_workflow(
        self,
        *,
        candidate_problem_ids: list[str],
        max_problems: int = 20,
    ) -> str:
        return await self._runner.start_workflow(
            candidate_problem_ids=candidate_problem_ids,
            max_problems=max_problems,
        )

    async def get_state(self, run_id: str) -> dict:
        return dict(await self._runner.get_state(run_id))

    async def resume_workflow(
        self,
        run_id: str,
        *,
        checkpoint_type: CheckpointType,
        decision: CheckpointDecision,
        feedback: str = "",
        edited_data: Optional[dict[str, Any]] = None,
    ) -> dict:
        return dict(
            await self._runner.resume_workflow(
                run_id,
                checkpoint_type=checkpoint_type,
                decision=decision,
                feedback=feedback,
                edited_data=edited_data,
            )
        )


class CycleBudgetExceeded(Exception):
    """Raised when a decision would exceed the cycle's budget."""


@dataclass
class CycleResult:
    """The outcome of one :func:`run_cycle` call."""

    report: Optional[PipelineRun]
    cycle: Optional[ResearchCycle]
    exit_code: int
    skipped_reason: Optional[str] = None
    url: Optional[str] = None


def _workflow_status(state: dict) -> str:
    return str(state.get("status") or "")


def derive_pending_gate(
    state: dict,
    decisions: list[GateDecision] | None = None,
) -> tuple[str, Optional[str]]:
    """Map a workflow state to ``(cycle_workflow_state, pending_gate)``.

    The workflow's own ``current_step`` lags the interrupt (the passthrough gate
    node only runs after the interrupt), so this reads the *facts* the agents
    wrote: a ranked list with no selection is the selection gate, a proposal
    with no approval is the proposal gate, and an evaluation with no verdict is
    the evaluation gate. A recorded rejection wins over everything.
    """
    decisions = decisions or []
    rejected = {d.gate for d in decisions if d.decision in ("reject", "auto_reject")}
    status = _workflow_status(state)

    if status in (WorkflowStatus.CANCELLED.value,):
        return "cancelled", None
    if status in (WorkflowStatus.FAILED.value,) or state.get("errors"):
        return "failed", None

    selected_decision = next(
        (d for d in decisions if d.gate == CheckpointType.SELECT_PROBLEM.value), None
    )
    proposal_decision = next(
        (d for d in decisions if d.gate == CheckpointType.APPROVE_PROPOSAL.value), None
    )
    evaluation_decision = next(
        (d for d in decisions if d.gate == CheckpointType.REVIEW_EVALUATION.value), None
    )

    if CheckpointType.SELECT_PROBLEM.value in rejected:
        return "rejected", None
    if not state.get("selected_problem_id"):
        return "awaiting_gate", CheckpointType.SELECT_PROBLEM.value

    if CheckpointType.APPROVE_PROPOSAL.value in rejected:
        return "rejected", None
    if state.get("proposal") and not state.get("proposal_approved"):
        return "awaiting_gate", CheckpointType.APPROVE_PROPOSAL.value
    if not proposal_decision and not state.get("proposal"):
        return "running", None

    if CheckpointType.REVIEW_EVALUATION.value in rejected:
        return "rejected", None
    if state.get("evaluation_result") and not state.get("evaluation_approved"):
        return "awaiting_gate", CheckpointType.REVIEW_EVALUATION.value
    if not evaluation_decision and not state.get("evaluation_result"):
        return "running", None

    if state.get("synthesis_report"):
        return "completed", None
    if selected_decision and proposal_decision and evaluation_decision:
        return "completed", None
    return "running", None


def _sync_entry(entry: CycleWorkflow, state: dict, *, now: str) -> CycleWorkflow:
    """Update a cycle's workflow entry from the workflow's durable state."""
    new_state, pending = derive_pending_gate(state, entry.gate_decisions)
    entry.state = new_state  # type: ignore[assignment]
    entry.current_step = str(state.get("current_step") or "")
    entry.pending_gate = pending  # type: ignore[assignment]
    entry.problem_statement = (
        state.get("selected_problem_statement") or entry.problem_statement
    )
    entry.updated_at = now
    if new_state in ("completed", "rejected", "failed", "cancelled"):
        entry.finished_at = entry.finished_at or now
    if state.get("errors"):
        entry.error = "; ".join(str(error) for error in state["errors"])
    return entry


def _cycle_status(cycle: ResearchCycle) -> str:
    """Derive the cycle's status from its workflows, without losing a decision."""
    if cycle.status in ("failed", "stopped_by_budget", "skipped"):
        return cycle.status
    if not cycle.workflow_runs:
        return "completed"
    states = {item.state for item in cycle.workflow_runs}
    if states <= {"completed"}:
        return "completed"
    if states <= {"completed", "rejected", "failed", "cancelled"}:
        return "partial" if states - {"completed"} else "completed"
    if "awaiting_gate" in states:
        return "awaiting_gate"
    return "running"


def _build_report(
    cycle: ResearchCycle,
    *,
    now: datetime,
    exit_code_hint: int = 0,
) -> PipelineRun:
    """Project the cycle onto the shared ``PipelineRun`` contract."""
    status_map = {
        "running": "running",
        "awaiting_gate": "running",
        "completed": "succeeded",
        "partial": "partial",
        "failed": "failed",
        "stopped_by_budget": "partial",
        "skipped": "succeeded",
    }
    selected = ", ".join(cycle.selected_problems) or "none"
    failures: list[PipelineFailure] = []
    for item in cycle.workflow_runs:
        if item.error:
            failures.append(
                PipelineFailure(step=f"workflow:{item.workflow_run_id}", message=item.error)
            )
    queries = [
        PipelineQueryResult(
            query_id=item.workflow_run_id,
            query=item.problem_statement or item.problem_id or "—",
            topic=f"research_cycle:{item.pending_gate or item.state}",
            limit=1,
            papers_seen=0,
            papers_new=0,
            status=item.state,
            error=item.error,
        )
        for item in cycle.workflow_runs
    ]
    report = PipelineRun(
        run_id=cycle.cycle_id,
        started_at=cycle.started_at,
        finished_at=cycle.finished_at,
        status=status_map.get(cycle.status, "running"),  # type: ignore[arg-type]
        trigger="research_cycle",
        namespace=cycle.namespace,
        queries=queries,
        totals=PipelineTotals(
            committed_operations=sum(
                1 for item in cycle.workflow_runs if item.state == "completed"
            ),
            deferred_candidates=len(cycle.pending_gates()),
            honest_nulls=0,
        ),
        budget=PipelineBudget(
            max_papers=0,
            max_llm_usd=cycle.budget.max_llm_usd,
            est_llm_usd=cycle.budget.est_llm_usd,
            stopped_by_budget=cycle.budget.stopped_by_budget,
        ),
        proposed_queries=[
            {"problem_id": pid, "selected_in_cycle": cycle.cycle_id}
            for pid in cycle.selected_problems
        ],
        failures=failures,
        git_sha=cycle.git_sha,
        image=cycle.image,
    )
    if cycle.note:
        report.failures.append(PipelineFailure(step="cycle", message=cycle.note))
    if not report.queries and cycle.status == "completed":
        report.totals.honest_nulls = 1
        report.failures.append(
            PipelineFailure(
                step="rank",
                message="no candidate problems were available to start a workflow",
            )
        )
    _ = (now, exit_code_hint, selected)
    return report


def _finalize(
    cycle: ResearchCycle,
    *,
    now: datetime,
    repository: Any,
    runs_dir: Optional[str | Path],
    runs_bucket: Optional[str | None],
    write_json: bool,
    write_neo4j: bool,
) -> CycleResult:
    cycle_status = _cycle_status(cycle)
    cycle.status = cycle_status  # type: ignore[assignment]
    terminal = cycle_status not in ("running", "awaiting_gate")
    if terminal and not cycle.finished_at:
        cycle.finished_at = iso(now)
    report = _build_report(cycle, now=now)
    url: Optional[str] = None
    write_errors: list[str] = []

    if write_json and runs_dir:
        try:
            url = write_report_json(
                report,
                runs_dir=runs_dir,
                runs_bucket=runs_bucket,
                kind="research-cycles",
            )
        except Exception as exc:  # noqa: BLE001 - reported, affects exit code
            logger.exception("failed to write the cycle JSON report")
            write_errors.append(f"report_json: {exc}")
    elif write_json and not runs_dir:
        write_errors.append("report_json: no runs_dir configured")

    if write_neo4j:
        try:
            save_pipeline_run(report, repository=repository)
        except Exception as exc:  # noqa: BLE001 - reported, affects exit code
            logger.exception("failed to write the cycle PipelineRun node")
            write_errors.append(f"neo4j: {exc}")

    for message in write_errors:
        report.failures.append(PipelineFailure(step="report", message=message))

    event = (
        "pipeline_run_failed"
        if cycle_status == "failed"
        else "pipeline_run_completed"
    )
    logger.info(
        json.dumps(
            {
                "event": event,
                "trigger": "research_cycle",
                "run_id": cycle.cycle_id,
                "status": report.status,
                "cycle_status": cycle_status,
                "workflows": len(cycle.workflow_runs),
                "pending_gates": len(cycle.pending_gates()),
                "budget": cycle.budget.model_dump(),
                "url": url,
            },
            sort_keys=True,
        )
    )

    if cycle_status == "failed":
        exit_code = 2
    elif cycle_status == "stopped_by_budget" or cycle.budget.stopped_by_budget or write_errors:
        exit_code = 1
    else:
        exit_code = 0
    return CycleResult(
        report=report,
        cycle=cycle,
        exit_code=exit_code,
        url=url,
    )


def _stale(cycle: ResearchCycle, *, now: datetime, max_wall_seconds: int) -> bool:
    if max_wall_seconds <= 0:
        return False
    try:
        started = datetime.fromisoformat(cycle.started_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (now - started).total_seconds() > max_wall_seconds


def gate_confidence(gate: str, state: dict) -> Optional[float]:
    """The confidence the autonomy policy may use for ``gate``.

    These are model self-reports, not calibrated probabilities. The shipped
    policy is ``human`` so no threshold is consulted; ADR-0010 requires
    measured agreement before a gate may use one.
    """
    if gate == CheckpointType.SELECT_PROBLEM.value:
        ranked = state.get("ranked_problems") or []
        if ranked:
            return ranked[0].get("score")
        return None
    if gate == CheckpointType.APPROVE_PROPOSAL.value:
        proposal = state.get("proposal") or {}
        return proposal.get("confidence")
    if gate == CheckpointType.REVIEW_EVALUATION.value:
        evaluation = state.get("evaluation_result") or {}
        return evaluation.get("feasibility_score")
    return None


async def _auto_advance(
    entry: CycleWorkflow,
    state: dict,
    *,
    cycle: ResearchCycle,
    runner: CycleWorkflowRunner,
    policy: AutonomyPolicy,
    now_iso: str,
) -> dict:
    """Resolve gates the policy says may proceed without a human.

    The shipped policy is all ``human``, so this returns immediately. It exists
    so ``auto_if``/``auto`` are a data change, not a code change, once ADR-0010's
    evidence is accepted. Every automatic decision is recorded on the cycle with
    ``decided_by="policy:<mode>"``.
    """
    for _ in range(len(GATE_ORDER)):
        gate = entry.pending_gate
        if gate is None:
            break
        gate_policy = policy.gate(gate)
        if not gate_policy.resolve(gate_confidence(gate, state)):
            break
        if gate == CheckpointType.APPROVE_PROPOSAL.value:
            if cycle.budget.max_experiments and (
                cycle.budget.experiments >= cycle.budget.max_experiments
            ):
                cycle.budget.stopped_by_budget = True
                cycle.note = (
                    "auto-approval refused: max_experiments="
                    f"{cycle.budget.max_experiments} reached"
                )
                break
            cycle.budget.experiments += 1
        entry.gate_decisions.append(
            GateDecision(
                gate=gate,  # type: ignore[arg-type]
                decision="auto_approve",
                decided_by=f"policy:{gate_policy.mode}",
                decided_at=now_iso,
                note=f"resolved by autonomy policy v{policy.version}",
            )
        )
        try:
            state = await runner.resume_workflow(
                entry.workflow_run_id,
                checkpoint_type=CheckpointType(gate),
                decision=CheckpointDecision.APPROVE,
                feedback=f"autonomy policy v{policy.version}",
            )
        except Exception as exc:  # noqa: BLE001 - recorded, not silently swallowed
            entry.error = f"auto-resume failed: {type(exc).__name__}: {exc}"
            entry.state = "failed"
            entry.finished_at = now_iso
            return state
        _sync_entry(entry, state, now=now_iso)
    return state


async def _reconcile(
    cycle: ResearchCycle,
    runner: CycleWorkflowRunner,
    *,
    policy: AutonomyPolicy,
    now_iso: str,
) -> ResearchCycle:
    """Refresh every workflow entry from its durable state."""
    for entry in cycle.workflow_runs:
        try:
            state = await runner.get_state(entry.workflow_run_id)
        except Exception as exc:  # noqa: BLE001 - one broken workflow is not fatal
            entry.error = f"{type(exc).__name__}: {exc}"
            entry.updated_at = now_iso
            continue
        _sync_entry(entry, state, now=now_iso)
        if entry.pending_gate is not None:
            await _auto_advance(
                entry,
                state,
                cycle=cycle,
                runner=runner,
                policy=policy,
                now_iso=now_iso,
            )
    cycle.status = _cycle_status(cycle)  # type: ignore[assignment]
    return cycle


async def run_cycle(
    *,
    config: CycleConfig,
    cycles: ResearchCycleRepository,
    runner: CycleWorkflowRunner,
    policy: Optional[AutonomyPolicy] = None,
    now: Optional[datetime] = None,
    runs_dir: Optional[str | Path] = None,
    runs_bucket: Optional[str] = None,
    repository: Any = None,
    write_json: bool = True,
    write_neo4j: bool = True,
) -> CycleResult:
    """Close finished workflows, re-rank, and start this tick's workflows."""
    started = now or utc_now()
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    stamp = iso(started)
    policy = policy or load_policy()

    if not config.enabled:
        logger.info(
            json.dumps(
                {
                    "event": "research_cycle_disabled",
                    "reason": f"{ENV_ENABLED} is false (Terraform research_cycle_enabled)",
                },
                sort_keys=True,
            )
        )
        return CycleResult(
            report=None,
            cycle=None,
            exit_code=0,
            skipped_reason="disabled",
        )

    # Close stale running cycles before applying the one-at-a-time guard.
    for candidate in cycles.find_running():
        if _stale(candidate, now=started, max_wall_seconds=config.max_wall_seconds):
            candidate.status = "failed"
            candidate.finished_at = stamp
            candidate.note = (
                f"closed as stale: exceeded RESEARCH_CYCLE_MAX_WALL_SECONDS="
                f"{config.max_wall_seconds}"
            )
            candidate.budget.stopped_by_budget = True
            cycles.save(candidate)

    running = cycles.find_running()
    if running:
        logger.info(
            json.dumps(
                {
                    "event": "research_cycle_skipped",
                    "reason": "another cycle is running",
                    "running_cycle_id": running[0].cycle_id,
                },
                sort_keys=True,
            )
        )
        return CycleResult(
            report=None,
            cycle=running[0],
            exit_code=0,
            skipped_reason="another_cycle_running",
        )

    # Reconcile every cycle that is waiting at a gate before re-ranking.
    for waiting in cycles.find_status(("awaiting_gate",)):
        await _reconcile(waiting, runner, policy=policy, now_iso=stamp)
        if _cycle_status(waiting) not in ("running", "awaiting_gate"):
            waiting.finished_at = waiting.finished_at or stamp
        cycles.save(waiting)

    cycle = ResearchCycle(
        cycle_id=new_cycle_id(started),
        namespace=config.namespace,
        started_at=stamp,
        status="running",
        trigger=config.trigger,
        policy_version=policy.version,
        budget=CycleBudget(
            max_llm_usd=config.max_llm_usd,
            max_experiments=config.max_experiments,
            max_wall_seconds=config.max_wall_seconds,
        ),
        git_sha=config.git_sha,
        image=config.image,
    )
    cycles.save(cycle)

    try:
        ranked = await runner.rank_candidates(max_problems=max(config.top_k * 5, 20))
    except Exception as exc:  # noqa: BLE001 - a failed ranking ends the cycle honestly
        logger.exception("research cycle ranking failed")
        cycle.status = "failed"
        cycle.note = f"ranking failed: {type(exc).__name__}: {exc}"
        cycle.finished_at = iso(utc_now())
        return _finalize(
            cycle,
            now=utc_now(),
            repository=repository,
            runs_dir=runs_dir,
            runs_bucket=runs_bucket,
            write_json=write_json,
            write_neo4j=write_neo4j,
        )

    selected = ranked[: config.top_k]
    cycle.selected_problems = [
        str(item.get("problem_id")) for item in selected if item.get("problem_id")
    ]

    if not selected:
        cycle.note = "no ranked candidate problems were available"
        cycle.finished_at = iso(utc_now())
        return _finalize(
            cycle,
            now=utc_now(),
            repository=repository,
            runs_dir=runs_dir,
            runs_bucket=runs_bucket,
            write_json=write_json,
            write_neo4j=write_neo4j,
        )

    for item in selected:
        problem_id = item.get("problem_id")
        if not problem_id:
            continue
        if cycle.budget.max_experiments and (
            len(cycle.workflow_runs) >= cycle.budget.max_experiments
        ):
            cycle.budget.stopped_by_budget = True
            cycle.note = (
                f"stopped before starting {problem_id}: max_experiments="
                f"{cycle.budget.max_experiments}"
            )
            break
        if cycle.budget.max_llm_usd > 0:
            projected = cycle.budget.est_llm_usd + config.est_usd_per_workflow
            if projected > cycle.budget.max_llm_usd:
                cycle.budget.stopped_by_budget = True
                cycle.note = (
                    f"stopped before starting {problem_id}: projected "
                    f"est_llm_usd {projected:.4f} > cap "
                    f"{cycle.budget.max_llm_usd:.4f}"
                )
                break
        if _stale(cycle, now=started, max_wall_seconds=config.max_wall_seconds):
            cycle.budget.stopped_by_budget = True
            cycle.note = (
                "stopped before starting a workflow: wall-time cap "
                f"{config.max_wall_seconds}s exceeded"
            )
            break

        try:
            run_id = await runner.start_workflow(
                candidate_problem_ids=[str(problem_id)],
                max_problems=max(config.top_k * 5, 20),
            )
        except Exception as exc:  # noqa: BLE001 - one workflow failure is recorded
            logger.exception("failed to start workflow for %s", problem_id)
            cycle.workflow_runs.append(
                CycleWorkflow(
                    workflow_run_id=f"failed:{problem_id}:{len(cycle.workflow_runs)}",
                    problem_id=str(problem_id),
                    problem_statement=str(item.get("statement") or ""),
                    state="failed",
                    started_at=stamp,
                    updated_at=stamp,
                    finished_at=stamp,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        cycle.budget.est_llm_usd = round(
            cycle.budget.est_llm_usd + config.est_usd_per_workflow, 6
        )
        entry = CycleWorkflow(
            workflow_run_id=run_id,
            problem_id=str(problem_id),
            problem_statement=str(item.get("statement") or ""),
            state="awaiting_gate",
            started_at=stamp,
            updated_at=stamp,
        )
        cycle.workflow_runs.append(entry)
        try:
            state = await runner.get_state(run_id)
            _sync_entry(entry, state, now=stamp)
            if entry.pending_gate is not None:
                await _auto_advance(
                    entry,
                    state,
                    cycle=cycle,
                    runner=runner,
                    policy=policy,
                    now_iso=stamp,
                )
        except Exception as exc:  # noqa: BLE001 - state read is best effort
            entry.error = f"{type(exc).__name__}: {exc}"

    cycle.status = _cycle_status(cycle)  # type: ignore[assignment]
    cycle.budget.wall_seconds = 0.0
    if cycle.status not in ("running", "awaiting_gate"):
        cycle.finished_at = iso(utc_now())
    cycles.save(cycle)
    return _finalize(
        cycle,
        now=utc_now(),
        repository=repository,
        runs_dir=runs_dir,
        runs_bucket=runs_bucket,
        write_json=write_json,
        write_neo4j=write_neo4j,
    )


class ResearchCycleService:
    """Gate decisions over a cycle, shared by the API and tests.

    A rejection still resumes the workflow so LangGraph closes it (its routing
    function sends a rejected gate to ``END``); it never reaches synthesis. An
    approval is the only path that lets the workflow write.
    """

    def __init__(
        self,
        cycles: ResearchCycleRepository,
        runner: CycleWorkflowRunner,
        *,
        policy: Optional[AutonomyPolicy] = None,
    ) -> None:
        self._cycles = cycles
        self._runner = runner
        self._policy = policy or load_policy()

    def get(self, cycle_id: str) -> ResearchCycle:
        return self._cycles.get(cycle_id)

    def list(self, limit: int = 50) -> list[ResearchCycle]:
        return self._cycles.list(limit=limit)

    def pending_gates(self, limit: int = 50) -> list[ResearchCycle]:
        return self._cycles.list_pending_gates(limit=limit)

    async def decide_gate(
        self,
        cycle_id: str,
        workflow_run_id: str,
        *,
        gate: str,
        decision: str,
        user_id: str,
        note: str = "",
        edited_data: Optional[dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> ResearchCycle:
        if decision not in ("approve", "reject", "edit"):
            raise ValueError(f"invalid gate decision: {decision!r}")
        if gate not in GATE_ORDER:
            raise ValueError(f"unknown gate: {gate!r}")

        cycle = self._cycles.get(cycle_id)
        entry = cycle.workflow(workflow_run_id)
        if entry is None:
            raise ResearchCycleNotFoundError(
                f"workflow {workflow_run_id} is not part of cycle {cycle_id}"
            )
        if entry.pending_gate != gate:
            raise ValueError(
                f"workflow {workflow_run_id} is waiting at "
                f"{entry.pending_gate!r}, not {gate!r}"
            )

        if gate == CheckpointType.APPROVE_PROPOSAL.value and decision in (
            "approve",
            "edit",
        ):
            if cycle.budget.max_experiments and (
                cycle.budget.experiments >= cycle.budget.max_experiments
            ):
                cycle.budget.stopped_by_budget = True
                cycle.note = (
                    "gate decision refused: max_experiments="
                    f"{cycle.budget.max_experiments} reached"
                )
                self._cycles.save(cycle)
                raise CycleBudgetExceeded(cycle.note)
            cycle.budget.experiments += 1

        stamp = iso(now or utc_now())
        record = GateDecision(
            gate=gate,  # type: ignore[arg-type]
            decision=decision,  # type: ignore[arg-type]
            decided_by=user_id,
            decided_at=stamp,
            note=note,
            edited_data=edited_data,
        )
        entry.gate_decisions.append(record)
        entry.updated_at = stamp

        checkpoint_type = CheckpointType(gate)
        checkpoint_decision = {
            "approve": CheckpointDecision.APPROVE,
            "reject": CheckpointDecision.REJECT,
            "edit": CheckpointDecision.EDIT,
        }[decision]
        try:
            state = await self._runner.resume_workflow(
                workflow_run_id,
                checkpoint_type=checkpoint_type,
                decision=checkpoint_decision,
                feedback=note,
                edited_data=edited_data,
            )
            _sync_entry(entry, state, now=stamp)
        except Exception as exc:  # noqa: BLE001 - decision is recorded, resume failed
            entry.error = f"resume failed: {type(exc).__name__}: {exc}"
            entry.state = "failed"
            entry.finished_at = stamp
        cycle.status = _cycle_status(cycle)  # type: ignore[assignment]
        if cycle.status not in ("running", "awaiting_gate"):
            cycle.finished_at = cycle.finished_at or stamp
        self._cycles.save(cycle)
        logger.info(
            json.dumps(
                {
                    "event": "research_gate_decided",
                    "cycle_id": cycle_id,
                    "workflow_run_id": workflow_run_id,
                    "gate": gate,
                    "decision": decision,
                    "decided_by": user_id,
                },
                sort_keys=True,
            )
        )
        return cycle


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic_kg.pipeline.cycle",
        description="Run one research-progression cycle tick.",
    )
    parser.add_argument("--trigger", default=None, choices=["schedule", "manual", "weekly"])
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--max-llm-usd", type=float, default=None)
    parser.add_argument("--max-experiments", type=int, default=None)
    parser.add_argument("--max-wall-seconds", type=int, default=None)
    parser.add_argument("--namespace", default=None)
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--runs-bucket", default=None)
    parser.add_argument("--git-sha", default=None)
    parser.add_argument("--image", default=None)
    parser.add_argument(
        "--enabled",
        action="store_true",
        help="Force-enable for a manual run (the scheduled kill switch still defaults off).",
    )
    parser.add_argument("--no-neo4j", action="store_true", help="Skip the Neo4j write.")
    return parser


async def _run_from_args(args: argparse.Namespace) -> int:
    config = CycleConfig.from_env(
        enabled=True if args.enabled else None,
        top_k=args.top_k,
        max_llm_usd=args.max_llm_usd,
        max_experiments=args.max_experiments,
        max_wall_seconds=args.max_wall_seconds,
        namespace=args.namespace,
        trigger=args.trigger or "schedule",
        git_sha=args.git_sha,
        image=args.image,
    )
    runs_dir = args.runs_dir or os.getenv(ENV_RUNS_DIR)
    runs_bucket = args.runs_bucket or os.getenv(ENV_RUNS_BUCKET)

    if not config.enabled:
        result = await run_cycle(
            config=config,
            cycles=_NullCycleRepository(),
            runner=_NullRunner(),
            runs_dir=runs_dir,
            runs_bucket=runs_bucket,
        )
        return result.exit_code

    from agentic_kg.agents.checkpoint_store import create_durable_checkpointer
    from agentic_kg.agents.runner import WorkflowRunner
    from agentic_kg.extraction.llm_client import create_llm_client
    from agentic_kg.knowledge_graph.relations import get_relation_service
    from agentic_kg.knowledge_graph.repository import get_repository
    from agentic_kg.knowledge_graph.search import get_search_service

    repository = get_repository()
    runner = WorkflowRunner(
        llm_client=create_llm_client(),
        repository=repository,
        search_service=get_search_service(),
        relation_service=get_relation_service(),
        checkpointer=create_durable_checkpointer(
            repository, namespace=config.namespace
        ),
    )
    cycles = ResearchCycleRepository(repository, namespace=config.namespace)
    result = await run_cycle(
        config=config,
        cycles=cycles,
        runner=WorkflowRunnerAdapter(runner),
        runs_dir=runs_dir,
        runs_bucket=runs_bucket,
        repository=repository,
        write_json=bool(runs_dir),
        write_neo4j=not args.no_neo4j,
    )
    payload = result.report.model_dump(mode="json") if result.report else {}
    print(json.dumps(payload, sort_keys=True))
    return result.exit_code


class _NullRunner:
    async def rank_candidates(self, *, max_problems: int = 20) -> list[dict]:
        return []

    async def start_workflow(
        self, *, candidate_problem_ids: list[str], max_problems: int = 20
    ) -> str:
        raise RuntimeError("disabled")

    async def get_state(self, run_id: str) -> dict:
        raise KeyError(run_id)

    async def resume_workflow(self, run_id: str, **kwargs: Any) -> dict:
        raise KeyError(run_id)


class _NullCycleRepository:
    def find_running(self) -> list[ResearchCycle]:
        return []

    def find_status(self, statuses: tuple[str, ...]) -> list[ResearchCycle]:
        return []

    def save(self, cycle: ResearchCycle) -> ResearchCycle:
        return cycle


def main(argv: Optional[list[str]] = None) -> int:
    import asyncio

    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    return asyncio.run(_run_from_args(args))


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "CycleBudgetExceeded",
    "CycleConfig",
    "CycleResult",
    "CycleWorkflowRunner",
    "DEFAULT_MAX_EXPERIMENTS",
    "DEFAULT_MAX_WALL_SECONDS",
    "DEFAULT_TOP_K",
    "GATE_ORDER",
    "ResearchCycleService",
    "WorkflowRunnerAdapter",
    "derive_pending_gate",
    "gate_confidence",
    "main",
    "new_cycle_id",
    "run_cycle",
]
