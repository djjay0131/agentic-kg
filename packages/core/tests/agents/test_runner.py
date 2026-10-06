"""Tests for ``WorkflowRunner`` unknown-workflow handling (issue #76).

``MemorySaver.aget_state`` returns an empty snapshot for an unknown thread id
instead of raising, so a runner that trusts it answers "present" for a
workflow that was never started. The API maps a runner ``KeyError`` to
HTTP 404 and anything else to 500, so these tests pin the runner contract:
an unknown run id raises ``KeyError`` before any graph access.
"""

from __future__ import annotations

import pytest
from agentic_kg.agents.runner import WorkflowRunner
from agentic_kg.agents.schemas import CheckpointDecision, CheckpointType


def _runner_with_tracked(*run_ids: str) -> WorkflowRunner:
    """Build a runner with only the tracked-workflow registry populated.

    The graph, agents and checkpointer are irrelevant to the guard under
    test, so we bypass ``__init__`` rather than construct a real graph.
    """
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner._workflows = {rid: {"run_id": rid} for rid in run_ids}
    return runner


class TestUnknownWorkflow:
    def test_has_workflow_false_for_unknown(self):
        assert _runner_with_tracked().has_workflow("nope") is False

    def test_has_workflow_true_for_tracked(self):
        assert _runner_with_tracked("run-1").has_workflow("run-1") is True

    def test_require_workflow_raises_keyerror(self):
        with pytest.raises(KeyError):
            _runner_with_tracked()._require_workflow("nope")

    async def test_get_state_unknown_raises_keyerror(self):
        with pytest.raises(KeyError):
            await _runner_with_tracked().get_state("nope")

    async def test_resume_workflow_unknown_raises_keyerror(self):
        with pytest.raises(KeyError):
            await _runner_with_tracked().resume_workflow(
                run_id="nope",
                checkpoint_type=CheckpointType.SELECT_PROBLEM,
                decision=CheckpointDecision.APPROVE,
            )

    async def test_cancel_workflow_unknown_raises_keyerror(self):
        with pytest.raises(KeyError):
            await _runner_with_tracked().cancel_workflow("nope")
