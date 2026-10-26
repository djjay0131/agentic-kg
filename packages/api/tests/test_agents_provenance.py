"""GET /api/agents/outputs/{id}/provenance (ADR-0010)."""

from __future__ import annotations

import pytest
from agentic_kg.agents.graph_port import SOURCE_CANONICAL, SOURCE_LEGACY
from agentic_kg.agents.provenance import (
    InMemoryAgentOutputStore,
    build_provenance,
    record_agent_output,
)
from agentic_kg_api.dependencies import get_agent_outputs
from agentic_kg_api.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def store():
    return InMemoryAgentOutputStore()


@pytest.fixture
def client(store):
    app.dependency_overrides[get_agent_outputs] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.pop(get_agent_outputs, None)


def _output(store, *, source):
    provenance = build_provenance(
        agent="ranking",
        run_id="run-1",
        source=source,
        assertion_ids=["a1"] if source == SOURCE_CANONICAL else [],
        assertion_evidence={"a1": ["ev1"]},
        evidence_ids=["ev1"],
        rationale="ranked on evidence",
        model="test-model",
    )
    return record_agent_output(
        store,
        agent="ranking",
        run_id="run-1",
        source=source,
        output_kind="ranked_problem",
        payload={"score": 0.9},
        provenance=provenance,
        model="test-model",
        prompt_version="v1",
    )


def test_unknown_output_is_404(client):
    response = client.get("/api/agents/outputs/agentout_missing/provenance")
    assert response.status_code == 404


def test_legacy_output_returns_an_abstaining_envelope(client, store):
    output_id = _output(store, source=SOURCE_LEGACY)
    response = client.get(f"/api/agents/outputs/{output_id}/provenance")
    assert response.status_code == 200
    body = response.json()
    assert body["output_id"] == output_id
    assert body["agent"] == "ranking"
    assert body["source"] == "legacy"
    assert body["provenance"]["abstained"] is True
    assert body["provenance"]["citations"] == []
    assert body["explanations"] == []
    assert body["canonical_available"] is True


def test_canonical_output_exposes_citations_and_lineage(client, store):
    output_id = _output(store, source=SOURCE_CANONICAL)
    response = client.get(f"/api/agents/outputs/{output_id}/provenance")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "canonical"
    assert body["provenance"]["citations"][0]["assertion_id"] == "a1"
    assert body["provenance"]["citations"][0]["evidence_ids"] == ["ev1"]
    # The endpoint reports whether the KGPS services were reachable instead
    # of hiding a missing optional extra.
    assert isinstance(body["canonical_available"], bool)


def test_explanations_are_included_when_the_canonical_reader_answers(client, store, monkeypatch):
    from agentic_kg_api.routers import agents as agents_router

    output_id = _output(store, source=SOURCE_CANONICAL)
    monkeypatch.setattr(
        agents_router,
        "_canonical_explanations",
        lambda ids: (True, None, [{"assertion_id": ids[0], "summary": "because"}]),
    )
    response = client.get(f"/api/agents/outputs/{output_id}/provenance")
    assert response.status_code == 200
    assert response.json()["explanations"][0]["summary"] == "because"
