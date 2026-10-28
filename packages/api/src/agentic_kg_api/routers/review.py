"""Nightly review-queue endpoints (P2).

``GET /api/review`` lists deferred candidates the adviser graded;
``GET /api/review/{id}`` shows the evidence text and rationale; and
``POST /api/review/{id}`` records a human decision. Approval delegates to the
KGCS commit path in the core service; rejection only records the decision.
Both append an immutable ``:Canon__ReviewDecision`` audit node.

Authentication matches the legacy review queue: a mutating call requires the
``X-User-ID`` header, so every decision has a named human behind it.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from agentic_kg.knowledge_graph.models.review_item import ReviewItem
from agentic_kg.knowledge_graph.review_items import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    CandidateReviewService,
    ReviewItemBlockedError,
    ReviewItemNotFoundError,
    ReviewItemNotPendingError,
)
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from agentic_kg_api.dependencies import get_review_items
from agentic_kg_api.schemas import (
    ReviewDecisionRequest,
    ReviewEvidenceRefResponse,
    ReviewItemDetail,
    ReviewItemListResponse,
    ReviewItemSummary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/review", tags=["review"])


def _require_user(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-ID"),
) -> str:
    """Require X-User-ID, the mechanism the other mutating review routes use."""
    if not x_user_id:
        raise HTTPException(
            status_code=401,
            detail="X-User-ID header required for this operation",
        )
    return x_user_id


def _summary(item: ReviewItem) -> ReviewItemSummary:
    return ReviewItemSummary(
        id=item.id,
        run_id=item.run_id,
        candidate_id=item.candidate_id,
        candidate_kind=item.candidate_kind,
        entity_type=item.entity_type,
        candidate_summary=item.candidate_summary,
        adviser_verdict=item.adviser_verdict,
        confidence=item.confidence,
        adviser_abstained=item.adviser_abstained,
        status=item.status,
        committable=item.committable,
        commit_blocker=item.commit_blocker,
        created_at=item.created_at,
        decided_at=item.decided_at,
    )


def _detail(item: ReviewItem) -> ReviewItemDetail:
    return ReviewItemDetail(
        **_summary(item).model_dump(),
        namespace=item.namespace,
        candidate_payload=item.candidate_payload,
        evidence_refs=[
            ReviewEvidenceRefResponse(
                evidence_id=ref.evidence_id,
                relationship=ref.relationship,
                content=ref.content,
            )
            for ref in item.evidence_refs
        ],
        rationale=item.rationale,
        adviser_type=item.adviser_type,
        adviser_version=item.adviser_version,
        adviser_model_id=item.adviser_model_id,
        adviser_model_version=item.adviser_model_version,
        prompt_version=item.prompt_version,
        decided_by=item.decided_by,
        decision_note=item.decision_note,
        committed_epoch=item.committed_epoch,
        committed_identity_ids=item.committed_identity_ids,
    )


@router.get("", response_model=ReviewItemListResponse)
def list_review_items(
    status: Optional[Literal["pending", "approved", "rejected"]] = Query(default=None),
    candidate_type: Optional[str] = Query(
        default=None,
        alias="type",
        description="Candidate kind or entity type (Problem, relation, ...)",
    ),
    run_id: Optional[str] = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    service: CandidateReviewService = Depends(get_review_items),
) -> ReviewItemListResponse:
    """List review items, newest first, with optional filters."""
    items, total = service.list(
        status=status,
        candidate_type=candidate_type,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )
    return ReviewItemListResponse(
        items=[_summary(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{item_id}", response_model=ReviewItemDetail)
def get_review_item(
    item_id: str,
    service: CandidateReviewService = Depends(get_review_items),
) -> ReviewItemDetail:
    """Return one review item with its evidence text and adviser rationale."""
    try:
        return _detail(service.get(item_id))
    except ReviewItemNotFoundError:
        raise HTTPException(status_code=404, detail=f"Review item not found: {item_id}")


@router.post("/{item_id}", response_model=ReviewItemDetail)
def decide_review_item(
    item_id: str,
    request: ReviewDecisionRequest,
    user_id: str = Depends(_require_user),
    service: CandidateReviewService = Depends(get_review_items),
) -> ReviewItemDetail:
    """Approve or reject one review item.

    Approval commits the candidate through KGCS and is refused (409) when the
    item is not committable. Rejection never touches the canonical graph. An
    item that already has a decision is never overwritten.
    """
    try:
        if request.decision == "approve":
            item = service.approve(item_id, user_id=user_id, note=request.note)
        else:
            item = service.reject(item_id, user_id=user_id, note=request.note)
    except ReviewItemNotFoundError:
        raise HTTPException(status_code=404, detail=f"Review item not found: {item_id}")
    except (ReviewItemNotPendingError, ReviewItemBlockedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - an upstream commit failure is not a 500
        logger.exception("failed to decide review item %s", item_id)
        raise HTTPException(
            status_code=502,
            detail=f"review decision failed: {type(exc).__name__}: {exc}",
        )
    logger.info(
        "review item %s %s by %s", item_id, request.decision, user_id
    )
    return _detail(item)


__all__ = ["router"]
