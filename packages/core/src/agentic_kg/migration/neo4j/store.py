"""A Neo4j `GraphMutationStore` + `TemporalGraphReader` + `CapabilityDeclaring`.

Neither KGIS nor KGCS ships a Neo4j adapter — KGIS's only in-tree implementation
is the dict-backed ``MemoryGraphStore`` reference — so this is the adopter's to
build (mapping spec §4.2). It is validated against the *shared* upstream
conformance suite, ``kg_contracts.testing.contract.GraphMutationStoreContract``,
so "conforms" means the same seven tests the reference store passes, unchanged.

The three protocols on one class
--------------------------------
``kg_contracts.testing.contract._TestableGraphStore`` (`contract.py:49`) is the
type-only union the suite drives:
``GraphMutationStore + TemporalGraphReader + CapabilityDeclaring``. All three
are implemented here, on this one class, because the suite's ``make_store()``
returns a single object and casts it to that union.

The adapter-internal writer protocols (``GraphWriter``,
``TransactionalGraphWriter``, ``BulkGraphWriter``) are deliberately *not*
public methods. ``kg_contracts`` does not re-export them (`kg_contracts/__init__.py`
says so in as many words), the mapping spec §4.2 calls for them to stay
"internal", and a public ``put_assertion`` would be a raw canonical write
sitting one attribute lookup away from anything holding this object. Their
equivalents here are private, take an open transaction, and cannot be reached
without one.

History is never rewritten, so status is epoch-versioned
--------------------------------------------------------
This is the property the evidence-evolution scenario turns on, and it is the one
place this adapter deliberately does *more* than the reference store.

``MemoryGraphStore.mark_superseded`` mutates the assertion in place. Under a
snapshot read that is lossy: after a supersession at epoch N+1, asking for epoch
N returns the *new* status for a record that was ``ACTIVE`` then, so the old
interpretation is not "still queryable at its epoch" — it is gone, and only
``include_superseded=True`` gets it back, which is a different question with a
different answer.

So an assertion here carries a ``status_history``: an append-only list of
``{epoch, status, superseded_at}`` entries, the first written at attach time and
one appended per transition. The effective status for a read is the last entry
at or before the requested epoch. A read at epoch N after a supersession at
epoch N+1 therefore sees ``ACTIVE``, with no flags, which is what §9 law 10
("never rewriting history") actually requires. Identities carry the same
mechanism, so a ``MERGE_IDENTITIES`` at epoch N+1 leaves the merged identity
visible and ``ACTIVE`` at epoch N.

``REVOKED`` is the one status this does *not* apply to, and that exception is
the contract's rather than this adapter's: ``kg_contracts.curation`` says a
revoked record "is returned by **no** default read, at any epoch". So a
revocation is terminal and global - asking for an earlier epoch does not
un-revoke it - while the record itself is retained at its original
``curation_epoch`` and served by ``include_revoked=True``. See
:func:`_reported_status`.

**Stated limitation:** *subject* reassignment (``MERGE_IDENTITIES`` /
``SPLIT_IDENTITY`` / ``REASSIGN_ASSERTION``) is applied in place and is **not**
epoch-versioned — a read at an older epoch shows the post-merge subject. Only
status is. Recorded here rather than discovered later.

Never a reasonless non-commit (KGIS ADR-0021)
---------------------------------------------
``CommitResult`` rejects ``committed=False`` with neither ``error`` nor
``failed_preconditions`` at construction, and ADR-0021's external-adopter caveat
names exactly this adapter's shape as the risk: an out-of-tree store returning a
bare ``False`` would now raise inside itself. Every non-commit path here names a
reason — a failed precondition, or an ``error`` string with a machine-readable
prefix (``invalid_payload:``, ``unknown_assertion:``, ``unknown_identity:``,
``unsupported_status:``, ``neo4j_error:``). ``test_every_non_commit_names_a_reason``
pins it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from neo4j import Driver, ManagedTransaction

from agentic_kg.migration.neo4j._contracts import (
    AdapterCapabilities,
    Assertion,
    CanonicalEntity,
    CommitResult,
    CurationOperation,
    CurationOperationType,
    CurationStatus,
    EntityRef,
    GraphMutationBatch,
    GraphReadOptions,
    Precondition,
    UnsupportedCapabilityError,
)
from agentic_kg.migration.neo4j.operations import SUPPORTED_OPERATIONS
from agentic_kg.migration.neo4j.schema import (
    DDL_STATEMENTS,
    LABEL_ASSERTION,
    LABEL_IDENTITY,
    LABEL_META,
    LABEL_VERSION,
    uid,
)

DEFAULT_DATABASE = "neo4j"

#: Statuses a ``RETRACT_ASSERTION`` may move an assertion to. ``ACTIVE`` is not
#: a retraction; restoring an assertion is an ``ATTACH_ASSERTION``, which since
#: agentic-kgcs#34 is what `Compensator` emits (as a full assertion) when
#: inverting a retract.
_RETRACTABLE_TO = frozenset({CurationStatus.SUPERSEDED, CurationStatus.REVOKED})

#: Statuses an ``ATTACH_ASSERTION`` restore record may move an assertion to.
#: Defined as the complement of :data:`_RETRACTABLE_TO` so the two operations
#: provably partition the status space and cannot drift apart: whatever a
#: retraction may set, a restore may not, and vice versa. Without this,
#: ``restore_status`` accepted *any* parseable status — including ``REVOKED``,
#: which is a retraction wearing an attach's clothing, and one that skips the
#: ``superseded_at`` handling :meth:`_apply_retract` performs.
_RESTORABLE_TO = frozenset(CurationStatus) - _RETRACTABLE_TO


class CommitRefused(Exception):
    """An operation cannot be applied; the transaction must roll back.

    Carries the ``error`` string the resulting ``CommitResult`` reports, so the
    non-commit always names a reason (KGIS ADR-0021).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(UTC)


