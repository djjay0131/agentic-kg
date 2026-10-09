"""A directory-scoped single-writer lease for the on-disk KGIS ledger.

The shadow candidate ledger and evidence registry are SQLite. SQLite is
single-writer, and its WAL journal mode relies on POSIX advisory locks and a
shared-memory (``-shm``) file. The staging ingest Job mounts its durable ledger
directory from GCS with a Cloud Run GCS volume, and Google documents that Cloud
Storage FUSE "does not provide concurrency control for multiple writes (file
locking)" and "is not a fully POSIX-compliant file system". SQLite's own locking
therefore cannot be trusted to serialise two writers on that mount.

The **hard** barrier is Cloud Run: the ingest Job runs with ``parallelism = 1``
and ``task_count = 1`` (enforced in ``infra/main.tf``), so at most one writer
exists. This lease is the directory-level backstop that catches a stray second
writer anyway — a hand-run CLI pointed at the same directory, a future job with
the parallelism raised, or two executions racing.

It is deliberately simple and filesystem-portable:

* **acquire** creates ``writer.lease`` with ``O_CREAT | O_EXCL``. On a POSIX
  filesystem that is atomic; on a GCS FUSE mount the create maps to an object
  insert and a name collision is surfaced rather than silently overwritten.
* a lease older than ``ttl_seconds`` is treated as abandoned (a crashed run)
  and reclaimed, so one crash cannot wedge the pipeline forever.
* **release** unlinks it, best-effort.

This is a *guard*, not a lock, and it says so: it stops a second cooperating
writer. It cannot make SQLite-on-FUSE safe against the mount's own semantics,
which is why the runbook records that limitation and why ADR-0012's backend swap
behind ``CandidateSink`` / ``LedgerReader`` remains the real fix.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

#: The lease file lives beside ``ledger.db`` / ``evidence.db`` in the ledger
#: directory. It is removed on release, so it is not part of the ledger data.
LEASE_FILENAME = "writer.lease"

#: A lease older than this is assumed abandoned. Six hours is comfortably past
#: the ingest Job's 30-minute timeout, so a live run is never reclaimed.
DEFAULT_LEASE_TTL_SECONDS = 6 * 60 * 60


class ConcurrentWriterError(RuntimeError):
    """Another writer holds (or appears to hold) the ledger directory."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_payload(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_acquired_at(payload: dict[str, object]) -> datetime | None:
    raw = payload.get("acquired_at")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class WriterLease:
    """A held writer lease. Release it, or use it as a context manager."""

    path: Path
    payload: dict[str, object]

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return

    def __enter__(self) -> WriterLease:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
        return None


def acquire_writer_lease(
    directory: str | os.PathLike[str],
    *,
    owner: str,
    ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    now: Callable[[], datetime] = _utcnow,
) -> WriterLease:
    """Take the single-writer lease for ``directory``, or raise.

    ``owner`` is a human-readable identity (the run id plus pid) recorded in the
    lease so a refusal names who holds it. ``now`` is injectable for tests.

    Raises:
        ConcurrentWriterError: a fresh lease is held by another owner.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    lease_path = root / LEASE_FILENAME
    payload: dict[str, object] = {
        "owner": owner,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "acquired_at": now().isoformat(),
    }
    encoded = json.dumps(payload).encode("utf-8")

    # Two attempts: the first may find a stale lease, which it removes so the
    # second can create. A lease created between remove and create is still
    # caught, because O_EXCL fails and the second attempt raises below.
    for _ in range(2):
        try:
            fd = os.open(lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            existing = _read_payload(lease_path)
            acquired_at = _parse_acquired_at(existing)
            age = None if acquired_at is None else (now() - acquired_at).total_seconds()
            if age is not None and age < ttl_seconds:
                raise ConcurrentWriterError(
                    f"another writer holds {lease_path} "
                    f"(owner={existing.get('owner')!r}, "
                    f"acquired_at={existing.get('acquired_at')!r}); the ingest "
                    "Job enforces parallelism=1, so this is a second execution or "
                    "a CLI pointed at the same ledger directory"
                ) from None
            # Stale or unreadable: reclaim it and try once more.
            try:
                lease_path.unlink()
            except FileNotFoundError:
                pass
            continue
        try:
            os.write(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        return WriterLease(path=lease_path, payload=payload)

    raise ConcurrentWriterError(
        f"could not acquire the writer lease at {lease_path} after reclaiming a "
        "stale lease; another writer keeps winning the create race"
    )


__all__ = [
    "DEFAULT_LEASE_TTL_SECONDS",
    "LEASE_FILENAME",
    "ConcurrentWriterError",
    "WriterLease",
    "acquire_writer_lease",
]
