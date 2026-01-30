"""
Background task execution for agent workflows.

Runs workflows asynchronously so the API endpoint returns immediately.
Broadcasts progress via the event bus → WebSocket manager bridge.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agentic_kg.agents.events import WorkflowEvent, WorkflowEventType, event_bus
from agentic_kg_api.websocket import ws_manager

logger = logging.getLogger(__name__)

# Track background tasks so they aren't garbage-collected
_running_tasks: dict[str, asyncio.Task] = {}


async def run_workflow_background(
    runner: Any,
    domain_filter: str | None = None,
    status_filter: str | None = None,
    max_problems: int = 20,
    min_confidence: float = 0.3,
) -> str:
    """
    Start a workflow and schedule its execution as a background task.

    Returns the run_id immediately. The workflow continues running
    in the background.
    """
    run_id = await runner.start_workflow(
        domain_filter=domain_filter,
        status_filter=status_filter,
        max_problems=max_problems,
        min_confidence=min_confidence,
    )
    return run_id


async def _bridge_event_to_websocket(event: WorkflowEvent) -> None:
    """Bridge workflow events to WebSocket broadcasts."""
    if event.event_type == WorkflowEventType.STEP_STARTED:
        await ws_manager.send_step_update(
            event.run_id,
            step=event.step,
            status="started",
            data=event.data,
        )
    elif event.event_type == WorkflowEventType.STEP_COMPLETED:
        await ws_manager.send_step_update(
            event.run_id,
            step=event.step,
            status="completed",
            data=event.data,
        )
    elif event.event_type == WorkflowEventType.CHECKPOINT_REACHED:
        await ws_manager.send_checkpoint(
            event.run_id,
            checkpoint_type=event.step,
            data=event.data,
        )
    elif event.event_type == WorkflowEventType.WORKFLOW_COMPLETED:
        await ws_manager.send_complete(event.run_id, summary=event.data)
    elif event.event_type == WorkflowEventType.WORKFLOW_FAILED:
        await ws_manager.send_error(
            event.run_id,
            error=event.data.get("error", "Unknown error"),
        )


def setup_event_bridge() -> None:
    """Subscribe the WebSocket bridge to the event bus."""
    event_bus.subscribe(_bridge_event_to_websocket)


def teardown_event_bridge() -> None:
    """Unsubscribe the WebSocket bridge."""
    event_bus.unsubscribe(_bridge_event_to_websocket)
