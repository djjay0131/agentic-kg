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

Run metadata (status, current step, timestamps, error) is persisted through the
same store on a ``:Canon__AgentRun`` node. The workflow list used to live only
in ``WorkflowRunner``'s process memory, so every deploy showed "No workflows
yet" and a gate decision on a second Cloud Run instance could not see the run.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from langgraph.checkpoint.memory import InMemorySaver

from agentic_kg.agents.errors import CheckpointConflictError
from agentic_kg.knowledge_graph.repository import Neo4jRepository

LABEL_AGENT_CHECKPOINT = "Canon__AgentCheckpoint"
#: Run metadata (status, current step, timestamps, error) lives beside the
#: checkpoint blobs so the workflow list survives a restart and is shared by
#: every API instance. App namespace, same as the checkpoint nodes.
LABEL_AGENT_RUN = "Canon__AgentRun"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _encode_typed(value: tuple[str, bytes]) -> list[str]:
    return [value[0], base64.b64encode(value[1]).decode("ascii")]


def _decode_typed(value: list[str]) -> tuple[str, bytes]:
    return value[0], base64.b64decode(value[1].encode("ascii"))


@runtime_checkable
class CheckpointStore(Protocol):
    """A durable place to keep one serialized thread payload.

    Every write is a compare-and-set on a monotonically increasing revision.
    ``expected_revision=0`` means "the row must not exist yet"; a positive value
    means "the row is at this revision". A mismatch raises
    :class:`CheckpointConflictError` and leaves the stored copy untouched, so a
    stale process can never overwrite a newer one.
    """

    def load_thread(self, thread_id: str) -> Optional[dict[str, Any]]:
        """Return the stored payload for ``thread_id``, or ``None``."""

    def thread_revision(self, thread_id: str) -> int:
        """The stored revision for ``thread_id`` (``0`` when absent)."""

    def save_thread(
        self,
        thread_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: Optional[int] = None,
    ) -> int:
        """Persist the payload for ``thread_id`` and return the new revision."""

    def delete_thread(self, thread_id: str) -> None:
        """Remove every checkpoint for ``thread_id``."""

    def list_thread_ids(self) -> list[str]:
        """Return every thread id with a stored checkpoint."""

    def run_revision(self, run_id: str) -> int:
        """The stored metadata revision for ``run_id`` (``0`` when absent)."""

    def save_run(
        self,
        run_id: str,
        metadata: dict[str, Any],
        *,
        expected_revision: Optional[int] = None,
    ) -> int:
        """Persist the list/detail metadata for one workflow run."""

    def load_run(self, run_id: str) -> Optional[dict[str, Any]]:
        """Return the persisted run metadata for ``run_id``, or ``None``."""

    def list_runs(self) -> list[dict[str, Any]]:
        """Return every persisted run's metadata."""


