"""True in-process end-to-end test of the compiled production graph.

The staging failures after #161/#163/#164 were invisible to CI because the
existing tests mocked the agents (and sometimes the runner) instead of walking
the real graph. This test does the opposite:

* the real ``WorkflowRunner`` compiles the real ``build_workflow`` graph;
* the real Ranking/Continuation/Evaluation/Synthesis agents run;
* the real ``OpenAIClient`` + instructor wrapper is used, with only the HTTP
  transport faked to return schema-valid JSON per call site;
* a fake ``GraphPort`` supplies 20 candidates and a fake sandbox returns a
  metric line;
* checkpoints go through ``DurableCheckpointSaver(InMemoryCheckpointStore)``,
  so a second runner over the same store is a process restart.

It walks the success path to synthesis, checks the gate agreement records
(ADR-0013) each human decision writes, and walks a rejection to a clean end.
No Docker, no network, no real model call.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from agentic_kg.agents.autonomy import parse_policy
from agentic_kg.agents.checkpoint_store import (
    DurableCheckpointSaver,
    InMemoryCheckpointStore,
)
from agentic_kg.agents.config import AgentConfig
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
from agentic_kg.agents.schemas import (
    CheckpointType,
    WorkflowStatus,
)
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
from openai import AsyncOpenAI

NOW = "2026-11-27T23:30:00Z"


def _ranking_ids(prompt: str) -> list[str]:
    return re.findall(r"\(ID: ([^)]+)\)", prompt)


class _FakeOpenAI:
    """An HTTP transport that answers each structured call by schema name."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body["response_format"]["json_schema"]["name"]
        prompt = body["messages"][-1]["content"]
        self.calls.append(name)
        content = self._content(name, prompt)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-e2e",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
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
            ids = _ranking_ids(prompt)
            assert ids, "ranking batch asked for no ids"
            scores = [
                {
                    "problem_id": problem_id,
                    "score": round(0.95 - index * 0.01, 4),
                    "rationale": "Tractable with available data. " * 8,
                    "invented_extra_field": {"note": "the model added this"},
                }
                for index, problem_id in enumerate(ids)
            ]
            # A plausible invented id must be ignored, not ranked.
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
                    # The model invents its own slug. It must be rebound to the
                    # selected problem rather than used as a graph key.
                    "problem_id": "llm-hallucination-knowledge-graphs",
                    "title": "Scale the baseline approach",
                    "methodology": (
                        "Extend the baseline with a deterministic scheduling layer "
                        "and measure the effect on throughput. " * 12
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
                    # Invented endpoints must be dropped, never written.
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
    """20 candidates and a context rich enough for a promising evaluation."""

    source = SOURCE_LEGACY

    def __init__(self, count: int = 20) -> None:
        self._candidates = [
            GraphProblem(
                problem_id=f"p{index}",
                statement=f"Research problem {index}",
                status="open",
                confidence=0.8,
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
            experiment_id="exp-e2e",
        )


class _FakeRepo:
    """Only the writes the legacy SynthesisAgent performs."""

    def __init__(self) -> None:
        self.created: list[Any] = []
        self.status_updates: list[tuple[str, Any]] = []

    def get_problem(self, problem_id: str):
        from types import SimpleNamespace

        if not problem_id.startswith("p"):
            raise KeyError(f"Problem not found: {problem_id}")
        return SimpleNamespace(id=problem_id, evidence=None)

    def create_problem(self, problem: Any) -> Any:
        self.created.append(problem)
        return problem

    def create_derived_from(self, *args: Any, **kwargs: Any) -> None:
        return None

    def set_problem_status(self, problem_id: str, status: Any) -> None:
        self.status_updates.append((problem_id, status))


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


def _cycle_for(run_id: str, state: dict) -> ResearchCycle:
    workflow_state, gate = derive_pending_gate(state)
    assert gate == CheckpointType.SELECT_PROBLEM.value
    ranked = state.get("ranked_problems") or []
    recommendation = build_recommendation(gate, state, produced_at=NOW)
    return ResearchCycle(
        cycle_id="cycle-e2e",
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
                problem_id=str((ranked[0] if ranked else {}).get("problem_id", "")),
                problem_statement=str(state.get("selected_problem_statement") or ""),
                state=workflow_state,
                current_step=str(state.get("current_step") or ""),
                pending_gate=gate,
                pending_recommendation=recommendation,
                started_at=NOW,
                updated_at=NOW,
            )
        ],
    )


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


