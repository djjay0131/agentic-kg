"""The compiled workflow walk mirrored through the HTTP API.

The Workflows detail page and the Research Loop page send specific endpoints
and payloads. This test drives a real ``WorkflowRunner`` (real graph, real
agents, only the HTTP transport faked) through FastAPI's ``TestClient`` so a
route or schema mismatch between the UI and the API fails here instead of in
staging.

It also pins the two failures this branch fixes at the API surface:

* the Workflows page sends ``domain_filter``; the API used to ignore it
  (Pydantic dropped the extra field and answered 200);
* the workflow list must survive a restart, so a fresh runner over the same
  durable store still lists the run.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest
from agentic_kg.agents.autonomy import parse_policy
from agentic_kg.agents.checkpoint_store import (
    DurableCheckpointSaver,
    InMemoryCheckpointStore,
)
from agentic_kg.agents.config import AgentConfig
from agentic_kg.agents.dispatch import DispatchOutcome
from agentic_kg.agents.gate_agreement import build_recommendation
from agentic_kg.agents.graph_port import (
    SOURCE_LEGACY,
    DatasetInfo,
    GraphProblem,
    MetricInfo,
    ProblemContext,
)
from agentic_kg.agents.provenance import InMemoryAgentOutputStore
from agentic_kg.agents.runner import WorkflowRunner
from agentic_kg.agents.sandbox import SandboxResult
from agentic_kg.agents.schemas import CheckpointType, WorkflowStatus
from agentic_kg.extraction.llm_client import LLMConfig, LLMProvider, OpenAIClient
from agentic_kg.knowledge_graph.models.research_cycle import (
    CycleBudget,
    CycleWorkflow,
    ResearchCycle,
)
from agentic_kg.pipeline.cycle import (
    ResearchCycleService,
    WorkflowRunnerAdapter,
    derive_pending_gate,
)
from agentic_kg_api.dependencies import get_research_cycles
from agentic_kg_api.main import app
from agentic_kg_api.routers import agents
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

NOW = "2026-11-27T23:30:00Z"


class _FakeOpenAI:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body["response_format"]["json_schema"]["name"]
        prompt = body["messages"][-1]["content"]
        self.calls.append(name)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-api-e2e",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": self._content(name, prompt),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            },
        )

    def _content(self, name: str, prompt: str) -> str:
        if name == "RankingBatchResult":
            ids = re.findall(r"\(ID: ([^)]+)\)", prompt)
            assert ids
            scores = [
                {
                    "problem_id": problem_id,
                    "score": round(0.9 - index * 0.01, 4),
                    "rationale": "Tractable with available data. " * 8,
                    "invented_extra_field": {"note": "the model added this"},
                }
                for index, problem_id in enumerate(ids)
            ]
            scores.append(
                {
                    "problem_id": "ghost-ranking-id",
                    "score": 1.0,
                    "rationale": "A perfect score for a problem that does not exist.",
                }
            )
            return json.dumps({"scores": scores})
        if name == "ContinuationProposal":
            return json.dumps(
                {
                    "problem_id": "llm-hallucination-knowledge-graphs",
                    "title": "Scale the baseline approach",
                    "methodology": (
                        "Extend the baseline with a deterministic scheduler and "
                        "measure the throughput effect. " * 12
                    ),
                    "expected_outcome": "A measurable throughput improvement.",
                    "required_resources": ["one GPU"],
                    "experimental_steps": [
                        {
                            "step_number": 1,
                            "description": "Build the scheduling layer.",
                            "expected_output": "A runnable prototype.",
                            "tools_or_methods": ["python"],
                        }
                    ],
                    "metrics_to_evaluate": ["accuracy"],
                    "confidence": 0.9,
                    "rationale": "The baseline leaves headroom. " * 6,
                    "invented_extra_field": [1, 2, 3],
                }
            )
        if name == "GeneratedCode":
            return json.dumps(
                {
                    "code": "print('{\"metrics\": {\"accuracy\": 0.9}}')\n",
                    "explanation": "Plausible but unused extra output. " * 10,
                }
            )
        if name == "SynthesisReport":
            return json.dumps(
                {
                    "summary": (
                        "The continuation improved the metric end to end and "
                        "suggests a follow-up scheduling study. " * 10
                    ),
                    "new_problems": ["How does the scheduler behave under load?"],
                    "new_relations": [
                        {
                            "source_id": "ghost-source-id",
                            "target_id": "ghost-target-id",
                            "type": "EXTENDS",
                        }
                    ],
                    "recommendations": ["Run the follow-up at larger scale."],
                    "source_problem_id": "ghost-source-id",
                    "invented_extra_field": {"note": "the model added this"},
                }
            )
        raise AssertionError(f"unexpected response model {name!r}")


class _FakeGraphPort:
    source = SOURCE_LEGACY

    def __init__(self, count: int = 20) -> None:
        self._candidates = [
            GraphProblem(
                problem_id=f"p{index}",
                statement=f"Research problem {index}",
                status="open",
            )
            for index in range(count)
        ]

    def list_candidates(self, *, topic_filter, status_filter, max_problems):
        return list(self._candidates[:max_problems])

    def load_context(self, problem_id: str) -> ProblemContext:
        if problem_id not in {c.problem_id for c in self._candidates}:
            raise KeyError(f"Problem not found: {problem_id}")
        return ProblemContext(
            problem_id=problem_id,
            statement=f"Research problem {problem_id}",
            topic="ml",
            constraints=["must run on one GPU"],
            datasets=[DatasetInfo(name="dataset", available=True)],
            baselines=["accuracy 0.5"],
            metrics=[MetricInfo(name="accuracy", baseline_value=0.5)],
        )


class _FakeSandbox:
    def execute(self, code: str, **_: Any) -> SandboxResult:
        return SandboxResult(
            success=True,
            stdout='{"metrics": {"accuracy": 0.9}}',
            stderr="",
            exit_code=0,
            backend="fake",
            experiment_id="exp-api-e2e",
        )


class _FakeRepo:
    def get_problem(self, problem_id: str):
        from types import SimpleNamespace

        if not problem_id.startswith("p"):
            raise KeyError(f"Problem not found: {problem_id}")
        return SimpleNamespace(id=problem_id, evidence=None)

    def create_problem(self, problem):
        return problem

    def create_derived_from(self, *args, **kwargs):
        return None

    def set_problem_status(self, problem_id, status):
        return None


class _FakeCycleRepository:
    def __init__(self, cycles: list[ResearchCycle]) -> None:
        self.cycles = {cycle.cycle_id: cycle.model_copy(deep=True) for cycle in cycles}

    def save(self, cycle: ResearchCycle) -> ResearchCycle:
        self.cycles[cycle.cycle_id] = cycle.model_copy(deep=True)
        return cycle

    def get(self, cycle_id: str) -> ResearchCycle:
        from agentic_kg.knowledge_graph.research_cycles import (
            ResearchCycleNotFoundError,
        )

        if cycle_id not in self.cycles:
            raise ResearchCycleNotFoundError(cycle_id)
        return self.cycles[cycle_id].model_copy(deep=True)

    def list(self, limit: int = 50) -> list[ResearchCycle]:
        return [cycle.model_copy(deep=True) for cycle in self.cycles.values()][:limit]

    def find_status(self, statuses: tuple[str, ...]) -> list[ResearchCycle]:
        return [
            cycle.model_copy(deep=True)
            for cycle in self.cycles.values()
            if cycle.status in statuses
        ]

    def list_pending_gates(self, limit: int = 50) -> list[ResearchCycle]:
        return [
            cycle.model_copy(deep=True)
            for cycle in self.cycles.values()
            if cycle.status == "awaiting_gate" and cycle.pending_gates()
        ][:limit]


def _policy():
    return parse_policy(
        {
            "version": "1",
            "gates": {
                "select_problem": {"mode": "human"},
                "approve_proposal": {"mode": "human"},
                "review_evaluation": {"mode": "human"},
            },
        }
    )


def _build_llm(handler: _FakeOpenAI):
    client = OpenAIClient(
        LLMConfig(
            provider=LLMProvider.OPENAI,
            model="gpt-6.2-sol",
            api_key="test-key",
            reasoning_effort="medium",
        )
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._client = AsyncOpenAI(api_key="test-key", http_client=http_client)
    return client, http_client


def _build_runner(llm, store, *, outputs, dispatcher=None, sandbox=None):
    return WorkflowRunner(
        llm_client=llm,
        repository=_FakeRepo(),
        checkpointer=DurableCheckpointSaver(store),
        config=AgentConfig(graph_source=SOURCE_LEGACY),
        graph_port=_FakeGraphPort(),
        sandbox=sandbox or _FakeSandbox(),
        output_store=outputs,
        dispatcher=dispatcher,
    )


class _FailedSandbox:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def execute(self, code: str, **_: Any) -> SandboxResult:
        if self.mode == "unavailable":
            raise RuntimeError("EXPERIMENT_GCS_BUCKET is required")
        if self.mode == "timeout":
            return SandboxResult(
                success=False,
                stdout="",
                stderr="execution exceeded 300s",
                exit_code=-1,
                timed_out=True,
                backend="cloudrun_job",
            )
        return SandboxResult(
            success=False,
            stdout="",
            stderr="Traceback: ZeroDivisionError",
            exit_code=1,
            backend="cloudrun_job",
        )


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def dispatch(self, run_id: str, *, phase: str) -> bool:
        self.calls.append((run_id, phase))
        return True


def _cycle_for(run_id: str, state: dict) -> ResearchCycle:
    workflow_state, gate = derive_pending_gate(state)
    assert gate == CheckpointType.SELECT_PROBLEM.value
    ranked = state.get("ranked_problems") or []
    return ResearchCycle(
        cycle_id="cycle-api",
        namespace="canon",
        started_at=NOW,
        status="awaiting_gate",
        trigger="manual",
        policy_version="1",
        selected_problems=[item["problem_id"] for item in ranked[:1]],
        budget=CycleBudget(max_experiments=5),
        workflow_runs=[
            CycleWorkflow(
                workflow_run_id=run_id,
                problem_id=str(ranked[0]["problem_id"]) if ranked else "",
                state=workflow_state,
                current_step=str(state.get("current_step") or ""),
                pending_gate=gate,
                pending_recommendation=build_recommendation(
                    gate, state, produced_at=NOW
                ),
                started_at=NOW,
                updated_at=NOW,
            )
        ],
    )


@pytest.fixture
def harness(monkeypatch):
    import asyncio

    from agentic_kg.extraction.llm_client import reset_llm_clients

    monkeypatch.setenv("LLM_RANKING_BATCH_SIZE", "8")
    # The real limiter would pace these fake calls against the default TPM
    # ceiling; pin it high and reset the process-global bucket instead.
    monkeypatch.setenv("OPENAI_TPM", "100000000")
    reset_llm_clients()
    store = InMemoryCheckpointStore()
    handler = _FakeOpenAI()
    llm, http_client = _build_llm(handler)
    outputs = InMemoryAgentOutputStore()
    runner = _build_runner(llm, store, outputs=outputs)
    agents.set_workflow_runner(runner)
    try:
        yield {
            "store": store,
            "handler": handler,
            "llm": llm,
            "outputs": outputs,
            "runner": runner,
            "client": TestClient(app),
        }
    finally:
        agents.set_workflow_runner(None)
        app.dependency_overrides.clear()
        reset_llm_clients()
        try:
            asyncio.run(http_client.aclose())
        except RuntimeError:
            pass


def _start(client: TestClient, **payload: Any) -> str:
    response = client.post("/api/agents/workflows", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def test_workflows_page_walk_uses_the_ui_payloads(harness):
    client: TestClient = harness["client"]
    runner: WorkflowRunner = harness["runner"]

    # The Workflows page sends ``domain_filter`` (not ``topic_filter``).
    run_id = _start(client, domain_filter="ml", max_problems=20)
    assert (client.get(f"/api/agents/workflows/{run_id}").json())["current_step"] == (
        "select_problem"
    )

    # The filter is no longer silently dropped.
    import asyncio

    state = asyncio.run(runner.get_state(run_id))
    assert state["topic_filter"] == "ml"
    assert "ghost-ranking-id" not in {
        ranked["problem_id"] for ranked in state["ranked_problems"]
    }

    listed = client.get("/api/agents/workflows").json()
    assert [item["run_id"] for item in listed] == [run_id]
    assert listed[0]["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    # Ranking produced state; the list must not keep defaulting to zero.
    assert listed[0]["completed_steps"] == 1
    assert listed[0]["total_steps"] == 7

    steps = [
        (
            "select_problem",
            {
                "decision": "approve",
                "feedback": "go",
                "edited_data": {"problem_id": "p0"},
            },
        ),
        ("approve_proposal", {"decision": "approve", "feedback": "approved"}),
        ("review_evaluation", {"decision": "approve", "feedback": "write it"}),
    ]
    expected_next = ["approve_proposal", "review_evaluation", "synthesis"]
    for (checkpoint, payload), next_step in zip(steps, expected_next):
        response = client.post(
            f"/api/agents/workflows/{run_id}/checkpoints/{checkpoint}", json=payload
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["current_step"] in (next_step, "synthesis")
        if checkpoint == "select_problem":
            # The model invented a slug; the API returns the selected id.
            assert body["proposal"]["problem_id"] == "p0"
        if checkpoint == "approve_proposal":
            assert body["evaluation_result"]["proposal_id"] == "p0"

    final = client.get(f"/api/agents/workflows/{run_id}").json()
    assert final["status"] == WorkflowStatus.COMPLETED.value
    assert final["synthesis_report"]["source_problem_id"] == "p0"
    assert "ghost" not in json.dumps(final["synthesis_report"])
    listed = client.get("/api/agents/workflows").json()
    assert listed[0]["completed_steps"] == listed[0]["total_steps"] == 7


def test_research_loop_walk_records_agreement_and_survives_a_restart(harness):
    client: TestClient = harness["client"]
    store = harness["store"]
    runner = harness["runner"]

    run_id = _start(client, max_problems=20)
    import asyncio

    state = asyncio.run(runner.get_state(run_id))
    cycles = _FakeCycleRepository([_cycle_for(run_id, state)])
    service = ResearchCycleService(
        cycles, WorkflowRunnerAdapter(runner), policy=_policy()
    )
    app.dependency_overrides[get_research_cycles] = lambda: service

    gates = client.get("/api/research/gates")
    assert gates.status_code == 200
    gate_body = gates.json()
    assert gate_body["total"] == 1
    assert gate_body["gates"][0]["gate"] == "select_problem"
    assert gate_body["gates"][0]["recommendation"] is not None

    # Decide the first gate, then simulate a restart before the next one.
    first = client.post(
        f"/api/research/gates/cycle-api/{run_id}",
        headers={"X-User-ID": "alice"},
        json={"gate": "select_problem", "decision": "approve", "note": "go"},
    )
    assert first.status_code == 202, first.text
    decision = first.json()["workflow_runs"][0]["gate_decisions"][0]
    assert decision["decided_by"] == "alice"
    assert decision["agent_recommendation"] is not None
    assert decision["agreed"] is True

    # A fresh runner over the same store is the second Cloud Run instance.
    fresh = _build_runner(harness["llm"], store, outputs=harness["outputs"])
    agents.set_workflow_runner(fresh)
    listed = client.get("/api/agents/workflows").json()
    assert [item["run_id"] for item in listed] == [run_id]
    assert client.get(f"/api/agents/workflows/{run_id}").json()["current_step"] == (
        "approve_proposal"
    )

    service = ResearchCycleService(
        cycles, WorkflowRunnerAdapter(fresh), policy=_policy()
    )
    app.dependency_overrides[get_research_cycles] = lambda: service

    for gate in ("approve_proposal", "review_evaluation"):
        response = client.post(
            f"/api/research/gates/cycle-api/{run_id}",
            headers={"X-User-ID": "alice"},
            json={"gate": gate, "decision": "approve", "note": "ok"},
        )
        assert response.status_code == 202, response.text

    agreement = client.get("/api/research/agreement")
    assert agreement.status_code == 200
    body = agreement.json()
    assert body["total_decisions"] == 3
    assert body["total_agreed"] == 3

    final = client.get(f"/api/agents/workflows/{run_id}").json()
    assert final["status"] == WorkflowStatus.COMPLETED.value
    assert final["synthesis_report"]

    cycles_list = client.get("/api/research/cycles")
    assert cycles_list.status_code == 200
    assert cycles_list.json()["cycles"][0]["workflow_runs"][0]["state"] == "completed"


def test_reject_through_the_api_ends_the_run_cleanly(harness):
    client: TestClient = harness["client"]
    runner = harness["runner"]

    run_id = _start(client, max_problems=20)
    import asyncio

    state = asyncio.run(runner.get_state(run_id))
    cycles = _FakeCycleRepository([_cycle_for(run_id, state)])
    service = ResearchCycleService(
        cycles, WorkflowRunnerAdapter(runner), policy=_policy()
    )
    app.dependency_overrides[get_research_cycles] = lambda: service

    response = client.post(
        f"/api/research/gates/cycle-api/{run_id}",
        headers={"X-User-ID": "alice"},
        json={
            "gate": "select_problem",
            "decision": "reject",
            "note": "not this problem",
        },
    )
    assert response.status_code == 202, response.text
    decision = response.json()["workflow_runs"][0]["gate_decisions"][-1]
    assert decision["decision"] == "reject"
    assert decision["agent_recommendation"] is not None
    assert decision["agreed"] is False

    final = client.get(f"/api/agents/workflows/{run_id}").json()
    assert final["status"] == WorkflowStatus.REJECTED.value
    assert final["synthesis_report"] is None
    assert final["current_step"] == "select_problem"


def test_async_start_and_resume_return_before_the_worker(harness):
    """POST returns 202 with a running step; the worker completes it later."""
    import asyncio

    client: TestClient = harness["client"]
    dispatcher = _RecordingDispatcher()
    runner = _build_runner(
        harness["llm"],
        harness["store"],
        outputs=harness["outputs"],
        dispatcher=dispatcher,
    )
    agents.set_workflow_runner(runner)

    response = client.post("/api/agents/workflows", json={"max_problems": 20})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert dispatcher.calls == [(run_id, "start")]

    body = client.get(f"/api/agents/workflows/{run_id}").json()
    assert body["status"] == WorkflowStatus.RUNNING.value
    assert body["current_step"] == "ranking"

    asyncio.run(runner.advance_workflow(run_id))
    body = client.get(f"/api/agents/workflows/{run_id}").json()
    assert body["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert body["current_step"] == "select_problem"

    decision = client.post(
        f"/api/agents/workflows/{run_id}/checkpoints/select_problem",
        json={
            "decision": "approve",
            "feedback": "go",
            "edited_data": {"problem_id": "p0"},
        },
    )
    assert decision.status_code == 202, decision.text
    assert dispatcher.calls[-1] == (run_id, "resume")
    assert decision.json()["status"] == WorkflowStatus.RUNNING.value
    assert decision.json()["current_step"] == "continuation"

    asyncio.run(runner.advance_workflow(run_id))
    body = client.get(f"/api/agents/workflows/{run_id}").json()
    assert body["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert body["current_step"] == "approve_proposal"


def test_async_research_gate_returns_202_and_leaves_the_cycle_running(harness):
    """The Research Loop decision endpoint must not run evaluation in-request."""
    import asyncio

    client: TestClient = harness["client"]
    dispatcher = _RecordingDispatcher()
    runner = _build_runner(
        harness["llm"],
        harness["store"],
        outputs=harness["outputs"],
        dispatcher=dispatcher,
    )
    agents.set_workflow_runner(runner)

    run_id = _start(client, max_problems=20)
    asyncio.run(runner.advance_workflow(run_id))
    state = asyncio.run(runner.get_state(run_id))
    cycles = _FakeCycleRepository([_cycle_for(run_id, state)])
    service = ResearchCycleService(
        cycles, WorkflowRunnerAdapter(runner), policy=_policy()
    )
    app.dependency_overrides[get_research_cycles] = lambda: service

    response = client.post(
        f"/api/research/gates/cycle-api/{run_id}",
        headers={"X-User-ID": "alice"},
        json={"gate": "select_problem", "decision": "approve", "note": "go"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["workflow_runs"][0]["state"] == "running"
    assert dispatcher.calls[-1] == (run_id, "resume")

    asyncio.run(runner.advance_workflow(run_id))
    body = client.get(f"/api/agents/workflows/{run_id}").json()
    assert body["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert body["current_step"] == "approve_proposal"


@pytest.mark.parametrize("mode", ["non_zero", "timeout", "unavailable"])
def test_sandbox_failures_are_reviewable_through_the_api(harness, mode):
    """A sandbox failure reaches review_evaluation instead of 500ing the run."""
    client: TestClient = harness["client"]
    runner = _build_runner(
        harness["llm"],
        harness["store"],
        outputs=harness["outputs"],
        sandbox=_FailedSandbox(mode),
    )
    agents.set_workflow_runner(runner)

    run_id = _start(client, max_problems=20)
    first = client.post(
        f"/api/agents/workflows/{run_id}/checkpoints/select_problem",
        json={
            "decision": "approve",
            "feedback": "go",
            "edited_data": {"problem_id": "p0"},
        },
    )
    assert first.status_code == 202, first.text

    second = client.post(
        f"/api/agents/workflows/{run_id}/checkpoints/approve_proposal",
        json={"decision": "approve", "feedback": "run it"},
    )
    assert second.status_code == 202, second.text
    body = second.json()
    assert body["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert body["current_step"] == "review_evaluation"
    result = body["evaluation_result"]
    assert result["execution_success"] is False
    if mode == "timeout":
        assert result["verdict"] == "not_viable"
    elif mode == "unavailable":
        assert result["execution_provenance"]["backend"] == "unavailable"
    else:
        assert result["verdict"] == "inconclusive"


class _FailingDispatcher:
    """Simulates a 403 from run.jobs.runWithOverrides."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def dispatch(self, run_id: str, *, phase: str) -> DispatchOutcome:
        self.calls.append((run_id, phase))
        raise RuntimeError(
            "403 PermissionDenied: run.jobs.runWithOverrides needs "
            "roles/run.jobsExecutorWithOverrides"
        )


