"""Problem CRUD endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg.knowledge_graph.models import Problem, ProblemStatus
from agentic_kg.knowledge_graph.repository import Neo4jRepository, NotFoundError

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import (
    ProblemDetail,
    ProblemListResponse,
    ProblemSummary,
    ProblemUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/problems", tags=["problems"])


def _problem_to_summary(p: Problem) -> ProblemSummary:
    """Convert a Problem model to a summary response."""
    confidence = None
    if p.extraction_metadata:
        confidence = p.extraction_metadata.confidence_score
    return ProblemSummary(
        id=p.id,
        statement=p.statement,
        domain=p.domain,
        status=p.status.value if isinstance(p.status, ProblemStatus) else str(p.status),
        confidence=confidence,
        created_at=p.created_at,
    )


def _problem_to_detail(p: Problem) -> ProblemDetail:
    """Convert a Problem model to a detail response."""
    evidence = None
    if p.evidence:
        evidence = {
            "source_doi": p.evidence.source_doi,
            "source_title": p.evidence.source_title,
            "section": p.evidence.section,
            "quoted_text": p.evidence.quoted_text,
        }

    extraction_metadata = None
    if p.extraction_metadata:
        extraction_metadata = {
            "extraction_model": p.extraction_metadata.extraction_model,
            "confidence_score": p.extraction_metadata.confidence_score,
            "extractor_version": p.extraction_metadata.extractor_version,
            "human_reviewed": p.extraction_metadata.human_reviewed,
        }

    return ProblemDetail(
        id=p.id,
        statement=p.statement,
        domain=p.domain,
        status=p.status.value if isinstance(p.status, ProblemStatus) else str(p.status),
        assumptions=[{"text": a.text, "implicit": a.implicit, "confidence": a.confidence} for a in p.assumptions],
        constraints=[{"text": c.text, "type": c.type.value if hasattr(c.type, "value") else str(c.type), "confidence": c.confidence} for c in p.constraints],
        datasets=[{"name": d.name, "url": d.url, "available": d.available} for d in p.datasets],
        metrics=[{"name": m.name, "description": m.description, "baseline_value": m.baseline_value} for m in p.metrics],
        baselines=[{"name": b.name, "paper_doi": b.paper_doi} for b in p.baselines],
        evidence=evidence,
        extraction_metadata=extraction_metadata,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("", response_model=ProblemListResponse)
def list_problems(
    status: Optional[str] = Query(default=None, description="Filter by status"),
    domain: Optional[str] = Query(default=None, description="Filter by domain"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repo: Neo4jRepository = Depends(get_repo),
) -> ProblemListResponse:
    """List problems with optional filtering."""
    problem_status = None
    if status:
        try:
            problem_status = ProblemStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    problems = repo.list_problems(
        status=problem_status,
        domain=domain,
        limit=limit,
        offset=offset,
    )
    return ProblemListResponse(
        problems=[_problem_to_summary(p) for p in problems],
        total=len(problems),
        limit=limit,
        offset=offset,
    )


@router.get("/{problem_id}", response_model=ProblemDetail)
def get_problem(
    problem_id: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> ProblemDetail:
    """Get a problem by ID."""
    try:
        problem = repo.get_problem(problem_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Problem not found: {problem_id}")
    return _problem_to_detail(problem)


@router.put("/{problem_id}", response_model=ProblemDetail)
def update_problem(
    problem_id: str,
    update: ProblemUpdate,
    repo: Neo4jRepository = Depends(get_repo),
) -> ProblemDetail:
    """Update a problem's status or fields."""
    try:
        problem = repo.get_problem(problem_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Problem not found: {problem_id}")

    if update.status:
        try:
            problem.status = ProblemStatus(update.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {update.status}")
    if update.domain is not None:
        problem.domain = update.domain
    if update.statement is not None:
        problem.statement = update.statement

    updated = repo.update_problem(problem_id, problem)
    return _problem_to_detail(updated)


@router.delete("/{problem_id}")
def delete_problem(
    problem_id: str,
    repo: Neo4jRepository = Depends(get_repo),
) -> dict:
    """Soft-delete a problem."""
    try:
        repo.delete_problem(problem_id, soft=True)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"Problem not found: {problem_id}")
    return {"deleted": True, "id": problem_id}