class Neo4jCanonicalGraphStore:
    """The KGCS canonical graph, backed by Neo4j.

    Args:
        driver: An open ``neo4j.Driver``. Its lifetime is the caller's unless
            ``owns_driver=True``.
        namespace: Scopes every read and write (see
            :mod:`~agentic_kg.migration.neo4j.schema`). Two stores with
            different namespaces over one database cannot observe each other.
        database: The Neo4j database name. On Community there is only one, which
            is why ``namespace`` exists; on Enterprise/Aura pass a dedicated
            database for real physical separation (spec §4.2, U-1).
        clock: The fallback ``superseded_at`` for a ``RETRACT_ASSERTION``
            payload that omits one. Since agentic-kgcs#34 the ``Compensator``
            supplies it, so this is now defence for hand-built and older plans
            rather than a workaround for upstream (see :meth:`_apply_retract`).
        owns_driver: Close the driver on :meth:`close`.
    """

    def __init__(
        self,
        driver: Driver,
        *,
        namespace: str,
        database: str = DEFAULT_DATABASE,
        clock: Callable[[], datetime] | None = None,
        owns_driver: bool = False,
    ) -> None:
        if not namespace:
            raise ValueError("namespace must be a non-empty string")
        self._driver = driver
        self._namespace = namespace
        self._database = database
        self._clock = clock or _now
        self._owns_driver = owns_driver

    # --- lifecycle ------------------------------------------------------------

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def database(self) -> str:
        return self._database

    def ensure_schema(self) -> None:
        """Create the constraints and indexes. Idempotent; safe to re-run."""
        with self._driver.session(database=self._database) as session:
            for statement in DDL_STATEMENTS:
                session.run(statement).consume()

    def close(self) -> None:
        if self._owns_driver:
            self._driver.close()

    def read_only(self) -> Any:
        """A read surface over the same data that is *not* a write surface.

        Returns a :class:`~agentic_kg.migration.neo4j.reader.Neo4jCanonicalGraphReader`:
        a `GraphReader`/`TemporalGraphReader` with no ``apply`` and no reachable
        reference to this store. Hand *that* to the projector and to anything
        application-side; never this object.
        """
        from agentic_kg.migration.neo4j.reader import Neo4jCanonicalGraphReader

        return Neo4jCanonicalGraphReader(
            self._driver, namespace=self._namespace, database=self._database
        )

    # --- CapabilityDeclaring --------------------------------------------------

    def capabilities(self) -> AdapterCapabilities:
        """What this adapter actually supports — declared, not assumed.

        ``supports_vector_search`` / ``supports_full_text`` /
        ``supports_graph_algorithms`` stay ``False`` even though the *engine*
        can do all three: the canonical surface exposes none of them, and the
        six named vector indexes of spec §4.4 live on the projection surface,
        not here. A capability is a promise about this adapter, not about Neo4j.
        """
        return AdapterCapabilities(
            supports_transactions=True,
            supports_temporal_queries=True,
            supports_snapshot_reads=True,
            supports_constraints=True,
            supports_bulk_upsert=True,
        )

    # --- GraphMutationStore ---------------------------------------------------

    def apply(
        self, batch: GraphMutationBatch, preconditions: Sequence[Precondition]
    ) -> CommitResult:
        """Apply one batch as an atomic curation epoch.

        Ordering matters and mirrors the executor's own contract:

        1. Unsupported operation types are rejected **before a session is
           opened**, with ``NotImplementedError`` — the exception
           ``PlanExecutor`` catches and reports as ``UNSUPPORTED_OPERATION``
           with the store untouched. Returning a ``CommitResult`` here instead
           would be reported as ``ERROR``, which is a different and less
           actionable thing.
        2. Preconditions are checked inside the write transaction, before any
           write, so a failure is an atomic no-op rather than a partial batch.
        3. Everything else runs in that one transaction; any
           :class:`CommitRefused` rolls it back in full.
        """
        unsupported = sorted(
            {op.type.value for op in batch.operations if op.type not in SUPPORTED_OPERATIONS}
        )
        if unsupported:
            raise NotImplementedError(
                f"{', '.join(unsupported)} not supported by "
                f"{type(self).__name__}; see operations.UNSUPPORTED_REASONS"
            )

        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_write(self._apply_tx, batch, tuple(preconditions))
        except CommitRefused as exc:
            return CommitResult(batch_id=batch.batch_id, committed=False, error=exc.reason)
        except Exception as exc:  # noqa: BLE001 - re-raising would lose the reason
            if isinstance(exc, NotImplementedError):
                raise
            return CommitResult(
                batch_id=batch.batch_id,
                committed=False,
                error=f"neo4j_error: {type(exc).__name__}: {exc}",
            )

    def _apply_tx(
        self,
        tx: ManagedTransaction,
        batch: GraphMutationBatch,
        preconditions: tuple[Precondition, ...],
    ) -> CommitResult:
        failed = tuple(p for p in preconditions if not self._precondition_holds(tx, p))
        if failed:
            return CommitResult(
                batch_id=batch.batch_id, committed=False, failed_preconditions=failed
            )

        new_epoch = self._advance_epoch(tx)
        touched: list[str] = []
        for operation in batch.operations:
            touched.extend(self._apply_operation(tx, operation, new_epoch))
        for subject in touched:
            self._bump_version(tx, subject)
        return CommitResult(batch_id=batch.batch_id, committed=True, new_epoch=new_epoch)

    def _precondition_holds(self, tx: ManagedTransaction, precondition: Precondition) -> bool:
        """Only ``entity_version`` is enforced here — as in the reference store.

        Plan-level ``snapshot_version`` guards are enforced by ``PlanExecutor``
        itself against the graph epoch (it holds a ``GraphReader``); enforcing
        them here as well would double-reject. Any other kind is not understood
        by this adapter and is not treated as failing, matching
        ``MemoryGraphStore._precondition_holds``.
        """
        if precondition.kind != "entity_version":
            return True
        return str(self._entity_version(tx, precondition.subject)) == precondition.expected

    def _apply_operation(
        self, tx: ManagedTransaction, operation: CurationOperation, epoch: int
    ) -> list[str]:
        payload = dict(operation.payload)
        try:
            if operation.type is CurationOperationType.CREATE_IDENTITY:
                return self._apply_create_identity(tx, payload, epoch)
            if operation.type is CurationOperationType.ATTACH_ASSERTION:
                return self._apply_attach(tx, payload, epoch)
            if operation.type is CurationOperationType.RETRACT_ASSERTION:
                return self._apply_retract(tx, payload, epoch)
            if operation.type is CurationOperationType.MERGE_IDENTITIES:
                return self._apply_merge(tx, payload, epoch)
            if operation.type is CurationOperationType.SPLIT_IDENTITY:
                return self._apply_split(tx, payload, epoch)
            if operation.type is CurationOperationType.REASSIGN_ASSERTION:
                return self._apply_reassign(tx, payload, epoch)
            if operation.type is CurationOperationType.REVOKE_IDENTITY:
                return self._apply_revoke_identity(tx, payload, epoch)
        except CommitRefused:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise CommitRefused(
                f"invalid_payload: {operation.type.value} payload rejected: {exc}"
            ) from exc
        # Unreachable: apply() pre-checks the type against SUPPORTED_OPERATIONS.
        raise NotImplementedError(f"{operation.type} is not supported by {type(self).__name__}")

    # --- operations -----------------------------------------------------------

    def _apply_create_identity(
        self, tx: ManagedTransaction, payload: dict[str, Any], epoch: int
    ) -> list[str]:
        entity = CanonicalEntity.model_validate({**payload, "curation_epoch": epoch})
        self._write_identity(tx, entity, history=[_history_entry(epoch, entity.status, None)])
        return [entity.identity_id]

    def _apply_revoke_identity(
        self, tx: ManagedTransaction, payload: dict[str, Any], epoch: int
    ) -> list[str]:
        """Tombstone an identity: the ``CREATE_IDENTITY`` inverse (ADR-0025).

        Payload is ``{"identity_id": ...}`` plus an optional ``reason`` — an
        identity *reference*, deliberately not an entity dump, so a stale copy
        carried in a plan cannot overwrite what is actually in the graph. The
        pre-revoke entity travels in the operation's ``reversal_data``, which
        is what lets the revoke itself be compensated by a ``CREATE_IDENTITY``.

        Two things this does **not** do, both load-bearing:

        * it does not delete, and it does not advance ``curation_epoch``. The
          epoch stamp records the epoch the identity was *created* in; moving
          it forward would make the identity vanish from every epoch-scoped
          read of the history that created it — a rollback that erases the
          record of what it rolled back.
        * it does not reuse ``_RETRACTABLE_TO``. That frozenset governs
          *assertion* status transitions; an identity revocation is a distinct
          operation with a distinct payload and its own status entry.
        """
        identity_id = payload.get("identity_id")
        if not isinstance(identity_id, str) or not identity_id:
            raise CommitRefused(
                "invalid_payload: REVOKE_IDENTITY requires a non-empty string "
                f"'identity_id' (got {identity_id!r})"
            )
        if self._load_identity(tx, identity_id) is None:
            raise CommitRefused(
                f"unknown_identity: REVOKE_IDENTITY names an unknown identity "
                f"{identity_id!r} in namespace {self._namespace!r}"
            )
        self._append_identity_status(tx, identity_id, epoch, CurationStatus.REVOKED)
        return [identity_id]

    def _apply_attach(
        self, tx: ManagedTransaction, payload: dict[str, Any], epoch: int
    ) -> list[str]:
        """Attach a new assertion, or restore a retracted one.

        Two payload shapes reach this operation:

        * a full ``Assertion`` dump — what ``EvolutionPlanner`` and the
          conformance suite emit, and, **since agentic-kgcs#34**, also what
          ``kgcs.executor.compensate.Compensator`` emits when it inverts a
          ``RETRACT_ASSERTION``: the inverse is now the full pre-retraction
          assertion dump, carrying the ``assertion_id`` it restores. That makes
          the compensating attach an *upsert by id*, which ADR-0018 Decision 5
          requires of the adapter — see :meth:`_insert_assertion` for how it is
          honoured without rewriting the record's history.
        * ``{assertion_id, subject_identity, restore_status, ...}`` — the
          partial restore record KGCS emitted **before** #34, when an inverse
          operation's payload was the whole of the original's ``reversal_data``.
          Kept as a compatibility path for hand-built and third-party plans
          (KGCS still falls back to the flat dict when ``INVERSE_PAYLOAD_KEY``
          is absent, so a *persisted* pre-#34 plan still applies). Nothing
          upstream produces it any more, so it is reached only from hand-built
          plans and is covered by tests that build one directly. It appends a
          status-history entry rather than editing the existing one, and is
          constrained to :data:`_RESTORABLE_TO` — an attach reinstates, it
          never retracts.
        """
        if "predicate" in payload:
            assertion = Assertion.model_validate({**payload, "curation_epoch": epoch})
            # The effective subject, which on an upsert is the stored node's
            # rather than this payload's — so the version bump lands on the
            # identity the assertion actually belongs to.
            return [self._insert_assertion(tx, assertion)]

        assertion_id = payload.get("assertion_id")
        if not assertion_id:
            raise CommitRefused(
                "invalid_payload: ATTACH_ASSERTION payload is neither a full "
                "Assertion (no 'predicate') nor a restore record (no 'assertion_id')"
            )
        record = self._load_assertion(tx, str(assertion_id))
        if record is None:
            raise CommitRefused(
                f"unknown_assertion: cannot restore {assertion_id!r} - no such "
                f"assertion in namespace {self._namespace!r}"
            )
        status = _parse_status(payload.get("restore_status", CurationStatus.ACTIVE.value))
        if status not in _RESTORABLE_TO:
            raise CommitRefused(
                f"unsupported_status: ATTACH_ASSERTION cannot restore an assertion to "
                f"{status.value!r}; that is a retraction - use RETRACT_ASSERTION with "
                f"'new_status'"
            )
        self._append_status(tx, str(assertion_id), epoch, status, None)
        return [str(record["subject_identity"])]

    def _apply_retract(
        self, tx: ManagedTransaction, payload: dict[str, Any], epoch: int
    ) -> list[str]:
        """Supersede (or revoke) an assertion. Never deletes it.

        ``new_status`` and ``superseded_at`` are both optional. They used to be
        *absent*: before agentic-kgcs#34 the ``Compensator``'s inverse of an
        ``ATTACH_ASSERTION`` dropped them, because its payload was the whole of
        the attach's ``reversal_data``. #34 fixed that (its defect (c)) — the
        shared ``retract_inverse_payload`` now supplies ``assertion_id``,
        ``subject_identity``, ``new_status=SUPERSEDED`` and
        ``superseded_at=recorded_at``, so the compensator no longer relies on
        either default.

        The defaults are kept as defence for hand-built and older plans:
        ``SUPERSEDED``, the meaning ``EvolutionPlanner.plan_supersession`` gives
        a retract, and the injected clock. Both are stated here rather than
        guessed at silently.
        """
        assertion_id = payload.get("assertion_id")
        if not assertion_id:
            raise CommitRefused("invalid_payload: RETRACT_ASSERTION requires 'assertion_id'")
        record = self._load_assertion(tx, str(assertion_id))
        if record is None:
            raise CommitRefused(
                f"unknown_assertion: cannot retract {assertion_id!r} - no such "
                f"assertion in namespace {self._namespace!r}"
            )
        status = _parse_status(payload.get("new_status", CurationStatus.SUPERSEDED.value))
        if status not in _RETRACTABLE_TO:
            raise CommitRefused(
                f"unsupported_status: RETRACT_ASSERTION cannot move an assertion to "
                f"{status.value!r}; use ATTACH_ASSERTION with 'restore_status' to "
                f"reinstate one"
            )
        raw_at = payload.get("superseded_at")
        at = _parse_datetime(raw_at) if raw_at is not None else self._clock()
        self._append_status(tx, str(assertion_id), epoch, status, at)
        return [str(record["subject_identity"])]

    def _apply_merge(
        self, tx: ManagedTransaction, payload: dict[str, Any], epoch: int
    ) -> list[str]:
        survivor = str(payload.get("survivor_identity") or "")
        merged = [str(m) for m in payload.get("merged_identities") or []]
        if not survivor or not merged:
            raise CommitRefused(
                "invalid_payload: MERGE_IDENTITIES requires 'survivor_identity' "
                "and a non-empty 'merged_identities'"
            )
        merged = [m for m in dict.fromkeys(merged) if m != survivor]
        if self._load_identity(tx, survivor) is None:
            raise CommitRefused(
                f"unknown_identity: survivor {survivor!r} does not exist in "
                f"namespace {self._namespace!r}"
            )
        touched = [survivor]
        for member in merged:
            record = self._load_identity(tx, member)
            if record is None:
                raise CommitRefused(
                    f"unknown_identity: merged member {member!r} does not exist in "
                    f"namespace {self._namespace!r}"
                )
            self._move_subject(tx, from_identity=member, to_identity=survivor, lineage=member)
            self._redirect_objects(tx, from_identity=member, to_identity=survivor)
            current = _effective(json.loads(record["status_history"]), None)[0]
            self._append_identity_status(tx, member, epoch, CurationStatus.SUPERSEDED)
            self._set_identity_lineage(tx, member, merged_into=survivor, premerge_status=current)
            touched.append(member)
        return touched

    def _apply_split(
        self, tx: ManagedTransaction, payload: dict[str, Any], epoch: int
    ) -> list[str]:
        """Restore identities previously merged into ``source_identity``.

        ``SPLIT_IDENTITY`` is the declared inverse of ``MERGE_IDENTITIES``
        (`kgcs/executor/compensate.py:59`), and ``EvolutionPlanner.plan_split``
        puts the pre-merge membership in ``reversal_data`` for exactly that.
        So this restores lineage; it does **not** mint identities. Splitting off
        an identity that was never merged in is refused by name rather than
        invented, because inventing one would fabricate an identity with no
        aliases — and an identity is made of its aliases.
        """
        source = str(payload.get("source_identity") or "")
        into = [str(t) for t in payload.get("into_identities") or []]
        if not source or not into:
            raise CommitRefused(
                "invalid_payload: SPLIT_IDENTITY requires 'source_identity' and a "
                "non-empty 'into_identities'"
            )
        targets = [t for t in dict.fromkeys(into) if t != source]
        if self._load_identity(tx, source) is None:
            raise CommitRefused(
                f"unknown_identity: source {source!r} does not exist in "
                f"namespace {self._namespace!r}"
            )
        touched = [source]
        for target in targets:
            record = self._load_identity(tx, target)
            if record is None or record.get("merged_into") != source:
                raise CommitRefused(
                    f"unknown_identity: {target!r} is not recorded as merged into "
                    f"{source!r}; SPLIT_IDENTITY restores merge lineage and does "
                    f"not mint identities"
                )
            self._move_subject(
                tx, from_identity=source, to_identity=target, lineage=None, only_lineage=target
            )
            self._restore_objects(tx, to_identity=target)
            restored = _parse_status(record.get("premerge_status") or CurationStatus.ACTIVE.value)
            self._append_identity_status(tx, target, epoch, restored)
            self._set_identity_lineage(tx, target, merged_into=None, premerge_status=None)
            touched.append(target)
        return touched

    def _apply_reassign(
        self, tx: ManagedTransaction, payload: dict[str, Any], epoch: int
    ) -> list[str]:
        assertion_id = str(payload.get("assertion_id") or "")
        from_identity = str(payload.get("from_identity") or "")
        to_identity = str(payload.get("to_identity") or "")
        if not (assertion_id and from_identity and to_identity):
            raise CommitRefused(
                "invalid_payload: REASSIGN_ASSERTION requires 'assertion_id', "
                "'from_identity' and 'to_identity'"
            )
        record = self._load_assertion(tx, assertion_id)
        if record is None:
            raise CommitRefused(
                f"unknown_assertion: cannot reassign {assertion_id!r} - no such "
                f"assertion in namespace {self._namespace!r}"
            )
        if record["subject_identity"] != from_identity:
            raise CommitRefused(
                f"unknown_identity: assertion {assertion_id!r} has subject "
                f"{record['subject_identity']!r}, not {from_identity!r}; refusing a "
                f"reassignment computed against a stale subject"
            )
        self._rewrite_subject(tx, assertion_id, to_identity, lineage=None)
        return [from_identity, to_identity]

    # --- GraphReader / TemporalGraphReader ------------------------------------

    def current_epoch(self) -> int:
        with self._driver.session(database=self._database) as session:
            return int(session.execute_read(_read_epoch, self._namespace))

    def get_entity(
        self, identity_id: str, options: GraphReadOptions = GraphReadOptions()
    ) -> CanonicalEntity | None:
        self._check_temporal_options(options)
        with self._driver.session(database=self._database) as session:
            record = session.execute_read(_read_identity, self._namespace, identity_id)
        if record is None:
            return None
        return _visible_entity(record, options)

    def find_entities(
        self,
        entity_type: str | None = None,
        alias: EntityRef | None = None,
        options: GraphReadOptions = GraphReadOptions(),
    ) -> list[CanonicalEntity]:
        self._check_temporal_options(options)
        with self._driver.session(database=self._database) as session:
            rows = session.execute_read(
                _read_identities,
                self._namespace,
                entity_type,
                alias.render() if alias is not None else None,
            )
        entities = [_visible_entity(row, options) for row in rows]
        return [e for e in entities if e is not None]

    def assertions_for(
        self, identity_id: str, options: GraphReadOptions = GraphReadOptions()
    ) -> list[Assertion]:
        self._check_temporal_options(options)
        with self._driver.session(database=self._database) as session:
            rows = session.execute_read(_read_assertions, self._namespace, identity_id)
        found = [_visible_assertion(row, options) for row in rows]
        return [a for a in found if a is not None]

    def neighborhood(
        self, identity_id: str, hops: int = 1, options: GraphReadOptions = GraphReadOptions()
    ) -> list[CanonicalEntity]:
        """Breadth-first over ``object_identity`` edges, mirroring the reference.

        Composed from :meth:`assertions_for` and :meth:`get_entity` rather than
        expressed as one variable-length Cypher pattern. That is a deliberate
        trade: canonical assertions are stored as nodes keyed by their subject
        (an object identity may legitimately not exist yet, so a real
        relationship would have to mint a stub node for it), and traversing
        through the same two readers guarantees a neighbour is visible here on
        exactly the terms it would be visible under a direct read.
        """
        visited = {identity_id}
        frontier = {identity_id}
        result: list[CanonicalEntity] = []
        for _ in range(hops):
            next_frontier: set[str] = set()
            for subject in sorted(frontier):
                for assertion in self.assertions_for(subject, options):
                    target = assertion.object_identity
                    if target is None or target in visited:
                        continue
                    visited.add(target)
                    entity = self.get_entity(target, options)
                    if entity is not None:
                        next_frontier.add(target)
                        result.append(entity)
            frontier = next_frontier
            if not frontier:
                break
        return result

    def _check_temporal_options(self, options: GraphReadOptions) -> None:
        wants_temporal = options.valid_at is not None or options.transaction_at is not None
        if wants_temporal and not self.capabilities().supports_temporal_queries:
            raise UnsupportedCapabilityError(
                "valid_at/transaction_at require an adapter with supports_temporal_queries"
            )

    # --- adapter-internal writers (never public; see module docstring) --------

    def _advance_epoch(self, tx: ManagedTransaction) -> int:
        record = tx.run(
            f"MERGE (m:{LABEL_META} {{uid: $uid}}) "
            f"ON CREATE SET m.ns = $ns, m.epoch = 0, m.seq = 0 "
            f"SET m.epoch = m.epoch + 1 RETURN m.epoch AS epoch",
            uid=self._namespace,
            ns=self._namespace,
        ).single()
        assert record is not None
        return int(record["epoch"])

    def _next_seq(self, tx: ManagedTransaction) -> int:
        record = tx.run(
            f"MATCH (m:{LABEL_META} {{uid: $uid}}) SET m.seq = m.seq + 1 RETURN m.seq AS seq",
            uid=self._namespace,
        ).single()
        assert record is not None
        return int(record["seq"])

    def _entity_version(self, tx: ManagedTransaction, subject: str) -> int:
        record = tx.run(
            f"MATCH (v:{LABEL_VERSION} {{uid: $uid}}) RETURN v.value AS value",
            uid=uid(self._namespace, subject),
        ).single()
        return 0 if record is None else int(record["value"])

    def _bump_version(self, tx: ManagedTransaction, subject: str) -> None:
        tx.run(
            f"MERGE (v:{LABEL_VERSION} {{uid: $uid}}) "
            f"ON CREATE SET v.ns = $ns, v.subject = $subject, v.value = 0 "
            f"SET v.value = v.value + 1",
            uid=uid(self._namespace, subject),
            ns=self._namespace,
            subject=subject,
        ).consume()

    def _write_identity(
        self, tx: ManagedTransaction, entity: CanonicalEntity, *, history: list[dict[str, Any]]
    ) -> None:
        tx.run(
            f"MERGE (i:{LABEL_IDENTITY} {{uid: $uid}}) "
            f"SET i.ns = $ns, i.identity_id = $identity_id, i.entity_type = $entity_type, "
            f"    i.curation_epoch = $curation_epoch, i.alias_refs = $alias_refs, "
            f"    i.payload = $payload, i.status_history = $status_history, "
            f"    i.merged_into = NULL, i.premerge_status = NULL",
            uid=uid(self._namespace, entity.identity_id),
            ns=self._namespace,
            identity_id=entity.identity_id,
            entity_type=entity.entity_type,
            curation_epoch=entity.curation_epoch,
            alias_refs=[a.render() for a in entity.aliases],
            payload=entity.model_dump_json(),
            status_history=json.dumps(history),
        ).consume()

    def _insert_assertion(self, tx: ManagedTransaction, assertion: Assertion) -> str:
        """Write a new assertion, or upsert one that already exists by id.

        Returns the ``subject_identity`` the write actually landed on, which is
        not always the payload's — see *placement* below. Callers use it to
        decide whose ``entity_version`` to bump.

        The upsert branch is a **store obligation**, not an optimisation. KGCS
        ADR-0018 Decision 5: *"a compensating ATTACH_ASSERTION is an UPSERT BY
        ``assertion_id``, never an append ... an adapter MUST replace in
        place"*. Since agentic-kgcs#34 the ``Compensator``'s inverse of a
        supersession ``RETRACT`` is the **full pre-retraction assertion dump**,
        so it arrives here carrying the ``assertion_id`` it is restoring, and
        the ``MERGE`` on ``uid`` is what discharges that obligation. A
        non-compensating re-attach of an existing id lands here too and is
        treated identically; assertion ids are content-derived, so the same id
        is the same fact being re-asserted.

        **The payload is authoritative for content, never for history or
        placement.** It is a snapshot of the record as it stood at some earlier
        epoch, so every column a *later committed operation* may have moved is
        carried over from the stored node rather than restamped from the
        payload. Restamping any of them is a write that reports ``COMMITTED``
        while discarding a committed fact — §9 law 10, which is the whole
        subject of this method.

        *History* — three columns:

        * ``curation_epoch`` — the epoch the record was **minted** at. It is
          the only epoch gate on the read path (:func:`_epoch_visible`), and it
          is evaluated *before* the status history is consulted, so restamping
          it retroactively deletes the assertion from every earlier snapshot
          even when the history is perfectly intact.
        * ``status_history`` — append-only by construction. The status this
          write establishes is *appended*; the ``SUPERSEDED`` entry the
          rollback is undoing stays on the record, because a compensation adds
          a fact, it does not erase one.
        * ``seq`` — the stable write-order tiebreaker :func:`_read_assertions`
          sorts on, so restamping it silently reorders every read.

        *Placement* — four columns, owned by ``REASSIGN_ASSERTION``,
        ``MERGE_IDENTITIES`` and ``SPLIT_IDENTITY``, never by an attach:

        * ``subject_identity`` / ``object_identity`` — moved by a reassignment
          (:meth:`_apply_reassign`) or by a merge (:meth:`_move_subject`,
          :meth:`_redirect_objects`). Unlike status, placement is **not**
          epoch-versioned, so restamping it from a stale payload leaves no
          trace at any epoch: the record simply never appears under the
          identity it was moved to, at any point in history.
        * ``subject_lineage`` / ``object_lineage`` — the breadcrumb a merge
          leaves so its declared inverse can find what to take back.
          :meth:`_move_subject` matches on ``subject_lineage`` and
          :meth:`_restore_objects` on ``object_lineage``, so clearing them
          turns a later ``SPLIT_IDENTITY`` into a silent no-op — that breaks
          the ``MERGE``/``SPLIT`` inverse pair, not just one record.

        A compensating attach inverts a ``RETRACT``, and a ``RETRACT`` moves
        only *status*. Undoing it must therefore move only status. Carrying
        placement is what keeps the two concerns composable: a reassignment and
        a supersession can be rolled back independently, in either order, and
        ADR-0018's own liveness argument (on an adapter honouring Decision 5
        the inverses are idempotent) depends on it. Refusing the upsert on a
        placement mismatch was the alternative; it would reject a *safe*
        rollback because of an unrelated committed operation, which ADR-0018
        classes as a liveness defect rather than a safety feature.

        The history entry itself is stamped at ``assertion.curation_epoch``,
        which :meth:`_apply_attach` has already set to the epoch of the write
        happening *now* — so the appended entry correctly names the
        compensation's epoch while the node keeps the minting epoch.
        """
        existing = self._load_assertion(tx, assertion.assertion_id)
        entry = _history_entry(assertion.curation_epoch, assertion.status, assertion.superseded_at)
        if existing is None:
            history = [entry]
            minted_epoch = assertion.curation_epoch
            seq = self._next_seq(tx)
            subject = assertion.subject_identity
            obj = assertion.object_identity
            subject_lineage = None
            object_lineage = None
            stored = assertion
        else:
            history = [*json.loads(existing["status_history"]), entry]
            minted_epoch = int(existing["curation_epoch"])
            seq = int(existing["seq"])
            subject = str(existing["subject_identity"])
            obj = existing["object_identity"]
            subject_lineage = existing["subject_lineage"]
            object_lineage = existing["object_lineage"]
            # The payload is handed back to readers verbatim, so it has to
            # agree with the columns above rather than with its own stale copy.
            stored = assertion.model_copy(
                update={
                    "curation_epoch": minted_epoch,
                    "subject_identity": subject,
                    "object_identity": obj,
                }
            )
        tx.run(
            f"MERGE (a:{LABEL_ASSERTION} {{uid: $uid}}) "
            f"SET a.ns = $ns, a.assertion_id = $assertion_id, "
            f"    a.subject_identity = $subject_identity, "
            f"    a.object_identity = $object_identity, a.predicate = $predicate, "
            f"    a.curation_epoch = $curation_epoch, a.seq = $seq, "
            f"    a.payload = $payload, a.status_history = $status_history, "
            f"    a.subject_lineage = $subject_lineage, "
            f"    a.object_lineage = $object_lineage",
            uid=uid(self._namespace, assertion.assertion_id),
            ns=self._namespace,
            assertion_id=assertion.assertion_id,
            subject_identity=subject,
            object_identity=obj,
            subject_lineage=subject_lineage,
            object_lineage=object_lineage,
            predicate=assertion.predicate,
            curation_epoch=minted_epoch,
            seq=seq,
            payload=stored.model_dump_json(),
            status_history=json.dumps(history),
        ).consume()
        return subject

    def _load_assertion(self, tx: ManagedTransaction, assertion_id: str) -> dict[str, Any] | None:
        record = tx.run(
            f"MATCH (a:{LABEL_ASSERTION} {{uid: $uid}}) "
            f"RETURN a.payload AS payload, a.subject_identity AS subject_identity, "
            f"       a.status_history AS status_history, a.curation_epoch AS curation_epoch, "
            f"       a.seq AS seq, a.object_identity AS object_identity, "
            f"       a.subject_lineage AS subject_lineage, a.object_lineage AS object_lineage",
            uid=uid(self._namespace, assertion_id),
        ).single()
        return None if record is None else dict(record)

    def _load_identity(self, tx: ManagedTransaction, identity_id: str) -> dict[str, Any] | None:
        record = tx.run(
            f"MATCH (i:{LABEL_IDENTITY} {{uid: $uid}}) "
            f"RETURN i.payload AS payload, i.status_history AS status_history, "
            f"       i.merged_into AS merged_into, i.premerge_status AS premerge_status, "
            f"       i.curation_epoch AS curation_epoch",
            uid=uid(self._namespace, identity_id),
        ).single()
        return None if record is None else dict(record)

    def _append_status(
        self,
        tx: ManagedTransaction,
        assertion_id: str,
        epoch: int,
        status: CurationStatus,
        at: datetime | None,
    ) -> None:
        """Append one status-history entry. Never edits an existing one."""
        record = tx.run(
            f"MATCH (a:{LABEL_ASSERTION} {{uid: $uid}}) RETURN a.status_history AS h",
            uid=uid(self._namespace, assertion_id),
        ).single()
        assert record is not None
        history = json.loads(record["h"])
        history.append(_history_entry(epoch, status, at))
        tx.run(
            f"MATCH (a:{LABEL_ASSERTION} {{uid: $uid}}) SET a.status_history = $h",
            uid=uid(self._namespace, assertion_id),
            h=json.dumps(history),
        ).consume()

    def _append_identity_status(
        self, tx: ManagedTransaction, identity_id: str, epoch: int, status: CurationStatus
    ) -> None:
        record = tx.run(
            f"MATCH (i:{LABEL_IDENTITY} {{uid: $uid}}) RETURN i.status_history AS h",
            uid=uid(self._namespace, identity_id),
        ).single()
        assert record is not None
        history = json.loads(record["h"])
        history.append(_history_entry(epoch, status, None))
        tx.run(
            f"MATCH (i:{LABEL_IDENTITY} {{uid: $uid}}) SET i.status_history = $h",
            uid=uid(self._namespace, identity_id),
            h=json.dumps(history),
        ).consume()

    def _set_identity_lineage(
        self,
        tx: ManagedTransaction,
        identity_id: str,
        *,
        merged_into: str | None,
        premerge_status: CurationStatus | None,
    ) -> None:
        tx.run(
            f"MATCH (i:{LABEL_IDENTITY} {{uid: $uid}}) "
            f"SET i.merged_into = $merged_into, i.premerge_status = $premerge_status",
            uid=uid(self._namespace, identity_id),
            merged_into=merged_into,
            premerge_status=None if premerge_status is None else premerge_status.value,
        ).consume()

    def _move_subject(
        self,
        tx: ManagedTransaction,
        *,
        from_identity: str,
        to_identity: str,
        lineage: str | None,
        only_lineage: str | None = None,
    ) -> None:
        """Move every assertion whose subject is ``from_identity``.

        ``only_lineage`` restricts the move to assertions previously moved *off*
        that identity, which is how a split takes back exactly what the merge
        contributed instead of everything the survivor now holds.
        """
        clause = "" if only_lineage is None else " AND a.subject_lineage = $only_lineage"
        rows = tx.run(
            f"MATCH (a:{LABEL_ASSERTION} {{ns: $ns}}) "
            f"WHERE a.subject_identity = $from_identity{clause} "
            f"RETURN a.assertion_id AS assertion_id",
            ns=self._namespace,
            from_identity=from_identity,
            only_lineage=only_lineage,
        ).data()
        for row in rows:
            self._rewrite_subject(
                tx,
                str(row["assertion_id"]),
                to_identity,
                lineage=lineage,
                clear_lineage=only_lineage is not None,
            )

    def _rewrite_subject(
        self,
        tx: ManagedTransaction,
        assertion_id: str,
        to_identity: str,
        *,
        lineage: str | None,
        clear_lineage: bool = False,
    ) -> None:
        record = self._load_assertion(tx, assertion_id)
        assert record is not None
        assertion = Assertion.model_validate_json(record["payload"])
        moved = assertion.model_copy(update={"subject_identity": to_identity})
        tx.run(
            f"MATCH (a:{LABEL_ASSERTION} {{uid: $uid}}) "
            f"SET a.subject_identity = $subject_identity, a.payload = $payload, "
            f"    a.subject_lineage = $lineage",
            uid=uid(self._namespace, assertion_id),
            subject_identity=to_identity,
            payload=moved.model_dump_json(),
            lineage=None if clear_lineage else lineage,
        ).consume()

    def _redirect_objects(
        self, tx: ManagedTransaction, *, from_identity: str, to_identity: str
    ) -> None:
        rows = tx.run(
            f"MATCH (a:{LABEL_ASSERTION} {{ns: $ns}}) "
            f"WHERE a.object_identity = $from_identity "
            f"RETURN a.assertion_id AS assertion_id, a.payload AS payload",
            ns=self._namespace,
            from_identity=from_identity,
        ).data()
        for row in rows:
            assertion = Assertion.model_validate_json(row["payload"])
            moved = assertion.model_copy(update={"object_identity": to_identity})
            tx.run(
                f"MATCH (a:{LABEL_ASSERTION} {{uid: $uid}}) "
                f"SET a.object_identity = $object_identity, a.payload = $payload, "
                f"    a.object_lineage = $lineage",
                uid=uid(self._namespace, str(row["assertion_id"])),
                object_identity=to_identity,
                payload=moved.model_dump_json(),
                lineage=from_identity,
            ).consume()

    def _restore_objects(self, tx: ManagedTransaction, *, to_identity: str) -> None:
        rows = tx.run(
            f"MATCH (a:{LABEL_ASSERTION} {{ns: $ns}}) "
            f"WHERE a.object_lineage = $to_identity "
            f"RETURN a.assertion_id AS assertion_id, a.payload AS payload",
            ns=self._namespace,
            to_identity=to_identity,
        ).data()
        for row in rows:
            assertion = Assertion.model_validate_json(row["payload"])
            moved = assertion.model_copy(update={"object_identity": to_identity})
            tx.run(
                f"MATCH (a:{LABEL_ASSERTION} {{uid: $uid}}) "
                f"SET a.object_identity = $object_identity, a.payload = $payload, "
                f"    a.object_lineage = NULL",
                uid=uid(self._namespace, str(row["assertion_id"])),
                object_identity=to_identity,
                payload=moved.model_dump_json(),
            ).consume()


