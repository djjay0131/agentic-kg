"""
Human Review Queue Service.

Manages the queue of problem mention-to-concept matches that were escalated
from agent review and require human judgment.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from agentic_kg.agents.matching.schemas import (
    AgentContext,
    EscalationReason,
    SuggestedConcept,
)
from agentic_kg.agents.matching.state import MatchingWorkflowState
from agentic_kg.knowledge_graph.models import (
    PendingReview,
    ProblemMention,
    ReviewPriority,
    ReviewQueueStatus,
    ReviewResolution,
)
from agentic_kg.knowledge_graph.models.entities import (
    AgentContextForReview,
    SuggestedConceptForReview,
)

if TYPE_CHECKING:
    from agentic_kg.knowledge_graph.repository import Neo4jRepository

logger = logging.getLogger(__name__)


class ReviewQueueError(Exception):
    """Error in review queue operations."""

    pass


class ReviewNotFoundError(ReviewQueueError):
    """Review not found in queue."""

    pass


# =============================================================================
# SLA Configuration
# =============================================================================

# SLA hours by priority tier
SLA_HOURS = {
    "high": 24,  # Priority 1-3: 24 hours
    "medium": 168,  # Priority 4-6: 7 days
    "low": 720,  # Priority 7-10: 30 days
}

# High-impact domains get priority boost
HIGH_IMPACT_DOMAINS = {"NLP", "CV", "ML", "deep_learning", "nlp", "cv", "ml"}


# =============================================================================
# Review Queue Service
# =============================================================================


class ReviewQueueService:
    """
    Manages the human review queue for escalated matching decisions.

    Features:
    - Enqueue mentions with priority and SLA calculation
    - Query pending reviews with filters
    - Assign reviews to users
    - Resolve reviews and apply decisions
    """

    def __init__(self, repository: Neo4jRepository) -> None:
        """
        Initialize the ReviewQueueService.

        Args:
            repository: Neo4j repository for database operations.
        """
        self._repo = repository

    # =========================================================================
    # Enqueue Operations
    # =========================================================================

    async def enqueue(
        self,
        mention: ProblemMention,
        suggested_concepts: list[SuggestedConcept],
        workflow_state: MatchingWorkflowState,
        priority: Optional[int] = None,
    ) -> PendingReview:
        """
        Add a mention to the review queue.

        Args:
            mention: The ProblemMention needing review.
            suggested_concepts: Top candidate concepts from matching.
            workflow_state: Full state from the matching workflow.
            priority: Optional override for priority (1=highest, 10=lowest).

        Returns:
            Created PendingReview record.
        """
        trace_id = workflow_state.get("trace_id", str(uuid.uuid4()))

        # Calculate priority if not provided
        if priority is None:
            priority = self._calculate_priority(mention, suggested_concepts)

        # Calculate SLA deadline
        sla_hours = self._get_sla_hours(priority)
        sla_deadline = datetime.now(timezone.utc) + timedelta(hours=sla_hours)

        # Convert suggested concepts
        suggested = [
            SuggestedConceptForReview(
                concept_id=c.concept_id,
                canonical_statement=c.canonical_statement,
                similarity_score=c.similarity_score,
                final_score=c.final_score,
                reasoning=c.reasoning,
                domain=c.domain,
                mention_count=c.mention_count,
            )
            for c in suggested_concepts[:5]  # Top 5 only
        ]

        # Build agent context
        agent_context = AgentContextForReview(
            escalation_reason=self._determine_escalation_reason(workflow_state),
            evaluator_result=workflow_state.get("evaluator_result"),
            maker_results=workflow_state.get("maker_results", []),
            hater_results=workflow_state.get("hater_results", []),
            arbiter_results=workflow_state.get("arbiter_results", []),
            rounds_attempted=workflow_state.get("current_round", 0),
            final_confidence=workflow_state.get("final_confidence", 0.0),
        )

        # Create PendingReview
        review = PendingReview(
            id=str(uuid.uuid4()),
            trace_id=trace_id,
            mention_id=mention.id,
            mention_statement=mention.statement,
            paper_doi=mention.paper_doi or "",
            paper_title=None,  # Could be enriched later
            domain=mention.domain,
            suggested_concepts=suggested,
            agent_context=agent_context,
            priority=ReviewPriority(priority),
            status=ReviewQueueStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            sla_deadline=sla_deadline,
        )

        # Store in Neo4j
        await self._store_review(review, mention.id)

        logger.info(
            f"[ReviewQueue] Enqueued review {review.id} for mention {mention.id} "
            f"(priority={priority}, sla={sla_hours}h, reason={agent_context.escalation_reason})"
        )

        return review

    async def _store_review(self, review: PendingReview, mention_id: str) -> None:
        """Store review in Neo4j and link to mention."""
        query = """
        CREATE (r:PendingReview $props)
        WITH r
        MATCH (m:ProblemMention {id: $mention_id})
        CREATE (r)-[:REVIEWS]->(m)
        """

        props = review.to_neo4j_properties()

        async def _tx(tx):
            await tx.run(query, props=props, mention_id=mention_id)

        await self._repo.write_transaction(_tx)

    # =========================================================================
    # Query Operations
    # =========================================================================

    async def get_pending(
        self,
        limit: int = 20,
        priority_filter: Optional[int] = None,
        domain_filter: Optional[str] = None,
    ) -> list[PendingReview]:
        """
        Get pending reviews sorted by priority and SLA.

        Args:
            limit: Max reviews to return.
            priority_filter: Only reviews with priority <= this value.
            domain_filter: Only reviews from this domain.

        Returns:
            List of PendingReview ordered by priority ASC, sla_deadline ASC.
        """
        query = """
        MATCH (r:PendingReview)
        WHERE r.status = 'pending'
          AND ($priority IS NULL OR r.priority <= $priority)
          AND ($domain IS NULL OR r.domain = $domain)
        RETURN r
        ORDER BY r.priority ASC, r.sla_deadline ASC
        LIMIT $limit
        """

        async def _tx(tx):
            result = await tx.run(
                query,
                priority=priority_filter,
                domain=domain_filter,
                limit=limit,
            )
            records = await result.data()
            return [self._record_to_review(r["r"]) for r in records]

        return await self._repo.read_transaction(_tx)

    async def get_by_id(self, review_id: str) -> PendingReview:
        """
        Get a specific review by ID.

        Args:
            review_id: The review ID.

        Returns:
            PendingReview if found.

        Raises:
            ReviewNotFoundError: If review not found.
        """
        query = """
        MATCH (r:PendingReview {id: $review_id})
        RETURN r
        """

        async def _tx(tx):
            result = await tx.run(query, review_id=review_id)
            record = await result.single()
            if record is None:
                raise ReviewNotFoundError(f"Review {review_id} not found")
            return self._record_to_review(record["r"])

        return await self._repo.read_transaction(_tx)

    async def get_by_mention_id(self, mention_id: str) -> Optional[PendingReview]:
        """
        Get a pending review for a specific mention.

        Args:
            mention_id: The ProblemMention ID.

        Returns:
            PendingReview if exists, None otherwise.
        """
        query = """
        MATCH (r:PendingReview)-[:REVIEWS]->(m:ProblemMention {id: $mention_id})
        WHERE r.status = 'pending'
        RETURN r
        LIMIT 1
        """

        async def _tx(tx):
            result = await tx.run(query, mention_id=mention_id)
            record = await result.single()
            return self._record_to_review(record["r"]) if record else None

        return await self._repo.read_transaction(_tx)

    async def count_pending(
        self,
        priority_filter: Optional[int] = None,
        domain_filter: Optional[str] = None,
    ) -> int:
        """Count pending reviews matching filters."""
        query = """
        MATCH (r:PendingReview)
        WHERE r.status = 'pending'
          AND ($priority IS NULL OR r.priority <= $priority)
          AND ($domain IS NULL OR r.domain = $domain)
        RETURN count(r) AS count
        """

        async def _tx(tx):
            result = await tx.run(
                query,
                priority=priority_filter,
                domain=domain_filter,
            )
            record = await result.single()
            return record["count"] if record else 0

        return await self._repo.read_transaction(_tx)

    # =========================================================================
    # Assignment Operations
    # =========================================================================

    async def assign(self, review_id: str, user_id: str) -> PendingReview:
        """
        Assign a review to a user.

        Args:
            review_id: The review ID.
            user_id: User ID to assign to.

        Returns:
            Updated PendingReview.
        """
        now = datetime.now(timezone.utc)

        query = """
        MATCH (r:PendingReview {id: $review_id})
        SET r.status = 'assigned',
            r.assigned_to = $user_id,
            r.assigned_at = $assigned_at
        RETURN r
        """

        async def _tx(tx):
            result = await tx.run(
                query,
                review_id=review_id,
                user_id=user_id,
                assigned_at=now.isoformat(),
            )
            record = await result.single()
            if record is None:
                raise ReviewNotFoundError(f"Review {review_id} not found")
            return self._record_to_review(record["r"])

        review = await self._repo.write_transaction(_tx)

        logger.info(f"[ReviewQueue] Assigned review {review_id} to user {user_id}")

        return review

    async def unassign(self, review_id: str) -> PendingReview:
        """
        Unassign a review (return to pending).

        Args:
            review_id: The review ID.

        Returns:
            Updated PendingReview.
        """
        query = """
        MATCH (r:PendingReview {id: $review_id})
        SET r.status = 'pending',
            r.assigned_to = NULL,
            r.assigned_at = NULL
        RETURN r
        """

        async def _tx(tx):
            result = await tx.run(query, review_id=review_id)
            record = await result.single()
            if record is None:
                raise ReviewNotFoundError(f"Review {review_id} not found")
            return self._record_to_review(record["r"])

        review = await self._repo.write_transaction(_tx)

        logger.info(f"[ReviewQueue] Unassigned review {review_id}")

        return review

    # =========================================================================
    # Resolution Operations
    # =========================================================================

    async def resolve(
        self,
        review_id: str,
        resolution: ReviewResolution,
        concept_id: Optional[str],
        user_id: str,
        notes: Optional[str] = None,
    ) -> PendingReview:
        """
        Resolve a pending review.

        Args:
            review_id: The review ID.
            resolution: Resolution type (LINKED, CREATED_NEW, BLACKLISTED).
            concept_id: Concept ID if resolution is LINKED.
            user_id: User ID resolving the review.
            notes: Optional resolution notes.

        Returns:
            Updated PendingReview.
        """
        now = datetime.now(timezone.utc)

        query = """
        MATCH (r:PendingReview {id: $review_id})
        SET r.status = 'resolved',
            r.resolution = $resolution,
            r.resolved_concept_id = $concept_id,
            r.resolved_by = $user_id,
            r.resolved_at = $resolved_at,
            r.resolution_notes = $notes
        RETURN r
        """

        async def _tx(tx):
            result = await tx.run(
                query,
                review_id=review_id,
                resolution=resolution.value,
                concept_id=concept_id,
                user_id=user_id,
                resolved_at=now.isoformat(),
                notes=notes,
            )
            record = await result.single()
            if record is None:
                raise ReviewNotFoundError(f"Review {review_id} not found")
            return self._record_to_review(record["r"])

        review = await self._repo.write_transaction(_tx)

        logger.info(
            f"[ReviewQueue] Resolved review {review_id}: "
            f"resolution={resolution.value}, concept_id={concept_id}, by={user_id}"
        )

        return review

    # =========================================================================
    # Priority and SLA Calculation
    # =========================================================================

    def _calculate_priority(
        self,
        mention: ProblemMention,
        candidates: list[SuggestedConcept],
    ) -> int:
        """
        Calculate priority (1=highest, 10=lowest).

        Formula: base + confidence_factor + domain_factor

        - Lower confidence = higher priority (needs review sooner)
        - High-impact domains get priority boost (-1)
        """
        base = 5

        # Lower confidence = higher priority
        top_score = candidates[0].similarity_score if candidates else 0
        confidence_factor = int((1 - top_score) * 5)

        # High-impact domains get priority
        domain_factor = -1 if mention.domain in HIGH_IMPACT_DOMAINS else 0

        priority = base + confidence_factor + domain_factor
        return max(1, min(10, priority))

    def _get_sla_hours(self, priority: int) -> int:
        """Get SLA hours based on priority tier."""
        if priority <= 3:
            return SLA_HOURS["high"]  # 24 hours
        elif priority <= 6:
            return SLA_HOURS["medium"]  # 7 days
        else:
            return SLA_HOURS["low"]  # 30 days

    def _determine_escalation_reason(
        self, workflow_state: MatchingWorkflowState
    ) -> str:
        """Determine why the match was escalated."""
        # Check for max rounds exceeded
        current_round = workflow_state.get("current_round", 0)
        max_rounds = workflow_state.get("max_rounds", 3)
        if current_round >= max_rounds:
            return EscalationReason.MAX_ROUNDS_EXCEEDED.value

        # Check for evaluator escalation
        evaluator_decision = workflow_state.get("evaluator_decision")
        if evaluator_decision == "escalate":
            return EscalationReason.EVALUATOR_UNCERTAIN.value

        # Check arbiter results
        arbiter_results = workflow_state.get("arbiter_results", [])
        if arbiter_results:
            last_arbiter = arbiter_results[-1]
            if last_arbiter.get("confidence", 0) < 0.7:
                return EscalationReason.ARBITER_LOW_CONFIDENCE.value

        # Default to consensus failed
        return EscalationReason.CONSENSUS_FAILED.value

    # =========================================================================
    # Helpers
    # =========================================================================

    def _record_to_review(self, record: dict) -> PendingReview:
        """Convert Neo4j record to PendingReview model."""
        # Parse JSON fields
        suggested_concepts = record.get("suggested_concepts", [])
        if isinstance(suggested_concepts, str):
            suggested_concepts = json.loads(suggested_concepts)

        agent_context = record.get("agent_context", {})
        if isinstance(agent_context, str):
            agent_context = json.loads(agent_context)

        # Parse datetimes
        def parse_datetime(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val.replace("Z", "+00:00"))

        return PendingReview(
            id=record["id"],
            trace_id=record.get("trace_id", ""),
            mention_id=record.get("mention_id", ""),
            mention_statement=record.get("mention_statement", ""),
            paper_doi=record.get("paper_doi", ""),
            paper_title=record.get("paper_title"),
            domain=record.get("domain"),
            suggested_concepts=[
                SuggestedConceptForReview(**c) if isinstance(c, dict) else c
                for c in suggested_concepts
            ],
            agent_context=AgentContextForReview(**agent_context)
            if isinstance(agent_context, dict)
            else agent_context,
            priority=ReviewPriority(record.get("priority", 5)),
            status=ReviewQueueStatus(record.get("status", "pending")),
            assigned_to=record.get("assigned_to"),
            assigned_at=parse_datetime(record.get("assigned_at")),
            created_at=parse_datetime(record.get("created_at")) or datetime.now(timezone.utc),
            sla_deadline=parse_datetime(record.get("sla_deadline")) or datetime.now(timezone.utc),
            resolution=ReviewResolution(record["resolution"]) if record.get("resolution") else None,
            resolved_concept_id=record.get("resolved_concept_id"),
            resolved_by=record.get("resolved_by"),
            resolved_at=parse_datetime(record.get("resolved_at")),
            resolution_notes=record.get("resolution_notes"),
        )


# =============================================================================
# Singleton
# =============================================================================

_queue_service: Optional[ReviewQueueService] = None


def get_review_queue_service(repository: Optional[Neo4jRepository] = None) -> ReviewQueueService:
    """Get or create ReviewQueueService singleton."""
    global _queue_service

    if _queue_service is None:
        if repository is None:
            raise ValueError("Repository required for first call")
        _queue_service = ReviewQueueService(repository)

    return _queue_service


def reset_review_queue_service() -> None:
    """Reset the singleton (for testing)."""
    global _queue_service
    _queue_service = None
