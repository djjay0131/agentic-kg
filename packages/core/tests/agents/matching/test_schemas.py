"""
Unit tests for matching agent schemas.

Tests model validation, serialization, and edge cases for:
- EvaluatorResult
- MakerResult / HaterResult
- ArbiterResult
- MatchingWorkflowState
"""

import pytest
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

from agentic_kg.agents.matching.schemas import (
    AgentContext,
    ArbiterDecision,
    ArbiterResult,
    Argument,
    EscalationReason,
    EvaluatorDecision,
    EvaluatorResult,
    HaterResult,
    MakerResult,
    MatchingWorkflowSummary,
    ReviewResolution,
    SuggestedConcept,
)
from agentic_kg.agents.matching.state import (
    MatchingWorkflowState,
    add_matching_error,
    add_matching_message,
    complete_matching_workflow,
    create_matching_state,
    escalate_to_human,
)


class TestEvaluatorResult:
    """Tests for EvaluatorResult model."""

    def test_valid_approve_decision(self):
        """Test valid APPROVE decision."""
        result = EvaluatorResult(
            decision=EvaluatorDecision.APPROVE,
            confidence=0.95,
            reasoning="Strong semantic similarity and domain match.",
            key_factors=["same domain", "similar constraints", "overlapping metrics"],
        )
        assert result.decision == EvaluatorDecision.APPROVE
        assert result.confidence == 0.95
        assert len(result.key_factors) == 3

    def test_valid_reject_decision(self):
        """Test valid REJECT decision."""
        result = EvaluatorResult(
            decision=EvaluatorDecision.REJECT,
            confidence=0.85,
            reasoning="Different problem domains despite surface similarity.",
            key_factors=["domain mismatch"],
        )
        assert result.decision == EvaluatorDecision.REJECT

    def test_valid_escalate_decision(self):
        """Test valid ESCALATE decision."""
        result = EvaluatorResult(
            decision=EvaluatorDecision.ESCALATE,
            confidence=0.55,
            reasoning="Uncertain about relationship - needs consensus.",
            key_factors=["ambiguous domain", "unclear constraints"],
        )
        assert result.decision == EvaluatorDecision.ESCALATE

    def test_empty_key_factors_gets_default(self):
        """Test that empty key_factors gets a default value."""
        result = EvaluatorResult(
            decision=EvaluatorDecision.APPROVE,
            confidence=0.9,
            reasoning="This is valid reasoning text.",
            key_factors=[],
        )
        assert result.key_factors == ["No specific factors identified"]

    def test_invalid_confidence_below_zero(self):
        """Test confidence below 0 raises error."""
        with pytest.raises(ValidationError):
            EvaluatorResult(
                decision=EvaluatorDecision.APPROVE,
                confidence=-0.1,
                reasoning="This is valid reasoning text.",
            )

    def test_invalid_confidence_above_one(self):
        """Test confidence above 1 raises error."""
        with pytest.raises(ValidationError):
            EvaluatorResult(
                decision=EvaluatorDecision.APPROVE,
                confidence=1.5,
                reasoning="This is valid reasoning text.",
            )

    def test_reasoning_too_short(self):
        """Test reasoning that is too short raises error."""
        with pytest.raises(ValidationError):
            EvaluatorResult(
                decision=EvaluatorDecision.APPROVE,
                confidence=0.9,
                reasoning="Short",  # Less than 10 chars
            )

    def test_serialization(self):
        """Test model serialization to dict."""
        result = EvaluatorResult(
            decision=EvaluatorDecision.APPROVE,
            confidence=0.95,
            reasoning="Strong semantic similarity.",
            key_factors=["domain match"],
            similarity_assessment="High similarity in problem structure",
            domain_match=True,
        )
        data = result.model_dump()
        assert data["decision"] == "approve"
        assert data["confidence"] == 0.95
        assert data["domain_match"] is True


class TestArgument:
    """Tests for Argument model (used by Maker/Hater)."""

    def test_valid_argument(self):
        """Test valid argument creation."""
        arg = Argument(
            claim="The problems share the same core optimization target.",
            evidence="Both mention minimizing loss function with L2 regularization.",
            strength=0.8,
        )
        assert arg.claim == "The problems share the same core optimization target."
        assert arg.strength == 0.8

    def test_claim_too_short(self):
        """Test claim that is too short raises error."""
        with pytest.raises(ValidationError):
            Argument(
                claim="Yes",  # Less than 5 chars
                evidence="Some evidence",
            )

    def test_default_strength(self):
        """Test default strength value."""
        arg = Argument(
            claim="Valid claim here",
            evidence="Supporting evidence",
        )
        assert arg.strength == 0.5


