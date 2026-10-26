"""Offline tests for the scheduled research-progression cycle.

Everything here runs with fakes: a cycle repository that keeps records in a
dict and a workflow runner that produces deterministic states. The real Neo4j
and LangGraph paths are covered by their own tests; these pin the scheduling
and gate semantics the brief asks for.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from agentic_kg.agents.autonomy import parse_policy
from agentic_kg.agents.schemas import CheckpointDecision, CheckpointType
from agentic_kg.knowledge_graph.models.research_cycle import (
    CycleBudget,
    CycleWorkflow,
    GateDecision,
    ResearchCycle,
)
from agentic_kg.pipeline.cycle import (
    CycleBudgetExceeded,
    CycleConfig,
    ResearchCycleService,
    derive_pending_gate,
    run_cycle,
)

NOW = datetime(2026, 10, 26, 3, 0, 0, tzinfo=timezone.utc)


class FakeCycleRepository:
    def __init__(self, cycles: list[ResearchCycle] | None = None):
        self.cycles: dict[str, ResearchCycle] = {
            cycle.cycle_id: cycle.model_copy(deep=True) for cycle in (cycles or [])
        }

    def save(self, cycle: ResearchCycle) -> ResearchCycle:
        self.cycles[cycle.cycle_id] = cycle.model_copy(deep=True)
        return cycle

    def get(self, cycle_id: str) -> ResearchCycle:
        from agentic_kg.knowledge_graph.research_cycles import (
            ResearchCycleNotFoundError,
        )

        if cycle_id not in self.cycles:
            raise ResearchCycleNotFoundError(cycle_id)
        return self.cycles[cycle_id]

    def find_running(self) -> list[ResearchCycle]:
        return self.find_status(("running",))

    def find_status(self, statuses: tuple[str, ...]) -> list[ResearchCycle]:
        return [
            cycle
            for cycle in self.cycles.values()
            if cycle.status in statuses
        ]

    def list(self, limit: int = 50) -> list[ResearchCycle]:
        return list(self.cycles.values())[:limit]

    def list_pending_gates(self, limit: int = 50) -> list[ResearchCycle]:
        return [
            cycle
            for cycle in self.find_status(("awaiting_gate",))
            if cycle.pending_gates()
        ][:limit]


class FakeRunner:
    def __init__(self, ranked: list[dict] | None = None):
        self.ranked = ranked or [
            {"problem_id": f"p{i}", "statement": f"Problem {i}", "score": 0.9 - i / 10}
            for i in range(5)
        ]
        self.states: dict[str, dict] = {}
        self.started: list[list[str]] = []
        self.resumes: list[dict[str, Any]] = []

    async def rank_candidates(self, *, max_problems: int = 20) -> list[dict]:
        return list(self.ranked)

    async def start_workflow(
        self, *, candidate_problem_ids: list[str], max_problems: int = 20
    ) -> str:
        run_id = f"wf-{len(self.started) + 1}"
        self.started.append(list(candidate_problem_ids))
        self.states[run_id] = {
            "run_id": run_id,
            "status": "running",
            "current_step": "ranking",
            "ranked_problems": [
                {"problem_id": pid, "statement": f"Problem {pid}"}
                for pid in candidate_problem_ids
            ],
            "selected_problem_id": None,
            "proposal": None,
            "proposal_approved": False,
            "evaluation_result": None,
            "evaluation_approved": False,
            "synthesis_report": None,
            "errors": [],
        }
        return run_id

    async def get_state(self, run_id: str) -> dict:
        return dict(self.states[run_id])

    async def resume_workflow(
        self,
        run_id: str,
        *,
        checkpoint_type: CheckpointType,
        decision: CheckpointDecision,
        feedback: str = "",
        edited_data: dict[str, Any] | None = None,
    ) -> dict:
        self.resumes.append(
            {
                "run_id": run_id,
                "gate": checkpoint_type.value,
                "decision": decision.value,
                "feedback": feedback,
                "edited_data": edited_data,
            }
        )
        state = dict(self.states[run_id])
        gate = checkpoint_type
        if gate == CheckpointType.SELECT_PROBLEM:
            if decision == CheckpointDecision.REJECT:
                state["selected_problem_id"] = None
            else:
                problem_id = (edited_data or {}).get(
                    "problem_id",
                    state["ranked_problems"][0]["problem_id"],
                )
                state["selected_problem_id"] = problem_id
                state["selected_problem_statement"] = f"Problem {problem_id}"
                state["proposal"] = {
                    "problem_id": problem_id,
                    "title": "A proposal",
                    "methodology": "Do the thing in a valid length way.",
                }
                state["proposal_approved"] = False
        elif gate == CheckpointType.APPROVE_PROPOSAL:
            state["proposal_approved"] = decision != CheckpointDecision.REJECT
            if state["proposal_approved"]:
                state["evaluation_result"] = {
                    "proposal_id": state["proposal"]["problem_id"],
                    "verdict": "promising",
                }
                state["evaluation_approved"] = False
        elif gate == CheckpointType.REVIEW_EVALUATION:
            state["evaluation_approved"] = decision != CheckpointDecision.REJECT
            if state["evaluation_approved"]:
                state["synthesis_report"] = {"summary": "Wrote the result."}
                state["status"] = "completed"
        self.states[run_id] = state
        return dict(state)


def _policy_all_human():
    return parse_policy(
        {
            "version": "test",
            "gates": {
                "select_problem": {"mode": "human"},
                "approve_proposal": {"mode": "human"},
                "review_evaluation": {"mode": "human"},
            },
        }
    )


async def test_cycle_starts_top_k_and_stops_at_selection_gate():
    repo = FakeCycleRepository()
    runner = FakeRunner()
    result = await run_cycle(
        config=CycleConfig(enabled=True, top_k=2, trigger="schedule"),
        cycles=repo,
        runner=runner,
        policy=_policy_all_human(),
        now=NOW,
        write_json=False,
        write_neo4j=False,
    )

    assert result.exit_code == 0
    cycle = result.cycle
    assert cycle is not None
    assert cycle.status == "awaiting_gate"
    assert cycle.selected_problems == ["p0", "p1"]
    assert len(cycle.workflow_runs) == 2
    assert runner.started == [["p0"], ["p1"]]
    assert all(item.pending_gate == "select_problem" for item in cycle.workflow_runs)
    assert result.report is not None
    assert result.report.trigger == "research_cycle"
    assert result.report.status == "running"
    assert repo.cycles[cycle.cycle_id].status == "awaiting_gate"


async def test_disabled_kill_switch_does_nothing():
    repo = FakeCycleRepository()
    runner = FakeRunner()
    result = await run_cycle(
        config=CycleConfig(enabled=False),
        cycles=repo,
        runner=runner,
        policy=_policy_all_human(),
        now=NOW,
    )
    assert result.skipped_reason == "disabled"
    assert result.report is None
    assert repo.cycles == {}
    assert runner.started == []


async def test_another_running_cycle_blocks_a_new_one():
    running = ResearchCycle(
        cycle_id="cycle-running",
        namespace="canon",
        started_at="2026-10-26T03:00:00Z",
        status="running",
    )
    repo = FakeCycleRepository([running])
    runner = FakeRunner()
    result = await run_cycle(
        config=CycleConfig(enabled=True, top_k=2),
        cycles=repo,
        runner=runner,
        policy=_policy_all_human(),
        now=NOW,
        write_json=False,
        write_neo4j=False,
    )
    assert result.skipped_reason == "another_cycle_running"
    assert runner.started == []
    assert list(repo.cycles) == ["cycle-running"]


async def test_max_experiments_stops_starting_more_workflows():
    repo = FakeCycleRepository()
    runner = FakeRunner()
    result = await run_cycle(
        config=CycleConfig(enabled=True, top_k=3, max_experiments=1),
        cycles=repo,
        runner=runner,
        policy=_policy_all_human(),
        now=NOW,
        write_json=False,
        write_neo4j=False,
    )
    cycle = result.cycle
    assert cycle is not None
    assert len(cycle.workflow_runs) == 1
    assert cycle.budget.stopped_by_budget is True
    assert "max_experiments=1" in (cycle.note or "")
    assert result.exit_code == 1


async def test_usd_cap_stops_before_a_second_workflow():
    repo = FakeCycleRepository()
    runner = FakeRunner()
    result = await run_cycle(
        config=CycleConfig(
            enabled=True,
            top_k=3,
            max_llm_usd=0.05,
            est_usd_per_workflow=0.05,
        ),
        cycles=repo,
        runner=runner,
        policy=_policy_all_human(),
        now=NOW,
        write_json=False,
        write_neo4j=False,
    )
    cycle = result.cycle
    assert cycle is not None
    assert len(cycle.workflow_runs) == 1
    assert cycle.budget.est_llm_usd == 0.05
    assert cycle.budget.stopped_by_budget is True
    assert "est_llm_usd" in (cycle.note or "")


async def test_reconcile_closes_a_cycle_whose_workflows_finished():
    finished_state = {
        "status": "completed",
        "current_step": "synthesis",
        "selected_problem_id": "p0",
        "proposal": {"problem_id": "p0"},
        "proposal_approved": True,
        "evaluation_result": {"verdict": "promising"},
        "evaluation_approved": True,
        "synthesis_report": {"summary": "done"},
        "errors": [],
    }
    runner = FakeRunner()
    runner.states["wf-old"] = finished_state
    old = ResearchCycle(
        cycle_id="cycle-old",
        namespace="canon",
        started_at="2026-10-25T03:00:00Z",
        status="awaiting_gate",
        workflow_runs=[
            CycleWorkflow(
                workflow_run_id="wf-old",
                problem_id="p0",
                state="awaiting_gate",
                pending_gate="review_evaluation",
                gate_decisions=[
                    GateDecision(
                        gate="select_problem",
                        decision="approve",
                        decided_by="alice",
                        decided_at="2026-10-25T04:00:00Z",
                    ),
                    GateDecision(
                        gate="approve_proposal",
                        decision="approve",
                        decided_by="alice",
                        decided_at="2026-10-25T04:10:00Z",
                    ),
                    GateDecision(
                        gate="review_evaluation",
                        decision="approve",
                        decided_by="alice",
                        decided_at="2026-10-25T04:20:00Z",
                    ),
                ],
            )
        ],
    )
    repo = FakeCycleRepository([old])
    result = await run_cycle(
        config=CycleConfig(enabled=True, top_k=1),
        cycles=repo,
        runner=runner,
        policy=_policy_all_human(),
        now=NOW,
        write_json=False,
        write_neo4j=False,
    )
    assert repo.cycles["cycle-old"].status == "completed"
    assert repo.cycles["cycle-old"].finished_at is not None
    assert result.cycle is not None and result.cycle.cycle_id != "cycle-old"


def test_derive_pending_gate_reads_workflow_facts():
    ranked_only = {"status": "running", "ranked_problems": [{"problem_id": "p0"}]}
    assert derive_pending_gate(ranked_only) == ("awaiting_gate", "select_problem")

    after_select = {
        **ranked_only,
        "selected_problem_id": "p0",
        "proposal": {"problem_id": "p0"},
    }
    assert derive_pending_gate(after_select) == (
        "awaiting_gate",
        "approve_proposal",
    )

    after_approval = {
        **after_select,
        "proposal_approved": True,
        "evaluation_result": {"verdict": "promising"},
    }
    assert derive_pending_gate(after_approval) == (
        "awaiting_gate",
        "review_evaluation",
    )

    completed = {
        **after_approval,
        "evaluation_approved": True,
        "synthesis_report": {"summary": "done"},
        "status": "completed",
    }
    assert derive_pending_gate(completed) == ("completed", None)


def _gated_cycle(*, budget: CycleBudget | None = None) -> ResearchCycle:
    return ResearchCycle(
        cycle_id="cycle-1",
        namespace="canon",
        started_at="2026-10-26T03:00:00Z",
        status="awaiting_gate",
        workflow_runs=[
            CycleWorkflow(
                workflow_run_id="wf-1",
                problem_id="p0",
                state="awaiting_gate",
                pending_gate="select_problem",
            )
        ],
        budget=budget or CycleBudget(),
    )


async def test_gate_decision_is_audited_and_resumes_the_workflow():
    cycle = _gated_cycle()
    repo = FakeCycleRepository([cycle])
    runner = FakeRunner()
    runner.states["wf-1"] = {
        "status": "running",
        "ranked_problems": [{"problem_id": "p0"}],
        "selected_problem_id": None,
    }
    service = ResearchCycleService(repo, runner, policy=_policy_all_human())

    updated = await service.decide_gate(
        "cycle-1",
        "wf-1",
        gate="select_problem",
        decision="approve",
        user_id="alice",
        note="looks tractable",
        now=NOW,
    )

    workflow = updated.workflow("wf-1")
    assert workflow is not None
    decision = workflow.gate_decisions[0]
    assert decision.decided_by == "alice"
    assert decision.note == "looks tractable"
    assert decision.decided_at == "2026-10-26T03:00:00Z"
    assert runner.resumes[0]["decision"] == "approve"
    assert workflow.pending_gate == "approve_proposal"


async def test_rejecting_the_evaluation_gate_never_reaches_synthesis():
    cycle = _gated_cycle()
    workflow = cycle.workflow_runs[0]
    workflow.pending_gate = "review_evaluation"
    workflow.problem_id = "p0"
    repo = FakeCycleRepository([cycle])
    runner = FakeRunner()
    runner.states["wf-1"] = {
        "status": "running",
        "selected_problem_id": "p0",
        "proposal": {"problem_id": "p0"},
        "proposal_approved": True,
        "evaluation_result": {"verdict": "inconclusive"},
        "evaluation_approved": False,
        "synthesis_report": None,
        "errors": [],
    }
    service = ResearchCycleService(repo, runner, policy=_policy_all_human())

    updated = await service.decide_gate(
        "cycle-1",
        "wf-1",
        gate="review_evaluation",
        decision="reject",
        user_id="alice",
        note="not convincing",
        now=NOW,
    )

    saved = updated.workflow("wf-1")
    assert saved is not None
    assert saved.state == "rejected"
    assert runner.resumes[0]["decision"] == "reject"
    assert runner.states["wf-1"].get("synthesis_report") is None


async def test_experiment_budget_refuses_an_approval():
    budget = CycleBudget(max_experiments=1, experiments=1)
    cycle = _gated_cycle(budget=budget)
    workflow = cycle.workflow_runs[0]
    workflow.pending_gate = "approve_proposal"
    repo = FakeCycleRepository([cycle])
    runner = FakeRunner()
    service = ResearchCycleService(repo, runner, policy=_policy_all_human())

    with pytest.raises(CycleBudgetExceeded):
        await service.decide_gate(
            "cycle-1",
            "wf-1",
            gate="approve_proposal",
            decision="approve",
            user_id="alice",
            now=NOW,
        )
    assert runner.resumes == []
    saved = repo.cycles["cycle-1"]
    assert saved.budget.stopped_by_budget is True
    assert "max_experiments=1" in (saved.note or "")


async def test_auto_policy_resolves_a_gate_without_a_human():
    repo = FakeCycleRepository()
    runner = FakeRunner()
    policy = parse_policy(
        {
            "version": "auto-select",
            "gates": {
                "select_problem": {"mode": "auto"},
                "approve_proposal": {"mode": "human"},
                "review_evaluation": {"mode": "human"},
            },
        }
    )
    result = await run_cycle(
        config=CycleConfig(enabled=True, top_k=1),
        cycles=repo,
        runner=runner,
        policy=policy,
        now=NOW,
        write_json=False,
        write_neo4j=False,
    )
    workflow = result.cycle.workflow_runs[0]
    assert workflow.pending_gate == "approve_proposal"
    assert workflow.gate_decisions[0].decision == "auto_approve"
    assert workflow.gate_decisions[0].decided_by == "policy:auto"
    assert result.cycle.policy_version == "auto-select"
