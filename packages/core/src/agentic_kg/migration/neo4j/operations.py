"""Which `CurationOperationType` values this adapter actually applies.

`kgcs.executor.PlanExecutor` takes a ``supported_operations`` frozenset and
refuses anything outside it with ``ExecutionOutcome.UNSUPPORTED_OPERATION``
*before touching the store*. The set is therefore a promise the adapter makes
to the executor, and an over-declared set is worse than a narrow one: the
executor would hand the store an operation it cannot apply, and the only thing
standing between that and a crash is the executor's defensive
``NotImplementedError`` catch.

So the enumeration below is exhaustive over `CurationOperationType` — all seven
values, each either supported with its semantics stated or excluded with its
reason stated. There is no "everything else" bucket.

Why the executor default is not enough
--------------------------------------
``kgcs.executor.DEFAULT_SUPPORTED_OPERATIONS`` is
``{CREATE_IDENTITY, ATTACH_ASSERTION}`` — the two the Plan-1 reference
``MemoryGraphStore`` implements; its ``apply()`` raises ``NotImplementedError``
for the other five. That is precisely why the evidence-evolution scenario
cannot run against the reference store: ``EvolutionPlanner.plan_supersession``
emits ``ATTACH_ASSERTION(new)`` **then** ``RETRACT_ASSERTION(old)``
(`kgcs/recuration/evolution.py`), and ``RETRACT_ASSERTION`` is outside the
default set, so the whole plan comes back ``UNSUPPORTED_OPERATION`` with the
store untouched. Supporting it here is the point of this adapter.

Hand :data:`SUPPORTED_OPERATIONS` to the executor explicitly::

    PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)
"""

from __future__ import annotations

from agentic_kg.migration.neo4j._contracts import CurationOperationType

#: Operation types :class:`~agentic_kg.migration.neo4j.store.Neo4jCanonicalGraphStore`
#: applies. Six of the seven.
#:
#: * ``CREATE_IDENTITY`` — upserts a ``Canon__Identity`` node stamped with the
#:   committing epoch. Matches the reference store: re-creating an existing
#:   identity overwrites rather than failing, because replay protection is the
#:   executor's snapshot precondition, not the store's.
#: * ``ATTACH_ASSERTION`` — appends a ``Canon__Assertion`` node stamped with the
#:   committing epoch. Also accepts the *restore* payload shape the
#:   ``Compensator`` produces when inverting a ``RETRACT_ASSERTION`` (see
#:   ``store._apply_attach``).
#: * ``RETRACT_ASSERTION`` — supersession. Flips an existing assertion's status
#:   to ``SUPERSEDED`` (or ``REVOKED``) and stamps ``superseded_at``; the record
#:   itself is never deleted, so it stays queryable at its own epoch and inside
#:   its transaction-time window (§9 law 10).
#: * ``MERGE_IDENTITIES`` — moves every assertion off the merged identities onto
#:   the survivor, rewrites object references, and marks each merged identity
#:   ``SUPERSEDED`` with ``merged_into``/``premerge_status`` lineage.
#: * ``SPLIT_IDENTITY`` — the inverse: restores identities previously merged
#:   into the source, using the lineage ``MERGE_IDENTITIES`` recorded.
#: * ``REASSIGN_ASSERTION`` — moves one assertion from one subject to another.
SUPPORTED_OPERATIONS: frozenset[CurationOperationType] = frozenset(
    {
        CurationOperationType.CREATE_IDENTITY,
        CurationOperationType.ATTACH_ASSERTION,
        CurationOperationType.RETRACT_ASSERTION,
        CurationOperationType.MERGE_IDENTITIES,
        CurationOperationType.SPLIT_IDENTITY,
        CurationOperationType.REASSIGN_ASSERTION,
    }
)

#: The one operation type this adapter deliberately does not apply, with its
#: reason. ``PROMOTE_ONTOLOGY_TERM`` writes an *ontology term* (payload:
#: ``term_id``/``term``/``term_kind``/``graph_id``/``state``), which is not a
#: canonical-graph record: `GraphReader` has no surface that could read one back
#: — not ``get_entity``, not ``find_entities``, not ``assertions_for``. Storing
#: it here would create data that no port can observe, and the mapping spec
#: gives the ontology no canonical home. Excluded rather than faked; the
#: executor reports ``UNSUPPORTED_OPERATION`` for it without touching the store.
UNSUPPORTED_OPERATIONS: frozenset[CurationOperationType] = frozenset(
    {CurationOperationType.PROMOTE_ONTOLOGY_TERM}
)

UNSUPPORTED_REASONS: dict[CurationOperationType, str] = {
    CurationOperationType.PROMOTE_ONTOLOGY_TERM: (
        "PROMOTE_ONTOLOGY_TERM writes an ontology term, not a canonical-graph "
        "record; no GraphReader method could read it back, so this adapter "
        "declines it rather than storing unobservable data"
    ),
}

# The enumeration must stay exhaustive: adding an eighth CurationOperationType
# upstream should break this import, not silently fall into an unstated bucket.
assert SUPPORTED_OPERATIONS | UNSUPPORTED_OPERATIONS == frozenset(CurationOperationType), (
    "SUPPORTED_OPERATIONS + UNSUPPORTED_OPERATIONS must cover every "
    "CurationOperationType; unclassified: "
    f"{sorted(set(CurationOperationType) - SUPPORTED_OPERATIONS - UNSUPPORTED_OPERATIONS)}"
)
assert not (SUPPORTED_OPERATIONS & UNSUPPORTED_OPERATIONS)

__all__ = ["SUPPORTED_OPERATIONS", "UNSUPPORTED_OPERATIONS", "UNSUPPORTED_REASONS"]
