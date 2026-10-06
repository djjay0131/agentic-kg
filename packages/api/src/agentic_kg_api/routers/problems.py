"""Problem CRUD endpoints."""

import logging
from typing import Optional

from agentic_kg.knowledge_graph.models import ProblemStatus
from agentic_kg.knowledge_graph.repository import Neo4jRepository, NotFoundError
from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import (
    EvidenceResponse,
    ExtractionMetadataResponse,
    ProblemDetail,
    ProblemListResponse,
    ProblemMentionResponse,
    ProblemSummary,
    ProblemUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/problems", tags=["problems"])


def _view_to_summary(view: dict) -> ProblemSummary:
    """Convert a canonical problem view to a summary response."""
    return ProblemSummary(
        id=view["id"],
        statement=view.get("statement") or "",
        status=str(view.get("status") or ProblemStatus.OPEN.value),
        confidence=view.get("confidence"),
        created_at=view.get("created_at"),
        canonical_statement=view.get("canonical_statement"),
        mention_count=view.get("mention_count") or 0,
        paper_count=view.get("paper_count") or 0,
    )


def _view_to_detail(view: dict) -> ProblemDetail:
    """Convert a canonical problem view to a detail response."""
    evidence = None
    raw_evidence = view.get("evidence")
    if raw_evidence:
        evidence = EvidenceResponse(
            source_doi=raw_evidence.get("source_doi"),
            source_title=raw_evidence.get("source_title"),
            section=raw_evidence.get("section"),
            quoted_text=raw_evidence.get("quoted_text"),
        )

    extraction_metadata = None
    raw_meta = view.get("extraction_metadata")
    if raw_meta:
        extraction_metadata = ExtractionMetadataResponse(
            extraction_model=raw_meta.get("extraction_model"),
            confidence_score=raw_meta.get("confidence_score"),
            extractor_version=raw_meta.get("extractor_version"),
            human_reviewed=bool(raw_meta.get("human_reviewed", False)),
        )

    return ProblemDetail(
        id=view["id"],
        statement=view.get("statement") or "",
        status=str(view.get("status") or ProblemStatus.OPEN.value),
        assumptions=view.get("assumptions") or [],
        constraints=view.get("constraints") or [],
        datasets=view.get("datasets") or [],
        metrics=view.get("metrics") or [],
        baselines=view.get("baselines") or [],
        evidence=evidence,
        extraction_metadata=extraction_metadata,
        created_at=view.get("created_at"),
        updated_at=view.get("updated_at"),
        canonical_statement=view.get("canonical_statement"),
        mention_count=view.get("mention_count") or 0,
        paper_count=view.get("paper_count") or 0,
        mentions=[
            ProblemMentionResponse(**mention)
            for mention in (view.get("mentions") or [])
        ],
        papers=view.get("papers") or [],
    )


@router.get("", response_model=ProblemListResponse)
def list_problems(
    status: Optional[str] = Query(default=None, description="Filter by status"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repo: Neo4jRepository = Depends(get_repo),
) -> ProblemListResponse:
    """List canonical problems (ProblemConcepts unioned with legacy Problems)."""
    problem_status = None
    if status:
        try:
            problem_status = ProblemStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    views = repo.list_problem_views(
        status=problem_status,
        limit=limit,
        offset=offset,
    )
    return ProblemListResponse(
        problems=[_view_to_summary(view) for view in views],
        total=len(views),
        limit=limit,
        offset=offset,
    )


@router.get("/{problem_id}", response_model=ProblemDetail)
def get_problem(
    problem_id: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> ProblemDetail:
    """Get a canonical problem by id (concept, mention, or legacy Problem)."""
    try:
        view = repo.get_problem_view(problem_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Problem not found: {problem_id}")
    return _view_to_detail(view)


@router.put("/{problem_id}", response_model=ProblemDetail)
def update_problem(
    problem_id: str,
    update: ProblemUpdate,
    repo: Neo4jRepository = Depends(get_repo),
) -> ProblemDetail:
    """Update a problem's status or statement.

    Canonical ``ProblemConcept`` nodes are written through the concept
    writer; legacy ``:Problem`` nodes keep the original path.
    """
    try:
        view = repo.get_problem_view(problem_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Problem not found: {problem_id}")

    new_status = None
    if update.status:
        try:
            new_status = ProblemStatus(update.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {update.status}")

    if view.get("kind") == "concept":
        updated_view = repo.update_problem_concept(
            problem_id,
            status=new_status,
            statement=update.statement,
        )
        return _view_to_detail(updated_view)

    # Legacy :Problem path.
    try:
        problem = repo.get_problem(problem_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Problem not found: {problem_id}")

    if new_status:
        problem.status = new_status
    if update.statement is not None:
        problem.statement = update.statement

    repo.update_problem(problem)
    refreshed = repo.get_problem_view(problem_id)
    return _view_to_detail(refreshed)


@router.delete("/{problem_id}")
def delete_problem(
    problem_id: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> dict:
    """Soft-delete a problem (concept or legacy)."""
    try:
        repo.delete_problem(problem_id, soft=True)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Problem not found: {problem_id}")
    return {"deleted": True, "id": problem_id}
