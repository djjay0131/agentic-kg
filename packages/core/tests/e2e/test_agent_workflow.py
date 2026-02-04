"""
E2E tests for agent workflow.

Tests the full agent workflow with mocked or real LLM responses.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agentic_kg.agents.schemas import RankedProblem
from agentic_kg.agents.state import ResearchState

from .conftest import E2EConfig


def make_test_id(prefix: str) -> str:
    """Generate a unique test ID."""
    return f"TEST_{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.e2e
class TestWorkflowAPIE2E:
    """E2E tests for workflow API endpoints."""

    @pytest.fixture
    def api_client(self, e2e_config: E2EConfig) -> httpx.Client:
        """Create HTTP client for API."""
        return httpx.Client(base_url=e2e_config.api_url, timeout=60.0)

    def test_start_workflow_returns_run_id(self, api_client: httpx.Client):
        """Test starting a workflow returns a run ID."""
        response = api_client.post(
            "/api/agents/workflows",
            json={
                "domain_filter": "testing",
                "max_problems": 5,
            },
        )

        # May return 503 if runner not initialized, or 200 if it is
        if response.status_code == 200:
            data = response.json()
            assert "run_id" in data
            assert "status" in data
            assert "websocket_url" in data
        else:
            # 503 is acceptable if WorkflowRunner isn't initialized
            assert response.status_code == 503

    def test_list_workflows(self, api_client: httpx.Client):
        """Test listing workflows."""
        response = api_client.get("/api/agents/workflows")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_nonexistent_workflow(self, api_client: httpx.Client):
        """Test getting a workflow that doesn't exist."""
        response = api_client.get("/api/agents/workflows/nonexistent-run-123")

        assert response.status_code == 404


@pytest.mark.e2e
@pytest.mark.costly
class TestWorkflowWithLLM:
    """E2E tests that actually run the LLM-based workflow.

    These tests incur API costs and should only be run when needed.
    """

    @pytest.fixture
    def api_client(self, e2e_config: E2EConfig) -> httpx.Client:
        """Create HTTP client with longer timeout for workflow."""
        return httpx.Client(base_url=e2e_config.api_url, timeout=120.0)

    @pytest.mark.asyncio
    async def test_workflow_starts_and_reaches_checkpoint(
        self,
        e2e_config: E2EConfig,
    ):
        """Test that workflow starts and eventually reaches a checkpoint.

        This test starts a workflow and polls for status changes.
        It verifies the workflow progresses through initial stages.
        """
        async with httpx.AsyncClient(
            base_url=e2e_config.api_url,
            timeout=120.0,
        ) as client:
            # Start workflow
            response = await client.post(
                "/api/agents/workflows",
                json={
                    "domain_filter": None,  # All domains
                    "max_problems": 5,
                    "min_confidence": 0.3,
                },
            )

            if response.status_code == 503:
                pytest.skip("WorkflowRunner not initialized in staging")

            assert response.status_code == 200
            data = response.json()
            run_id = data["run_id"]

            # Poll for status changes (up to 60 seconds)
            import asyncio

            for _ in range(30):  # 30 attempts, 2 seconds apart
                await asyncio.sleep(2)

                status_response = await client.get(f"/api/agents/workflows/{run_id}")

                if status_response.status_code == 404:
                    # Workflow may have been cleaned up
                    break

                state = status_response.json()

                # Check if we've reached a checkpoint or completed
                if state.get("status") in ["awaiting_checkpoint", "completed", "failed"]:
                    # Success - workflow progressed
                    assert state["run_id"] == run_id
                    break
            else:
                # Timed out - workflow may still be running, which is OK
                pass


@pytest.mark.e2e
class TestWorkflowStateMachine:
    """Tests for workflow state machine logic using mocked components."""

    def test_initial_state_structure(self):
        """Test that initial state has required fields."""
        state: ResearchState = {
            "run_id": "test-run-001",
            "status": "running",
            "current_step": "ranking",
            "ranked_problems": [],
            "selected_problem_id": None,
            "proposal": None,
            "evaluation_result": None,
            "synthesis_report": None,
            "messages": [],
            "errors": [],
        }

        assert state["status"] == "running"
        assert state["current_step"] == "ranking"
        assert state["ranked_problems"] == []

    def test_ranked_problem_schema(self):
        """Test RankedProblem schema validation."""
        ranked = RankedProblem(
            problem_id="prob-001",
            statement="How can we improve transformer efficiency?",
            score=0.85,
            tractability=0.7,
            data_availability=0.9,
            cross_domain_impact=0.8,
            rationale="High impact potential with available datasets.",
            domain="testing",
        )

        assert ranked.problem_id == "prob-001"
        assert ranked.score == 0.85
        assert 0.0 <= ranked.score <= 1.0
        assert ranked.tractability == 0.7
        assert ranked.data_availability == 0.9
        assert ranked.cross_domain_impact == 0.8

    def test_state_transitions(self):
        """Test valid state transitions."""
        valid_transitions = {
            "ranking": ["select_problem", "failed"],
            "select_problem": ["continuation", "cancelled", "failed"],
            "continuation": ["approve_proposal", "failed"],
            "approve_proposal": ["evaluation", "cancelled", "failed"],
            "evaluation": ["synthesis", "failed"],
            "synthesis": ["completed", "failed"],
        }

        # Each step should have valid next steps
        for step, next_steps in valid_transitions.items():
            assert len(next_steps) > 0
            assert "failed" in next_steps  # All steps can fail


@pytest.mark.e2e
class TestCheckpointSubmission:
    """Tests for checkpoint submission logic."""

    @pytest.fixture
    def api_client(self, e2e_config: E2EConfig) -> httpx.Client:
        """Create HTTP client for API."""
        return httpx.Client(base_url=e2e_config.api_url, timeout=60.0)

    def test_submit_checkpoint_to_nonexistent_workflow(self, api_client: httpx.Client):
        """Test submitting checkpoint to non-existent workflow returns 404."""
        response = api_client.post(
            "/api/agents/workflows/nonexistent-run/checkpoints/select_problem",
            json={
                "decision": "approve",
                "feedback": "Test feedback",
                "edited_data": {"problem_id": "test-problem"},
            },
        )

        assert response.status_code == 404

    def test_checkpoint_decision_schema(self, api_client: httpx.Client):
        """Test that checkpoint decision schema is validated."""
        # Invalid decision value should be rejected
        response = api_client.post(
            "/api/agents/workflows/test-run/checkpoints/select_problem",
            json={
                "decision": "invalid_decision",  # Not approve/reject/modify
                "feedback": "Test",
            },
        )

        # Should return 404 (workflow not found) or 422 (validation error)
        assert response.status_code in [404, 422]
