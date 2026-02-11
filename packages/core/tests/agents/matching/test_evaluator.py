"""
Unit tests for EvaluatorAgent.

Tests evaluation logic with mocked LLM responses to verify:
- APPROVE decisions for strong matches
- REJECT decisions for different problems
- ESCALATE decisions for uncertain cases
- Error handling for timeouts and invalid responses
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_kg.agents.matching.evaluator import (
    EvaluatorAgent,
    EvaluatorError,
    EvaluatorLLMResponse,
    create_evaluator_agent,
)
from agentic_kg.agents.matching.schemas import EvaluatorDecision
from agentic_kg.agents.matching.state import create_matching_state


# =============================================================================
# Mock LLM Client
# =============================================================================


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, response: Optional[EvaluatorLLMResponse] = None):
        self.response = response
        self.extract = AsyncMock(side_effect=self._extract)
        self.call_count = 0
        self.last_prompt = None
        self.last_system_prompt = None

    async def _extract(
        self,
        prompt: str,
        response_model: type,
        system_prompt: Optional[str] = None,
    ):
        """Mock extract method."""
        self.call_count += 1
        self.last_prompt = prompt
        self.last_system_prompt = system_prompt

        if self.response is None:
            raise ValueError("No mock response configured")

        return MagicMock(content=self.response)


class ErrorLLMClient:
    """Mock LLM client that raises errors."""

    def __init__(self, error: Exception):
        self.error = error
        self.extract = AsyncMock(side_effect=error)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def approve_response() -> EvaluatorLLMResponse:
    """Mock response for APPROVE decision."""
    return EvaluatorLLMResponse(
        decision="approve",
        confidence=0.92,
        reasoning="The problems are semantically equivalent. Both address gradient "
        "vanishing in deep networks with similar scope and constraints.",
        key_factors=[
            "Same core problem",
            "Matching domain",
            "Compatible scope",
        ],
        similarity_assessment="High semantic overlap with matching technical terminology",
        domain_match=True,
    )


@pytest.fixture
def reject_response() -> EvaluatorLLMResponse:
    """Mock response for REJECT decision."""
    return EvaluatorLLMResponse(
        decision="reject",
        confidence=0.88,
        reasoning="While both mention optimization, the mention focuses on hyperparameter "
        "tuning while the concept addresses convergence speed. Different problems.",
        key_factors=[
            "Different problem scope",
            "Different optimization targets",
            "Surface-level keyword overlap only",
        ],
        similarity_assessment="Keywords overlap but different underlying problems",
        domain_match=True,
    )


@pytest.fixture
def escalate_response() -> EvaluatorLLMResponse:
    """Mock response for ESCALATE decision."""
    return EvaluatorLLMResponse(
        decision="escalate",
        confidence=0.55,
        reasoning="Unclear whether these are the same problem. The mention discusses "
        "a specific application while the concept is more general. Need more analysis.",
        key_factors=[
            "Ambiguous scope relationship",
            "Partial overlap",
            "Context-dependent interpretation",
        ],
        similarity_assessment="Moderate similarity with unclear boundaries",
        domain_match=True,
    )


@pytest.fixture
def sample_state() -> dict[str, Any]:
    """Create a sample workflow state for testing."""
    return create_matching_state(
        mention_id="mention-123",
        mention_statement="How to prevent gradient vanishing in deep neural networks?",
        mention_embedding=[0.1] * 1536,  # Dummy embedding
        candidate_concept_id="concept-456",
        candidate_statement="Gradient vanishing problem in deep learning architectures",
        similarity_score=0.87,
        paper_doi="10.1234/paper.123",
        mention_domain="deep_learning",
        trace_id="test-trace-001",
    )


# =============================================================================
# Test: APPROVE Decision
# =============================================================================


@pytest.mark.asyncio
async def test_evaluate_approve(approve_response, sample_state):
    """Test that APPROVE decision is correctly returned."""
    llm = MockLLMClient(approve_response)
    agent = EvaluatorAgent(llm_client=llm)

    # Add candidate info to state
    sample_state["candidate_domain"] = "deep_learning"
    sample_state["candidate_mention_count"] = 5

    updated_state, result = await agent.evaluate(sample_state)

    # Verify decision
    assert result.decision == EvaluatorDecision.APPROVE
    assert result.confidence == 0.92
    assert "semantically equivalent" in result.reasoning
    assert len(result.key_factors) == 3

    # Verify state updates
    assert updated_state["evaluator_decision"] == "approve"
    assert updated_state["evaluator_result"] is not None
    assert updated_state["current_step"] == "evaluator_complete"

    # Verify LLM was called
    assert llm.call_count == 1
    assert "gradient vanishing" in llm.last_prompt.lower()


@pytest.mark.asyncio
async def test_evaluate_approve_logs_trace_id(approve_response, sample_state, caplog):
    """Test that trace ID is included in logs."""
    llm = MockLLMClient(approve_response)
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"
    sample_state["candidate_mention_count"] = 5

    await agent.evaluate(sample_state)

    # Check trace ID in logs
    assert "test-trace-001" in caplog.text


# =============================================================================
# Test: REJECT Decision
# =============================================================================


@pytest.mark.asyncio
async def test_evaluate_reject(reject_response, sample_state):
    """Test that REJECT decision is correctly returned."""
    llm = MockLLMClient(reject_response)
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["candidate_domain"] = "optimization"
    sample_state["candidate_statement"] = "Improving optimizer convergence speed"

    updated_state, result = await agent.evaluate(sample_state)

    # Verify decision
    assert result.decision == EvaluatorDecision.REJECT
    assert result.confidence == 0.88
    assert "Different problems" in result.reasoning

    # Verify state updates
    assert updated_state["evaluator_decision"] == "reject"


# =============================================================================
# Test: ESCALATE Decision
# =============================================================================


@pytest.mark.asyncio
async def test_evaluate_escalate(escalate_response, sample_state):
    """Test that ESCALATE decision is correctly returned."""
    llm = MockLLMClient(escalate_response)
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"
    sample_state["similarity_score"] = 0.82  # Borderline MEDIUM

    updated_state, result = await agent.evaluate(sample_state)

    # Verify decision
    assert result.decision == EvaluatorDecision.ESCALATE
    assert result.confidence == 0.55
    assert "Need more analysis" in result.reasoning

    # Verify state updates
    assert updated_state["evaluator_decision"] == "escalate"


# =============================================================================
# Test: Unknown Decision Handling
# =============================================================================


@pytest.mark.asyncio
async def test_unknown_decision_defaults_to_escalate(sample_state):
    """Test that unknown decisions default to ESCALATE."""
    # LLM returns an invalid decision
    invalid_response = EvaluatorLLMResponse(
        decision="maybe",  # Invalid
        confidence=0.7,
        reasoning="Not sure what to do",
        key_factors=["uncertainty"],
    )
    llm = MockLLMClient(invalid_response)
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"

    updated_state, result = await agent.evaluate(sample_state)

    # Should default to ESCALATE
    assert result.decision == EvaluatorDecision.ESCALATE


# =============================================================================
# Test: Edge Cases
# =============================================================================


@pytest.mark.asyncio
async def test_empty_mention_statement_raises_error(sample_state):
    """Test that empty mention statement raises EvaluatorError."""
    llm = MockLLMClient(None)
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["mention_statement"] = ""  # Empty

    with pytest.raises(EvaluatorError) as exc_info:
        await agent.evaluate(sample_state)

    assert "Empty mention statement" in str(exc_info.value)


@pytest.mark.asyncio
async def test_empty_candidate_statement_raises_error(sample_state):
    """Test that empty candidate statement raises EvaluatorError."""
    llm = MockLLMClient(None)
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["candidate_statement"] = ""  # Empty

    with pytest.raises(EvaluatorError) as exc_info:
        await agent.evaluate(sample_state)

    assert "Empty candidate statement" in str(exc_info.value)


@pytest.mark.asyncio
async def test_missing_optional_fields(approve_response, sample_state):
    """Test that missing optional fields don't cause errors."""
    llm = MockLLMClient(approve_response)
    agent = EvaluatorAgent(llm_client=llm)

    # Remove optional fields
    sample_state["mention_domain"] = None
    sample_state["paper_doi"] = None
    sample_state["candidate_domain"] = None
    sample_state["candidate_mention_count"] = 0

    # Should not raise
    updated_state, result = await agent.evaluate(sample_state)
    assert result.decision == EvaluatorDecision.APPROVE