# --- module-level read helpers (shared with the read-only façade) -------------


def _read_epoch(tx: ManagedTransaction, namespace: str) -> int:
    record = tx.run(
        f"MATCH (m:{LABEL_META} {{uid: $uid}}) RETURN m.epoch AS epoch", uid=namespace
    ).single()
    return 0 if record is None else int(record["epoch"])


def _read_identity(
    tx: ManagedTransaction, namespace: str, identity_id: str
) -> dict[str, Any] | None:
    record = tx.run(
        f"MATCH (i:{LABEL_IDENTITY} {{uid: $uid}}) "
        f"RETURN i.payload AS payload, i.status_history AS status_history, "
        f"       i.curation_epoch AS curation_epoch",
        uid=uid(namespace, identity_id),
    ).single()
    return None if record is None else dict(record)


def _read_identities(
    tx: ManagedTransaction,
    namespace: str,
    entity_type: str | None,
    alias_ref: str | None,
) -> list[dict[str, Any]]:
    """Fetch candidate identities. Epoch and status are *not* filtered here.

    The selective predicates (namespace, entity type, alias) are pushed into
    Cypher; epoch and status visibility are resolved in exactly one place,
    :func:`_epoch_visible` / :func:`_reported_status`. Duplicating the epoch
    bound as a query prefilter as well would be faster and *untestable*: with
    two independent gates enforcing one rule, breaking either leaves the suite
    green, so neither can be shown to be doing its job. One rule, one
    implementation.
    """
    return tx.run(
        f"MATCH (i:{LABEL_IDENTITY} {{ns: $ns}}) "
        f"WHERE ($entity_type IS NULL OR i.entity_type = $entity_type) "
        f"  AND ($alias_ref IS NULL OR $alias_ref IN i.alias_refs) "
        f"RETURN i.payload AS payload, i.status_history AS status_history, "
        f"       i.curation_epoch AS curation_epoch "
        f"ORDER BY i.identity_id",
        ns=namespace,
        entity_type=entity_type,
        alias_ref=alias_ref,
    ).data()