class TestMakerResult:
    """Tests for MakerResult model."""

    def test_valid_maker_result(self):
        """Test valid MakerResult with multiple arguments."""
        result = MakerResult(
            arguments=[
                Argument(
                    claim="Both problems target neural network optimization.",
                    evidence="Keywords: gradient descent, backpropagation",
                    strength=0.9,
                ),
                Argument(
                    claim="Same benchmark datasets mentioned.",
                    evidence="Both mention ImageNet and CIFAR-10",
                    strength=0.85,
                ),
            ],
            confidence=0.87,
            strongest_argument="Identical optimization objectives",
            semantic_similarity_evidence="Cosine similarity = 0.92",
        )
        assert len(result.arguments) == 2
        assert result.confidence == 0.87

    def test_empty_arguments_raises_error(self):
        """Test that empty arguments list raises error."""
        with pytest.raises(ValidationError):
            MakerResult(
                arguments=[],
                confidence=0.8,
                strongest_argument="Test",
            )

    def test_too_many_arguments(self):
        """Test that more than 5 arguments raises error."""
        args = [
            Argument(claim=f"Argument {i}", evidence=f"Evidence {i}")
            for i in range(6)
        ]
        with pytest.raises(ValidationError):
            MakerResult(
                arguments=args,
                confidence=0.8,
                strongest_argument="Test",
            )


class TestHaterResult:
    """Tests for HaterResult model."""

    def test_valid_hater_result(self):
        """Test valid HaterResult with counter-arguments."""
        result = HaterResult(
            arguments=[
                Argument(
                    claim="Different optimization constraints.",
                    evidence="One requires real-time, other is offline",
                    strength=0.75,
                ),
            ],
            confidence=0.7,
            strongest_argument="Fundamentally different constraints",
            semantic_difference_evidence="Key terms differ: real-time vs batch",
        )
        assert len(result.arguments) == 1
        assert result.confidence == 0.7


class TestArbiterResult:
    """Tests for ArbiterResult model."""

    def test_valid_link_decision(self):
        """Test valid LINK decision."""
        result = ArbiterResult(
            decision=ArbiterDecision.LINK,
            confidence=0.85,
            reasoning="Maker's arguments for shared objective outweigh Hater's constraint concerns.",
            maker_weight=0.7,
            hater_weight=0.3,
            decisive_factor="Core optimization target is identical",
            false_negative_risk="Low - clear semantic match",
        )
        assert result.decision == ArbiterDecision.LINK
        assert result.maker_weight > result.hater_weight

    def test_valid_create_new_decision(self):
        """Test valid CREATE_NEW decision."""
        result = ArbiterResult(
            decision=ArbiterDecision.CREATE_NEW,
            confidence=0.9,
            reasoning="Hater's domain mismatch argument is compelling.",
            maker_weight=0.3,
            hater_weight=0.7,
            decisive_factor="Different research domains",
        )
        assert result.decision == ArbiterDecision.CREATE_NEW

    def test_valid_retry_decision(self):
        """Test valid RETRY decision."""
        result = ArbiterResult(
            decision=ArbiterDecision.RETRY,
            confidence=0.55,
            reasoning="Arguments are evenly matched, need more debate.",
            maker_weight=0.5,
            hater_weight=0.5,
            decisive_factor="No clear winner",
        )
        assert result.decision == ArbiterDecision.RETRY

    def test_reasoning_too_short(self):
        """Test reasoning that is too short raises error."""
        with pytest.raises(ValidationError):
            ArbiterResult(
                decision=ArbiterDecision.LINK,
                confidence=0.85,
                reasoning="Short",  # Less than 20 chars
                maker_weight=0.7,
                hater_weight=0.3,
                decisive_factor="Test",
            )


class TestSuggestedConcept:
    """Tests for SuggestedConcept model."""

    def test_valid_suggested_concept(self):
        """Test valid suggested concept."""
        concept = SuggestedConcept(
            concept_id="concept-123",
            canonical_statement="Optimizing neural network training efficiency",
            similarity_score=0.92,
            final_score=0.95,
            reasoning="High semantic overlap with mention",
            domain="machine_learning",
            mention_count=5,
        )
        assert concept.concept_id == "concept-123"
        assert concept.final_score == 0.95


class TestAgentContext:
    """Tests for AgentContext model."""

    def test_valid_agent_context(self):
        """Test valid agent context with escalation."""
        context = AgentContext(
            escalation_reason=EscalationReason.CONSENSUS_FAILED,
            rounds_attempted=3,
            final_confidence=0.55,
        )
        assert context.escalation_reason == EscalationReason.CONSENSUS_FAILED
        assert context.rounds_attempted == 3


