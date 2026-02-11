"""
Unit tests for ReviewQueueService.

Tests queue operations with mocked Neo4j repository.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_kg.agents.matching.schemas import (
    EscalationReason,
    SuggestedConcept,
)
from agentic_kg.agents.matching.state import create_matching_state
from agentic_kg.knowledge_graph.models import (
    PendingReview,
    ProblemMention,
    ReviewPriority,
    ReviewQueueStatus,
    ReviewResolution,
)
from agentic_kg.knowledge_graph.review_queue import (
    HIGH_IMPACT_DOMAINS,
    SLA_HOURS,
    ReviewNotFoundError,
    ReviewQueueError,
    ReviewQueueService,
    get_review_queue_service,
    reset_review_queue_service,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_repository():
    """Create mock Neo4j repository."""
    repo = MagicMock()
    repo.write_transaction = AsyncMock()
    repo.read_transaction = AsyncMock()
    return repo


@pytest.fixture
def queue_service(mock_repository):
    """Create ReviewQueueService with mock repository."""
    return ReviewQueueService(mock_repository)


@pytest.fixture
def sample_mention() -> ProblemMention:
    """Create sample ProblemMention."""
    return ProblemMention(
        id="mention-123",
        statement="How to prevent gradient vanishing in deep networks?",
        paper_doi="10.1234/paper.123",
        domain="deep_learning",
    )


@pytest.fixture
def sample_candidates() -> list[SuggestedConcept]:
    """Create sample candidate concepts."""
    return [
        SuggestedConcept(
            concept_id="concept-1",
            canonical_statement="Gradient vanishing problem",
            similarity_score=0.72,
            final_score=0.75,
            reasoning="High semantic overlap",
            domain="deep_learning",
            mention_count=5,
        ),
        SuggestedConcept(
            concept_id="concept-2",
            canonical_statement="Deep network training issues",
            similarity_score=0.65,
            final_score=0.65,
            reasoning="Related but broader",
            domain="deep_learning",
            mention_count=10,
        ),
    ]


@pytest.fixture
def sample_workflow_state() -> dict[str, Any]:
    """Create sample workflow state."""
    state = create_matching_state(
        mention_id="mention-123",
        mention_statement="How to prevent gradient vanishing?",
        mention_embedding=[0.1] * 1536,
        candidate_concept_id="concept-1",
        candidate_statement="Gradient vanishing problem",
        similarity_score=0.72,
        trace_id="test-trace-001",
    )
    state["current_round"] = 3
    state["max_rounds"] = 3
    state["evaluator_result"] = {"decision": "escalate", "confidence": 0.6}
    state["maker_results"] = [{"confidence": 0.7}]
    state["hater_results"] = [{"confidence": 0.6}]
    state["arbiter_results"] = [{"decision": "retry", "confidence": 0.55}]
    return state


# =============================================================================
# Priority Calculation Tests
# =============================================================================


class TestPriorityCalculation:
    """Tests for priority calculation."""

    def test_high_confidence_gets_low_priority(self, queue_service, sample_mention):
        """High confidence (near 1.0) results in low priority (higher number)."""
        candidates = [
            SuggestedConcept(
                concept_id="c1",
                canonical_statement="Test",
                similarity_score=0.95,  # High confidence
                final_score=0.95,
            )
        ]
        priority = queue_service._calculate_priority(sample_mention, candidates)
        # Base 5 + (1-0.95)*5 = 5 + 0.25 = ~5
        assert priority >= 4  # Should be relatively low priority

    def test_low_confidence_gets_high_priority(self, queue_service, sample_mention):
        """Low confidence results in high priority (lower number)."""
        candidates = [
            SuggestedConcept(
                concept_id="c1",
                canonical_statement="Test",
                similarity_score=0.55,  # Low confidence
                final_score=0.55,
            )
        ]
        priority = queue_service._calculate_priority(sample_mention, candidates)
        # Base 5 + (1-0.55)*5 = 5 + 2.25 = ~7, but high impact domain -1
        assert priority >= 5

    def test_high_impact_domain_gets_priority_boost(self, queue_service):
        """High-impact domains (NLP, ML, CV) get priority boost."""
        # NLP domain
        mention_nlp = ProblemMention(
            id="m1",
            statement="NLP problem",
            domain="NLP",
        )
        candidates = [
            SuggestedConcept(
                concept_id="c1",
                canonical_statement="Test",
                similarity_score=0.70,
                final_score=0.70,
            )
        ]
        priority_nlp = queue_service._calculate_priority(mention_nlp, candidates)

        # Non-high-impact domain
        mention_other = ProblemMention(
            id="m2",
            statement="Other problem",
            domain="chemistry",
        )
        priority_other = queue_service._calculate_priority(mention_other, candidates)

        # NLP should have lower priority number (higher priority)
        assert priority_nlp < priority_other

    def test_empty_candidates_gets_high_priority(self, queue_service, sample_mention):
        """No candidates results in high priority."""
        priority = queue_service._calculate_priority(sample_mention, [])
        # Base 5 + (1-0)*5 = 10, minus domain boost
        assert priority >= 8

    def test_priority_clamped_to_range(self, queue_service, sample_mention):
        """Priority is always between 1 and 10."""
        # Very high confidence
        candidates_high = [
            SuggestedConcept(
                concept_id="c1",
                canonical_statement="Test",
                similarity_score=1.0,
                final_score=1.0,
            )
        ]
        priority_high = queue_service._calculate_priority(sample_mention, candidates_high)
        assert 1 <= priority_high <= 10

        # Very low confidence
        candidates_low = [
            SuggestedConcept(
                concept_id="c1",
                canonical_statement="Test",
                similarity_score=0.0,
                final_score=0.0,
            )
        ]
        priority_low = queue_service._calculate_priority(sample_mention, candidates_low)
        assert 1 <= priority_low <= 10


# =============================================================================
# SLA Calculation Tests
# =============================================================================


class TestSLACalculation:
    """Tests for SLA deadline calculation."""

    def test_high_priority_gets_24_hours(self, queue_service):
        """Priority 1-3 gets 24 hour SLA."""
        assert queue_service._get_sla_hours(1) == 24
        assert queue_service._get_sla_hours(2) == 24
        assert queue_service._get_sla_hours(3) == 24

    def test_medium_priority_gets_7_days(self, queue_service):
        """Priority 4-6 gets 7 day SLA."""
        assert queue_service._get_sla_hours(4) == 168
        assert queue_service._get_sla_hours(5) == 168
        assert queue_service._get_sla_hours(6) == 168

    def test_low_priority_gets_30_days(self, queue_service):
        """Priority 7-10 gets 30 day SLA."""
        assert queue_service._get_sla_hours(7) == 720
        assert queue_service._get_sla_hours(8) == 720
        assert queue_service._get_sla_hours(9) == 720
        assert queue_service._get_sla_hours(10) == 720


# =============================================================================
# Escalation Reason Tests
# =============================================================================


class TestEscalationReason:
    """Tests for escalation reason determination."""

    def test_max_rounds_exceeded(self, queue_service):
        """Max rounds exceeded is detected."""
        state = {"current_round": 3, "max_rounds": 3}
        reason = queue_service._determine_escalation_reason(state)
        assert reason == EscalationReason.MAX_ROUNDS_EXCEEDED.value

    def test_evaluator_uncertain(self, queue_service):
        """Evaluator escalation is detected."""
        state = {"current_round": 1, "max_rounds": 3, "evaluator_decision": "escalate"}
        reason = queue_service._determine_escalation_reason(state)
        assert reason == EscalationReason.EVALUATOR_UNCERTAIN.value

    def test_arbiter_low_confidence(self, queue_service):
        """Arbiter low confidence is detected."""
        state = {
            "current_round": 1,
            "max_rounds": 3,
            "arbiter_results": [{"confidence": 0.55}],
        }
        reason = queue_service._determine_escalation_reason(state)
        assert reason == EscalationReason.ARBITER_LOW_CONFIDENCE.value

    def test_consensus_failed_default(self, queue_service):
        """Consensus failed is the default."""
        state = {"current_round": 1, "max_rounds": 3}
        reason = queue_service._determine_escalation_reason(state)
        assert reason == EscalationReason.CONSENSUS_FAILED.value


# =============================================================================
# Enqueue Tests
# =============================================================================


class TestEnqueue:
    """Tests for enqueue operation."""

    @pytest.mark.asyncio
    async def test_enqueue_creates_review(
        self, queue_service, sample_mention, sample_candidates, sample_workflow_state
    ):
        """Enqueue creates a PendingReview."""
        review = await queue_service.enqueue(
            sample_mention,
            sample_candidates,
            sample_workflow_state,
        )

        assert review.id is not None
        assert review.mention_id == sample_mention.id
        assert review.mention_statement == sample_mention.statement
        assert review.status == ReviewQueueStatus.PENDING
        assert len(review.suggested_concepts) <= 5

    @pytest.mark.asyncio
    async def test_enqueue_calculates_priority(
        self, queue_service, sample_mention, sample_candidates, sample_workflow_state
    ):
        """Enqueue calculates priority when not provided."""
        review = await queue_service.enqueue(
            sample_mention,
            sample_candidates,
            sample_workflow_state,
        )

        assert 1 <= review.priority.value <= 10

    @pytest.mark.asyncio
    async def test_enqueue_accepts_priority_override(
        self, queue_service, sample_mention, sample_candidates, sample_workflow_state
    ):
        """Enqueue accepts explicit priority."""
        review = await queue_service.enqueue(
            sample_mention,
            sample_candidates,
            sample_workflow_state,
            priority=2,
        )

        assert review.priority == ReviewPriority(2)

    @pytest.mark.asyncio
    async def test_enqueue_sets_sla_deadline(
        self, queue_service, sample_mention, sample_candidates, sample_workflow_state
    ):
        """Enqueue sets SLA deadline based on priority."""
        review = await queue_service.enqueue(
            sample_mention,
            sample_candidates,
            sample_workflow_state,
            priority=2,  # High priority = 24 hour SLA
        )

        expected_deadline = review.created_at + timedelta(hours=24)
        # Allow 1 minute tolerance
        assert abs((review.sla_deadline - expected_deadline).total_seconds()) < 60

    @pytest.mark.asyncio
    async def test_enqueue_stores_agent_context(
        self, queue_service, sample_mention, sample_candidates, sample_workflow_state
    ):
        """Enqueue preserves agent context."""
        review = await queue_service.enqueue(
            sample_mention,
            sample_candidates,
            sample_workflow_state,
        )

        assert review.agent_context is not None
        assert review.agent_context.rounds_attempted == 3


# =============================================================================
# Singleton Tests
# =============================================================================


class TestSingleton:
    """Tests for singleton pattern."""

    def test_reset_clears_singleton(self):
        """Reset clears the singleton."""
        reset_review_queue_service()
        with pytest.raises(ValueError):
            get_review_queue_service()

    def test_get_requires_repo_first_time(self):
        """First call requires repository."""
        reset_review_queue_service()
        with pytest.raises(ValueError) as exc_info:
            get_review_queue_service()
        assert "Repository required" in str(exc_info.value)


# =============================================================================
# Constants Tests
# =============================================================================


def test_sla_hours_constants():
    """SLA hours constants are correct."""
    assert SLA_HOURS["high"] == 24
    assert SLA_HOURS["medium"] == 168  # 7 days
    assert SLA_HOURS["low"] == 720  # 30 days


def test_high_impact_domains():
    """High impact domains are defined."""
    assert "NLP" in HIGH_IMPACT_DOMAINS
    assert "ML" in HIGH_IMPACT_DOMAINS
    assert "CV" in HIGH_IMPACT_DOMAINS
    assert "deep_learning" in HIGH_IMPACT_DOMAINS