class _OutcomeDispatcher:
    def __init__(self, outcome: DispatchOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, str]] = []

    def dispatch(self, run_id: str, *, phase: str) -> DispatchOutcome:
        self.calls.append((run_id, phase))
        return self.outcome


def test_permission_denied_decision_completes_in_request(harness):
    """The API response is the next gate, not a stuck old gate."""
    client: TestClient = harness["client"]
    dispatcher = _FailingDispatcher()
    runner = _build_runner(
        harness["llm"],
        harness["store"],
        outputs=harness["outputs"],
        dispatcher=dispatcher,
    )
    agents.set_workflow_runner(runner)

    run_id = _start(client, max_problems=20)
    response = client.post(
        f"/api/agents/workflows/{run_id}/checkpoints/select_problem",
        json={
            "decision": "approve",
            "feedback": "go",
            "edited_data": {"problem_id": "p0"},
        },
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert body["current_step"] == "approve_proposal"
    assert body["execution_mode"] == "in_request"
    assert body["dispatch_result"] == "refused"
    assert "running in request" in " ".join(
        str(message.get("content") or "") for message in body["messages"]
    )


def test_dispatched_worker_that_never_starts_is_failed_on_read(harness, monkeypatch):
    """GET closes an accepted-but-dead dispatch with the reason it was accepted."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("WORKFLOW_DISPATCH_WATCHDOG_SECONDS", "1")
    client: TestClient = harness["client"]
    runner = _build_runner(
        harness["llm"],
        harness["store"],
        outputs=harness["outputs"],
        dispatcher=_OutcomeDispatcher(
            DispatchOutcome(dispatched=True, execution="execution-dead")
        ),
    )
    agents.set_workflow_runner(runner)

    run_id = _start(client, max_problems=20)
    runner._workflows[run_id]["dispatched_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat()
    runner._persist_metadata(run_id)

    body = client.get(f"/api/agents/workflows/{run_id}").json()

    assert body["status"] == WorkflowStatus.FAILED.value
    reason = body["errors"][-1]
    assert "no worker started" in reason
    assert "dispatch_result=dispatched" in reason
    assert "execution-dead" in reason


def test_dispatched_worker_runs_to_the_next_gate_through_the_api(harness):
    import asyncio

    client: TestClient = harness["client"]
    dispatcher = _OutcomeDispatcher(
        DispatchOutcome(dispatched=True, execution="execution-live")
    )
    runner = _build_runner(
        harness["llm"],
        harness["store"],
        outputs=harness["outputs"],
        dispatcher=dispatcher,
    )
    agents.set_workflow_runner(runner)

    run_id = _start(client, max_problems=20)
    body = client.get(f"/api/agents/workflows/{run_id}").json()
    assert body["status"] == WorkflowStatus.RUNNING.value
    assert body["current_step"] == "ranking"
    assert body["dispatch_result"] == "dispatched"
    assert body["job_execution"] == "execution-live"

    runner.record_worker_start(run_id, execution="execution-live")
    asyncio.run(runner.advance_workflow(run_id))
    body = client.get(f"/api/agents/workflows/{run_id}").json()
    assert body["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert body["current_step"] == "select_problem"

    decision = client.post(
        f"/api/agents/workflows/{run_id}/checkpoints/select_problem",
        json={
            "decision": "approve",
            "feedback": "go",
            "edited_data": {"problem_id": "p0"},
        },
    )
    assert decision.status_code == 202, decision.text
    # The decision response presents the in-flight step, not the stale gate.
    assert decision.json()["status"] == WorkflowStatus.RUNNING.value
    assert decision.json()["current_step"] == "continuation"

    runner.record_worker_start(run_id, execution="execution-live-2")
    asyncio.run(runner.advance_workflow(run_id))
    body = client.get(f"/api/agents/workflows/{run_id}").json()
    assert body["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert body["current_step"] == "approve_proposal"
