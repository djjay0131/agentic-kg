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
and denormalizes only the fields a *query* selects on — namespace, entity type,
alias, subject, object, predicate, epoch. Reads reconstruct through
``model_validate_json``, so round-trip fidelity is the pydantic model's problem
rather than a hand-written column mapping's.

Bitemporal predicates (``valid_at``, ``transaction_at``) and curation status are
deliberately **not** denormalized into columns. They are resolved once, in
``store.py``, from the reconstructed record. Denormalizing them as well would
give each rule two implementations — a Cypher one and a Python one — and a rule
enforced twice cannot be shown by a mutation test to be enforced at all; the
epoch bound was written that way first and the duplicate had to be removed
before the suite could detect its removal. Adding a temporal index later is a
deliberate change with its own test, not a silent second gate.
"""

from __future__ import annotations

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
