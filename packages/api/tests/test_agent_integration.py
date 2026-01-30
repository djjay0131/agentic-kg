"""
Integration tests for agent workflow API endpoints.

Tests the full flow: API router → WorkflowRunner → state management,
with mocked LLM and Neo4j but real FastAPI + runner wiring.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from agentic_kg_api.main import app
from agentic_kg_api.routers import agents


@pytest.fixture
def mock_runner():
    """Create a mock WorkflowRunner."""
    runner = AsyncMock()
    runner.start_workflow = AsyncMock(return_value="test-run-001")
    runner.list_workflows = MagicMock(
        return_value=[
            {
                "run_id": "test-run-001",
                "status": "running",
                "current_step": "ranking",
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-01T00:00:01Z",
            }
        ]
    )
    runner.get_state = AsyncMock(
        return_value={
            "run_id": "test-run-001",
            "status": "awaiting_checkpoint",
            "current_step": "select_problem",
            "ranked_problems": [
                {"problem_id": "p1", "score": 0.9, "rationale": "High impact"}
            ],
            "selected_problem_id": None,
            "proposal": None,
            "evaluation_result": None,
            "synthesis_report": None,
            "messages": [],
            "errors": [],
        }
    )
    runner.resume_workflow = AsyncMock(
        return_value={
            "run_id": "test-run-001",
            "status": "running",
            "current_step": "continuation",
            "ranked_problems": [{"problem_id": "p1", "score": 0.9}],
            "selected_problem_id": "p1",
            "proposal": None,
            "evaluation_result": None,
            "synthesis_report": None,
            "messages": [],
            "errors": [],
        }
    )
    runner.cancel_workflow = AsyncMock()
    return runner


@pytest.fixture
def client_with_runner(mock_runner):
    """Create a test client with a mock runner wired in."""
    agents.set_workflow_runner(mock_runner)
    yield TestClient(app)
    agents.set_workflow_runner(None)


class TestStartWorkflow:
    def test_start_workflow_success(self, client_with_runner, mock_runner):
        resp = client_with_runner.post(
            "/api/agents/workflows",
            json={"domain_filter": "NLP"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "test-run-001"
        assert data["status"] == "running"
        assert "websocket_url" in data
        mock_runner.start_workflow.assert_awaited_once()

    def test_start_workflow_default_params(self, client_with_runner, mock_runner):
        resp = client_with_runner.post("/api/agents/workflows", json={})
        assert resp.status_code == 200
        mock_runner.start_workflow.assert_awaited_once_with(
            domain_filter=None,
            status_filter=None,
            max_problems=20,
            min_confidence=0.3,
        )

    def test_start_workflow_runner_unavailable(self):
        agents.set_workflow_runner(None)
        client = TestClient(app)
        resp = client.post("/api/agents/workflows", json={})
        assert resp.status_code == 503


class TestListWorkflows:
    def test_list_workflows(self, client_with_runner, mock_runner):
        resp = client_with_runner.get("/api/agents/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["run_id"] == "test-run-001"
        assert data[0]["status"] == "running"


class TestGetWorkflow:
    def test_get_workflow_state(self, client_with_runner, mock_runner):
        resp = client_with_runner.get("/api/agents/workflows/test-run-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "test-run-001"
        assert data["current_step"] == "select_problem"
        assert len(data["ranked_problems"]) == 1

    def test_get_workflow_not_found(self, client_with_runner, mock_runner):
        mock_runner.get_state.side_effect = KeyError("not found")
        resp = client_with_runner.get("/api/agents/workflows/nonexistent")
        assert resp.status_code == 404


class TestSubmitCheckpoint:
    def test_approve_checkpoint(self, client_with_runner, mock_runner):
        resp = client_with_runner.post(
            "/api/agents/workflows/test-run-001/checkpoints/select_problem",
            json={
                "decision": "approve",
                "feedback": "Looks good",
                "edited_data": {"problem_id": "p1"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["selected_problem_id"] == "p1"
        mock_runner.resume_workflow.assert_awaited_once()

    def test_reject_checkpoint(self, client_with_runner, mock_runner):
        resp = client_with_runner.post(
            "/api/agents/workflows/test-run-001/checkpoints/approve_proposal",
            json={"decision": "reject", "feedback": "Not feasible"},
        )
        assert resp.status_code == 200


class TestCancelWorkflow:
    def test_cancel_workflow(self, client_with_runner, mock_runner):
        resp = client_with_runner.delete("/api/agents/workflows/test-run-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        mock_runner.cancel_workflow.assert_awaited_once_with("test-run-001")

    def test_cancel_workflow_not_found(self, client_with_runner, mock_runner):
        mock_runner.cancel_workflow.side_effect = KeyError("not found")
        resp = client_with_runner.delete("/api/agents/workflows/nonexistent")
        assert resp.status_code == 404