def _read_assertions(
    tx: ManagedTransaction, namespace: str, identity_id: str
) -> list[dict[str, Any]]:
    """Fetch every assertion about ``identity_id``; visibility is resolved above."""
    return tx.run(
        f"MATCH (a:{LABEL_ASSERTION} {{ns: $ns}}) "
        f"WHERE a.subject_identity = $subject "
        f"RETURN a.payload AS payload, a.status_history AS status_history, "
        f"       a.curation_epoch AS curation_epoch "
        f"ORDER BY a.seq",
        ns=namespace,
        subject=identity_id,
    ).data()


# --- visibility ---------------------------------------------------------------


def _history_entry(epoch: int, status: CurationStatus, at: datetime | None) -> dict[str, Any]:
    return {
        "epoch": epoch,
        "status": status.value,
        "superseded_at": None if at is None else at.isoformat(),
    }


def _effective(
    history: Iterable[dict[str, Any]], at_epoch: int | None
) -> tuple[CurationStatus, datetime | None]:
    """The status in force at ``at_epoch`` (``None`` = latest).

    The entry with the highest ``epoch`` at or before the requested one wins.
    An empty history cannot occur — every write path seeds one — but the
    fallback is ``ACTIVE`` rather than an exception so a read never crashes on
    a record written by an older version of this adapter.
    """
    chosen: dict[str, Any] | None = None
    for entry in history:
        if at_epoch is not None and int(entry["epoch"]) > at_epoch:
            continue
        if chosen is None or int(entry["epoch"]) >= int(chosen["epoch"]):
            chosen = entry
    if chosen is None:
        return CurationStatus.ACTIVE, None
    raw_at = chosen.get("superseded_at")
    return (
        CurationStatus(chosen["status"]),
        None if raw_at is None else _parse_datetime(raw_at),
    )


