"""Research-progression cycle endpoints.

``GET /api/research/cycles`` lists scheduling ticks; ``GET /api/research/gates``
flattens every workflow currently waiting at a human gate so the Research page
can show one work queue. ``POST /api/research/gates/{cycle}/{workflow}`` records
a decision (who/when/note) on the cycle and resumes the workflow through the
durable checkpoint store.

A decision needs ``X-User-ID`` for the same reason the review queue does: every
gate has a named human behind it. Rejection still resumes the workflow so
LangGraph closes it, but its routing function never reaches synthesis, so a
rejection writes nothing canonical.
"""

from __future__ import annotations

import logging
from typing import Optional

from agentic_kg.knowledge_graph.models.research_cycle import (
    GateDecision,
    ResearchCycle,
)
from agentic_kg.knowledge_graph.research_cycles import (
    ResearchCycleNotFoundError,
)
from agentic_kg.pipeline.cycle import CycleBudgetExceeded, ResearchCycleService
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from agentic_kg_api.dependencies import get_research_cycles
from agentic_kg_api.schemas import (
    PendingResearchGate,
    PendingResearchGateListResponse,
    ResearchCycleBudgetResponse,
    ResearchCycleListResponse,
    ResearchCycleResponse,
    ResearchGateDecisionRequest,
    ResearchGateDecisionResponse,
    ResearchWorkflowResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/research", tags=["research"])


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


def _decision(record: GateDecision) -> ResearchGateDecisionResponse:
    return ResearchGateDecisionResponse(
        gate=record.gate,
        decision=record.decision,
        decided_by=record.decided_by,
        decided_at=record.decided_at,
        note=record.note,
        edited_data=record.edited_data,
    )


def _cycle(cycle: ResearchCycle) -> ResearchCycleResponse:
    return ResearchCycleResponse(
        cycle_id=cycle.cycle_id,
        namespace=cycle.namespace,
        started_at=cycle.started_at,
        finished_at=cycle.finished_at,
        status=cycle.status,
        trigger=cycle.trigger,
        policy_version=cycle.policy_version,
        selected_problems=cycle.selected_problems,
        workflow_runs=[
            ResearchWorkflowResponse(
                workflow_run_id=item.workflow_run_id,
                problem_id=item.problem_id,
                problem_statement=item.problem_statement,
                state=item.state,
                current_step=item.current_step,
                pending_gate=item.pending_gate,
                started_at=item.started_at,
                updated_at=item.updated_at,
                finished_at=item.finished_at,
                gate_decisions=[_decision(d) for d in item.gate_decisions],
                error=item.error,
            )
            for item in cycle.workflow_runs
        ],
        budget=ResearchCycleBudgetResponse(**cycle.budget.model_dump()),
        git_sha=cycle.git_sha,
        image=cycle.image,
        note=cycle.note,
    )


@router.get("/cycles", response_model=ResearchCycleListResponse)
def list_cycles(
    limit: int = Query(default=50, ge=1, le=200),
    service: ResearchCycleService = Depends(get_research_cycles),
) -> ResearchCycleListResponse:
    """List cycle records newest first."""
    cycles = service.list(limit=limit)
    return ResearchCycleListResponse(
        cycles=[_cycle(cycle) for cycle in cycles], total=len(cycles)
    )


@router.get("/cycles/{cycle_id}", response_model=ResearchCycleResponse)
def get_cycle(
    cycle_id: str,
    service: ResearchCycleService = Depends(get_research_cycles),
) -> ResearchCycleResponse:
    """Return one cycle with every workflow and gate decision."""
    try:
        return _cycle(service.get(cycle_id))
    except ResearchCycleNotFoundError:
        raise HTTPException(status_code=404, detail=f"Cycle not found: {cycle_id}")


@router.get("/gates", response_model=PendingResearchGateListResponse)
def list_pending_gates(
    limit: int = Query(default=50, ge=1, le=200),
    service: ResearchCycleService = Depends(get_research_cycles),
) -> PendingResearchGateListResponse:
    """Every workflow waiting at a gate, across cycles that still have one."""
    gates: list[PendingResearchGate] = []
    for cycle in service.pending_gates(limit=limit):
        for workflow in cycle.pending_gates():
            gates.append(
                PendingResearchGate(
                    cycle_id=cycle.cycle_id,
                    workflow_run_id=workflow.workflow_run_id,
                    problem_id=workflow.problem_id,
                    problem_statement=workflow.problem_statement,
                    gate=workflow.pending_gate or "",
                    updated_at=workflow.updated_at,
                )
            )
    return PendingResearchGateListResponse(gates=gates, total=len(gates))


@router.post(
    "/gates/{cycle_id}/{workflow_run_id}",
    response_model=ResearchCycleResponse,
)
async def decide_gate(
    cycle_id: str,
    workflow_run_id: str,
    request: ResearchGateDecisionRequest,
    user_id: str = Depends(_require_user),
    service: ResearchCycleService = Depends(get_research_cycles),
) -> ResearchCycleResponse:
    """Approve, reject or edit one gate and resume its workflow.

    The decision is recorded before the resume is attempted, so a failed resume
    leaves an auditable record rather than a silently lost click.
    """
    try:
        cycle = await service.decide_gate(
            cycle_id,
            workflow_run_id,
            gate=request.gate,
            decision=request.decision,
            user_id=user_id,
            note=request.note,
            edited_data=request.edited_data,
        )
    except ResearchCycleNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Cycle or workflow not found: {cycle_id}/{workflow_run_id}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except CycleBudgetExceeded as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    logger.info(
        "research gate %s/%s %s by %s",
        cycle_id,
        workflow_run_id,
        request.decision,
        user_id,
    )
    return _cycle(cycle)


__all__ = ["router"]
