"""Durable checkpoint store: a gated workflow survives a process restart."""

from __future__ import annotations

from typing import TypedDict

import pytest
from agentic_kg.agents.checkpoint_store import (
    DurableCheckpointSaver,
    InMemoryCheckpointStore,
    Neo4jCheckpointStore,
)
from langgraph.graph import END, StateGraph


class TinyState(TypedDict, total=False):
    value: int
    result: str


def _add_one(state: TinyState) -> TinyState:
    return {**state, "value": state.get("value", 0) + 1}


def _finish(state: TinyState) -> TinyState:
    return {**state, "result": f"finished {state['value']}"}


def _build(saver):
    graph = StateGraph(TinyState)
    graph.add_node("add_one", _add_one)
    graph.add_node("finish", _finish)
    graph.set_entry_point("add_one")
    graph.add_edge("add_one", "finish")
    graph.add_edge("finish", END)
    return graph.compile(
        checkpointer=saver,
        interrupt_before=["finish"],
    )


CONFIG = {"configurable": {"thread_id": "thread-1"}}


async def test_gated_workflow_resumes_in_a_new_saver_over_the_same_store():
    store = InMemoryCheckpointStore()

    first = DurableCheckpointSaver(store)
    graph = _build(first)
    await graph.ainvoke({"value": 0}, config=CONFIG)

    snapshot = await graph.aget_state(CONFIG)
    assert snapshot.values == {"value": 1}
    assert snapshot.next == ("finish",)
    assert store.load_thread("thread-1") is not None

    # A "different process": a fresh saver and a freshly compiled graph over
    # the same durable store must see the same checkpoint and resume from it.
    second = DurableCheckpointSaver(store)
    resumed_graph = _build(second)
    assert second.has_thread("thread-1") is True

    resumed = await resumed_graph.aget_state(CONFIG)
    assert resumed.values == {"value": 1}
    assert resumed.next == ("finish",)

    await resumed_graph.ainvoke(None, config=CONFIG)
    assert (await resumed_graph.aget_state(CONFIG)).values == {
        "value": 1,
        "result": "finished 1",
    }


async def test_new_saver_does_not_see_a_thread_that_was_never_written():
    store = InMemoryCheckpointStore()
    saver = DurableCheckpointSaver(store)
    assert saver.has_thread("unknown") is False


async def test_list_thread_ids_sees_durable_threads():
    store = InMemoryCheckpointStore()
    saver = DurableCheckpointSaver(store)
    graph = _build(saver)
    await graph.ainvoke({"value": 0}, config=CONFIG)

    assert store.list_thread_ids() == ["thread-1"]
    assert DurableCheckpointSaver(store).list_thread_ids() == ["thread-1"]


async def test_delete_thread_removes_the_persisted_payload():
    store = InMemoryCheckpointStore()
    saver = DurableCheckpointSaver(store)
    graph = _build(saver)
    await graph.ainvoke({"value": 0}, config=CONFIG)
    assert store.load_thread("thread-1") is not None

    await saver.adelete_thread("thread-1")
    assert store.load_thread("thread-1") is None
    assert DurableCheckpointSaver(store).has_thread("thread-1") is False


class _FakeSession:
    def __init__(self, store):
        self.store = store

    def execute_write(self, work):
        return work(_FakeTx(self.store))

    def execute_read(self, work):
        return work(_FakeTx(self.store))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeTx:
    def __init__(self, store):
        self.store = store
        self.calls = []

    def run(self, query, **params):
        self.store.calls.append((query, params))
        from unittest.mock import MagicMock

        result = MagicMock()
        if "RETURN c.revision AS revision" in query or (
            "RETURN r.revision AS revision" in query
        ):
            result.single.return_value = {"revision": 1}
        else:
            result.single.return_value = None
        return result


class _FakeRepo:
    def __init__(self):
        self.calls = []

    def session(self):
        return _FakeSession(self)


