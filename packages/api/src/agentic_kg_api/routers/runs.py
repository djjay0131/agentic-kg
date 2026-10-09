"""Nightly PipelineRun report endpoints.

Contract: ``docs/design/nightly-pipeline-contract.md``. Read-only: the runs are
written by the nightly pipeline job (T29) via
``agentic_kg.knowledge_graph.pipeline_runs.save_pipeline_run``; this router only
reads ``:PipelineRun`` nodes from Neo4j (no GCS access).
"""

from __future__ import annotations

from typing import Optional

from agentic_kg.knowledge_graph.models.pipeline_run import PipelineRun
from agentic_kg.knowledge_graph.pipeline_runs import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    InvalidCursorError,
    PipelineRunNotFoundError,
    PipelineRunRepository,
)
from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg_api.dependencies import get_pipeline_runs
from agentic_kg_api.schemas import PipelineRunListResponse, PipelineRunSummary

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _to_summary(run: PipelineRun) -> PipelineRunSummary:
    """Project a full run onto the list row."""
    return PipelineRunSummary(
        run_id=run.run_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        trigger=run.trigger,
        namespace=run.namespace,
        totals=run.totals,
        budget_stopped=run.budget.stopped_by_budget,
        failures_count=len(run.failures),
        review_queue_size=run.review_queue_size,
    )


@router.get("", response_model=PipelineRunListResponse)
def list_runs(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: Optional[str] = Query(default=None, description="Opaque pagination cursor"),
    store: PipelineRunRepository = Depends(get_pipeline_runs),
) -> PipelineRunListResponse:
    """List nightly runs newest-first, paginated.

    An empty report is a valid answer: ``{"runs": [], "next_cursor": null}``.
    """
    try:
        runs, next_cursor = store.list(limit=limit, cursor=cursor)
    except InvalidCursorError:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return PipelineRunListResponse(
        runs=[_to_summary(run) for run in runs],
        next_cursor=next_cursor,
    )


@router.get("/{run_id}", response_model=PipelineRun)
def get_run(
    run_id: str,
    store: PipelineRunRepository = Depends(get_pipeline_runs),
) -> PipelineRun:
    """Return the full record for one run, or 404 if unknown."""
    try:
        return store.get(run_id)
    except PipelineRunNotFoundError:
        raise HTTPException(status_code=404, detail=f"Pipeline run not found: {run_id}")


__all__ = ["router"]
