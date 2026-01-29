"""
Agent workflow API router.

Endpoints for starting, monitoring, and interacting with
research agent workflows.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agentic_kg.agents.schemas import CheckpointDecision, CheckpointType, WorkflowStatus
from agentic_kg_api.websocket import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

# Module-level runner reference, set during app startup
_runner = None


def set_workflow_runner(runner: Any) -> None:
    """Set the workflow runner instance (called during app startup)."""
    global _runner
    _runner = runner


def _get_runner():
    """Get the workflow runner, raising if not configured."""
    if _runner is None:
        raise HTTPException(
            status_code=503,
            detail="Workflow runner not configured. Agent workflows are not available.",
        )
    return _runner


# --- Request/Response schemas ---


class StartWorkflowRequest(BaseModel):
    """Request to start a new workflow."""

    domain_filter: Optional[str] = None
    status_filter: Optional[str] = None
    max_problems: int = Field(default=20, ge=1, le=100)
    min_confidence: float = Field(default=0.3, ge=0.0, le=1.0)


class StartWorkflowResponse(BaseModel):
    """Response after starting a workflow."""

    run_id: str
    status: str = "running"
    websocket_url: str


class CheckpointDecisionRequest(BaseModel):
    """Request to submit a human decision at a checkpoint."""

    decision: CheckpointDecision
    feedback: str = ""
    edited_data: Optional[dict[str, Any]] = None


class WorkflowStatusResponse(BaseModel):
    """Workflow status summary."""

    run_id: str
    status: str
    current_step: str
    created_at: str
    updated_at: str
    total_steps: int = 7
    completed_steps: int = 0


class WorkflowStateResponse(BaseModel):
    """Full workflow state."""

    run_id: str
    status: str
    current_step: str
    ranked_problems: list[dict] = []
    selected_problem_id: Optional[str] = None
    proposal: Optional[dict] = None
    evaluation_result: Optional[dict] = None
    synthesis_report: Optional[dict] = None
    messages: list[dict] = []
    errors: list[str] = []


# --- Endpoints ---


@router.post("/workflows", response_model=StartWorkflowResponse)
async def start_workflow(request: StartWorkflowRequest) -> StartWorkflowResponse:
    """Start a new research agent workflow."""
    runner = _get_runner()
    try:
        run_id = await runner.start_workflow(
            domain_filter=request.domain_filter,
            status_filter=request.status_filter,
            max_problems=request.max_problems,
            min_confidence=request.min_confidence,
        )
        return StartWorkflowResponse(
            run_id=run_id,
            status="running",
            websocket_url=f"/ws/workflows/{run_id}",
        )
    except Exception as e:
        logger.error(f"Failed to start workflow: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/workflows", response_model=list[WorkflowStatusResponse])
async def list_workflows() -> list[WorkflowStatusResponse]:
    """List all tracked workflows."""
    runner = _get_runner()
    workflows = runner.list_workflows()
    return [
        WorkflowStatusResponse(
            run_id=w["run_id"],
            status=w.get("status", "unknown"),
            current_step=w.get("current_step", ""),
            created_at=w.get("created_at", ""),
            updated_at=w.get("updated_at", ""),
        )
        for w in workflows
    ]


@router.get("/workflows/{run_id}", response_model=WorkflowStateResponse)
async def get_workflow(run_id: str) -> WorkflowStateResponse:
    """Get the full state of a workflow."""
    runner = _get_runner()
    try:
        state = await runner.get_state(run_id)
        return WorkflowStateResponse(
            run_id=state.get("run_id", run_id),
            status=state.get("status", "unknown"),
            current_step=state.get("current_step", ""),
            ranked_problems=state.get("ranked_problems", []),
            selected_problem_id=state.get("selected_problem_id"),
            proposal=state.get("proposal"),
            evaluation_result=state.get("evaluation_result"),
            synthesis_report=state.get("synthesis_report"),
            messages=state.get("messages", []),
            errors=state.get("errors", []),
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Workflow not found: {e}")


@router.post("/workflows/{run_id}/checkpoints/{checkpoint_type}")
async def submit_checkpoint(
    run_id: str,
    checkpoint_type: CheckpointType,
    request: CheckpointDecisionRequest,
) -> WorkflowStateResponse:
    """Submit a human decision at a workflow checkpoint."""
    runner = _get_runner()
    try:
        state = await runner.resume_workflow(
            run_id=run_id,
            checkpoint_type=checkpoint_type,
            decision=request.decision,
            feedback=request.feedback,
            edited_data=request.edited_data,
        )

        # Notify WebSocket clients
        await ws_manager.send_step_update(
            run_id,
            step=checkpoint_type.value,
            status="decided",
            data={"decision": request.decision.value},
        )

        return WorkflowStateResponse(
            run_id=state.get("run_id", run_id),
            status=state.get("status", "unknown"),
            current_step=state.get("current_step", ""),
            ranked_problems=state.get("ranked_problems", []),
            selected_problem_id=state.get("selected_problem_id"),
            proposal=state.get("proposal"),
            evaluation_result=state.get("evaluation_result"),
            synthesis_report=state.get("synthesis_report"),
            messages=state.get("messages", []),
            errors=state.get("errors", []),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Workflow {run_id} not found")
    except Exception as e:
        logger.error(f"Checkpoint submission failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/workflows/{run_id}")
async def cancel_workflow(run_id: str) -> dict:
    """Cancel a running workflow."""
    runner = _get_runner()
    try:
        await runner.cancel_workflow(run_id)
        return {"status": "cancelled", "run_id": run_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Workflow {run_id} not found")


# --- WebSocket endpoint ---


@router.websocket("/ws/workflows/{run_id}")
async def workflow_websocket(websocket: WebSocket, run_id: str) -> None:
    """WebSocket endpoint for real-time workflow updates."""
    await ws_manager.connect(websocket, run_id)
    try:
        while True:
            # Keep connection alive; client can send pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, run_id)
