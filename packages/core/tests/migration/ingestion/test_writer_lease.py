"""The single-writer lease that backstops SQLite on the GCS FUSE mount.

Fast, filesystem-only: a temporary directory stands in for the ledger directory.
No SQLite, no kgis store, no network. The point under test is the guard itself —
that a second cooperating writer is refused, that a crashed writer's lease is
reclaimed, and that a clean exit frees the directory.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip(
    "kgis",
    reason=(
        "the opt-in 'migration' extra is not installed; "
        "pip install './packages/core[migration]'"
    ),
)

from agentic_kg.migration.ingestion.writer_lease import (  # noqa: E402
    LEASE_FILENAME,
    ConcurrentWriterError,
    acquire_writer_lease,
)

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_acquire_creates_the_lease_and_release_removes_it(tmp_path):
    lease = acquire_writer_lease(tmp_path, owner="run-1:1")
    lease_path = tmp_path / LEASE_FILENAME
    assert lease_path.exists()
    assert lease.payload["owner"] == "run-1:1"

    lease.release()
    assert not lease_path.exists()


def test_a_second_writer_is_refused(tmp_path):
    first = acquire_writer_lease(tmp_path, owner="run-1:1")
    try:
        with pytest.raises(ConcurrentWriterError, match="run-1:1"):
            acquire_writer_lease(tmp_path, owner="run-2:2")
    finally:
        first.release()


def test_release_frees_the_directory_for_the_next_run(tmp_path):
    acquire_writer_lease(tmp_path, owner="run-1:1").release()
    second = acquire_writer_lease(tmp_path, owner="run-2:2")
    try:
        assert second.payload["owner"] == "run-2:2"
    finally:
        second.release()


def test_context_manager_releases_on_the_way_out(tmp_path):
    lease_path = tmp_path / LEASE_FILENAME
    with acquire_writer_lease(tmp_path, owner="run-1:1"):
        assert lease_path.exists()
    assert not lease_path.exists()


def test_a_stale_lease_is_reclaimed(tmp_path):
    acquire_writer_lease(
        tmp_path, owner="crashed", ttl_seconds=60, now=lambda: _BASE
    )
    survivor = acquire_writer_lease(
        tmp_path,
        owner="run-2",
        ttl_seconds=60,
        now=lambda: _BASE + timedelta(hours=2),
    )
    try:
        assert survivor.payload["owner"] == "run-2"
    finally:
        survivor.release()


def test_a_fresh_lease_is_not_stolen(tmp_path):
    acquire_writer_lease(
        tmp_path, owner="live", ttl_seconds=3600, now=lambda: _BASE
    )
    with pytest.raises(ConcurrentWriterError):
        acquire_writer_lease(
            tmp_path,
            owner="run-2",
            ttl_seconds=3600,
            now=lambda: _BASE + timedelta(seconds=10),
        )


def test_an_unreadable_lease_is_reclaimed(tmp_path):
    """A truncated/garbage lease file must not wedge the pipeline forever."""
    (tmp_path / LEASE_FILENAME).write_text("not json", encoding="utf-8")
    lease = acquire_writer_lease(tmp_path, owner="run-2")
    try:
        assert lease.payload["owner"] == "run-2"
    finally:
        lease.release()


def test_the_ledger_directory_is_created_if_absent(tmp_path):
    nested = tmp_path / "mnt" / "ledger" / "staging"
    lease = acquire_writer_lease(nested, owner="run-1")
    try:
        assert nested.is_dir()
        assert (nested / LEASE_FILENAME).exists()
    finally:
        lease.release()
