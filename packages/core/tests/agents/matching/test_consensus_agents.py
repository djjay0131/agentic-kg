"""
Unit tests for consensus agents (Maker, Hater, Arbiter).

Tests the Maker/Hater/Arbiter debate pattern for LOW confidence matches.
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_kg.agents.matching.maker import (
    MakerAgent,
    MakerError,
    MakerLLMResponse,
    create_maker_agent,
)
from agentic_kg.agents.matching.hater import (
    HaterAgent,
    HaterError,
    HaterLLMResponse,
    create_hater_agent,
)
from agentic_kg.agents.matching.arbiter import (
    ArbiterAgent,
    ArbiterError,
    ArbiterLLMResponse,
    ArbiterDecision,
    ARBITER_CONFIDENCE_THRESHOLD,
    create_arbiter_agent,
    format_arguments,
)
from agentic_kg.agents.matching.state import create_matching_state


# =============================================================================
# Mock LLM Client
# =============================================================================


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, response: Optional[Any] = None):
        self.response = response
        self.extract = AsyncMock(side_effect=self._extract)
        self.call_count = 0
        self.last_prompt = None

    async def _extract(self, prompt: str, response_model: type, system_prompt: Optional[str] = None):
        self.call_count += 1
        self.last_prompt = prompt
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
def sample_state() -> dict[str, Any]:
    """Create sample workflow state for testing."""
    return create_matching_state(
        mention_id="mention-123",
        mention_statement="How to prevent gradient vanishing in deep neural networks?",
        mention_embedding=[0.1] * 1536,
        candidate_concept_id="concept-456",
        candidate_statement="Gradient vanishing problem in deep learning architectures",
        similarity_score=0.72,  # LOW confidence
        paper_doi="10.1234/paper.123",
        mention_domain="deep_learning",
        trace_id="test-trace-001",
    )


@pytest.fixture
def maker_response() -> MakerLLMResponse:
    """Mock response for MakerAgent."""
    return MakerLLMResponse(
        arguments=[
            {"claim": "Both address vanishing gradients", "evidence": "Identical problem", "strength": 0.9},
            {"claim": "Same domain (deep learning)", "evidence": "Both mention neural networks", "strength": 0.8},
            {"claim": "Methodology overlap", "evidence": "Both focus on training stability", "strength": 0.7},
        ],
        confidence=0.85,
        strongest_argument="Both explicitly address vanishing gradient problem in deep networks",
        semantic_similarity_evidence="High overlap in technical terminology",
        domain_alignment_evidence="Both in deep learning domain",
    )


@pytest.fixture
def hater_response() -> HaterLLMResponse:
    """Mock response for HaterAgent."""
    return HaterLLMResponse(
        arguments=[
            {"claim": "Scope difference", "evidence": "Mention is more specific", "strength": 0.6},
            {"claim": "Framing difference", "evidence": "One is question, other is statement", "strength": 0.4},
        ],
        confidence=0.45,
        strongest_argument="The mention frames it as prevention, concept is about diagnosis",
        semantic_difference_evidence="Minor framing differences",
        domain_mismatch_evidence="None - same domain",
    )


@pytest.fixture
def arbiter_link_response() -> ArbiterLLMResponse:
    """Mock Arbiter response that decides to LINK."""
    return ArbiterLLMResponse(
        decision="link",
        confidence=0.88,
        reasoning="Maker arguments are more compelling. Both clearly address the same problem.",
        maker_weight=0.85,
        hater_weight=0.35,
        decisive_factor="Semantic equivalence outweighs minor framing differences",
        false_negative_risk="Low - these are clearly the same problem",
    )


@pytest.fixture
def arbiter_retry_response() -> ArbiterLLMResponse:
    """Mock Arbiter response that requests RETRY."""
    return ArbiterLLMResponse(
        decision="retry",
        confidence=0.55,
        reasoning="Arguments are closely balanced. Need more analysis.",
        maker_weight=0.55,
        hater_weight=0.50,
        decisive_factor="Cannot determine with confidence",
        false_negative_risk="Uncertain",
    )


@pytest.fixture
def arbiter_create_new_response() -> ArbiterLLMResponse:
    """Mock Arbiter response that decides to CREATE_NEW."""
    return ArbiterLLMResponse(
        decision="create_new",
        confidence=0.82,
        reasoning="Hater identified genuine scope differences that matter.",
        maker_weight=0.40,
        hater_weight=0.75,
        decisive_factor="Problems target different research communities",
        false_negative_risk="Minimal - distinct problems",
    )


# =============================================================================
# MakerAgent Tests
# =============================================================================


@pytest.mark.asyncio
async def test_maker_generates_arguments(maker_response, sample_state):
    """Test that MakerAgent generates arguments FOR linking."""
    llm = MockLLMClient(maker_response)
    agent = MakerAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"

    updated_state, result = await agent.argue(sample_state)

    assert len(result.arguments) == 3
    assert result.confidence == 0.85
    assert "vanishing gradient" in result.strongest_argument.lower()
    assert updated_state["current_step"] == "maker_complete"


@pytest.mark.asyncio
async def test_maker_appends_to_results(maker_response, sample_state):
    """Test that MakerAgent appends to maker_results list."""
    llm = MockLLMClient(maker_response)
    agent = MakerAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"
    sample_state["maker_results"] = [{"previous": "result"}]

    updated_state, _ = await agent.argue(sample_state)

    assert len(updated_state["maker_results"]) == 2


@pytest.mark.asyncio
async def test_maker_empty_mention_raises(sample_state):
    """Test MakerAgent raises on empty mention."""
    llm = MockLLMClient(None)
    agent = MakerAgent(llm_client=llm)
    sample_state["mention_statement"] = ""

    with pytest.raises(MakerError) as exc_info:
        await agent.argue(sample_state)
    assert "Empty mention" in str(exc_info.value)


@pytest.mark.asyncio
async def test_maker_run_returns_state(maker_response, sample_state):
    """Test MakerAgent.run() returns updated state."""
    llm = MockLLMClient(maker_response)
    agent = MakerAgent(llm_client=llm)
    sample_state["candidate_domain"] = "deep_learning"

    updated_state = await agent.run(sample_state)
    assert updated_state["current_step"] == "maker_complete"


def test_create_maker_agent():
    """Test factory function."""
    llm = MockLLMClient(None)
    agent = create_maker_agent(llm_client=llm)
    assert agent.temperature == 0.3
    assert agent.max_tokens == 1500


# =============================================================================
# HaterAgent Tests
# =============================================================================


@pytest.mark.asyncio
async def test_hater_generates_arguments(hater_response, sample_state):
    """Test that HaterAgent generates arguments AGAINST linking."""
    llm = MockLLMClient(hater_response)
    agent = HaterAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"

    updated_state, result = await agent.argue(sample_state)

    assert len(result.arguments) == 2
    assert result.confidence == 0.45
    assert "prevention" in result.strongest_argument.lower() or "framing" in result.strongest_argument.lower()
    assert updated_state["current_step"] == "hater_complete"


@pytest.mark.asyncio
async def test_hater_appends_to_results(hater_response, sample_state):
    """Test that HaterAgent appends to hater_results list."""
    llm = MockLLMClient(hater_response)
    agent = HaterAgent(llm_client=llm)

    sample_state["candidate_domain"] = "deep_learning"
    sample_state["hater_results"] = [{"previous": "result"}]

    updated_state, _ = await agent.argue(sample_state)

    assert len(updated_state["hater_results"]) == 2


@pytest.mark.asyncio
async def test_hater_empty_candidate_raises(sample_state):
    """Test HaterAgent raises on empty candidate."""
    llm = MockLLMClient(None)
    agent = HaterAgent(llm_client=llm)
    sample_state["candidate_statement"] = ""

    with pytest.raises(HaterError) as exc_info:
        await agent.argue(sample_state)
    assert "Empty candidate" in str(exc_info.value)


@pytest.mark.asyncio
async def test_hater_run_returns_state(hater_response, sample_state):
    """Test HaterAgent.run() returns updated state."""
    llm = MockLLMClient(hater_response)
    agent = HaterAgent(llm_client=llm)
    sample_state["candidate_domain"] = "deep_learning"

    updated_state = await agent.run(sample_state)
    assert updated_state["current_step"] == "hater_complete"


def test_create_hater_agent():
    """Test factory function."""
    llm = MockLLMClient(None)
    agent = create_hater_agent(llm_client=llm)
    assert agent.temperature == 0.3
    assert agent.max_tokens == 1500


# =============================================================================
# ArbiterAgent Tests
# =============================================================================


@pytest.mark.asyncio
async def test_arbiter_decides_link(arbiter_link_response, sample_state, maker_response, hater_response):
    """Test ArbiterAgent decides to LINK."""
    llm = MockLLMClient(arbiter_link_response)
    agent = ArbiterAgent(llm_client=llm)

    # Add maker/hater results to state
    sample_state["maker_results"] = [maker_response.model_dump()]
    sample_state["hater_results"] = [hater_response.model_dump()]
    sample_state["current_round"] = 1

    updated_state, result = await agent.decide(sample_state)

    assert result.decision == ArbiterDecision.LINK
    assert result.confidence == 0.88
    assert result.maker_weight > result.hater_weight
    assert updated_state["consensus_reached"] is True


@pytest.mark.asyncio
async def test_arbiter_decides_create_new(arbiter_create_new_response, sample_state, maker_response, hater_response):
    """Test ArbiterAgent decides to CREATE_NEW."""
    llm = MockLLMClient(arbiter_create_new_response)
    agent = ArbiterAgent(llm_client=llm)

    sample_state["maker_results"] = [maker_response.model_dump()]
    sample_state["hater_results"] = [hater_response.model_dump()]
    sample_state["current_round"] = 1

    updated_state, result = await agent.decide(sample_state)

    assert result.decision == ArbiterDecision.CREATE_NEW
    assert result.hater_weight > result.maker_weight
    assert updated_state["consensus_reached"] is True


@pytest.mark.asyncio
async def test_arbiter_requests_retry(arbiter_retry_response, sample_state, maker_response, hater_response):
    """Test ArbiterAgent requests RETRY when uncertain."""
    llm = MockLLMClient(arbiter_retry_response)
    agent = ArbiterAgent(llm_client=llm)

    sample_state["maker_results"] = [maker_response.model_dump()]
    sample_state["hater_results"] = [hater_response.model_dump()]
    sample_state["current_round"] = 1
    sample_state["max_rounds"] = 3

    updated_state, result = await agent.decide(sample_state)

    assert result.decision == ArbiterDecision.RETRY
    assert updated_state["consensus_reached"] is False


@pytest.mark.asyncio
async def test_arbiter_forces_retry_on_low_confidence(sample_state, maker_response, hater_response):
    """Test Arbiter forces RETRY when confidence < threshold."""
    # LLM says LINK but with low confidence
    low_confidence_response = ArbiterLLMResponse(
        decision="link",
        confidence=0.55,  # Below threshold
        reasoning="Leaning toward link but uncertain",
        maker_weight=0.55,
        hater_weight=0.50,
        decisive_factor="Slight edge to Maker",
        false_negative_risk="Moderate",
    )

    llm = MockLLMClient(low_confidence_response)
    agent = ArbiterAgent(llm_client=llm)

    sample_state["maker_results"] = [maker_response.model_dump()]
    sample_state["hater_results"] = [hater_response.model_dump()]
    sample_state["current_round"] = 1
    sample_state["max_rounds"] = 3

    updated_state, result = await agent.decide(sample_state)

    # Should be forced to RETRY due to low confidence
    assert result.decision == ArbiterDecision.RETRY


@pytest.mark.asyncio
async def test_arbiter_no_retry_on_final_round(sample_state, maker_response, hater_response):
    """Test Arbiter defaults to LINK on final round retry request."""
    retry_response = ArbiterLLMResponse(
        decision="retry",
        confidence=0.55,
        reasoning="Still uncertain",
        maker_weight=0.55,
        hater_weight=0.50,
        decisive_factor="Cannot decide",
        false_negative_risk="Unknown",
    )

    llm = MockLLMClient(retry_response)
    agent = ArbiterAgent(llm_client=llm)

    sample_state["maker_results"] = [maker_response.model_dump()]
    sample_state["hater_results"] = [hater_response.model_dump()]
    sample_state["current_round"] = 3  # Final round
    sample_state["max_rounds"] = 3

    updated_state, result = await agent.decide(sample_state)

    # Should default to LINK on final round (conservative)
    assert result.decision == ArbiterDecision.LINK


@pytest.mark.asyncio
async def test_arbiter_missing_maker_results_raises(sample_state, hater_response):
    """Test Arbiter raises when Maker results missing."""
    llm = MockLLMClient(None)
    agent = ArbiterAgent(llm_client=llm)

    sample_state["maker_results"] = []  # Empty
    sample_state["hater_results"] = [hater_response.model_dump()]

    with pytest.raises(ArbiterError) as exc_info:
        await agent.decide(sample_state)
    assert "Missing Maker" in str(exc_info.value)


@pytest.mark.asyncio
async def test_arbiter_run_returns_state(arbiter_link_response, sample_state, maker_response, hater_response):
    """Test ArbiterAgent.run() returns updated state."""
    llm = MockLLMClient(arbiter_link_response)
    agent = ArbiterAgent(llm_client=llm)

    sample_state["maker_results"] = [maker_response.model_dump()]
    sample_state["hater_results"] = [hater_response.model_dump()]
    sample_state["current_round"] = 1

    updated_state = await agent.run(sample_state)
    assert updated_state["current_step"] == "arbiter_complete"
    assert updated_state["consensus_reached"] is True


def test_create_arbiter_agent():
    """Test factory function."""
    llm = MockLLMClient(None)
    agent = create_arbiter_agent(llm_client=llm, confidence_threshold=0.8)
    assert agent.confidence_threshold == 0.8
    assert agent.temperature == 0.2


def test_arbiter_confidence_threshold_constant():
    """Test the confidence threshold constant."""
    assert ARBITER_CONFIDENCE_THRESHOLD == 0.7


# =============================================================================
# format_arguments Tests
# =============================================================================


def test_format_arguments_with_dict_args():
    """Test formatting arguments from dict format."""
    result = {
        "arguments": [
            {"claim": "First claim", "evidence": "First evidence", "strength": 0.8},
            {"claim": "Second claim", "evidence": "Second evidence", "strength": 0.6},
        ],
        "strongest_argument": "First claim is strongest",
        "confidence": 0.75,
    }

    formatted = format_arguments(result)

    assert "First claim" in formatted
    assert "First evidence" in formatted
    assert "80%" in formatted
    assert "strongest" in formatted.lower()
    assert "75%" in formatted


def test_format_arguments_empty():
    """Test formatting empty arguments."""
    result = {"arguments": [], "confidence": 0.5}
    formatted = format_arguments(result)
    assert "50%" in formatted


# =============================================================================
# Decision Parsing Tests
# =============================================================================


def test_parse_decision_link():
    """Test parsing LINK decision."""
    llm = MockLLMClient(None)
    agent = ArbiterAgent(llm_client=llm)

    assert agent._parse_decision("link", 0.9, 1, 3) == ArbiterDecision.LINK
    assert agent._parse_decision("LINK", 0.9, 1, 3) == ArbiterDecision.LINK


def test_parse_decision_create_new():
    """Test parsing CREATE_NEW decision."""
    llm = MockLLMClient(None)
    agent = ArbiterAgent(llm_client=llm)

    assert agent._parse_decision("create_new", 0.9, 1, 3) == ArbiterDecision.CREATE_NEW
    assert agent._parse_decision("CREATE_NEW", 0.9, 1, 3) == ArbiterDecision.CREATE_NEW


def test_parse_decision_retry():
    """Test parsing RETRY decision."""
    llm = MockLLMClient(None)
    agent = ArbiterAgent(llm_client=llm)

    assert agent._parse_decision("retry", 0.5, 1, 3) == ArbiterDecision.RETRY


def test_parse_decision_unknown_defaults_retry():
    """Test unknown decision defaults to RETRY."""
    llm = MockLLMClient(None)
    agent = ArbiterAgent(llm_client=llm)

    assert agent._parse_decision("maybe", 0.5, 1, 3) == ArbiterDecision.RETRY
    assert agent._parse_decision("", 0.5, 1, 3) == ArbiterDecision.RETRY
