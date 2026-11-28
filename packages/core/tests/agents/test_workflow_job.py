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

    def __init__(self, **kwargs) -> None:
        type(self).seen = kwargs

    async def advance_workflow(self, run_id: str) -> dict:
        return type(self).state


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

    monkeypatch.setattr(store, "create_durable_checkpointer", lambda _repo, **_kwargs: _FakeCheckpointer(metadata))
    monkeypatch.setattr(llm, "create_llm_client", lambda: object())
    monkeypatch.setattr(repository, "get_repository", lambda: _FakeRepo())
    monkeypatch.setattr(search, "get_search_service", lambda: object())
    monkeypatch.setattr(relations, "get_relation_service", lambda: object())
    monkeypatch.setattr(cycles, "ResearchCycleRepository", lambda _repo, namespace: object())
    monkeypatch.setattr(pipeline_cycle, "sync_cycle_run", lambda *_a, **_k: [])
    _FakeRunner.state = state
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
