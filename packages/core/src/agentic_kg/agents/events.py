"""
Async event bus for workflow step transitions.

Decouples the WorkflowRunner from transport (WebSocket, logging, etc.)
by emitting events that subscribers can handle independently.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class WorkflowEventType(str, Enum):
    """Types of events emitted during workflow execution."""

    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    CHECKPOINT_REACHED = "checkpoint_reached"
    CHECKPOINT_RESOLVED = "checkpoint_resolved"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_CANCELLED = "workflow_cancelled"


@dataclass
class WorkflowEvent:
    """A single workflow event."""

    event_type: WorkflowEventType
    run_id: str
    step: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# Type alias for async event handlers
EventHandler = Callable[[WorkflowEvent], Coroutine[Any, Any, None]]


class WorkflowEventBus:
    """
    Simple async pub/sub event bus for workflow events.

    Handlers are invoked concurrently via asyncio.gather.
    Exceptions in individual handlers are logged but do not
    propagate to the emitter.
    """

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> None:
        """Register an async event handler."""
        self._handlers.append(handler)

    def unsubscribe(self, handler: EventHandler) -> None:
        """Remove a previously registered handler."""
        self._handlers = [h for h in self._handlers if h is not handler]

    async def emit(self, event: WorkflowEvent) -> None:
        """Emit an event to all subscribed handlers."""
        if not self._handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in self._handlers),
            return_exceptions=True,
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Event handler %s failed for %s: %s",
                    self._handlers[i].__name__,
                    event.event_type.value,
                    result,
                )


# Module-level singleton
event_bus = WorkflowEventBus()
