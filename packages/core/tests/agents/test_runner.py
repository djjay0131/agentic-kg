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


class _Snapshot:
    def __init__(self, values: dict, next_nodes: tuple = ()):  # noqa: A002
        self.values = values
        self.next = next_nodes


class _FakeGraph:
    """A graph whose node raises, plus a state store for the failure write."""

    def __init__(self, *, raise_on_invoke: bool = False, states: dict | None = None):
        self._raise = raise_on_invoke
        self.states = states or {}
        self.writes: list[dict] = []

    async def ainvoke(self, *_args, **_kwargs):
        if self._raise:
            raise RuntimeError("ranking exploded")
        return None

    async def aget_state(self, config):
        return self.states.get(config["configurable"]["thread_id"], _Snapshot({}))

    async def aupdate_state(self, config, values):
        thread = config["configurable"]["thread_id"]
        self.writes.append(values)
        self.states[thread] = _Snapshot(dict(values), ())


class _ListingCheckpointer:
    def __init__(self, *thread_ids: str):
        self._thread_ids = list(thread_ids)

    def list_thread_ids(self):
        return list(self._thread_ids)


def _bare_runner(graph, checkpointer=None) -> WorkflowRunner:
    runner = WorkflowRunner.__new__(WorkflowRunner)
    runner._graph = graph
    runner._checkpointer = checkpointer or _ListingCheckpointer()
    runner._workflows = {}
    return runner


async def test_start_workflow_records_a_raising_node_as_failed_not_running():
    graph = _FakeGraph(raise_on_invoke=True)
    runner = _bare_runner(graph)

    run_id = await runner.start_workflow()

    meta = runner._workflows[run_id]
    assert meta["status"] == "failed"
    assert meta["current_step"] == "ranking"
    assert "ranking exploded" in meta["error"]
    state = await runner.get_state(run_id)
    assert state["status"] == "failed"
    assert "ranking exploded" in state["errors"][-1]


async def test_reconcile_marks_only_non_resumable_running_threads():
    graph = _FakeGraph(
        states={
            "dead": _Snapshot(
                {"run_id": "dead", "status": "running", "current_step": "ranking"},
                (),
            ),
            "gated": _Snapshot(
                {"run_id": "gated", "status": "running", "current_step": "ranking"},
                ("select_problem",),
            ),
        }
    )
    runner = _bare_runner(graph, _ListingCheckpointer("dead", "gated"))
    runner._workflows = {
        "dead": {"run_id": "dead", "status": "running", "current_step": "ranking"},
        "gated": {"run_id": "gated", "status": "running", "current_step": "ranking"},
    }

    stale = await runner.reconcile_stale_runs()

    assert stale == ["dead"]
    assert runner._workflows["dead"]["status"] == "failed"
    assert "stale" in runner._workflows["dead"]["error"]
    assert runner._workflows["gated"]["status"] == "running"