def _epoch_visible(record_epoch: int, options: GraphReadOptions) -> bool:
    """A record minted after the requested epoch is not yet in that snapshot.

    The only epoch gate in this module — see :func:`_read_assertions` for why it
    is deliberately not also enforced in Cypher.
    """
    return options.curation_epoch is None or record_epoch <= options.curation_epoch


def _reported_status(
    history: list[dict[str, Any]], options: GraphReadOptions
) -> tuple[CurationStatus, datetime | None] | None:
    """The status to report for a record, or ``None`` if it is not visible.

    Two switches over two different statuses, and **neither reveals the
    other's records** — the property
    ``test_include_superseded_and_include_revoked_are_independent`` asserts as
    a cross term. An adapter that collapsed them into one "show me everything"
    flag passes both single-flag tests and fails that one.

    The two statuses are also scoped differently, and that asymmetry is the
    contract's, not a convenience here:

    ``SUPERSEDED`` is **epoch-versioned**, per this module's "History is never
    rewritten" note: a read at epoch N after a supersession at epoch N+1 sees
    ``ACTIVE``, because that is what was true then.

    ``REVOKED`` is **terminal and global**. ``kg_contracts.curation`` states it
    in as many words — "a revoked record is returned by **no** default read, at
    any epoch" — so a revocation is not time-travelled away by asking for an
    earlier epoch. ``include_revoked=True`` is the one surface that serves it,
    and it still serves it at its *original* ``curation_epoch``, which is the
    whole point of not advancing that stamp on revoke.

    Before kg_contracts 2.0.0 this function did the opposite for ``REVOKED``:
    it let revoked records through every default read, because
    ``GraphReadOptions`` had no ``include_revoked`` and upstream's own
    ``test_revoked_record_visible_by_default`` pinned that behaviour. ADR-0025
    reversed it. Nothing in this repository ever wrote
    ``CurationStatus.REVOKED`` before this change, so no stored record changes
    visibility as a result.
    """
    latest, _ = _effective(history, None)
    if latest is CurationStatus.REVOKED:
        if not options.include_revoked:
            return None
        # Reported at its own terminal status, not at the status it held at
        # the requested epoch: the record is being served *because* it was
        # revoked, and saying ACTIVE here would describe it as live.
        _, at = _effective(history, options.curation_epoch)
        return CurationStatus.REVOKED, at

    status, at = _effective(history, options.curation_epoch)
    if status is CurationStatus.REVOKED:
        # Revoked at or before the requested epoch, then restored later.
        return (CurationStatus.REVOKED, at) if options.include_revoked else None
    if status is CurationStatus.SUPERSEDED and not options.include_superseded:
        return None
    return status, at