# =============================================================================
# Test: Error Handling
# =============================================================================


@pytest.mark.asyncio
async def test_llm_timeout_raises_error(sample_state):
    """Test that LLM timeout raises EvaluatorError."""
    llm = ErrorLLMClient(TimeoutError("Request timed out"))
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"

    with pytest.raises(EvaluatorError) as exc_info:
        await agent.evaluate(sample_state)

    assert "timed out" in str(exc_info.value)


@pytest.mark.asyncio
async def test_llm_api_error_raises_error(sample_state):
    """Test that LLM API errors raise EvaluatorError."""
    llm = ErrorLLMClient(RuntimeError("API rate limit exceeded"))
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"

    with pytest.raises(EvaluatorError) as exc_info:
        await agent.evaluate(sample_state)

    assert "failed" in str(exc_info.value).lower()


# =============================================================================
# Test: run() Method (LangGraph Node)
# =============================================================================


@pytest.mark.asyncio
async def test_run_returns_updated_state(approve_response, sample_state):
    """Test that run() returns updated state for LangGraph."""
    llm = MockLLMClient(approve_response)
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"

    updated_state = await agent.run(sample_state)

    assert updated_state["evaluator_decision"] == "approve"
    assert updated_state["current_step"] == "evaluator_complete"


@pytest.mark.asyncio
async def test_run_handles_errors_gracefully(sample_state):
    """Test that run() returns error state instead of raising."""
    llm = ErrorLLMClient(RuntimeError("API error"))
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"

    # Should not raise, but return error state
    updated_state = await agent.run(sample_state)

    assert updated_state["evaluator_decision"] == "escalate"
    assert updated_state["current_step"] == "evaluator_error"


