"""Review Queue API endpoints for human-in-the-loop review."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query

from agentic_kg.knowledge_graph.models import (
    PendingReview,
    ReviewResolution,
)
from agentic_kg.knowledge_graph.review_queue import (
    ReviewQueueService,
    ReviewNotFoundError,
)

from agentic_kg_api.dependencies import get_review_queue
from agentic_kg_api.schemas import (
    AgentContextResponse,
    PendingReviewDetail,
    PendingReviewListResponse,
    PendingReviewSummary,
    ReviewResolutionRequest,
    SuggestedConceptResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reviews", tags=["reviews"])


# =============================================================================
# Helper Functions
# =============================================================================


def _review_to_summary(review: PendingReview) -> PendingReviewSummary:
    """Convert PendingReview to summary response."""
    return PendingReviewSummary(
        id=review.id,
        trace_id=review.trace_id,
        mention_id=review.mention_id,
        mention_statement=review.mention_statement,
        paper_doi=review.paper_doi,
        domain=review.domain,
        priority=review.priority.value if hasattr(review.priority, "value") else int(review.priority),
        status=review.status.value if hasattr(review.status, "value") else str(review.status),
        assigned_to=review.assigned_to,
        created_at=review.created_at,
        sla_deadline=review.sla_deadline,
    )


def _review_to_detail(review: PendingReview) -> PendingReviewDetail:
    """Convert PendingReview to detail response."""
    suggested = [
        SuggestedConceptResponse(
            concept_id=c.concept_id,
            canonical_statement=c.canonical_statement,
            similarity_score=c.similarity_score,
            final_score=c.final_score,
            agent_reasoning=c.agent_reasoning,
            domain=c.domain,
            mention_count=c.mention_count,
        )
        for c in review.suggested_concepts
    ]

    agent_context = AgentContextResponse(
        escalation_reason=(
            review.agent_context.escalation_reason.value
            if hasattr(review.agent_context.escalation_reason, "value")
            else str(review.agent_context.escalation_reason)
        ),
        evaluator_decision=review.agent_context.evaluator_decision,
        evaluator_confidence=review.agent_context.evaluator_confidence,
        maker_arguments=review.agent_context.maker_arguments,
        hater_arguments=review.agent_context.hater_arguments,
        arbiter_decision=review.agent_context.arbiter_decision,
        rounds_attempted=review.agent_context.rounds_attempted,
        final_confidence=review.agent_context.final_confidence,
    )

    return PendingReviewDetail(
        id=review.id,
        trace_id=review.trace_id,
        mention_id=review.mention_id,
        mention_statement=review.mention_statement,
        paper_doi=review.paper_doi,
        paper_title=review.paper_title,
        domain=review.domain,
        suggested_concepts=suggested,
        agent_context=agent_context,
        priority=review.priority.value if hasattr(review.priority, "value") else int(review.priority),
        status=review.status.value if hasattr(review.status, "value") else str(review.status),
        assigned_to=review.assigned_to,
        assigned_at=review.assigned_at,
        created_at=review.created_at,
        sla_deadline=review.sla_deadline,
        resolution=review.resolution.value if review.resolution else None,
        resolved_concept_id=review.resolved_concept_id,
        resolved_by=review.resolved_by,
        resolved_at=review.resolved_at,
        resolution_notes=review.resolution_notes,
    )


def _require_user(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
) -> str:
    """Require X-User-ID header for authenticated operations."""
    if not x_user_id:
        raise HTTPException(
            status_code=401,
            detail="X-User-ID header required for this operation",
        )
    return x_user_id


# =============================================================================
# GET /api/reviews/pending - List Pending Reviews
# =============================================================================


@router.get("/pending", response_model=PendingReviewListResponse)
async def list_pending_reviews(
    limit: int = Query(default=20, ge=1, le=100, description="Max reviews to return"),
    priority: Optional[int] = Query(
        default=None, ge=1, le=10, description="Max priority (1=highest)"
    ),
    domain: Optional[str] = Query(default=None, description="Filter by domain"),
    queue_service: ReviewQueueService = Depends(get_review_queue),
) -> PendingReviewListResponse:
    """
    List pending reviews sorted by priority and SLA deadline.

    Returns reviews in order of urgency: priority ASC, sla_deadline ASC.
    """
    try:
        reviews = await queue_service.get_pending(
            limit=limit,
            priority_filter=priority,
            domain_filter=domain,
        )
        total = await queue_service.count_pending(
            priority_filter=priority,
            domain_filter=domain,
        )

        return PendingReviewListResponse(
            reviews=[_review_to_summary(r) for r in reviews],
            total=total,
            limit=limit,
        )
    except Exception as e:
        logger.error(f"Failed to list pending reviews: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# GET /api/reviews/{review_id} - Get Review Detail
# =============================================================================


@router.get("/{review_id}", response_model=PendingReviewDetail)
async def get_review(
    review_id: str,
    queue_service: ReviewQueueService = Depends(get_review_queue),
) -> PendingReviewDetail:
    """Get full detail of a pending review including agent debate context."""
    try:
        review = await queue_service.get_by_id(review_id)
        return _review_to_detail(review)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail=f"Review not found: {review_id}")
    except Exception as e:
        logger.error(f"Failed to get review {review_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# POST /api/reviews/{review_id}/assign - Assign Review
# =============================================================================


@router.post("/{review_id}/assign", response_model=PendingReviewDetail)
async def assign_review(
    review_id: str,
    user_id: str = Depends(_require_user),
    queue_service: ReviewQueueService = Depends(get_review_queue),
) -> PendingReviewDetail:
    """
    Assign a review to the current user.

    Requires X-User-ID header for authentication.
    """
    try:
        review = await queue_service.assign(review_id, user_id)
        logger.info(f"[ReviewAPI] Assigned review {review_id} to user {user_id}")
        return _review_to_detail(review)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail=f"Review not found: {review_id}")
    except Exception as e:
        logger.error(f"Failed to assign review {review_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# POST /api/reviews/{review_id}/unassign - Unassign Review
# =============================================================================


@router.post("/{review_id}/unassign", response_model=PendingReviewDetail)
async def unassign_review(
    review_id: str,
    user_id: str = Depends(_require_user),
    queue_service: ReviewQueueService = Depends(get_review_queue),
) -> PendingReviewDetail:
    """
    Unassign a review (return to pending).

    Requires X-User-ID header for authentication.
    """
    try:
        review = await queue_service.unassign(review_id)
        logger.info(f"[ReviewAPI] Unassigned review {review_id} by user {user_id}")
        return _review_to_detail(review)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail=f"Review not found: {review_id}")
    except Exception as e:
        logger.error(f"Failed to unassign review {review_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# POST /api/reviews/{review_id}/resolve - Resolve Review
# =============================================================================


@router.post("/{review_id}/resolve", response_model=PendingReviewDetail)
async def resolve_review(
    review_id: str,
    request: ReviewResolutionRequest,
    user_id: str = Depends(_require_user),
    queue_service: ReviewQueueService = Depends(get_review_queue),
) -> PendingReviewDetail:
    """
    Resolve a pending review with a decision.

    Requires X-User-ID header for authentication.

    Resolution types:
    - linked: Link mention to an existing concept (requires concept_id)
    - created_new: Create a new concept for this mention
    - blacklisted: Mark mention as invalid/spam
    """
    # Validate linked resolution has concept_id
    if request.resolution == "linked" and not request.concept_id:
        raise HTTPException(
            status_code=400,
            detail="concept_id is required when resolution is 'linked'",
        )

    try:
        resolution = ReviewResolution(request.resolution)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid resolution: {request.resolution}. Must be one of: linked, created_new, blacklisted",
        )

    try:
        review = await queue_service.resolve(
            review_id=review_id,
            resolution=resolution,
            concept_id=request.concept_id,
            user_id=user_id,
            notes=request.notes,
        )
        logger.info(
            f"[ReviewAPI] Resolved review {review_id}: "
            f"resolution={resolution.value}, concept_id={request.concept_id}, by={user_id}"
        )
        return _review_to_detail(review)
    except ReviewNotFoundError:
        raise HTTPException(status_code=404, detail=f"Review not found: {review_id}")
    except Exception as e:
        logger.error(f"Failed to resolve review {review_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
