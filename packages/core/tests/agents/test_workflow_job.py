"""The Cloud Run Job worker entrypoint (``workflow resume <run_id>``)."""

from __future__ import annotations

import pytest
from agentic_kg.agents import workflow_job
from agentic_kg.agents.config import SandboxConfig
from agentic_kg.agents.schemas import WorkflowStatus


class _FakeCheckpointer:
    def __init__(self, metadata: dict | None) -> None:
        self.metadata = metadata

    def load_run(self, run_id: str):
        return self.metadata


class _FakeRunner:
    seen: dict = {}
    state: dict = {}
    worker_starts: list[dict] = []
    cancelled: list[str] = []

    def __init__(self, **kwargs) -> None:
        type(self).seen = kwargs

    def record_worker_start(self, run_id: str, *, execution: str = "") -> None:
        type(self).worker_starts.append({"run_id": run_id, "execution": execution})

    async def advance_workflow(self, run_id: str) -> dict:
        return type(self).state

    async def get_state(self, run_id: str) -> dict:
        return type(self).state

    async def cancel_workflow(self, run_id: str) -> None:
        type(self).cancelled.append(run_id)

    def list_workflows(self) -> list[dict]:
        return []


class _FakeRepo:
    pass


def _patch(monkeypatch, *, metadata: dict | None, state: dict):
    import agentic_kg.agents.checkpoint_store as store
    import agentic_kg.extraction.llm_client as llm
    import agentic_kg.knowledge_graph.relations as relations
    import agentic_kg.knowledge_graph.repository as repository
    import agentic_kg.knowledge_graph.research_cycles as cycles
    import agentic_kg.knowledge_graph.search as search
    import agentic_kg.pipeline.cycle as pipeline_cycle

    monkeypatch.setattr(
        store,
        "create_durable_checkpointer",
        lambda _repo, **_kwargs: _FakeCheckpointer(metadata),
    )
    monkeypatch.setattr(llm, "create_llm_client", lambda: object())
    monkeypatch.setattr(repository, "get_repository", lambda: _FakeRepo())
    monkeypatch.setattr(search, "get_search_service", lambda: object())
    monkeypatch.setattr(relations, "get_relation_service", lambda: object())
    monkeypatch.setattr(cycles, "ResearchCycleRepository", lambda _repo, namespace: object())
    monkeypatch.setattr(pipeline_cycle, "sync_cycle_run", lambda *_a, **_k: [])
    _FakeRunner.state = state
    _FakeRunner.worker_starts = []
    _FakeRunner.cancelled = []
    monkeypatch.setattr(workflow_job, "WorkflowRunner", _FakeRunner)


def test_sandbox_config_from_metadata_applies_known_fields_only():
    config = workflow_job.sandbox_config_from_metadata(
        {
            "backend": "cloudrun_job",
            "gcs_bucket": "bucket",
            "job_name": "agentic-kg-experiment-staging",
            "not_a_field": "ignored",
        }
    )

    assert isinstance(config, SandboxConfig)
    assert config.backend == "cloudrun_job"
    assert config.gcs_bucket == "bucket"
    assert config.job_name == "agentic-kg-experiment-staging"


@pytest.mark.asyncio
async def test_run_rebuilds_the_api_execution_settings(monkeypatch):
    _patch(
        monkeypatch,
        metadata={
            "run_id": "run-1",
            "execution": {
                "graph_source": "legacy",
                "namespace": "canon",
                "sandbox": {
                    "backend": "cloudrun_job",
                    "gcs_bucket": "bucket",
                    "job_name": "agentic-kg-experiment-staging",
                },
            },
        },
        state={"run_id": "run-1", "status": WorkflowStatus.WAITING_CHECKPOINT.value},
    )

    code = await workflow_job.run("resume", "run-1")

    assert code == 0
    config = _FakeRunner.seen["config"]
    assert config.graph_source == "legacy"
    assert config.sandbox.backend == "cloudrun_job"
    assert config.sandbox.gcs_bucket == "bucket"
    assert _FakeRunner.seen["dispatcher"] is None
    # The Job proves a worker exists before touching the graph.
    assert _FakeRunner.worker_starts == [{"run_id": "run-1", "execution": ""}]


@pytest.mark.asyncio
async def test_run_reads_the_top_level_metadata_167_wrote(monkeypatch):
    """#167 persisted execution settings at the top level, not under execution.

    The worker must read that shape too, or it silently falls back to Docker
    sandbox defaults and resumes with the wrong settings.
    """
    _patch(
        monkeypatch,
        metadata={
            "run_id": "run-1",
            "graph_source": "canonical",
            "namespace": "staging",
            "sandbox": {
                "backend": "cloudrun_job",
                "gcs_bucket": "bucket-from-top-level",
            },
            "params": {"max_problems": 7},
        },
        state={"run_id": "run-1", "status": WorkflowStatus.WAITING_CHECKPOINT.value},
    )

    assert await workflow_job.run("resume", "run-1") == 0
    config = _FakeRunner.seen["config"]
    assert config.graph_source == "canonical"
    assert config.sandbox.backend == "cloudrun_job"
    assert config.sandbox.gcs_bucket == "bucket-from-top-level"


@pytest.mark.asyncio
async def test_run_fails_when_the_run_metadata_is_missing(monkeypatch):
    _patch(monkeypatch, metadata=None, state={})

    assert await workflow_job.run("resume", "gone") == 2


@pytest.mark.asyncio
async def test_run_exits_non_zero_when_the_workflow_failed(monkeypatch):
    _patch(
        monkeypatch,
        metadata={"run_id": "run-1", "execution": {}},
        state={"run_id": "run-1", "status": WorkflowStatus.FAILED.value},
    )

    assert await workflow_job.run("resume", "run-1") == 1


