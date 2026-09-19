"""Physical schema for the KGCS **canonical** surface in Neo4j.

This is the canonical store of the two surfaces in the mapping spec §4.2 — the
one written *only* by the KGCS ``PlanExecutor``. The legacy/projection surface
(`Paper`, `Topic`, `RESEARCHES`, the denormalized counters, the six vector
indexes) is a different module's problem and shares none of these labels.

Separation, and the Community-edition constraint (U-1)
------------------------------------------------------
§4.2 prefers two Neo4j *databases* so a ``DETACH DELETE`` rebuild of the
projection physically cannot reach canonical data, and flags UNDETERMINED U-1:
Neo4j Community supports only one user database. This module resolves U-1 in
the way §4.2 names as the fallback, and then strengthens it:

* every canonical label carries the ``Canon__`` prefix, so no projection query
  written against legacy labels can match a canonical node even by accident;
* every canonical node additionally carries a ``ns`` (namespace) property, and
  **every** read and write in :mod:`~agentic_kg.migration.neo4j.store` is
  scoped to the store's own namespace. Two stores over one physical database
  therefore cannot see each other's data, which is what makes a genuinely
  pristine store per ``make_store()`` possible without deleting anything;
* the store still takes a ``database`` name, so on Enterprise/Aura the stronger
  physical separation is a constructor argument away.

That is separation by convention plus scoping, not by engine enforcement, and
the weakening is recorded here as §4.2 requires.

Keys
----
Uniqueness is declared on a single synthetic ``uid`` property
(``"<ns>\\x1f<id>"``) rather than a composite ``(ns, id)`` constraint:
single-property uniqueness constraints are available on every Neo4j edition,
composite ones historically are not, and this adapter must run on whatever the
deployment turns out to be.

Value encoding
--------------
Each node stores the full record as a ``model_dump_json`` string in ``payload``
and *denormalizes* only the fields the read filters need. Reads reconstruct
through ``model_validate_json``, so round-trip fidelity is the pydantic model's
problem, not a hand-written column mapping's. Timestamps are denormalized as
POSIX floats (``*_ts``) so temporal predicates are plain numeric comparisons
with no timezone semantics in the query layer; the authoritative,
timezone-carrying value always comes back out of ``payload``.
"""

from __future__ import annotations

from datetime import UTC, datetime

#: Separator inside synthetic ``uid`` values. ASCII unit separator: it cannot
#: occur in an identity id (``kg://<graph>/identity/<ulid>``) or a ULID.
UID_SEP = "\x1f"

LABEL_IDENTITY = "Canon__Identity"
LABEL_ASSERTION = "Canon__Assertion"
LABEL_META = "Canon__Meta"
LABEL_VERSION = "Canon__Version"

CANONICAL_LABELS: tuple[str, ...] = (
    LABEL_IDENTITY,
    LABEL_ASSERTION,
    LABEL_META,
    LABEL_VERSION,
)

#: Executed once per store construction. ``IF NOT EXISTS`` makes this idempotent
#: and safe to run concurrently from several processes.
DDL_STATEMENTS: tuple[str, ...] = (
    f"CREATE CONSTRAINT canon_identity_uid IF NOT EXISTS "
    f"FOR (n:{LABEL_IDENTITY}) REQUIRE n.uid IS UNIQUE",
    f"CREATE CONSTRAINT canon_assertion_uid IF NOT EXISTS "
    f"FOR (n:{LABEL_ASSERTION}) REQUIRE n.uid IS UNIQUE",
    f"CREATE CONSTRAINT canon_meta_uid IF NOT EXISTS FOR (n:{LABEL_META}) REQUIRE n.uid IS UNIQUE",
    f"CREATE CONSTRAINT canon_version_uid IF NOT EXISTS "
    f"FOR (n:{LABEL_VERSION}) REQUIRE n.uid IS UNIQUE",
    f"CREATE INDEX canon_identity_ns IF NOT EXISTS FOR (n:{LABEL_IDENTITY}) ON (n.ns)",
    f"CREATE INDEX canon_assertion_subject IF NOT EXISTS "
    f"FOR (n:{LABEL_ASSERTION}) ON (n.ns, n.subject_identity)",
    f"CREATE INDEX canon_assertion_object IF NOT EXISTS "
    f"FOR (n:{LABEL_ASSERTION}) ON (n.ns, n.object_identity)",
)


def uid(namespace: str, key: str) -> str:
    """The synthetic single-property primary key for a canonical node."""
    return f"{namespace}{UID_SEP}{key}"


def to_ts(value: datetime | None) -> float | None:
    """POSIX seconds for a denormalized timestamp column.

    A naive datetime is read as UTC rather than as local time. Local time would
    make the same stored record compare differently depending on the machine
    that wrote it, which is a silently wrong temporal query — exactly what the
    bitemporal contract exists to prevent. The authoritative value is the one in
    ``payload``; this is only the comparison key.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()
