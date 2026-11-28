"""Workflow state errors shared by the core runner and the API.

The durable store is the source of truth for a workflow that more than one
process can touch (the API answers a human decision, the Cloud Run Job advances
the graph). These errors make a lost compare-and-set race explicit instead of
letting an older in-memory copy overwrite newer durable state.
"""

from __future__ import annotations

from typing import Optional


class WorkflowConflictError(RuntimeError):
    """A workflow write lost a compare-and-set race with another process."""

    def __init__(self, run_id: str, message: str = "") -> None:
        self.run_id = run_id
        super().__init__(
            message or f"workflow {run_id} was modified by another process"
        )


class GateMismatchError(WorkflowConflictError):
    """A decision named a gate that is not the one currently pending.

    ``pending_gate`` is the gate the durable state actually shows (``None``
    when the run is not waiting at any gate, e.g. a step is in flight or the run
    is terminal). The API returns it to the UI so the page can show the right
    gate instead of applying a decision to stale state.
    """

    def __init__(
        self,
        run_id: str,
        requested_gate: str,
        pending_gate: Optional[str],
    ) -> None:
        self.requested_gate = requested_gate
        self.pending_gate = pending_gate
        super().__init__(
            run_id,
            f"workflow {run_id} is waiting at {pending_gate!r}, "
            f"not {requested_gate!r}",
        )


class CheckpointConflictError(WorkflowConflictError):
    """A checkpoint write was based on a revision the store has moved past."""

    def __init__(
        self,
        thread_id: str,
        expected_revision: Optional[int],
        actual_revision: Optional[int],
    ) -> None:
        self.thread_id = thread_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            thread_id,
            f"checkpoint for {thread_id} changed under this writer "
            f"(expected revision {expected_revision}, store has "
            f"{actual_revision})",
        )


__all__ = [
    "CheckpointConflictError",
    "GateMismatchError",
    "WorkflowConflictError",
]