def test_run_metadata_survives_a_new_saver_over_the_same_store():
    """The workflow list must survive a restart (metadata beside checkpoints)."""
    store = InMemoryCheckpointStore()
    first = DurableCheckpointSaver(store)
    first.save_run(
        "thread-1",
        {"run_id": "thread-1", "status": "waiting_checkpoint", "current_step": "select_problem"},
    )

    second = DurableCheckpointSaver(store)
    assert second.load_run("thread-1")["status"] == "waiting_checkpoint"
    assert [run["run_id"] for run in second.list_runs()] == ["thread-1"]
    assert second.load_run("unknown") is None


def test_in_memory_store_metadata_is_upserted():
    store = InMemoryCheckpointStore()
    store.save_run("run-1", {"run_id": "run-1", "status": "running"})
    store.save_run("run-1", {"run_id": "run-1", "status": "completed"})
    assert store.load_run("run-1")["status"] == "completed"
    assert store.list_runs() == [{"run_id": "run-1", "status": "completed"}]


def test_neo4j_store_uses_the_app_namespace_label_and_a_uid_merge():
    repo = _FakeRepo()
    store = Neo4jCheckpointStore(repo, namespace="canon")
    store.save_thread("thread-1", {"storage": {}, "writes": {}, "blobs": {}})

    query, params = repo.calls[-1]
    assert "Canon__AgentCheckpoint" in query
    assert "MERGE" in query
    assert params["uid"] == "canon\x1fthread-1"
    assert params["props"]["ns"] == "canon"
    assert params["props"]["thread_id"] == "thread-1"


def test_neo4j_store_persists_run_metadata_beside_checkpoints():
    repo = _FakeRepo()
    store = Neo4jCheckpointStore(repo, namespace="canon")
    store.save_run("run-1", {"run_id": "run-1", "status": "waiting_checkpoint"})

    query, params = repo.calls[-1]
    assert "Canon__AgentRun" in query
    assert "MERGE" in query
    assert params["uid"] == "canon\x1frun-1"
    assert params["props"]["run_id"] == "run-1"
    assert '"status": "waiting_checkpoint"' in params["props"]["metadata"]


def test_neo4j_store_rejects_a_namespace_mismatch_on_load_shape():
    repo = _FakeRepo()
    store = Neo4jCheckpointStore(repo, namespace="canon")
    assert store.load_thread("missing") is None
    query, params = repo.calls[-1]
    assert params["ns"] == "canon"
    assert params["thread_id"] == "missing"


async def test_a_loaded_saver_reloads_a_thread_another_process_advanced():
    """The API process must not keep serving an old checkpoint (T65)."""
    store = InMemoryCheckpointStore()
    api = DurableCheckpointSaver(store)
    api_graph = _build(api)
    # The API reads before the Job has written anything, caching "no thread".
    assert (await api_graph.aget_state(CONFIG)).values == {}

    job = DurableCheckpointSaver(store)
    await _build(job).ainvoke({"value": 0}, config=CONFIG)

    # No restart: the same saver must pick up the Job's newer revision.
    snapshot = await api_graph.aget_state(CONFIG)
    assert snapshot.values == {"value": 1}
    assert snapshot.next == ("finish",)
    assert store.thread_revision("thread-1") >= 1


def test_store_revision_is_monotonic_and_compare_and_set_rejects_a_stale_write():
    from agentic_kg.agents.errors import CheckpointConflictError

    store = InMemoryCheckpointStore()
    assert store.thread_revision("thread-1") == 0
    assert store.save_thread("thread-1", {"value": 1}, expected_revision=0) == 1
    with pytest.raises(CheckpointConflictError):
        store.save_thread("thread-1", {"value": 99}, expected_revision=0)
    # The newer stored state is untouched by the stale writer.
    assert store.load_thread("thread-1")["value"] == 1
    assert store.thread_revision("thread-1") == 1
    assert store.save_thread("thread-1", {"value": 2}, expected_revision=1) == 2
    assert store.load_thread("thread-1")["value"] == 2


def test_run_metadata_revision_rejects_a_stale_metadata_write():
    from agentic_kg.agents.errors import CheckpointConflictError

    store = InMemoryCheckpointStore()
    assert store.save_run("run-1", {"status": "running"}, expected_revision=0) == 1
    with pytest.raises(CheckpointConflictError):
        store.save_run("run-1", {"status": "cancelled"}, expected_revision=0)
    assert store.load_run("run-1")["status"] == "running"
    assert store.run_revision("run-1") == 1