def _build_llm(handler: _FakeOpenAI) -> tuple[OpenAIClient, httpx.AsyncClient]:
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


def _build_runner(
    llm: OpenAIClient,
    store: InMemoryCheckpointStore,
    *,
    outputs: InMemoryAgentOutputStore,
    repo: _FakeRepo,
) -> WorkflowRunner:
    return WorkflowRunner(
        llm_client=llm,
        repository=repo,
        checkpointer=DurableCheckpointSaver(store),
        config=AgentConfig(graph_source=SOURCE_LEGACY),
        graph_port=_FakeGraphPort(),
        sandbox=_FakeSandbox(),
        output_store=outputs,
    )


@pytest.fixture
def e2e(monkeypatch):
    monkeypatch.setenv("LLM_RANKING_BATCH_SIZE", "8")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    store = InMemoryCheckpointStore()
    handler = _FakeOpenAI()
    llm, http_client = _build_llm(handler)
    outputs = InMemoryAgentOutputStore()
    repo = _FakeRepo()
    runner = _build_runner(llm, store, outputs=outputs, repo=repo)
    try:
        yield {
            "store": store,
            "handler": handler,
            "llm": llm,
            "outputs": outputs,
            "repo": repo,
            "runner": runner,
        }
    finally:
        import asyncio

        try:
            asyncio.run(http_client.aclose())
        except RuntimeError:  # a test loop is still active; mock transport is inert
            pass


def _assert_recommendation_written(cycle: ResearchCycle, gate: str) -> None:
    workflow = cycle.workflow_runs[0]
    decision = next(d for d in workflow.gate_decisions if d.gate == gate)
    assert decision.agent_recommendation is not None, gate
    assert decision.human_decision == decision.decision
    assert decision.agreed is True, gate
    assert decision.decided_by == "alice"


