"""Read-only canonical projection endpoints (Phase 8 first slice).

These expose the KGCS canonical graph at its latest published epoch. They are
strictly read-only: the dependency that reaches the graph returns a
``Neo4jCanonicalGraphReader``, which is structurally **not** a
``GraphMutationStore`` — no ``apply``, no writer primitive, no reference to a
store. Application code here holds no canonical write surface (ADR-0010, AC-1),
and the reader is obtained through ``agentic_kg.migration.canonical_read`` rather
than by importing the adapter package, so this router cannot accidentally widen
the application tree's imports.

Disabled by default. With ``CANONICAL_API_ENABLED`` unset every route returns
404 before any connection is opened; a flag-on deployment without the opt-in
``migration`` extra returns 503 for the same routes.
"""

from __future__ import annotations

from typing import Any

from agentic_kg.migration.canonical_read import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    canonical_summary,
    entity_detail,
    list_entities,
)
from fastapi import APIRouter, Depends, HTTPException, Query

from agentic_kg_api.dependencies import get_canonical_reader

router = APIRouter(prefix="/api/canonical", tags=["canonical"])


@router.get("/summary")
def get_canonical_summary(reader: Any = Depends(get_canonical_reader)) -> dict[str, Any]:
    """Epoch, and entity counts by type and status, at the latest epoch."""
    return canonical_summary(reader)


@router.get("/entities")
def get_canonical_entities(
    entity_type: str | None = Query(default=None, alias="type"),
    q: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    reader: Any = Depends(get_canonical_reader),
) -> dict[str, Any]:
    """A page of canonical entities, filterable by entity type and text."""
    return list_entities(reader, entity_type=entity_type, query=q, limit=limit, offset=offset)


@router.get("/entities/{identity_id}")
def get_canonical_entity(
    identity_id: str, reader: Any = Depends(get_canonical_reader)
) -> dict[str, Any]:
    """One canonical entity with its assertions and evidence references."""
    detail = entity_detail(reader, identity_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=(f"canonical entity {identity_id!r} is not visible at the latest epoch"),
        )
    return detail


__all__ = ["router"]
