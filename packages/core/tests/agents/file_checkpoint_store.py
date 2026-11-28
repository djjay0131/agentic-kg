"""A file-backed ``InMemoryCheckpointStore`` for cross-process tests.

The production store is Neo4j. A real subprocess cannot share a parent's
process memory, so the entrypoint test hands a run between processes through a
JSON file that mirrors the in-memory store's two dictionaries. This is test
support only; nothing under ``src`` imports it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from agentic_kg.agents.checkpoint_store import InMemoryCheckpointStore


class FileBackedCheckpointStore(InMemoryCheckpointStore):
    """Persist the in-memory store's contents to ``path`` after every write."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._payloads = dict(data.get("payloads") or {})
            self._runs = dict(data.get("runs") or {})

    def _flush(self) -> None:
        self.path.write_text(
            json.dumps(
                {"payloads": self._payloads, "runs": self._runs},
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def load_thread(self, thread_id: str) -> Optional[dict[str, Any]]:
        self._reload()
        return super().load_thread(thread_id)

    def load_run(self, run_id: str) -> Optional[dict[str, Any]]:
        self._reload()
        return super().load_run(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        self._reload()
        return super().list_runs()

    def list_thread_ids(self) -> list[str]:
        self._reload()
        return super().list_thread_ids()

    def _reload(self) -> None:
        """Pick up writes made by the other process since this instance loaded."""
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._payloads = dict(data.get("payloads") or {})
        self._runs = dict(data.get("runs") or {})

    def save_thread(self, thread_id: str, payload: dict[str, Any]) -> None:
        super().save_thread(thread_id, payload)
        self._flush()

    def delete_thread(self, thread_id: str) -> None:
        super().delete_thread(thread_id)
        self._flush()

    def save_run(self, run_id: str, metadata: dict[str, Any]) -> None:
        super().save_run(run_id, metadata)
        self._flush()


__all__ = ["FileBackedCheckpointStore"]
