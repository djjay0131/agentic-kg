"""
Unit tests for the matching workflow.

Tests the LangGraph workflow that orchestrates matching agents.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_kg.agents.matching.schemas import (
    ArbiterDecision,
    EscalationReason,
    EvaluatorDecision,
)
from agentic_kg.agents.matching.state import create_matching_state
from agentic_kg.agents.matching.workflow import (
    MAX_CONSENSUS_ROUNDS,
    build_matching_workflow,
    create_arbiter_node,
    create_evaluator_node,
    create_hater_node,
    create_human_review_node,
    create_link_node,
    create_maker_node,
    create_new_node,
    get_matching_workflow,
    reset_matching_workflow,
    route_arbiter_decision,
    route_by_confidence,
    route_evaluator_decision,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_state() -> dict[str, Any]:
    """Create sample workflow state."""
    state = create_matching_state(
        mention_id="mention-123",
        mention_statement="How to prevent gradient vanishing?",
        mention_embedding=[0.1] * 1536,
        candidate_concept_id="concept-456",
        candidate_statement="Gradient vanishing problem",
        similarity_score=0.72,
        paper_doi="10.1234/paper",
        mention_domain="deep_learning",
        trace_id="test-trace-001",
    )
    state["initial_confidence"] = "low"
    state["max_rounds"] = 3
    return state


@pytest.fixture
def mock_evaluator():
    """Create mock EvaluatorAgent."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value={
        "evaluator_result": {"decision": "approve", "confidence": 0.9},
        "evaluator_decision": "approve",
        "messages": [],
    })
    return agent


@pytest.fixture
def mock_maker():
    """Create mock MakerAgent."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value={
        "maker_results": [{"arguments": [], "confidence": 0.8}],
        "messages": [],
    })
    return agent


@pytest.fixture
def mock_hater():
    """Create mock HaterAgent."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value={
        "hater_results": [{"arguments": [], "confidence": 0.5}],
        "messages": [],
    })
    return agent


@pytest.fixture
def mock_arbiter():
    """Create mock ArbiterAgent."""
    agent = MagicMock()
    agent.run = AsyncMock(return_value={
        "arbiter_results": [{"decision": "link", "confidence": 0.85}],
        "consensus_reached": True,
        "final_confidence": 0.85,
        "messages": [],
    })
    return agent


# =============================================================================
# Routing Function Tests
# =============================================================================


class TestRouteByConfidence:
    """Tests for route_by_confidence function."""

    def test_routes_medium_to_evaluator(self, sample_state):
        """MEDIUM confidence routes to evaluator."""
        sample_state["initial_confidence"] = "medium"
        assert route_by_confidence(sample_state) == "evaluator"

    def test_routes_low_to_maker(self, sample_state):
        """LOW confidence routes to maker."""
        sample_state["initial_confidence"] = "low"
        assert route_by_confidence(sample_state) == "maker"

    def test_routes_high_to_end(self, sample_state):
        """HIGH confidence routes to end (handled by Phase 1)."""
        sample_state["initial_confidence"] = "high"
        assert route_by_confidence(sample_state) == "end"

    def test_routes_unknown_to_end(self, sample_state):
        """Unknown confidence routes to end."""
        sample_state["initial_confidence"] = ""
        assert route_by_confidence(sample_state) == "end"


class TestRouteEvaluatorDecision:
    """Tests for route_evaluator_decision function."""

    def test_approve_routes_to_link(self, sample_state):
        """APPROVE routes to link."""
        sample_state["evaluator_decision"] = "approve"
        assert route_evaluator_decision(sample_state) == "link"

    def test_reject_routes_to_create_new(self, sample_state):
        """REJECT routes to create_new."""
        sample_state["evaluator_decision"] = "reject"
        assert route_evaluator_decision(sample_state) == "create_new"

    def test_escalate_routes_to_maker(self, sample_state):
        """ESCALATE routes to maker for consensus."""
        sample_state["evaluator_decision"] = "escalate"
        assert route_evaluator_decision(sample_state) == "maker"

    def test_missing_decision_routes_to_maker(self, sample_state):
        """Missing decision defaults to maker (escalate)."""
        sample_state["evaluator_decision"] = None
        assert route_evaluator_decision(sample_state) == "maker"


class TestRouteArbiterDecision:
    """Tests for route_arbiter_decision function."""

    def test_link_decision_routes_to_link(self, sample_state):
        """LINK decision routes to link."""
        sample_state["arbiter_results"] = [{"decision": "link", "confidence": 0.85}]
        sample_state["current_round"] = 1
        assert route_arbiter_decision(sample_state) == "link"

    def test_create_new_decision_routes_to_create_new(self, sample_state):
        """CREATE_NEW decision routes to create_new."""
        sample_state["arbiter_results"] = [{"decision": "create_new", "confidence": 0.82}]
        sample_state["current_round"] = 1
        assert route_arbiter_decision(sample_state) == "create_new"

    def test_retry_routes_to_maker(self, sample_state):
        """RETRY routes to maker for another round."""
        sample_state["arbiter_results"] = [{"decision": "retry", "confidence": 0.55}]
        sample_state["current_round"] = 1
        assert route_arbiter_decision(sample_state) == "maker"

    def test_retry_max_rounds_routes_to_human(self, sample_state):
        """RETRY at max rounds routes to human_review."""
        sample_state["arbiter_results"] = [{"decision": "retry", "confidence": 0.55}]
        sample_state["current_round"] = 3
        sample_state["max_rounds"] = 3
        assert route_arbiter_decision(sample_state) == "human_review"

    def test_missing_results_routes_to_human(self, sample_state):
        """Missing arbiter results routes to human_review."""
        sample_state["arbiter_results"] = []
        assert route_arbiter_decision(sample_state) == "human_review"