class InMemoryCheckpointStore:
    """Process-local store. The same instance can outlive a saver (tests)."""

    def __init__(self) -> None:
        self._payloads: dict[str, str] = {}
        self._runs: dict[str, str] = {}
        self._run_revisions: dict[str, int] = {}

    def load_thread(self, thread_id: str) -> Optional[dict[str, Any]]:
        raw = self._payloads.get(thread_id)
        return json.loads(raw) if raw is not None else None

    def thread_revision(self, thread_id: str) -> int:
        payload = self.load_thread(thread_id)
        if payload is None:
            return 0
        return int(payload.get("revision") or 0)

    def save_thread(
        self,
        thread_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: Optional[int] = None,
    ) -> int:
        current = self.thread_revision(thread_id)
        if expected_revision is not None and current != expected_revision:
            raise CheckpointConflictError(thread_id, expected_revision, current)
        revision = current + 1
        stored = dict(payload)
        stored["revision"] = revision
        self._payloads[thread_id] = json.dumps(stored, sort_keys=True)
        return revision

    def delete_thread(self, thread_id: str) -> None:
        self._payloads.pop(thread_id, None)

    def list_thread_ids(self) -> list[str]:
        return list(self._payloads)

    def run_revision(self, run_id: str) -> int:
        if run_id not in self._runs:
            return 0
        return int(self._run_revisions.get(run_id, 0))

    def save_run(
        self,
        run_id: str,
        metadata: dict[str, Any],
        *,
        expected_revision: Optional[int] = None,
    ) -> int:
        current = self.run_revision(run_id)
        if expected_revision is not None and current != expected_revision:
            raise CheckpointConflictError(run_id, expected_revision, current)
        revision = current + 1
        self._runs[run_id] = json.dumps(metadata, sort_keys=True)
        self._run_revisions[run_id] = revision
        return revision

    def load_run(self, run_id: str) -> Optional[dict[str, Any]]:
        raw = self._runs.get(run_id)
        return json.loads(raw) if raw is not None else None

    def list_runs(self) -> list[dict[str, Any]]:
        return [json.loads(raw) for raw in self._runs.values()]


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

    def thread_revision(self, thread_id: str) -> int:
        uid = f"{self.namespace}\x1f{thread_id}"

        def _read(tx: Any) -> int:
            record = tx.run(
                f"MATCH (c:{LABEL_AGENT_CHECKPOINT} {{uid: $uid}}) "
                "RETURN coalesce(c.revision, 0) AS revision LIMIT 1",
                uid=uid,
            ).single()
            return int(record["revision"] or 0) if record else 0

        with self._repo.session() as session:
            return session.execute_read(_read)

    def save_thread(
        self,
        thread_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: Optional[int] = None,
    ) -> int:
        uid = f"{self.namespace}\x1f{thread_id}"
        props = {
            "uid": uid,
            "ns": self.namespace,
            "thread_id": thread_id,
            "payload": json.dumps(payload, sort_keys=True),
            "updated_at": _now_iso(),
        }

        def _save(tx: Any) -> Optional[int]:
            if expected_revision is None:
                query = (
                    f"MERGE (c:{LABEL_AGENT_CHECKPOINT} {{uid: $uid}}) "
                    "WITH c, coalesce(c.revision, 0) AS current "
                    "SET c = $props, c.revision = current + 1 "
                    "RETURN c.revision AS revision"
                )
            elif expected_revision == 0:
                # The row must not exist yet. MERGE lets this process create it
                # atomically; an existing node at a non-zero revision fails the
                # WHERE and returns no record (a conflict).
                query = (
                    f"MERGE (c:{LABEL_AGENT_CHECKPOINT} {{uid: $uid}}) "
                    "ON CREATE SET c.revision = 0 "
                    "WITH c, coalesce(c.revision, 0) AS current "
                    "WHERE current = $expected "
                    "SET c = $props, c.revision = current + 1 "
                    "RETURN c.revision AS revision"
                )
            else:
                query = (
                    f"MATCH (c:{LABEL_AGENT_CHECKPOINT} {{uid: $uid}}) "
                    "WITH c, coalesce(c.revision, 0) AS current "
                    "WHERE current = $expected "
                    "SET c = $props, c.revision = current + 1 "
                    "RETURN c.revision AS revision"
                )
            record = tx.run(
                query,
                uid=uid,
                props=props,
                expected=expected_revision,
            ).single()
            return int(record["revision"]) if record else None

        with self._repo.session() as session:
            revision = session.execute_write(_save)
        if revision is None:
            actual = self.thread_revision(thread_id)
            raise CheckpointConflictError(thread_id, expected_revision, actual)
        return revision

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

    def list_thread_ids(self) -> list[str]:
        def _list(tx: Any) -> list[str]:
            records = tx.run(
                f"MATCH (c:{LABEL_AGENT_CHECKPOINT} {{ns: $ns}}) "
                "RETURN c.thread_id AS thread_id",
                ns=self.namespace,
            )
            return [str(record["thread_id"]) for record in records]

        with self._repo.session() as session:
            return session.execute_read(_list)

    def run_revision(self, run_id: str) -> int:
        uid = f"{self.namespace}\x1f{run_id}"

        def _read(tx: Any) -> int:
            record = tx.run(
                f"MATCH (r:{LABEL_AGENT_RUN} {{uid: $uid}}) "
                "RETURN coalesce(r.revision, 0) AS revision LIMIT 1",
                uid=uid,
            ).single()
            return int(record["revision"] or 0) if record else 0

        with self._repo.session() as session:
            return session.execute_read(_read)

    def save_run(
        self,
        run_id: str,
        metadata: dict[str, Any],
        *,
        expected_revision: Optional[int] = None,
    ) -> int:
        uid = f"{self.namespace}\x1f{run_id}"
        props = {
            "uid": uid,
            "ns": self.namespace,
            "run_id": run_id,
            "metadata": json.dumps(metadata, sort_keys=True),
            "updated_at": _now_iso(),
        }

        def _save(tx: Any) -> Optional[int]:
            if expected_revision is None:
                query = (
                    f"MERGE (r:{LABEL_AGENT_RUN} {{uid: $uid}}) "
                    "WITH r, coalesce(r.revision, 0) AS current "
                    "SET r = $props, r.revision = current + 1 "
                    "RETURN r.revision AS revision"
                )
            elif expected_revision == 0:
                query = (
                    f"MERGE (r:{LABEL_AGENT_RUN} {{uid: $uid}}) "
                    "ON CREATE SET r.revision = 0 "
                    "WITH r, coalesce(r.revision, 0) AS current "
                    "WHERE current = $expected "
                    "SET r = $props, r.revision = current + 1 "
                    "RETURN r.revision AS revision"
                )
            else:
                query = (
                    f"MATCH (r:{LABEL_AGENT_RUN} {{uid: $uid}}) "
                    "WITH r, coalesce(r.revision, 0) AS current "
                    "WHERE current = $expected "
                    "SET r = $props, r.revision = current + 1 "
                    "RETURN r.revision AS revision"
                )
            record = tx.run(
                query,
                uid=uid,
                props=props,
                expected=expected_revision,
            ).single()
            return int(record["revision"]) if record else None

        with self._repo.session() as session:
            revision = session.execute_write(_save)
        if revision is None:
            actual = self.run_revision(run_id)
            raise CheckpointConflictError(run_id, expected_revision, actual)
        return revision

    def load_run(self, run_id: str) -> Optional[dict[str, Any]]:
        def _load(tx: Any) -> Optional[str]:
            record = tx.run(
                f"MATCH (r:{LABEL_AGENT_RUN} {{ns: $ns, run_id: $run_id}}) "
                "RETURN r.metadata AS metadata LIMIT 1",
                ns=self.namespace,
                run_id=run_id,
            ).single()
            return record["metadata"] if record else None

        with self._repo.session() as session:
            raw = session.execute_read(_load)
        return json.loads(raw) if raw else None

    def list_runs(self) -> list[dict[str, Any]]:
        def _list(tx: Any) -> list[str]:
            records = tx.run(
                f"MATCH (r:{LABEL_AGENT_RUN} {{ns: $ns}}) "
                "RETURN r.metadata AS metadata",
                ns=self.namespace,
            )
            return [
                str(record["metadata"]) for record in records if record["metadata"]
            ]

        with self._repo.session() as session:
            raw = session.execute_read(_list)
        return [json.loads(item) for item in raw]


