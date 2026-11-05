"""Tests for the research-cycle gate endpoints (``/api/research``)."""

from __future__ import annotations

import pytest
from agentic_kg.knowledge_graph.models.research_cycle import (
    CycleBudget,
    CycleWorkflow,
    GateDecision,
    ResearchCycle,
)
from agentic_kg.knowledge_graph.research_cycles import ResearchCycleNotFoundError
from agentic_kg.pipeline.cycle import CycleBudgetExceeded
from agentic_kg_api.dependencies import get_research_cycles
from agentic_kg_api.main import app
from fastapi.testclient import TestClient


def make_cycle() -> ResearchCycle:
    return ResearchCycle(
        cycle_id="cycle-1",
        namespace="canon",
        started_at="2026-10-26T03:00:00Z",
        status="awaiting_gate",
        trigger="schedule",
        policy_version="1",
        selected_problems=["p0"],
        budget=CycleBudget(max_experiments=3, experiments=0),
        workflow_runs=[
            CycleWorkflow(
                workflow_run_id="wf-1",
                problem_id="p0",
                problem_statement="How to scale things",
                state="awaiting_gate",
                pending_gate="select_problem",
                started_at="2026-10-26T03:00:00Z",
                updated_at="2026-10-26T03:00:00Z",
            )
        ],
    )


class FakeService:
    def __init__(self):
        self.cycle = make_cycle()
        self.calls = []
        self.error: Exception | None = None

    def list(self, limit=50):
        return [self.cycle]

    def get(self, cycle_id):
        if cycle_id != self.cycle.cycle_id:
            raise ResearchCycleNotFoundError(cycle_id)
        return self.cycle

    def pending_gates(self, limit=50):
        return [self.cycle]

    async def decide_gate(self, cycle_id, workflow_run_id, **kwargs):
        self.calls.append((cycle_id, workflow_run_id, kwargs))
        if self.error is not None:
            raise self.error
        workflow = self.cycle.workflow(workflow_run_id)
        if workflow is None:
            raise ResearchCycleNotFoundError(workflow_run_id)
        record = GateDecision(
            gate=kwargs["gate"],
            decision=kwargs["decision"],
            decided_by=kwargs["user_id"],
            decided_at="2026-10-26T03:10:00Z",
            note=kwargs.get("note") or "",
            edited_data=kwargs.get("edited_data"),
        )
        workflow.gate_decisions.append(record)
        workflow.pending_gate = "approve_proposal"
        return self.cycle


@pytest.fixture
def service():
    return FakeService()


@pytest.fixture
def client(service):
    app.dependency_overrides[get_research_cycles] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_lists_cycles_and_pending_gates(client):
    cycles = client.get("/api/research/cycles")
    assert cycles.status_code == 200
    assert cycles.json()["cycles"][0]["cycle_id"] == "cycle-1"

    gates = client.get("/api/research/gates")
    assert gates.status_code == 200
    body = gates.json()
    assert body["total"] == 1
    assert body["gates"][0] == {
        "cycle_id": "cycle-1",
        "workflow_run_id": "wf-1",
        "problem_id": "p0",
        "problem_statement": "How to scale things",
        "gate": "select_problem",
        "updated_at": "2026-10-26T03:00:00Z",
    }


def test_unknown_cycle_is_404(client):
    assert client.get("/api/research/cycles/nope").status_code == 404


def test_decision_requires_a_named_user(client):
    response = client.post(
        "/api/research/gates/cycle-1/wf-1",
        json={"gate": "select_problem", "decision": "approve", "note": "ok"},
    )
    assert response.status_code == 401


def test_approve_records_the_decision_and_returns_the_cycle(client, service):
    response = client.post(
        "/api/research/gates/cycle-1/wf-1",
        headers={"X-User-ID": "alice"},
        json={"gate": "select_problem", "decision": "approve", "note": "go"},
    )
    assert response.status_code == 200
    body = response.json()
    decision = body["workflow_runs"][0]["gate_decisions"][0]
    assert decision["decided_by"] == "alice"
    assert decision["note"] == "go"
    assert body["workflow_runs"][0]["pending_gate"] == "approve_proposal"
    assert service.calls[0][2]["user_id"] == "alice"


def test_wrong_gate_is_409(client, service):
    service.error = ValueError("workflow wf-1 is waiting at select_problem")
    response = client.post(
        "/api/research/gates/cycle-1/wf-1",
        headers={"X-User-ID": "alice"},
        json={"gate": "approve_proposal", "decision": "approve"},
    )
    assert response.status_code == 409


def test_budget_refusal_is_409(client, service):
    service.error = CycleBudgetExceeded("max_experiments reached")
    response = client.post(
        "/api/research/gates/cycle-1/wf-1",
        headers={"X-User-ID": "alice"},
        json={"gate": "approve_proposal", "decision": "approve"},
    )
    assert response.status_code == 409
    assert "max_experiments" in response.json()["detail"]