# =============================================================================
# Node Function Tests
# =============================================================================


class TestEvaluatorNode:
    """Tests for evaluator node."""

    @pytest.mark.asyncio
    async def test_evaluator_node_runs_agent(self, sample_state, mock_evaluator):
        """Evaluator node runs the agent and returns result."""
        node = create_evaluator_node(mock_evaluator)
        result = await node(sample_state)

        mock_evaluator.run.assert_called_once()
        assert result["evaluator_decision"] == "approve"
        assert result["current_step"] == "evaluator_complete"


class TestMakerNode:
    """Tests for maker node."""

    @pytest.mark.asyncio
    async def test_maker_node_increments_round(self, sample_state, mock_maker):
        """Maker node increments round counter."""
        sample_state["current_round"] = 0
        node = create_maker_node(mock_maker)
        result = await node(sample_state)

        assert result["current_round"] == 1
        assert result["current_step"] == "maker_complete"


class TestHaterNode:
    """Tests for hater node."""

    @pytest.mark.asyncio
    async def test_hater_node_runs_agent(self, sample_state, mock_hater):
        """Hater node runs the agent."""
        node = create_hater_node(mock_hater)
        result = await node(sample_state)

        mock_hater.run.assert_called_once()
        assert result["current_step"] == "hater_complete"


class TestArbiterNode:
    """Tests for arbiter node."""

    @pytest.mark.asyncio
    async def test_arbiter_node_runs_agent(self, sample_state, mock_arbiter):
        """Arbiter node runs the agent and tracks consensus."""
        node = create_arbiter_node(mock_arbiter)
        result = await node(sample_state)

        mock_arbiter.run.assert_called_once()
        assert result["consensus_reached"] is True
        assert result["current_step"] == "arbiter_complete"


class TestLinkNode:
    """Tests for link node."""

    @pytest.mark.asyncio
    async def test_link_node_marks_decision(self, sample_state):
        """Link node marks decision as linked."""
        node = create_link_node()
        result = await node(sample_state)

        assert result["final_decision"] == "linked"
        assert result["status"] == "completed"


class TestCreateNewNode:
    """Tests for create_new node."""

    @pytest.mark.asyncio
    async def test_create_new_node_marks_decision(self, sample_state):
        """Create new node marks decision as created_new."""
        node = create_new_node()
        result = await node(sample_state)

        assert result["final_decision"] == "created_new"
        assert result["status"] == "completed"


class TestHumanReviewNode:
    """Tests for human_review node."""

    @pytest.mark.asyncio
    async def test_human_review_node_escalates(self, sample_state):
        """Human review node marks for escalation."""
        node = create_human_review_node()
        result = await node(sample_state)

        assert result["status"] == "escalated"
        assert result["escalated"] is True

    @pytest.mark.asyncio
    async def test_human_review_max_rounds_reason(self, sample_state):
        """Human review sets correct reason for max rounds."""
        sample_state["current_round"] = 3
        node = create_human_review_node()
        result = await node(sample_state)

        assert result["escalation_reason"] == EscalationReason.MAX_ROUNDS_EXCEEDED.value

    @pytest.mark.asyncio
    async def test_human_review_evaluator_uncertain_reason(self, sample_state):
        """Human review sets correct reason for evaluator escalation."""
        sample_state["current_round"] = 0
        sample_state["evaluator_decision"] = "escalate"
        node = create_human_review_node()
        result = await node(sample_state)

        assert result["escalation_reason"] == EscalationReason.EVALUATOR_UNCERTAIN.value


# =============================================================================
# Workflow Builder Tests
# =============================================================================


class TestBuildMatchingWorkflow:
    """Tests for build_matching_workflow function."""

    def test_builds_workflow_with_nodes(
        self, mock_evaluator, mock_maker, mock_hater, mock_arbiter
    ):
        """Workflow builder creates graph with all nodes."""
        workflow = build_matching_workflow(
            evaluator=mock_evaluator,
            maker=mock_maker,
            hater=mock_hater,
            arbiter=mock_arbiter,
        )

        # Workflow should be compiled (has invoke method)
        assert hasattr(workflow, "ainvoke")


class TestWorkflowSingleton:
    """Tests for workflow singleton."""

    def test_reset_clears_singleton(self):
        """Reset clears the singleton instance."""
        reset_matching_workflow()
        # After reset, calling get without agents should raise
        with pytest.raises(ValueError):
            get_matching_workflow()

    def test_get_requires_agents_first_time(self):
        """First call must provide all agents."""
        reset_matching_workflow()
        with pytest.raises(ValueError) as exc_info:
            get_matching_workflow()
        assert "must provide all agents" in str(exc_info.value)


# =============================================================================
# Constants Tests
# =============================================================================


def test_max_consensus_rounds():
    """MAX_CONSENSUS_ROUNDS is set correctly."""
    assert MAX_CONSENSUS_ROUNDS == 3
