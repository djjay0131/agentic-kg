"""Durable LangGraph checkpoints for the research workflow.

``WorkflowRunner`` used ``MemorySaver`` by default, so a workflow that stopped at
a human gate vanished when the process exited. A gate the brief expects to
resume "days later" cannot do that on an in-memory saver. This module keeps the
same LangGraph semantics (it subclasses ``InMemorySaver``) but persists each
thread's checkpoint state after every write.

Two backends are shipped:

* :class:`InMemoryCheckpointStore` -- a process-local store used by tests and
  offline runs. It can simulate a restart: build a new saver over the same
  store and the workflow resumes.
* :class:`Neo4jCheckpointStore` -- a ``:Canon__AgentCheckpoint`` node per thread
  under the canonical namespace. App-namespace workflow state, never canonical
  graph content (ADR-0004/0005).

The payload is the saver's own serialized checkpoint bytes, base64-encoded so a
single JSON property can hold them. Because it is the saver's serialization, no
LangGraph object is pickled by this module and the graph's state schema stays
the only source of truth. The checkpoint API is pinned by
``packages/core/tests/agents/test_checkpoint_store.py``: a graph interrupted in
one saver resumes in a *different* saver instance over the same store.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from langgraph.checkpoint.memory import InMemorySaver

from agentic_kg.knowledge_graph.repository import Neo4jRepository

LABEL_AGENT_CHECKPOINT = "Canon__AgentCheckpoint"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _encode_typed(value: tuple[str, bytes]) -> list[str]:
    return [value[0], base64.b64encode(value[1]).decode("ascii")]


def _decode_typed(value: list[str]) -> tuple[str, bytes]:
    return value[0], base64.b64decode(value[1].encode("ascii"))


@runtime_checkable
class CheckpointStore(Protocol):
    """A durable place to keep one serialized thread payload."""

    def load_thread(self, thread_id: str) -> Optional[dict[str, Any]]:
        """Return the stored payload for ``thread_id``, or ``None``."""

    def save_thread(self, thread_id: str, payload: dict[str, Any]) -> None:
        """Persist the payload for ``thread_id`` (upsert)."""

    def delete_thread(self, thread_id: str) -> None:
        """Remove every checkpoint for ``thread_id``."""


class InMemoryCheckpointStore:
    """Process-local store. The same instance can outlive a saver (tests)."""

    def __init__(self) -> None:
        self._payloads: dict[str, str] = {}

    def load_thread(self, thread_id: str) -> Optional[dict[str, Any]]:
        raw = self._payloads.get(thread_id)
        return json.loads(raw) if raw is not None else None

    def save_thread(self, thread_id: str, payload: dict[str, Any]) -> None:
        self._payloads[thread_id] = json.dumps(payload, sort_keys=True)

    def delete_thread(self, thread_id: str) -> None:
        self._payloads.pop(thread_id, None)


class Neo4jCheckpointStore:
    """One ``:Canon__AgentCheckpoint`` node per thread."""

    def __init__(
        self,
        repository: Neo4jRepository,
        *,
        namespace: str = "canon",
    ) -> None:
        self._repo = repository
        self.namespace = namespace

    def load_thread(self, thread_id: str) -> Optional[dict[str, Any]]:
        def _load(tx: Any) -> Optional[str]:
            record = tx.run(
                f"MATCH (c:{LABEL_AGENT_CHECKPOINT} {{ns: $ns, thread_id: $thread_id}}) "
                "RETURN c.payload AS payload LIMIT 1",
                ns=self.namespace,
                thread_id=thread_id,
            ).single()
            return record["payload"] if record else None

        with self._repo.session() as session:
            raw = session.execute_read(_load)
        return json.loads(raw) if raw else None

    def save_thread(self, thread_id: str, payload: dict[str, Any]) -> None:
        uid = f"{self.namespace}\x1f{thread_id}"
        props = {
            "uid": uid,
            "ns": self.namespace,
            "thread_id": thread_id,
            "payload": json.dumps(payload, sort_keys=True),
            "updated_at": _now_iso(),
        }

        def _save(tx: Any) -> None:
            tx.run(
                f"MERGE (c:{LABEL_AGENT_CHECKPOINT} {{uid: $uid}}) SET c = $props",
                uid=uid,
                props=props,
            )

        with self._repo.session() as session:
            session.execute_write(_save)

    def delete_thread(self, thread_id: str) -> None:
        def _delete(tx: Any) -> None:
            tx.run(
                f"MATCH (c:{LABEL_AGENT_CHECKPOINT} {{ns: $ns, thread_id: $thread_id}}) "
                "DELETE c",
                ns=self.namespace,
                thread_id=thread_id,
            )

        with self._repo.session() as session:
            session.execute_write(_delete)


class DurableCheckpointSaver(InMemorySaver):
    """An ``InMemorySaver`` that persists each thread through a store."""

    def __init__(self, store: CheckpointStore) -> None:
        super().__init__()
        self._store = store
        self._loaded: set[str] = set()

    # -- payload conversion -------------------------------------------------

    def _serialise_thread(self, thread_id: str) -> dict[str, Any]:
        storage: dict[str, Any] = {}
        for checkpoint_ns, checkpoints in self.storage.get(thread_id, {}).items():
            storage[checkpoint_ns] = {
                checkpoint_id: {
                    "checkpoint": _encode_typed(entry[0]),
                    "metadata": _encode_typed(entry[1]),
                    "parent": entry[2],
                }
                for checkpoint_id, entry in checkpoints.items()
            }

        writes: dict[str, Any] = {}
        for (tid, checkpoint_ns, checkpoint_id), entries in self.writes.items():
            if tid != thread_id:
                continue
            writes.setdefault(checkpoint_ns, {})[checkpoint_id] = [
                {
                    "task_id": entry[0],
                    "idx": key[1],
                    "channel": entry[1],
                    "value": _encode_typed(entry[2]),
                    "task_path": entry[3],
                }
                for key, entry in entries.items()
            ]

        blobs: dict[str, Any] = {}
        for (tid, checkpoint_ns, channel, version), value in self.blobs.items():
            if tid != thread_id:
                continue
            blobs.setdefault(checkpoint_ns, {}).setdefault(channel, {})[
                str(version)
            ] = _encode_typed(value)

        return {"storage": storage, "writes": writes, "blobs": blobs}

    def _load_payload(self, thread_id: str, payload: dict[str, Any]) -> None:
        for checkpoint_ns, checkpoints in payload.get("storage", {}).items():
            for checkpoint_id, entry in checkpoints.items():
                self.storage[thread_id][checkpoint_ns][checkpoint_id] = (
                    _decode_typed(entry["checkpoint"]),
                    _decode_typed(entry["metadata"]),
                    entry.get("parent"),
                )

        for checkpoint_ns, checkpoints in payload.get("writes", {}).items():
            for checkpoint_id, entries in checkpoints.items():
                outer = (thread_id, checkpoint_ns, checkpoint_id)
                for entry in entries:
                    self.writes[outer][
                        (entry["task_id"], int(entry["idx"]))
                    ] = (
                        entry["task_id"],
                        entry["channel"],
                        _decode_typed(entry["value"]),
                        entry["task_path"],
                    )

        for checkpoint_ns, channels in payload.get("blobs", {}).items():
            for channel, versions in channels.items():
                for version, value in versions.items():
                    self.blobs[(thread_id, checkpoint_ns, channel, version)] = (
                        _decode_typed(value)
                    )

    def _ensure_loaded(self, thread_id: str) -> None:
        if thread_id in self._loaded:
            return
        payload = self._store.load_thread(thread_id)
        if payload:
            self._load_payload(thread_id, payload)
        self._loaded.add(thread_id)

    def _persist(self, thread_id: str) -> None:
        self._store.save_thread(thread_id, self._serialise_thread(thread_id))
        self._loaded.add(thread_id)

    def has_thread(self, thread_id: str) -> bool:
        """Whether the store has any checkpoint for ``thread_id``."""
        self._ensure_loaded(thread_id)
        return bool(self.storage.get(thread_id))

    # -- BaseCheckpointSaver overrides -------------------------------------

    def get_tuple(self, config: Any):
        thread_id = config["configurable"]["thread_id"]
        self._ensure_loaded(thread_id)
        return super().get_tuple(config)

    async def aget_tuple(self, config: Any):
        thread_id = config["configurable"]["thread_id"]
        self._ensure_loaded(thread_id)
        return await super().aget_tuple(config)

    def list(self, config: Any, **kwargs: Any):
        if config is not None:
            self._ensure_loaded(config["configurable"]["thread_id"])
        yield from super().list(config, **kwargs)

    async def alist(self, config: Any, **kwargs: Any):
        if config is not None:
            self._ensure_loaded(config["configurable"]["thread_id"])
        async for item in super().alist(config, **kwargs):
            yield item

    def put(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any):
        thread_id = config["configurable"]["thread_id"]
        self._ensure_loaded(thread_id)
        result = super().put(config, checkpoint, metadata, new_versions)
        self._persist(thread_id)
        return result

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any):
        thread_id = config["configurable"]["thread_id"]
        self._ensure_loaded(thread_id)
        result = await super().aput(config, checkpoint, metadata, new_versions)
        self._persist(thread_id)
        return result

    def put_writes(
        self,
        config: Any,
        writes: Any,
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        self._ensure_loaded(thread_id)
        super().put_writes(config, writes, task_id, task_path)
        self._persist(thread_id)

    async def aput_writes(
        self,
        config: Any,
        writes: Any,
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        self._ensure_loaded(thread_id)
        await super().aput_writes(config, writes, task_id, task_path)
        self._persist(thread_id)

    def delete_thread(self, thread_id: str) -> None:
        super().delete_thread(thread_id)
        self._loaded.add(thread_id)
        self._store.delete_thread(thread_id)

    async def adelete_thread(self, thread_id: str) -> None:
        await super().adelete_thread(thread_id)
        self._loaded.add(thread_id)
        self._store.delete_thread(thread_id)


def create_durable_checkpointer(
    repository: Neo4jRepository,
    *,
    namespace: str = "canon",
) -> DurableCheckpointSaver:
    """A Neo4j-backed saver for the research workflow."""
    return DurableCheckpointSaver(
        Neo4jCheckpointStore(repository, namespace=namespace)
    )


__all__ = [
    "CheckpointStore",
    "DurableCheckpointSaver",
    "InMemoryCheckpointStore",
    "LABEL_AGENT_CHECKPOINT",
    "Neo4jCheckpointStore",
    "create_durable_checkpointer",
]