class TestMatchingWorkflowState:
    """Tests for MatchingWorkflowState TypedDict."""

    def test_create_matching_state(self):
        """Test creating a new matching workflow state."""
        state = create_matching_state(
            mention_id="mention-123",
            mention_statement="How to optimize gradient descent?",
            mention_embedding=[0.1] * 1536,
            candidate_concept_id="concept-456",
            candidate_statement="Optimizing gradient descent methods",
            similarity_score=0.88,
            paper_doi="10.1234/test",
            mention_domain="machine_learning",
        )
        assert state["mention_id"] == "mention-123"
        assert state["similarity_score"] == 0.88
        assert state["status"] == "pending"
        assert len(state["mention_embedding"]) == 1536

    def test_add_matching_message(self):
        """Test adding a message to the state."""
        state = create_matching_state(
            mention_id="mention-123",
            mention_statement="Test problem statement",
            mention_embedding=[0.1] * 1536,
        )
        updated = add_matching_message(state, "evaluator", "Processing mention")
        assert len(updated["messages"]) == 1
        assert updated["messages"][0]["agent"] == "evaluator"

    def test_add_matching_error(self):
        """Test adding an error to the state."""
        state = create_matching_state(
            mention_id="mention-123",
            mention_statement="Test problem statement",
            mention_embedding=[0.1] * 1536,
        )
        updated = add_matching_error(state, "LLM timeout")
        assert len(updated["errors"]) == 1
        assert updated["status"] == "failed"

    def test_complete_matching_workflow(self):
        """Test completing the workflow with a decision."""
        state = create_matching_state(
            mention_id="mention-123",
            mention_statement="Test problem statement",
            mention_embedding=[0.1] * 1536,
        )
        completed = complete_matching_workflow(
            state,
            decision="linked",
            concept_id="concept-456",
            reasoning="High confidence match",
            confidence=0.95,
        )
        assert completed["status"] == "completed"
        assert completed["final_decision"] == "linked"
        assert completed["final_concept_id"] == "concept-456"
        assert completed["total_duration_ms"] >= 0

    def test_escalate_to_human(self):
        """Test escalating to human review."""
        state = create_matching_state(
            mention_id="mention-123",
            mention_statement="Test problem statement",
            mention_embedding=[0.1] * 1536,
        )
        suggested = [
            SuggestedConcept(
                concept_id="concept-1",
                canonical_statement="Suggested concept",
                similarity_score=0.75,
                final_score=0.78,
            ),
        ]
        escalated = escalate_to_human(
            state,
            reason=EscalationReason.MAX_ROUNDS_EXCEEDED,
            suggested_concepts=suggested,
        )
        assert escalated["status"] == "escalated"
        assert escalated["escalated"] is True
        assert escalated["escalation_reason"] == "max_rounds_exceeded"
        assert len(escalated["suggested_concepts"]) == 1


class TestMatchingWorkflowSummary:
    """Tests for MatchingWorkflowSummary model."""

    def test_valid_summary(self):
        """Test creating a valid workflow summary."""
        summary = MatchingWorkflowSummary(
            trace_id="match-abc123",
            mention_id="mention-123",
            mention_statement="Test problem",
            initial_confidence="MEDIUM",
            top_candidate_id="concept-456",
            top_candidate_score=0.88,
            final_decision="linked",
            final_concept_id="concept-456",
            agents_invoked=["evaluator"],
            total_rounds=0,
            total_duration_ms=1500,
            escalated_to_human=False,
        )
        assert summary.trace_id == "match-abc123"
        assert summary.final_decision == "linked"


class TestEnums:
    """Tests for matching-related enums."""

    def test_evaluator_decision_values(self):
        """Test EvaluatorDecision enum values."""
        assert EvaluatorDecision.APPROVE.value == "approve"
        assert EvaluatorDecision.REJECT.value == "reject"
        assert EvaluatorDecision.ESCALATE.value == "escalate"

    def test_arbiter_decision_values(self):
        """Test ArbiterDecision enum values."""
        assert ArbiterDecision.LINK.value == "link"
        assert ArbiterDecision.CREATE_NEW.value == "create_new"
        assert ArbiterDecision.RETRY.value == "retry"

    def test_escalation_reason_values(self):
        """Test EscalationReason enum values."""
        assert EscalationReason.EVALUATOR_UNCERTAIN.value == "evaluator_uncertain"
        assert EscalationReason.CONSENSUS_FAILED.value == "consensus_failed"
        assert EscalationReason.ARBITER_LOW_CONFIDENCE.value == "arbiter_low_confidence"
        assert EscalationReason.MAX_ROUNDS_EXCEEDED.value == "max_rounds_exceeded"

    def test_review_resolution_values(self):
        """Test ReviewResolution enum values."""
        assert ReviewResolution.LINKED.value == "linked"
        assert ReviewResolution.CREATED_NEW.value == "created_new"
        assert ReviewResolution.BLACKLISTED.value == "blacklisted"