def _visible_entity(row: dict[str, Any], options: GraphReadOptions) -> CanonicalEntity | None:
    if not _epoch_visible(int(row["curation_epoch"]), options):
        return None
    reported = _reported_status(json.loads(row["status_history"]), options)
    if reported is None:
        return None
    status, _ = reported
    entity = CanonicalEntity.model_validate_json(row["payload"])
    return entity if entity.status is status else entity.model_copy(update={"status": status})


def _visible_assertion(row: dict[str, Any], options: GraphReadOptions) -> Assertion | None:
    if not _epoch_visible(int(row["curation_epoch"]), options):
        return None
    reported = _reported_status(json.loads(row["status_history"]), options)
    if reported is None:
        return None
    status, superseded_at = reported
    assertion = Assertion.model_validate_json(row["payload"])
    if assertion.status is not status or assertion.superseded_at != superseded_at:
        assertion = assertion.model_copy(update={"status": status, "superseded_at": superseded_at})
    if options.valid_at is not None and not _valid_at_matches(assertion, options.valid_at):
        return None
    if options.transaction_at is not None and not _transaction_at_matches(
        assertion, options.transaction_at
    ):
        return None
    return assertion


def _valid_at_matches(assertion: Assertion, valid_at: datetime) -> bool:
    period = assertion.valid_period
    if period.valid_from is not None and valid_at < period.valid_from:
        return False
    if period.valid_to is not None and valid_at > period.valid_to:
        return False
    return True


def _transaction_at_matches(assertion: Assertion, transaction_at: datetime) -> bool:
    """Half-open ``[recorded_at, superseded_at)`` transaction-time window."""
    if assertion.recorded_at > transaction_at:
        return False
    if assertion.superseded_at is not None and assertion.superseded_at <= transaction_at:
        return False
    return True


def _parse_status(value: object) -> CurationStatus:
    if isinstance(value, CurationStatus):
        return value
    try:
        return CurationStatus(str(value))
    except ValueError as exc:
        raise CommitRefused(
            f"invalid_payload: {value!r} is not a CurationStatus "
            f"({', '.join(s.value for s in CurationStatus)})"
        ) from exc


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__ = ["CommitRefused", "Neo4jCanonicalGraphStore"]
