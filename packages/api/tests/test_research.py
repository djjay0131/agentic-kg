"""Tests for the research-cycle gate endpoints (``/api/research``)."""

from __future__ import annotations

import pytest
from agentic_kg.agents.autonomy import parse_policy
from agentic_kg.agents.gate_agreement import build_agreement_report
from agentic_kg.knowledge_graph.models.research_cycle import (
    AgentRecommendation,
    CycleBudget,
    CycleWorkflow,
    GateDecision,
    ResearchCycle,
)
from agentic_kg.knowledge_graph.research_cycles import ResearchCycleNotFoundError
from agentic_kg.pipeline.cycle import CycleBudgetExceeded
from agentic_kg_api import iap
from agentic_kg_api.config import reset_api_config
from agentic_kg_api.dependencies import get_research_cycles
from agentic_kg_api.main import app
from fastapi.testclient import TestClient

from .iap_test_support import AUDIENCE, KID, make_signer, make_token

PUBLIC_PEM, SIGNER = make_signer()


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

    def agreement(self, *, gate=None, since=None, last_n=10):
        cycle = make_cycle()
        recommendation = AgentRecommendation(
            action="approve", rationale="top-ranked p0", details={"problem_id": "p0"}
        )
        cycle.workflow_runs[0].gate_decisions.append(
            GateDecision(
                gate="select_problem",
                decision="approve",
                decided_by="alice",
                decided_at="2026-10-26T04:00:00Z",
                cycle_id="cycle-1",
                workflow_run_id="wf-1",
                policy_version="1",
                agent_recommendation=recommendation,
                human_decision="approve",
                agreed=True,
            )
        )
        policy = parse_policy(
            {
                "version": "1",
                "gates": {
                    "select_problem": {"mode": "human"},
                    "approve_proposal": {"mode": "human"},
                    "review_evaluation": {"mode": "human"},
                },
            }
        )
        return build_agreement_report(
            [cycle], policy=policy, gate=gate, since=since, last_n=last_n
        )

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


@pytest.fixture
def iap_mode(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "iap")
    monkeypatch.setenv("IAP_AUDIENCE", AUDIENCE)
    reset_api_config()
    iap.reset_iap_key_cache()
    monkeypatch.setattr(iap, "_fetch_iap_keys", lambda: {KID: PUBLIC_PEM})
    yield
    reset_api_config()
    iap.reset_iap_key_cache()


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
        "recommendation": None,
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


def test_no_assertion_is_401_in_iap_mode(client, iap_mode):
    response = client.post(
        "/api/research/gates/cycle-1/wf-1",
        headers={"X-User-ID": "alice"},
        json={"gate": "select_problem", "decision": "approve"},
    )
    assert response.status_code == 401


def test_decision_records_the_verified_email(client, service, iap_mode):
    token = make_token(SIGNER, email="tester@vt.edu")
    response = client.post(
        "/api/research/gates/cycle-1/wf-1",
        headers={"x-goog-iap-jwt-assertion": token},
        json={"gate": "select_problem", "decision": "approve", "note": "go"},
    )
    assert response.status_code == 200
    decision = response.json()["workflow_runs"][0]["gate_decisions"][0]
    assert decision["decided_by"] == "tester@vt.edu"
    assert service.calls[0][2]["user_id"] == "tester@vt.edu"


def test_agreement_endpoint_reports_rate_lower_bound_and_mode(client):
    response = client.get("/api/research/agreement")
    assert response.status_code == 200
    body = response.json()
    assert body["total_decisions"] == 1
    assert body["total_agreed"] == 1
    by_gate = {item["gate"]: item for item in body["gates"]}
    assert set(by_gate) == {
        "select_problem",
        "approve_proposal",
        "review_evaluation",
    }
    select = by_gate["select_problem"]
    assert select["mode"] == "human"
    assert select["decisions"] == 1
    assert select["agreed"] == 1
    assert select["agreement_rate"] == 1.0
    assert select["lower_bound"] == pytest.approx(0.2065, abs=1e-4)
    assert select["promotion_met"] is False  # no promotion criteria in this policy
    assert select["last_disagreements"] == []


def test_agreement_endpoint_filters_by_gate_and_rejects_unknown(client):
    filtered = client.get("/api/research/agreement?gate=select_problem")
    assert filtered.status_code == 200
    assert [item["gate"] for item in filtered.json()["gates"]] == [
        "select_problem"
    ]

    unknown = client.get("/api/research/agreement?gate=nope")
    assert unknown.status_code == 400