class DurableCheckpointSaver(InMemorySaver):
    """An ``InMemorySaver`` that persists each thread through a store."""

    def __init__(self, store: CheckpointStore) -> None:
        super().__init__()
        self._store = store
        #: thread id -> revision this saver's in-memory copy corresponds to.
        #: ``0`` means the store has no checkpoint for the thread.
        self._loaded: dict[str, int] = {}

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

    def _drop_thread(self, thread_id: str) -> None:
        """Discard this process's in-memory copy of one thread."""
        self.storage.pop(thread_id, None)
        for key in [key for key in list(self.writes) if key[0] == thread_id]:
            self.writes.pop(key, None)
        for key in [key for key in list(self.blobs) if key[0] == thread_id]:
            self.blobs.pop(key, None)

    def _stored_revision(self, thread_id: str) -> int:
        reader = getattr(self._store, "thread_revision", None)
        if reader is not None:
            return int(reader(thread_id) or 0)
        payload = self._store.load_thread(thread_id)
        return int((payload or {}).get("revision", 0) or 0)

    def _ensure_loaded(self, thread_id: str) -> None:
        """Make the in-memory copy match the store's current revision.

        ``InMemorySaver`` loads a thread once and never looks again. That is
        wrong when a *different* process (the Cloud Run Job) advances the run:
        the API would keep serving its stale gate, proposal and
        ``evaluation_result``. The store is the source of truth, so a newer
        stored revision replaces the in-memory copy before any read.
        """
        stored = self._stored_revision(thread_id)
        known = self._loaded.get(thread_id)
        if known is not None and known == stored:
            return
        self._drop_thread(thread_id)
        payload = self._store.load_thread(thread_id)
        if payload:
            self._load_payload(thread_id, payload)
        self._loaded[thread_id] = stored

    def _persist(self, thread_id: str) -> None:
        expected = self._loaded.get(thread_id, 0)
        try:
            revision = self._store.save_thread(
                thread_id,
                self._serialise_thread(thread_id),
                expected_revision=expected,
            )
        except CheckpointConflictError:
            # Adopt the newer stored copy before surfacing the conflict. Never
            # leave stale bytes in this saver that a later read would serve.
            self._drop_thread(thread_id)
            fresh = self._stored_revision(thread_id)
            payload = self._store.load_thread(thread_id)
            if payload:
                self._load_payload(thread_id, payload)
            self._loaded[thread_id] = fresh
            raise
        self._loaded[thread_id] = int(revision)

    def has_thread(self, thread_id: str) -> bool:
        """Whether the store has any checkpoint for ``thread_id``."""
        self._ensure_loaded(thread_id)
        return bool(self.storage.get(thread_id))

    def list_thread_ids(self) -> list[str]:
        """Every thread id the durable store knows about."""
        lister = getattr(self._store, "list_thread_ids", None)
        if lister is None:
            return list(self.storage)
        return lister()

    # -- run metadata (alongside the checkpoints) --------------------------

    def save_run(
        self,
        run_id: str,
        metadata: dict[str, Any],
        *,
        expected_revision: Optional[int] = None,
    ) -> int:
        """Persist a run's list/detail metadata in the same durable store."""
        return self._store.save_run(
            run_id, metadata, expected_revision=expected_revision
        )

    def run_revision(self, run_id: str) -> int:
        reader = getattr(self._store, "run_revision", None)
        return int(reader(run_id) or 0) if reader is not None else 0

    def load_run(self, run_id: str) -> Optional[dict[str, Any]]:
        return self._store.load_run(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        return self._store.list_runs()

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
        self._loaded[thread_id] = 0
        self._store.delete_thread(thread_id)

    async def adelete_thread(self, thread_id: str) -> None:
        await super().adelete_thread(thread_id)
        self._loaded[thread_id] = 0
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
    "LABEL_AGENT_RUN",
    "Neo4jCheckpointStore",
    "create_durable_checkpointer",
]