@pytest.mark.asyncio
async def test_compiled_graph_walks_to_synthesis_across_a_restart(e2e):
    store = e2e["store"]
    runner = e2e["runner"]
    outputs = e2e["outputs"]

    run_id = await runner.start_workflow(max_problems=20)
    state = await runner.get_state(run_id)

    # --- stop 1: select_problem -------------------------------------------
    assert state["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert state["current_step"] == CheckpointType.SELECT_PROBLEM.value
    assert len(state["ranked_problems"]) == 20
    assert e2e["handler"].calls.count("RankingBatchResult") == 3
    # The invented ranking id was offered a perfect score and still ignored.
    assert "ghost-ranking-id" not in {
        ranked["problem_id"] for ranked in state["ranked_problems"]
    }
    for ranked in state["ranked_problems"]:
        assert ranked["output_id"]
        assert ranked["provenance"]["abstained"] is True
    assert outputs.list_for_run(run_id)  # every ranked problem recorded one

    # A fresh runner over the same store is a process restart.
    restarted = _build_runner(
        e2e["llm"], store, outputs=outputs, repo=e2e["repo"]
    )
    listed = {item["run_id"]: item for item in restarted.list_workflows()}
    assert run_id in listed
    assert listed[run_id]["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert listed[run_id]["current_step"] == CheckpointType.SELECT_PROBLEM.value

    cycles = _FakeCycleRepository([_cycle_for(run_id, state)])
    service = ResearchCycleService(
        cycles,
        WorkflowRunnerAdapter(restarted),
        policy=_policy(),
    )
    cycle = service.get("cycle-e2e")
    assert cycle.workflow_runs[0].pending_recommendation is not None

    cycle = await service.decide_gate(
        "cycle-e2e",
        run_id,
        gate=CheckpointType.SELECT_PROBLEM.value,
        decision="approve",
        user_id="alice",
        note="go",
        now=datetime(2026, 11, 27, 23, 31, tzinfo=timezone.utc),
    )
    _assert_recommendation_written(cycle, CheckpointType.SELECT_PROBLEM.value)

    # --- stop 2: approve_proposal -----------------------------------------
    state = await restarted.get_state(run_id)
    assert state["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert state["current_step"] == CheckpointType.APPROVE_PROPOSAL.value
    assert state["selected_problem_id"] == "p0"
    # The model wrote its own slug; the stored proposal must use the selected id.
    assert state["proposal"]["problem_id"] == "p0"
    assert state["proposal"]["provenance"]["abstained"] is True
    assert state["proposal"]["output_id"]

    # A third process resumes the approval.
    restarted_again = _build_runner(
        e2e["llm"], store, outputs=outputs, repo=e2e["repo"]
    )
    service = ResearchCycleService(
        cycles, WorkflowRunnerAdapter(restarted_again), policy=_policy()
    )
    cycle = await service.decide_gate(
        "cycle-e2e",
        run_id,
        gate=CheckpointType.APPROVE_PROPOSAL.value,
        decision="approve",
        user_id="alice",
        note="approved",
        now=datetime(2026, 11, 27, 23, 32, tzinfo=timezone.utc),
    )
    _assert_recommendation_written(cycle, CheckpointType.APPROVE_PROPOSAL.value)

    # --- stop 3: review_evaluation ----------------------------------------
    state = await restarted_again.get_state(run_id)
    assert state["status"] == WorkflowStatus.WAITING_CHECKPOINT.value
    assert state["current_step"] == CheckpointType.REVIEW_EVALUATION.value
    assert state["evaluation_result"]["verdict"] == "promising"
    assert state["evaluation_result"]["proposal_id"] == "p0"
    assert state["evaluation_result"]["provenance"]["abstained"] is True
    assert state["evaluation_result"]["output_id"]

    service = ResearchCycleService(
        cycles, WorkflowRunnerAdapter(restarted_again), policy=_policy()
    )
    cycle = await service.decide_gate(
        "cycle-e2e",
        run_id,
        gate=CheckpointType.REVIEW_EVALUATION.value,
        decision="approve",
        user_id="alice",
        note="write it",
        now=datetime(2026, 11, 27, 23, 33, tzinfo=timezone.utc),
    )
    _assert_recommendation_written(cycle, CheckpointType.REVIEW_EVALUATION.value)

    # --- completed: synthesis --------------------------------------------
    state = await restarted_again.get_state(run_id)
    assert state["status"] == WorkflowStatus.COMPLETED.value
    assert state["current_step"] == "synthesis"
    assert state["synthesis_report"]["source_problem_id"] == "p0"
    assert state["synthesis_report"]["provenance"]["abstained"] is True
    assert state["synthesis_report"]["graph_updates"]
    assert e2e["repo"].created  # the legacy path wrote the new problem
    # The invented relation endpoints were dropped, not written as edges.
    assert "ghost" not in json.dumps(state["synthesis_report"])

    final_cycle = service.get("cycle-e2e")
    assert final_cycle.workflow_runs[0].state == "completed"
    assert [d.gate for d in final_cycle.workflow_runs[0].gate_decisions] == [
        "select_problem",
        "approve_proposal",
        "review_evaluation",
    ]


@pytest.mark.asyncio
async def test_rejection_ends_the_graph_cleanly_at_every_gate(e2e):
    store = e2e["store"]
    llm = e2e["llm"]
    outputs = e2e["outputs"]
    repo = e2e["repo"]

    for gate_index, gate in enumerate(
        (
            CheckpointType.SELECT_PROBLEM,
            CheckpointType.APPROVE_PROPOSAL,
            CheckpointType.REVIEW_EVALUATION,
        )
    ):
        runner = _build_runner(llm, store, outputs=outputs, repo=repo)
        run_id = await runner.start_workflow(max_problems=20)
        state = await runner.get_state(run_id)
        service = ResearchCycleService(
            _FakeCycleRepository([_cycle_for(run_id, state)]),
            WorkflowRunnerAdapter(runner),
            policy=_policy(),
        )

        for index in range(gate_index):
            pending = service.get("cycle-e2e").workflow_runs[0].pending_gate
            assert pending is not None
            await service.decide_gate(
                "cycle-e2e",
                run_id,
                gate=pending,
                decision="approve",
                user_id="alice",
                note="continue",
            )

        cycle = await service.decide_gate(
            "cycle-e2e",
            run_id,
            gate=gate.value,
            decision="reject",
            user_id="alice",
            note="not this one",
        )

        state = await runner.get_state(run_id)
        assert state["status"] == WorkflowStatus.REJECTED.value
        assert state["synthesis_report"] is None
        assert state["current_step"] == gate.value
        snapshot = await runner._graph.aget_state(
            {"configurable": {"thread_id": run_id}}
        )
        assert snapshot.next == ()

        workflow = cycle.workflow_runs[0]
        assert workflow.state == "rejected"
        decision = workflow.gate_decisions[-1]
        assert decision.gate == gate.value
        assert decision.decision == "reject"
        assert decision.agent_recommendation is not None
        assert decision.agreed is False
