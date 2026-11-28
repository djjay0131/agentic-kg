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
USAGE = "usage: workflow <start|resume|advance> <run_id>"


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


def _namespace(execution: dict) -> str:
    return str(
        execution.get("namespace")
        or os.getenv("KGCS_CANONICAL_NAMESPACE", "canon")
    )


async def run(phase: str, run_id: str) -> int:
    """Advance ``run_id`` and refresh its cycle. Returns a process exit code."""
    if phase not in PHASES:
        logger.error("unknown workflow phase %r; %s", phase, USAGE)
        return 2

    from agentic_kg.agents.checkpoint_store import create_durable_checkpointer
    from agentic_kg.extraction.llm_client import create_llm_client
    from agentic_kg.knowledge_graph.relations import get_relation_service
    from agentic_kg.knowledge_graph.repository import get_repository
    from agentic_kg.knowledge_graph.research_cycles import ResearchCycleRepository
    from agentic_kg.knowledge_graph.search import get_search_service
    from agentic_kg.pipeline.cycle import sync_cycle_run

    repository = get_repository()
    namespace = os.getenv("KGCS_CANONICAL_NAMESPACE", "canon")
    checkpointer = create_durable_checkpointer(repository, namespace=namespace)
    metadata = checkpointer.load_run(run_id)
    if metadata is None:
        logger.error("workflow run %r has no durable metadata", run_id)
        return 2

    execution = dict(metadata.get("execution") or {})
    graph_source = str(execution.get("graph_source") or "")
    sandbox = sandbox_config_from_metadata(execution.get("sandbox"))
    config = AgentConfig(
        graph_source=graph_source or os.getenv("AGENT_GRAPH_SOURCE", "legacy"),
        sandbox=sandbox,
    )
    runner = WorkflowRunner(
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

    try:
        state = await runner.advance_workflow(run_id)
    except KeyError:
        logger.error("workflow run %r is not resumable", run_id)
        return 2
    except Exception:  # noqa: BLE001 - advance records its own failure
        logger.exception("workflow run %s failed", run_id)
        state = await runner.get_state(run_id)

    try:
        cycles = ResearchCycleRepository(
            repository, namespace=_namespace(execution)
        )
        updated = sync_cycle_run(run_id, dict(state), cycles)
        if updated:
            logger.info("refreshed cycle(s) %s after %s", updated, run_id)
    except Exception:  # noqa: BLE001 - a cycle refresh must not fail the run
        logger.exception("could not refresh cycle for workflow %s", run_id)

    status = str(state.get("status") or "")
    return 1 if status == WorkflowStatus.FAILED.value else 0


def run_sync(phase: str, run_id: str) -> int:
    """Synchronous wrapper for the Job entrypoint and the CLI."""
    return asyncio.run(run(phase, run_id))


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in PHASES:
        sys.stderr.write(USAGE + "\n")
        return 2
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    return run_sync(args[0], args[1])


__all__ = [
    "PHASES",
    "main",
    "run",
    "run_sync",
    "sandbox_config_from_metadata",
]


if __name__ == "__main__":
    raise SystemExit(main())
