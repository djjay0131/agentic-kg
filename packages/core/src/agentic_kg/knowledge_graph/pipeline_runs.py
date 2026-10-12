"""Persistence for nightly ``PipelineRun`` reports in Neo4j.

The contract lives in ``docs/design/nightly-pipeline-contract.md``. This module
is the shared write/read path: the T29 nightly job writes runs with
``save_pipeline_run`` and the T30 Runs API reads them with
``list_pipeline_runs`` / ``get_pipeline_run``, so both sides agree on the storage
shape by construction.

The node label is ``:PipelineRun`` -- deliberately *not* under ``Canon__*``,
because a run is operational telemetry, not canonical graph content. Nested
fields are stored as JSON strings (Neo4j has no nested-map property); see
``agentic_kg.knowledge_graph.models.pipeline_run`` for the exact shape.

Pagination
----------
``list_pipeline_runs`` orders by ``started_at`` descending and breaks ties on
``run_id`` descending. ISO-8601 UTC strings of the contract's fixed width sort
lexicographically in time order, so the comparison is valid without parsing.
The cursor is an opaque base64url token carrying the last row's
``(started_at, run_id)``.
"""

from __future__ import annotations

import base64
import json
from typing import Optional

from neo4j import ManagedTransaction

from agentic_kg.knowledge_graph.models.pipeline_run import PipelineRun
from agentic_kg.knowledge_graph.repository import (
    Neo4jRepository,
    RepositoryError,
    get_repository,
)

#: Default page size for ``list_pipeline_runs``.
DEFAULT_LIMIT = 50

#: Maximum page size the API will serve.
MAX_LIMIT = 200

_LIST_QUERY = """
MATCH (r:PipelineRun)
WHERE $cursor_started_at IS NULL
   OR r.started_at < $cursor_started_at
   OR (r.started_at = $cursor_started_at AND r.run_id < $cursor_run_id)
RETURN r
ORDER BY r.started_at DESC, r.run_id DESC
LIMIT $limit
"""


class PipelineRunNotFoundError(RepositoryError):
    """Raised when a ``PipelineRun`` id is not present in the graph."""


class InvalidCursorError(ValueError):
    """Raised when a pagination cursor cannot be decoded."""


def _encode_cursor(run: PipelineRun) -> str:
    """Encode the last row of a page into an opaque continuation cursor."""
    payload = json.dumps({"started_at": run.started_at, "run_id": run.run_id})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    """Decode a continuation cursor into ``(started_at, run_id)``.

    Raises ``InvalidCursorError`` for anything that is not a token this module
    produced, so a caller can answer 400 rather than leak a 500.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        return str(data["started_at"]), str(data["run_id"])
    except Exception as e:  # noqa: BLE001 - any failure here is a bad cursor
        raise InvalidCursorError(f"invalid cursor: {cursor!r}") from e


class PipelineRunRepository:
    """Read/write access to ``:PipelineRun`` nodes for one repository."""

    def __init__(self, repository: Neo4jRepository) -> None:
        self._repo = repository

    def save(self, run: PipelineRun) -> PipelineRun:
        """Upsert a run by ``run_id``.

        ``MERGE`` (not ``CREATE``) so the nightly job can write a run once as
        ``running`` and update it to its terminal status in place.
        """
        props = run.to_neo4j_properties()

        def _save(tx: ManagedTransaction) -> None:
            tx.run(
                "MERGE (r:PipelineRun {run_id: $run_id}) SET r = $props",
                run_id=run.run_id,
                props=props,
            )

        with self._repo.session() as session:
            session.execute_write(_save)
        return run

    def get(self, run_id: str) -> PipelineRun:
        """Return one run, or raise ``PipelineRunNotFoundError``."""

        def _get(tx: ManagedTransaction) -> Optional[dict]:
            record = tx.run(
                "MATCH (r:PipelineRun {run_id: $run_id}) RETURN r LIMIT 1",
                run_id=run_id,
            ).single()
            return dict(record["r"]) if record else None

        with self._repo.session() as session:
            props = session.execute_read(_get)

        if props is None:
            raise PipelineRunNotFoundError(f"PipelineRun not found: {run_id}")
        return PipelineRun.from_neo4j_properties(props)

    def list(
        self,
        limit: int = DEFAULT_LIMIT,
        cursor: Optional[str] = None,
    ) -> tuple[list[PipelineRun], Optional[str]]:
        """Return a page of runs, newest first, plus a continuation cursor.

        Fetches ``limit + 1`` rows to detect whether a further page exists; the
        extra row is dropped and the cursor is built from the last returned row.
        """
        cursor_started_at: Optional[str] = None
        cursor_run_id: Optional[str] = None
        if cursor is not None:
            cursor_started_at, cursor_run_id = _decode_cursor(cursor)

        def _list(tx: ManagedTransaction) -> list[dict]:
            result = tx.run(
                _LIST_QUERY,
                cursor_started_at=cursor_started_at,
                cursor_run_id=cursor_run_id,
                limit=limit + 1,
            )
            return [dict(record["r"]) for record in result]

        with self._repo.session() as session:
            props_list = session.execute_read(_list)

        has_more = len(props_list) > limit
        props_list = props_list[:limit]
        runs = [PipelineRun.from_neo4j_properties(p) for p in props_list]
        next_cursor = _encode_cursor(runs[-1]) if has_more and runs else None
        return runs, next_cursor


def save_pipeline_run(
    run: PipelineRun, repository: Optional[Neo4jRepository] = None
) -> PipelineRun:
    """Save ``run`` using ``repository`` (or the process default repository)."""
    return PipelineRunRepository(repository or get_repository()).save(run)


def get_pipeline_run(
    run_id: str, repository: Optional[Neo4jRepository] = None
) -> PipelineRun:
    """Fetch one run by id (or raise ``PipelineRunNotFoundError``)."""
    return PipelineRunRepository(repository or get_repository()).get(run_id)


def list_pipeline_runs(
    limit: int = DEFAULT_LIMIT,
    cursor: Optional[str] = None,
    repository: Optional[Neo4jRepository] = None,
) -> tuple[list[PipelineRun], Optional[str]]:
    """List runs newest first, paginated by cursor."""
    return PipelineRunRepository(repository or get_repository()).list(
        limit=limit, cursor=cursor
    )


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "InvalidCursorError",
    "PipelineRunNotFoundError",
    "PipelineRunRepository",
    "get_pipeline_run",
    "list_pipeline_runs",
    "save_pipeline_run",
]
