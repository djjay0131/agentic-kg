"""Read-only canonical projection for application code.

The API's ``/api/canonical/*`` router is the first application surface to read
the KGCS canonical graph, and this module is the *only* door it is allowed to
use. It exists because of two constraints that pull in opposite directions:

* **The application tree must not name a canonical write surface.** AC-1 /
  mapping spec §2 rule 1, asserted by
  ``tests/migration/neo4j/test_no_application_write_surface.py``, whose
  ``FORBIDDEN_IMPORTS`` includes the substring ``agentic_kg.migration.neo4j``.
  So the API cannot import :class:`Neo4jCanonicalGraphReader` (or anything else)
  from that package directly, even the read-only façade.
* **The API must be importable without the opt-in ``migration`` extra.** The
  API image and the local default install must not fail at import time when
  ``kg_contracts`` is absent. So the optional import happens lazily, inside
  :func:`open_canonical_reader`, and this module's top level imports nothing
  optional.

Naming the adapter one module away from the application tree is not a loophole:
the object this module returns is the reader, which is structurally **not** a
``GraphMutationStore`` — no ``apply``, no writer primitive, no reference to a
store. The write path stays with ``PlanExecutor`` (ADR-0010). What the API gets
here can read canonical state and nothing else.

Scope of the filter, stated rather than implied
-----------------------------------------------
``entity_type`` is pushed into the adapter's Cypher (``_read_identities`` takes
it as a parameter), so it is server-side. ``q`` is applied **in process** over
the entities the adapter returns: the canonical reader has no full-text index on
the canonical surface (``supports_full_text`` is deliberately ``False``, schema
notes), and this first slice's graph is small. That is recorded as a limitation,
not hidden — a deployment with a large canonical graph should add a
Cypher-backed search rather than page the whole graph into memory.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Environment variable selecting the canonical namespace. Mirrors
#: ``migration.neo4j.factory.DEFAULT_NAMESPACE`` but is read here so the API
#: does not have to import the adapter package to learn its own scope.
ENV_CANONICAL_NAMESPACE = "KGCS_CANONICAL_NAMESPACE"
DEFAULT_CANONICAL_NAMESPACE = "canon"

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


@dataclass(frozen=True)
class CanonicalReadTarget:
    """Where to read canonical state, and in which namespace.

    Deliberately a plain value object: it carries no driver, no store and no
    connection, so constructing one never opens a socket.
    """

    uri: str
    username: str
    password: str
    database: str
    namespace: str


def canonical_read_target_from_env(
    environ: Mapping[str, str] | None = None,
) -> CanonicalReadTarget:
    """Read the canonical read target from the process environment.

    Uses the same ``NEO4J_*`` names the legacy repository uses, because staging
    runs one Community Neo4j and "which server" is the same question for both
    surfaces; "which labels/namespace" is the question this module adds.
    """
    env = os.environ if environ is None else environ
    return CanonicalReadTarget(
        uri=env.get("NEO4J_URI", "bolt://localhost:7687"),
        username=env.get("NEO4J_USERNAME", "neo4j"),
        password=env.get("NEO4J_PASSWORD", ""),
        database=env.get("NEO4J_DATABASE", "neo4j"),
        namespace=env.get(ENV_CANONICAL_NAMESPACE, DEFAULT_CANONICAL_NAMESPACE),
    )


def open_canonical_reader(target: CanonicalReadTarget) -> Any:
    """Open the read-only canonical façade. Requires the ``migration`` extra.

    The optional import lives here, not at module scope, so importing this
    module (or the API router that uses it) is safe on a default install. A
    caller with the flag on but the extra absent gets KGIS's own
    :class:`MigrationDependencyError` naming the distribution and the install
    command.
    """
    from neo4j import GraphDatabase

    from agentic_kg.migration.neo4j.reader import Neo4jCanonicalGraphReader

    driver = GraphDatabase.driver(target.uri, auth=(target.username, target.password))
    return Neo4jCanonicalGraphReader(driver, namespace=target.namespace, database=target.database)


def _as_dict(value: Any) -> dict[str, Any]:
    """Canonical records as JSON-ready dicts, without importing their types.

    Accepts a pydantic model (the adapter returns ``CanonicalEntity`` /
    ``Assertion``) or a plain mapping (the API tests' fake reader). Importing
    the contract types would pull ``kg_contracts`` into this module's import
    graph and defeat the lazy-import boundary above.
    """
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="json"))
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"canonical record is neither a pydantic model nor a mapping: {type(value)!r}")


def _matches(entity: dict[str, Any], needle: str) -> bool:
    """Case-insensitive substring match over a display name and alias keys."""
    haystacks = [str(entity.get("display_name") or "")]
    for alias in entity.get("aliases") or []:
        if isinstance(alias, Mapping):
            haystacks.append(str(alias.get("key") or ""))
            haystacks.append(str(alias.get("namespace") or ""))
        else:
            haystacks.append(str(alias))
    return any(needle in haystack.casefold() for haystack in haystacks)


def canonical_summary(reader: Any) -> dict[str, Any]:
    """Epoch and entity counts by type and status at the latest epoch.

    Counts only *entities*; there is deliberately no assertion total, because
    the reader exposes assertions per subject (``assertions_for``) and not a
    global listing, and a re-derived count over a partial page would be a number
    that means nothing.
    """
    entities = [_as_dict(entity) for entity in reader.find_entities()]
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for entity in entities:
        entity_type = str(entity.get("entity_type") or "unknown")
        status = str(entity.get("status") or "unknown")
        by_type[entity_type] = by_type.get(entity_type, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "epoch": int(reader.current_epoch()),
        "total_entities": len(entities),
        "entities_by_type": dict(sorted(by_type.items())),
        "entities_by_status": dict(sorted(by_status.items())),
        "visibility": "latest_epoch, namespace-scoped, active identities only",
    }


def list_entities(
    reader: Any,
    *,
    entity_type: str | None = None,
    query: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """A page of canonical entities, optionally filtered by type and text.

    ``entity_type`` is server-side (pushed into Cypher by the adapter); ``query``
    is an in-process substring filter — see the module docstring's scope note.
    """
    if limit < 0:
        raise ValueError("limit must be >= 0")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    limit = min(limit, MAX_LIMIT)

    entities = [_as_dict(entity) for entity in reader.find_entities(entity_type=entity_type)]
    if query:
        needle = query.casefold()
        entities = [entity for entity in entities if _matches(entity, needle)]
    entities.sort(key=lambda entity: str(entity.get("identity_id") or ""))
    window = entities[offset : offset + limit]
    return {
        "epoch": int(reader.current_epoch()),
        "count": len(entities),
        "offset": offset,
        "limit": limit,
        "entities": window,
    }


def entity_detail(reader: Any, identity_id: str) -> dict[str, Any] | None:
    """One canonical entity with its assertions and the evidence ids they cite.

    ``None`` means the identity is not visible at the latest epoch; the router
    turns that into a 404. Evidence *references* travel with the assertion
    (``Assertion.evidence_refs``); evidence *content* lives in the KGIS ledger,
    which is a different access path by design (KGIS ADR-0011) and is not served
    here.
    """
    entity = reader.get_entity(identity_id)
    if entity is None:
        return None
    detail = _as_dict(entity)
    assertions = [_as_dict(assertion) for assertion in reader.assertions_for(identity_id)]
    evidence_ids: set[str] = set()
    for assertion in assertions:
        for ref in assertion.get("evidence_refs") or []:
            if isinstance(ref, Mapping) and ref.get("evidence_id"):
                evidence_ids.add(str(ref["evidence_id"]))
    detail["assertions"] = assertions
    detail["evidence_refs"] = sorted(evidence_ids)
    return detail


__all__ = [
    "DEFAULT_CANONICAL_NAMESPACE",
    "DEFAULT_LIMIT",
    "ENV_CANONICAL_NAMESPACE",
    "MAX_LIMIT",
    "CanonicalReadTarget",
    "canonical_read_target_from_env",
    "canonical_summary",
    "entity_detail",
    "list_entities",
    "open_canonical_reader",
]