@pytest.mark.asyncio
async def test_selfcheck_reaches_select_problem_and_cancels(monkeypatch):
    _patch(
        monkeypatch,
        metadata={"run_id": "selfcheck-run", "execution": {}},
        state={
            "run_id": "selfcheck-run",
            "status": WorkflowStatus.WAITING_CHECKPOINT.value,
            "current_step": "select_problem",
            "ranked_problems": [{"problem_id": "p0"}],
        },
    )
    created: list[dict] = []

    def _create_workflow(self, **kwargs):
        created.append(kwargs)
        return "selfcheck-run"

    monkeypatch.setattr(_FakeRunner, "create_workflow", _create_workflow, raising=False)

    assert await workflow_job.run_selfcheck() == 0
    assert created and created[0]["tag"] == workflow_job.SELFCHECK_TAG
    assert _FakeRunner.cancelled == ["selfcheck-run"]


@pytest.mark.asyncio
async def test_selfcheck_fails_when_it_does_not_reach_the_gate(monkeypatch):
    _patch(
        monkeypatch,
        metadata={"run_id": "selfcheck-run", "execution": {}},
        state={
            "run_id": "selfcheck-run",
            "status": WorkflowStatus.FAILED.value,
            "current_step": "ranking",
            "ranked_problems": [],
        },
    )
    monkeypatch.setattr(
        _FakeRunner, "create_workflow", lambda self, **kwargs: "selfcheck-run", raising=False
    )

    assert await workflow_job.run_selfcheck() == 1
    # Cleanup still runs on a failed check.
    assert _FakeRunner.cancelled == ["selfcheck-run"]


def test_main_accepts_the_selfcheck_phase_without_a_run_id():
    import agentic_kg.agents.workflow_job as module

    seen: list[str] = []
    import agentic_kg.agents.workflow_job as workflow_module

    original = workflow_module.run_selfcheck_sync
    try:
        workflow_module.run_selfcheck_sync = lambda: seen.append("selfcheck") or 0
        assert module.main(["selfcheck"]) == 0
        assert seen == ["selfcheck"]
    finally:
        workflow_module.run_selfcheck_sync = original


def test_main_rejects_an_unknown_phase():
    assert workflow_job.main(["bogus", "run-1"]) == 2


def test_job_runner_cli_subprocess_advances_a_real_run(tmp_path, monkeypatch):
    """The real ``python -m agentic_kg.job_runner workflow resume`` entry.

    A mock of ``dispatch()`` cannot catch an entrypoint or argument regression,
    which is exactly the failure that leaves a run waiting forever. This drives
    a durable run through a *separate process* over a file-backed store.
    """
    import asyncio
    import os
    import subprocess
    import sys
    from pathlib import Path

    from agentic_kg.agents.checkpoint_store import DurableCheckpointSaver
    from agentic_kg.agents.config import AgentConfig
    from agentic_kg.agents.graph_port import SOURCE_LEGACY
    from agentic_kg.agents.provenance import InMemoryAgentOutputStore
    from agentic_kg.agents.runner import WorkflowRunner
    from agentic_kg.agents.schemas import (
        CheckpointDecision,
        CheckpointType,
        WorkflowStatus,
    )

    from . import test_workflow_e2e as fakes
    from .file_checkpoint_store import FileBackedCheckpointStore

    monkeypatch.setenv("KGCS_CANONICAL_NAMESPACE", "canon")
    store_path = tmp_path / "checkpoints.json"
    store = FileBackedCheckpointStore(store_path)
    llm, http_client = fakes._build_llm(fakes._FakeOpenAI())

    def _runner() -> WorkflowRunner:
        return WorkflowRunner(
            llm_client=llm,
            repository=fakes._FakeRepo(),
            checkpointer=DurableCheckpointSaver(store),
            config=AgentConfig(graph_source=SOURCE_LEGACY),
            graph_port=fakes._FakeGraphPort(),
            sandbox=fakes._FakeSandbox(),
            output_store=InMemoryAgentOutputStore(),
        )

    runner = _runner()
    try:
        run_id = asyncio.run(runner.start_workflow(max_problems=20))
        state = asyncio.run(runner.get_state(run_id))
        assert state["current_step"] == CheckpointType.SELECT_PROBLEM.value
        asyncio.run(
            runner.apply_checkpoint_decision(
                run_id,
                CheckpointType.SELECT_PROBLEM,
                CheckpointDecision.APPROVE,
                feedback="go",
                edited_data={"problem_id": "p0"},
            )
        )
    finally:
        asyncio.run(http_client.aclose())

    helper = Path(__file__).with_name("workflow_job_subprocess.py")
    env = dict(os.environ)
    env["KGCS_CANONICAL_NAMESPACE"] = "canon"
    proc = subprocess.run(
        [sys.executable, str(helper), str(store_path), run_id],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    store2 = FileBackedCheckpointStore(store_path)
    llm2, http_client2 = fakes._build_llm(fakes._FakeOpenAI())
    runner2 = WorkflowRunner(
        llm_client=llm2,
        repository=fakes._FakeRepo(),
        checkpointer=DurableCheckpointSaver(store2),
        config=AgentConfig(graph_source=SOURCE_LEGACY),
        graph_port=fakes._FakeGraphPort(),
        sandbox=fakes._FakeSandbox(),
        output_store=InMemoryAgentOutputStore(),
    )
    try:
        state = asyncio.run(runner2.get_state(run_id))
        assert state["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
        assert state["current_step"] == CheckpointType.APPROVE_PROPOSAL.value
        assert state["selected_problem_id"] == "p0"
    finally:
        asyncio.run(http_client2.aclose())
