"""Cloud Run Job entrypoint for one asynchronous workflow step.

The API creates a run (or persists a gate decision) and executes the existing
``job`` image with::

    python -m agentic_kg.job_runner workflow resume <run_id>

This module is what the Job runs. It reads the run metadata and LangGraph
checkpoint from Neo4j, reconstructs the execution settings the API persisted
with the run (graph source, sandbox coordinates), advances the graph to its
next gate, and refreshes any research cycle that owns the run. The run's
heartbeat/lease is maintained by ``WorkflowRunner.advance_workflow``; a killed
Job therefore leaves an expired lease that the API reports as ``failed``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import fields
from typing import Optional

from agentic_kg.agents.config import AgentConfig, SandboxConfig
from agentic_kg.agents.runner import WorkflowRunner
from agentic_kg.agents.schemas import WorkflowStatus

logger = logging.getLogger(__name__)

PHASES = ("start", "resume", "advance")
USAGE = "usage: workflow <start|resume|advance> <run_id> | workflow selfcheck"
SELFCHECK_TAG = "deploy-workflow-selfcheck"
SELFCHECK_MAX_PROBLEMS = 5


def sandbox_config_from_metadata(values: dict | None) -> SandboxConfig:
    """Rebuild the API's sandbox settings from run metadata.

    Only fields that exist on ``SandboxConfig`` are applied, so an older run
    (or a future field) cannot crash the worker. Environment defaults fill any
    field the run did not carry.
    """
    config = SandboxConfig()
    known = {field.name for field in fields(SandboxConfig)}
    for key, value in (values or {}).items():
        if key in known:
            setattr(config, key, value)
    return config


def execution_from_metadata(metadata: dict | None) -> dict:
    """The execution settings persisted with a run, in either shipped shape.

    #167 wrote them at the top level of the run metadata; this branch writes
    the same values under ``execution`` as well. Reading both means a run
    created by either version is resumed with the API's graph source and
    sandbox coordinates instead of silently falling back to Docker defaults.
    """
    meta = metadata or {}
    nested = dict(meta.get("execution") or {})
    if nested:
        return nested
    return {
        "graph_source": meta.get("graph_source"),
        "namespace": meta.get("namespace"),
        "sandbox": meta.get("sandbox"),
        "params": meta.get("params"),
    }


def _namespace(execution: dict) -> str:
    return str(
        execution.get("namespace")
        or os.getenv("KGCS_CANONICAL_NAMESPACE", "canon")
    )


def _runner(
    *,
    repository: object,
    checkpointer: object,
    graph_source: str,
    sandbox: SandboxConfig,
) -> WorkflowRunner:
    """Build the worker runner from the same components the API uses."""
    from agentic_kg.extraction.llm_client import create_llm_client
    from agentic_kg.knowledge_graph.relations import get_relation_service
    from agentic_kg.knowledge_graph.search import get_search_service

    config = AgentConfig(graph_source=graph_source, sandbox=sandbox)
    return WorkflowRunner(
        llm_client=create_llm_client(),
        repository=repository,
        search_service=get_search_service(),
        relation_service=get_relation_service(),
        config=config,
        checkpointer=checkpointer,
        graph_source=config.graph_source,
        # A worker must never dispatch another worker.
        dispatcher=None,
    )


async def run(phase: str, run_id: str) -> int:
    """Advance ``run_id`` and refresh its cycle. Returns a process exit code."""
    if phase not in PHASES:
        logger.error("unknown workflow phase %r; %s", phase, USAGE)
        return 2

    from agentic_kg.agents.checkpoint_store import create_durable_checkpointer
    from agentic_kg.knowledge_graph.repository import get_repository
    from agentic_kg.knowledge_graph.research_cycles import ResearchCycleRepository
    from agentic_kg.pipeline.cycle import sync_cycle_run

    repository = get_repository()
    probe = create_durable_checkpointer(
        repository, namespace=os.getenv("KGCS_CANONICAL_NAMESPACE", "canon")
    )
    metadata = probe.load_run(run_id)
    if metadata is None:
        logger.error("workflow run %r has no durable metadata", run_id)
        return 2

    execution = execution_from_metadata(metadata)
    namespace = _namespace(execution)
    # The env namespace and the run's namespace must agree; use the one the run
    # itself records so a Job whose env drifted still finds its own row.
    if namespace != os.getenv("KGCS_CANONICAL_NAMESPACE", "canon"):
        logger.info(
            "worker using the run's recorded namespace %r for %s", namespace, run_id
        )
    checkpointer = create_durable_checkpointer(repository, namespace=namespace)
    metadata = checkpointer.load_run(run_id)
    if metadata is None:
        logger.error(
            "workflow run %r has no durable metadata in namespace %r",
            run_id,
            namespace,
        )
        return 2
    execution = execution_from_metadata(metadata)

    sandbox = sandbox_config_from_metadata(execution.get("sandbox"))
    graph_source = str(
        execution.get("graph_source") or os.getenv("AGENT_GRAPH_SOURCE", "legacy")
    )
    runner = _runner(
        repository=repository,
        checkpointer=checkpointer,
        graph_source=graph_source,
        sandbox=sandbox,
    )

    # First action: prove a worker exists. Dispatch only proves the platform
    # accepted the execution; the API watchdog uses this heartbeat to tell
    # "accepted but never ran" from "running".
    runner.record_worker_start(
        run_id, execution=os.getenv("CLOUD_RUN_EXECUTION", "")
    )

    try:
        state = await runner.advance_workflow(run_id)
    except KeyError:
        logger.error("workflow run %r is not resumable", run_id)
        return 2
    except Exception:  # noqa: BLE001 - advance records its own failure
        logger.exception("workflow run %s failed", run_id)
        state = await runner.get_state(run_id)

    try:
        cycles = ResearchCycleRepository(repository, namespace=namespace)
        updated = sync_cycle_run(run_id, dict(state), cycles)
        if updated:
            logger.info("refreshed cycle(s) %s after %s", updated, run_id)
    except Exception:  # noqa: BLE001 - a cycle refresh must not fail the run
        logger.exception("could not refresh cycle for workflow %s", run_id)

    status = str(state.get("status") or "")
    return 1 if status == WorkflowStatus.FAILED.value else 0


async def run_selfcheck() -> int:
    """Create, advance and cancel a tagged run against the real store.

    Deploy's post-step runs this through the same Job override as ``llm-check``
    so a broken worker path fails the deploy without a browser. It asserts the
    run reaches the first human gate with candidates, then cancels it; a run it
    creates is never left behind.
    """
    from agentic_kg.agents.checkpoint_store import create_durable_checkpointer
    from agentic_kg.knowledge_graph.repository import get_repository

    repository = get_repository()
    namespace = os.getenv("KGCS_CANONICAL_NAMESPACE", "canon")
    checkpointer = create_durable_checkpointer(repository, namespace=namespace)
    graph_source = os.getenv("AGENT_GRAPH_SOURCE", "legacy")
    runner = _runner(
        repository=repository,
        checkpointer=checkpointer,
        graph_source=graph_source,
        sandbox=SandboxConfig(),
    )
    # Sweep this check's own leftovers first (only its tag). A cancelled
    # deploy before the finally block must not accumulate runs at a gate.
    terminal = {
        WorkflowStatus.COMPLETED.value,
        WorkflowStatus.FAILED.value,
        WorkflowStatus.CANCELLED.value,
        WorkflowStatus.REJECTED.value,
    }
    for existing in runner.list_workflows():
        if str(existing.get("tag") or "") != SELFCHECK_TAG:
            continue
        if str(existing.get("status") or "") in terminal:
            continue
        try:
            await runner.cancel_workflow(str(existing["run_id"]))
            logger.info("workflow selfcheck swept leftover %s", existing["run_id"])
        except Exception:  # noqa: BLE001 - a sweep must not fail the check
            logger.warning(
                "workflow selfcheck could not cancel leftover %s",
                existing.get("run_id"),
            )
    run_id = runner.create_workflow(
        max_problems=SELFCHECK_MAX_PROBLEMS, tag=SELFCHECK_TAG
    )
    try:
        state = await runner.advance_workflow(run_id)
        status = str(state.get("status") or "")
        current_step = str(state.get("current_step") or "")
        candidates = list(state.get("ranked_problems") or [])
        if (
            status == WorkflowStatus.WAITING_CHECKPOINT.value
            and current_step == "select_problem"
            and candidates
        ):
            logger.info(
                "workflow selfcheck reached select_problem with %d candidate(s) "
                "on run %s",
                len(candidates),
                run_id,
            )
            return 0
        logger.error(
            "workflow selfcheck did not reach select_problem: run=%s status=%s "
            "step=%s candidates=%d errors=%s",
            run_id,
            status,
            current_step,
            len(candidates),
            state.get("errors") or [],
        )
        return 1
    finally:
        try:
            await runner.cancel_workflow(run_id)
            logger.info("workflow selfcheck cancelled run %s", run_id)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            logger.exception("workflow selfcheck could not cancel run %s", run_id)


def run_sync(phase: str, run_id: str) -> int:
    """Synchronous wrapper for the Job entrypoint and the CLI."""
    return asyncio.run(run(phase, run_id))


def run_selfcheck_sync() -> int:
    return asyncio.run(run_selfcheck())


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    if args == ["selfcheck"]:
        return run_selfcheck_sync()
    if len(args) != 2 or args[0] not in PHASES:
        sys.stderr.write(USAGE + "\n")
        return 2
    return run_sync(args[0], args[1])


__all__ = [
    "PHASES",
    "SELFCHECK_MAX_PROBLEMS",
    "SELFCHECK_TAG",
    "execution_from_metadata",
    "main",
    "run",
    "run_selfcheck",
    "run_selfcheck_sync",
    "run_sync",
    "sandbox_config_from_metadata",
]


if __name__ == "__main__":
    raise SystemExit(main())
