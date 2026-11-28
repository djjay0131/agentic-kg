"""Research-progression cycle endpoints.

``GET /api/research/cycles`` lists scheduling ticks; ``GET /api/research/gates``
flattens every workflow currently waiting at a human gate so the Research page
can show one work queue. ``POST /api/research/gates/{cycle}/{workflow}`` records
a decision (who/when/note) on the cycle, persists it in the durable checkpoint
store, and starts the following agent step on the workflow Job. It answers 202
with the cycle while the step runs in the background; when no Job dispatcher is
configured it runs in-request and answers 202 with the step already finished.

Authentication is the shared :func:`agentic_kg_api.dependencies.current_user`
dependency (ADR-0012): staging requires a verified IAP assertion and records the
authenticated email; only ``AUTH_MODE=dev`` accepts ``X-User-ID``. Every gate
has a named human behind it. Rejection still resumes the workflow so LangGraph
closes it, but its routing function never reaches synthesis, so a rejection
writes nothing canonical.
"""

from __future__ import annotations

import logging

from agentic_kg.agents.errors import GateMismatchError, WorkflowConflictError
from agentic_kg.agents.gate_agreement import GATE_NAMES
from agentic_kg.knowledge_graph.models.research_cycle import (
    AgentRecommendation,
    GateDecision,
    ResearchCycle,
)
from agentic_kg.knowledge_graph.research_cycles import (
    ResearchCycleNotFoundError,
)
from agentic_kg.pipeline.cycle import CycleBudgetExceeded, ResearchCycleService
from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg_api.dependencies import current_user, get_research_cycles
from agentic_kg_api.schemas import (
    PendingResearchGate,
    PendingResearchGateListResponse,
    ResearchAgentRecommendation,
    ResearchAgreementResponse,
    ResearchCycleBudgetResponse,
    ResearchCycleListResponse,
    ResearchCycleResponse,
    ResearchGateAgreement,
    ResearchGateDecisionRequest,
    ResearchGateDecisionResponse,
    ResearchGateDisagreement,
    ResearchWorkflowResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/research", tags=["research"])


def _recommendation(
    record: AgentRecommendation | None,
) -> ResearchAgentRecommendation | None:
    if record is None:
        return None
    return ResearchAgentRecommendation(
        action=record.action,
        rationale=record.rationale,
        confidence=record.confidence,
        details=record.details,
        provenance_abstained=record.provenance_abstained,
        produced_at=record.produced_at,
    )


def _decision(record: GateDecision) -> ResearchGateDecisionResponse:
    return ResearchGateDecisionResponse(
        gate=record.gate,
        decision=record.decision,
        decided_by=record.decided_by,
        decided_at=record.decided_at,
        note=record.note,
        edited_data=record.edited_data,
        cycle_id=record.cycle_id,
        workflow_run_id=record.workflow_run_id,
        policy_version=record.policy_version,
        agent_recommendation=_recommendation(record.agent_recommendation),
        human_decision=record.human_decision,
        agreed=record.agreed,
        disagreement_reason=record.disagreement_reason,
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
    return ResearchCycleListResponse(cycles=[_cycle(cycle) for cycle in cycles], total=len(cycles))


@router.get("/agreement", response_model=ResearchAgreementResponse)
def gate_agreement(
    gate: str | None = Query(default=None),
    since: str | None = Query(default=None),
    last_n: int = Query(default=10, ge=1, le=100),
    service: ResearchCycleService = Depends(get_research_cycles),
) -> ResearchAgreementResponse:
    """Per-gate agent/human agreement and the current promotion recommendation.

    ``decisions`` counts only gates with both a recommendation and a human
    decision. ``lower_bound`` is the Wilson 95% lower bound; ``promotion_met``
    says whether the recorded evidence clears the gate's criteria. It never
    changes the policy — promotion is a reviewed edit of the versioned file.
    """
    if gate is not None and gate not in GATE_NAMES:
        raise HTTPException(status_code=400, detail=f"unknown gate: {gate}")
    report = service.agreement(gate=gate, since=since, last_n=last_n)
    return ResearchAgreementResponse(
        since=report.since,
        total_decisions=report.total_decisions,
        total_agreed=report.total_agreed,
        gates=[
            ResearchGateAgreement(
                gate=item.gate,
                mode=item.mode,
                decisions=item.decisions,
                agreed=item.agreed,
                agreement_rate=item.agreement_rate,
                lower_bound=item.lower_bound,
                min_decisions=item.min_decisions,
                min_agreement_lower_bound=item.min_agreement_lower_bound,
                promotion_met=item.promotion_met,
                last_disagreements=[
                    ResearchGateDisagreement(**disagreement.as_dict())
                    for disagreement in item.last_disagreements
                ],
            )
            for item in report.gates
        ],
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
                    recommendation=_recommendation(
                        workflow.pending_recommendation
                    ),
                )
            )
    return PendingResearchGateListResponse(gates=gates, total=len(gates))


@router.post(
    "/gates/{cycle_id}/{workflow_run_id}",
    response_model=ResearchCycleResponse,
    status_code=202,
)
async def decide_gate(
    cycle_id: str,
    workflow_run_id: str,
    request: ResearchGateDecisionRequest,
    user_id: str = Depends(current_user),
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
    except GateMismatchError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "requested_gate": exc.requested_gate,
                "pending_gate": exc.pending_gate,
            },
        )
    except WorkflowConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "run_id": exc.run_id},
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