# =============================================================================
# Test: Factory Function
# =============================================================================


def test_create_evaluator_agent():
    """Test the factory function creates agent with defaults."""
    llm = MockLLMClient(None)
    agent = create_evaluator_agent(llm_client=llm, model="gpt-4o")

    assert agent.model == "gpt-4o"
    assert agent.temperature == 0.2
    assert agent.max_tokens == 1024
    assert agent.timeout == 10.0


# =============================================================================
# Test: Prompt Content
# =============================================================================


@pytest.mark.asyncio
async def test_prompt_includes_all_context(approve_response, sample_state):
    """Test that the prompt includes all relevant context."""
    llm = MockLLMClient(approve_response)
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["mention_domain"] = "deep_learning"
    sample_state["candidate_domain"] = "neural_networks"
    sample_state["candidate_mention_count"] = 10
    sample_state["similarity_score"] = 0.89

    await agent.evaluate(sample_state)

    prompt = llm.last_prompt

    # Check all context is in prompt
    assert "gradient vanishing" in prompt.lower()
    assert "deep_learning" in prompt
    assert "neural_networks" in prompt
    assert "10 mentions" in prompt
    assert "89" in prompt  # 89% similarity


@pytest.mark.asyncio
async def test_system_prompt_emphasizes_approve(approve_response, sample_state):
    """Test that system prompt emphasizes APPROVE over REJECT."""
    llm = MockLLMClient(approve_response)
    agent = EvaluatorAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"

    await agent.evaluate(sample_state)

    system = llm.last_system_prompt

    # System prompt should emphasize false negative avoidance
    assert "err on the side of approve" in system.lower()
    assert "missing a duplicate is worse" in system.lower()


# =============================================================================
# Test: Decision Parsing
# =============================================================================


def test_parse_decision_approve():
    """Test decision parsing for approve."""
    llm = MockLLMClient(None)
    agent = EvaluatorAgent(llm_client=llm)

    assert agent._parse_decision("approve") == EvaluatorDecision.APPROVE
    assert agent._parse_decision("APPROVE") == EvaluatorDecision.APPROVE
    assert agent._parse_decision("  Approve  ") == EvaluatorDecision.APPROVE


def test_parse_decision_reject():
    """Test decision parsing for reject."""
    llm = MockLLMClient(None)
    agent = EvaluatorAgent(llm_client=llm)

    assert agent._parse_decision("reject") == EvaluatorDecision.REJECT
    assert agent._parse_decision("REJECT") == EvaluatorDecision.REJECT


def test_parse_decision_escalate():
    """Test decision parsing for escalate."""
    llm = MockLLMClient(None)
    agent = EvaluatorAgent(llm_client=llm)

    assert agent._parse_decision("escalate") == EvaluatorDecision.ESCALATE
    assert agent._parse_decision("ESCALATE") == EvaluatorDecision.ESCALATE


def test_parse_decision_unknown():
    """Test decision parsing for unknown values defaults to escalate."""
    llm = MockLLMClient(None)
    agent = EvaluatorAgent(llm_client=llm)

    assert agent._parse_decision("maybe") == EvaluatorDecision.ESCALATE
    assert agent._parse_decision("unsure") == EvaluatorDecision.ESCALATE
    assert agent._parse_decision("") == EvaluatorDecision.ESCALATE
