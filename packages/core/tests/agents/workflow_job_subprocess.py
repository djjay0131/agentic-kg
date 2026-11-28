"""Subprocess harness: run the real ``agentic_kg.job_runner`` workflow entry.

Called as::

    python workflow_job_subprocess.py <store_path> <run_id>

It installs the same fakes the compiled-graph e2e uses (fake HTTP transport,
fake graph port, fake sandbox) and a file-backed checkpoint store, then calls
``job_runner.main()`` with ``workflow resume <run_id>``. The point is to
exercise the real command line, ``workflow_job.main`` argument parsing and the
real ``WorkflowRunner`` in a *separate process*, which is the only way a
arg/entrypoint regression (the kind that silently left staging runs stuck)
would be caught by a mock of ``dispatch()``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# ``test_workflow_e2e`` is a normal module once its directory is importable;
# it defines the fakes but does not need pytest to be imported by a test.
sys.path.insert(0, str(_HERE))

import test_workflow_e2e as fakes  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: workflow_job_subprocess <store_path> <run_id>\n")
        return 2
    store_path, run_id = argv

    import agentic_kg.extraction.llm_client as llm_client
    import agentic_kg.knowledge_graph.relations as relations
    import agentic_kg.knowledge_graph.repository as repository
    import agentic_kg.knowledge_graph.research_cycles as cycles
    import agentic_kg.knowledge_graph.search as search
    import agentic_kg.pipeline.cycle as pipeline_cycle
    from agentic_kg.agents import checkpoint_store, workflow_job
    from agentic_kg.agents.checkpoint_store import DurableCheckpointSaver
    from agentic_kg.agents.provenance import InMemoryAgentOutputStore
    from agentic_kg.agents.runner import WorkflowRunner
    from file_checkpoint_store import FileBackedCheckpointStore
    store = FileBackedCheckpointStore(store_path)
    llm, http_client = fakes._build_llm(fakes._FakeOpenAI())

    def _checkpointer(_repo, *, namespace="canon"):
        return DurableCheckpointSaver(store)

    def _runner(**kwargs):
        kwargs["graph_port"] = fakes._FakeGraphPort()
        kwargs["sandbox"] = fakes._FakeSandbox()
        kwargs["output_store"] = InMemoryAgentOutputStore()
        kwargs["dispatcher"] = None
        return WorkflowRunner(**kwargs)

    checkpoint_store.create_durable_checkpointer = _checkpointer
    workflow_job.WorkflowRunner = _runner
    llm_client.create_llm_client = lambda: llm
    repository.get_repository = lambda: fakes._FakeRepo()
    search.get_search_service = lambda: object()
    relations.get_relation_service = lambda: object()
    cycles.ResearchCycleRepository = lambda _repo, namespace: object()
    pipeline_cycle.sync_cycle_run = lambda *_args, **_kwargs: []

    import agentic_kg.job_runner as job_runner

    sys.argv = ["job_runner", "workflow", "resume", run_id]
    try:
        job_runner.main()
    finally:
        try:
            import asyncio

            asyncio.run(http_client.aclose())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
