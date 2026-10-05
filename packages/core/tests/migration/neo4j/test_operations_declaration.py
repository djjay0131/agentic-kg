"""``supported_operations`` is honest, and every non-commit names a reason.

Two claims, both easy to fake and therefore both tested behaviourally:

* **Honest declaration.** Every type in :data:`SUPPORTED_OPERATIONS` is actually
  applied against a real Neo4j — declared support is worthless if the executor
  hands the store an operation it then cannot apply. And the one excluded type
  really is refused, in the shape the executor expects (``NotImplementedError``
  → ``UNSUPPORTED_OPERATION``, store untouched) rather than by crashing or by
  silently succeeding.
* **Never a reasonless non-commit** (KGIS ADR-0021). Every failure path is
  driven to a ``CommitResult`` and asserted to carry ``error`` or
  ``failed_preconditions``. ADR-0021's own external-adopter caveat names an
  out-of-tree `GraphMutationStore` returning a bare ``committed=False`` as the
  risk it could not close from upstream; this is the adopter side of that.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.neo4j import (
    SUPPORTED_OPERATIONS,
    UNSUPPORTED_OPERATIONS,
    UNSUPPORTED_REASONS,
)
from kg_contracts.assertions import CurationStatus
from kg_contracts.curation import CurationOperation, CurationOperationType
from kg_contracts.stores import GraphMutationBatch, GraphReadOptions
from kg_contracts.testing.factories import make_assertion, make_entity

from .scenarios import assert_entity_version_guard, probe_version

pytestmark = pytest.mark.integration


def _batch(plan_id: str, *operations: CurationOperation) -> GraphMutationBatch:
    return GraphMutationBatch(plan_id=plan_id, operations=operations)


def _op(op_type: CurationOperationType, payload: dict) -> CurationOperation:
    return CurationOperation(type=op_type, payload=payload)


def _seed(store, *, key: str = "seed"):
    """Commit one identity plus one assertion about it; return both."""
    entity = make_entity(key=key)
    assertion = make_assertion(subject_identity=entity.identity_id, predicate="p")
    result = store.apply(
        _batch(
            "pl_seed",
            _op(CurationOperationType.CREATE_IDENTITY, entity.model_dump(mode="json")),
            _op(CurationOperationType.ATTACH_ASSERTION, assertion.model_dump(mode="json")),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error
    return entity, assertion


# --- the declaration itself ---------------------------------------------------


def test_declaration_partitions_every_operation_type() -> None:
    """No operation type is left unclassified, and none is in both sets."""
    assert SUPPORTED_OPERATIONS | UNSUPPORTED_OPERATIONS == frozenset(CurationOperationType)
    assert not (SUPPORTED_OPERATIONS & UNSUPPORTED_OPERATIONS)
    assert UNSUPPORTED_OPERATIONS, "an empty exclusion set would make this vacuous"
    assert set(UNSUPPORTED_REASONS) == set(UNSUPPORTED_OPERATIONS)
    assert all(len(reason) > 40 for reason in UNSUPPORTED_REASONS.values())


def test_declaration_is_strictly_wider_than_the_kgcs_default() -> None:
    """The whole point of the adapter, stated as a relation to upstream."""
    from kgcs.executor.executor import DEFAULT_SUPPORTED_OPERATIONS

    assert DEFAULT_SUPPORTED_OPERATIONS < SUPPORTED_OPERATIONS
    assert CurationOperationType.RETRACT_ASSERTION not in DEFAULT_SUPPORTED_OPERATIONS
    assert CurationOperationType.RETRACT_ASSERTION in SUPPORTED_OPERATIONS


def test_every_inverse_of_a_supported_operation_is_itself_supported() -> None:
    """Rollback is only real if the inverse can be applied by the same store.

    ``INVERSE_OPERATION_TYPES`` says what type reverses what; it says nothing
    about whether *this* store can apply the result. The gap is not
    hypothetical: before the 0.3.0 re-pin ``CREATE_IDENTITY`` had no inverse at
    all, and the moment it got one (``REVOKE_IDENTITY``) a store that had not
    also learned it would hand ``PlanExecutor`` a compensating plan it reports
    ``UNSUPPORTED_OPERATION`` for — with the identity already committed.

    ``PROMOTE_ONTOLOGY_TERM`` is excluded because it has no inverse upstream
    (it is absent from the map), which this adapter does not get to fix.
    """
    from kg_contracts.curation import INVERSE_OPERATION_TYPES

    for op_type in sorted(SUPPORTED_OPERATIONS, key=lambda o: o.value):
        inverse = INVERSE_OPERATION_TYPES.get(op_type)
        assert inverse is not None, f"{op_type.value} has no declared inverse upstream"
        assert inverse in SUPPORTED_OPERATIONS, (
            f"{op_type.value} is supported but its inverse {inverse.value} is not; "
            f"a plan containing it could be applied and then not rolled back"
        )


def test_ac2_minimum_set_is_covered() -> None:
    """Mapping-spec AC-2, second clause."""
    required = {
        CurationOperationType.CREATE_IDENTITY,
        CurationOperationType.ATTACH_ASSERTION,
        CurationOperationType.RETRACT_ASSERTION,
        CurationOperationType.MERGE_IDENTITIES,
    }
    assert required <= SUPPORTED_OPERATIONS


def test_excluded_operation_is_refused_the_way_the_executor_expects(
    make_canonical_store,
) -> None:
    store = make_canonical_store()
    epoch_before = store.current_epoch()
    with pytest.raises(NotImplementedError) as excinfo:
        store.apply(
            _batch(
                "pl_promote",
                _op(
                    CurationOperationType.PROMOTE_ONTOLOGY_TERM,
                    {"term_id": "ot_x", "term": "x", "term_kind": "k", "graph_id": "g1"},
                ),
            ),
            preconditions=(),
        )
    assert "PROMOTE_ONTOLOGY_TERM" in str(excinfo.value)
    assert store.current_epoch() == epoch_before, "the store must be untouched"


def test_excluded_operation_reaches_the_executor_as_unsupported(make_canonical_store) -> None:
    """End to end: the refusal shape is the one ``PlanExecutor`` turns into
    ``UNSUPPORTED_OPERATION``, not an ``ERROR`` or an escaping exception.

    The executor pre-checks against the ``supported_operations`` it was given,
    so this also pins that handing it :data:`SUPPORTED_OPERATIONS` keeps the two
    declarations in agreement.
    """
    from kg_contracts.curation import CurationPlan
    from kgcs.executor.executor import ExecutionOutcome, PlanExecutor

    store = make_canonical_store()
    executor = PlanExecutor(store, supported_operations=SUPPORTED_OPERATIONS)
    plan = CurationPlan(
        candidate_ids=("c1",),
        snapshot_version="0",
        operations=(
            _op(
                CurationOperationType.PROMOTE_ONTOLOGY_TERM,
                {"term_id": "ot_x", "term": "x", "term_kind": "k", "graph_id": "g1"},
            ),
        ),
        preconditions=(),
        evidence_ids=(),
        policy_version="v1",
    )
    record = executor.execute(plan)
    assert record.outcome is ExecutionOutcome.UNSUPPORTED_OPERATION
    assert record.unsupported_types == ("PROMOTE_ONTOLOGY_TERM",)
    assert store.current_epoch() == 0


# --- each declared-supported operation actually applies -----------------------


def test_retract_assertion_applies(make_canonical_store) -> None:
    store = make_canonical_store()
    entity, assertion = _seed(store)
    result = store.apply(
        _batch(
            "pl_retract",
            _op(
                CurationOperationType.RETRACT_ASSERTION,
                {
                    "assertion_id": assertion.assertion_id,
                    "new_status": CurationStatus.SUPERSEDED.value,
                    "superseded_at": "2026-08-01T00:00:00+00:00",
                },
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error
    assert store.assertions_for(entity.identity_id) == []
    [retired] = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(include_superseded=True)
    )
    assert retired.status is CurationStatus.SUPERSEDED


def test_retract_can_revoke_as_well_as_supersede(make_canonical_store) -> None:
    """REVOKED is hidden by default and reachable only via ``include_revoked``.

    **This assertion is the reverse of what it was**, and the reversal is the
    behaviour break in kg_contracts 2.0.0. Before ADR-0025 ``GraphReadOptions``
    had ``include_superseded`` and no ``include_revoked``, upstream's own
    ``test_revoked_record_visible_by_default`` pinned REVOKED-visible, and
    filtering revoked records was held to be the projector's job. The read rule
    now lives in the contract: "a revoked record is returned by no default
    read, at any epoch".

    Nothing in this repository wrote ``CurationStatus.REVOKED`` before this
    change — ``_apply_retract`` accepts it, but no planner, no compensator and
    no test fixture in the shadow path emits ``new_status=REVOKED`` — so no
    stored record changes visibility as a result. This test is the one place
    that produced one, and it did so only to observe it.

    The ``include_superseded`` leg is the independence cross-term applied to
    *this adapter's own* retract path: the flag for the other status must not
    reveal this record.
    """
    store = make_canonical_store()
    entity, assertion = _seed(store)
    result = store.apply(
        _batch(
            "pl_revoke",
            _op(
                CurationOperationType.RETRACT_ASSERTION,
                {
                    "assertion_id": assertion.assertion_id,
                    "new_status": CurationStatus.REVOKED.value,
                    "superseded_at": "2026-08-01T00:00:00+00:00",
                },
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error

    assert store.assertions_for(entity.identity_id) == []
    assert (
        store.assertions_for(
            entity.identity_id, options=GraphReadOptions(include_superseded=True)
        )
        == []
    ), "include_superseded must not reveal a REVOKED record"
    [revoked] = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(include_revoked=True)
    )
    assert revoked.assertion_id == assertion.assertion_id
    assert revoked.status is CurationStatus.REVOKED


def test_revoke_identity_is_a_tombstone_not_a_delete(make_canonical_store) -> None:
    """``REVOKE_IDENTITY`` applied for real, with its three stated properties.

    The conformance suite asserts the same contract; this asserts it through
    *this adapter's* operation dispatch and against a namespace that also holds
    the identity's assertions, which the suite's fixture does not.
    """
    store = make_canonical_store()
    entity, assertion = _seed(store, key="tombstone")
    creation_epoch = store.get_entity(entity.identity_id).curation_epoch

    result = store.apply(
        _batch(
            "pl_revoke_identity",
            _op(
                CurationOperationType.REVOKE_IDENTITY,
                {"identity_id": entity.identity_id, "reason": "rollback"},
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error
    assert result.new_epoch > creation_epoch

    # 1. gone from ordinary reads ...
    assert store.get_entity(entity.identity_id) is None
    # ... at every epoch, including the one that created it.
    assert (
        store.get_entity(
            entity.identity_id, options=GraphReadOptions(curation_epoch=creation_epoch)
        )
        is None
    )
    # 2. retained, and reachable by identity with the flag.
    stored = store.get_entity(entity.identity_id, options=GraphReadOptions(include_revoked=True))
    assert stored is not None
    assert stored.identity_id == entity.identity_id
    assert stored.status is CurationStatus.REVOKED
    # 3. the creation epoch is NOT advanced by the revoke.
    assert stored.curation_epoch == creation_epoch
    assert (
        store.get_entity(
            entity.identity_id,
            options=GraphReadOptions(curation_epoch=creation_epoch, include_revoked=True),
        )
        is not None
    )
    # The assertions about it are untouched - a tombstone is not a cascade.
    assert [a.assertion_id for a in store.assertions_for(entity.identity_id)] == [
        assertion.assertion_id
    ]


def test_revoke_identity_names_a_reason_when_it_cannot_apply(make_canonical_store) -> None:
    """ADR-0021 for the new operation: both refusal paths carry an ``error``."""
    store = make_canonical_store()

    unknown = store.apply(
        _batch(
            "pl_unknown",
            _op(CurationOperationType.REVOKE_IDENTITY, {"identity_id": "kg://g1/identity/nope"}),
        ),
        preconditions=(),
    )
    assert unknown.committed is False
    assert unknown.error is not None and unknown.error.startswith("unknown_identity:")

    malformed = store.apply(
        _batch("pl_malformed", _op(CurationOperationType.REVOKE_IDENTITY, {"identity_id": 7})),
        preconditions=(),
    )
    assert malformed.committed is False
    assert malformed.error is not None and malformed.error.startswith("invalid_payload:")


def test_merge_identities_applies_and_moves_assertions(make_canonical_store) -> None:
    store = make_canonical_store()
    survivor, survivor_assertion = _seed(store, key="survivor")
    merged, merged_assertion = _seed(store, key="merged")

    result = store.apply(
        _batch(
            "pl_merge",
            _op(
                CurationOperationType.MERGE_IDENTITIES,
                {
                    "survivor_identity": survivor.identity_id,
                    "merged_identities": [merged.identity_id],
                },
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error

    on_survivor = {a.assertion_id for a in store.assertions_for(survivor.identity_id)}
    assert on_survivor == {survivor_assertion.assertion_id, merged_assertion.assertion_id}
    assert store.assertions_for(merged.identity_id) == []
    assert store.get_entity(merged.identity_id) is None, "merged identity is SUPERSEDED"
    assert store.get_entity(survivor.identity_id) is not None


def test_merge_leaves_the_pre_merge_epoch_intact(make_canonical_store) -> None:
    """Identity status is epoch-versioned too: at epoch N the merged identity
    was still a separate, ACTIVE identity, and a snapshot read must say so."""
    store = make_canonical_store()
    survivor, _ = _seed(store, key="s2")
    merged, _ = _seed(store, key="m2")
    epoch_n = store.current_epoch()

    store.apply(
        _batch(
            "pl_merge2",
            _op(
                CurationOperationType.MERGE_IDENTITIES,
                {
                    "survivor_identity": survivor.identity_id,
                    "merged_identities": [merged.identity_id],
                },
            ),
        ),
        preconditions=(),
    )
    at_n = store.get_entity(merged.identity_id, options=GraphReadOptions(curation_epoch=epoch_n))
    assert at_n is not None
    assert at_n.status is CurationStatus.ACTIVE


def test_split_identity_restores_a_merge(make_canonical_store) -> None:
    store = make_canonical_store()
    survivor, survivor_assertion = _seed(store, key="s3")
    merged, merged_assertion = _seed(store, key="m3")
    store.apply(
        _batch(
            "pl_merge3",
            _op(
                CurationOperationType.MERGE_IDENTITIES,
                {
                    "survivor_identity": survivor.identity_id,
                    "merged_identities": [merged.identity_id],
                },
            ),
        ),
        preconditions=(),
    )
    result = store.apply(
        _batch(
            "pl_split3",
            _op(
                CurationOperationType.SPLIT_IDENTITY,
                {
                    "source_identity": survivor.identity_id,
                    "into_identities": [merged.identity_id],
                },
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error
    assert [a.assertion_id for a in store.assertions_for(survivor.identity_id)] == [
        survivor_assertion.assertion_id
    ]
    assert [a.assertion_id for a in store.assertions_for(merged.identity_id)] == [
        merged_assertion.assertion_id
    ]
    assert store.get_entity(merged.identity_id) is not None


def test_reassign_assertion_applies(make_canonical_store) -> None:
    store = make_canonical_store()
    source, assertion = _seed(store, key="from")
    target, _ = _seed(store, key="to")
    result = store.apply(
        _batch(
            "pl_reassign",
            _op(
                CurationOperationType.REASSIGN_ASSERTION,
                {
                    "assertion_id": assertion.assertion_id,
                    "from_identity": source.identity_id,
                    "to_identity": target.identity_id,
                },
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error
    assert assertion.assertion_id not in {
        a.assertion_id for a in store.assertions_for(source.identity_id)
    }
    assert assertion.assertion_id in {
        a.assertion_id for a in store.assertions_for(target.identity_id)
    }


# --- ADR-0021: never a reasonless non-commit ----------------------------------


def _failure_cases(store, entity, assertion):
    """Every way this adapter can decline a batch, as (label, batch) pairs."""
    unknown = "kg://g1/identity/00000000000000000000000000"
    return [
        (
            "retract an assertion that does not exist",
            _batch(
                "pl_f1",
                _op(CurationOperationType.RETRACT_ASSERTION, {"assertion_id": "as_nope"}),
            ),
        ),
        (
            "retract to ACTIVE",
            _batch(
                "pl_f2",
                _op(
                    CurationOperationType.RETRACT_ASSERTION,
                    {
                        "assertion_id": assertion.assertion_id,
                        "new_status": CurationStatus.ACTIVE.value,
                    },
                ),
            ),
        ),
        (
            "retract with no assertion_id",
            _batch("pl_f3", _op(CurationOperationType.RETRACT_ASSERTION, {"x": 1})),
        ),
        (
            "create an identity from a malformed payload",
            _batch(
                "pl_f4",
                _op(CurationOperationType.CREATE_IDENTITY, {"identity_id": "not-an-identity"}),
            ),
        ),
        (
            "attach a payload that is neither an assertion nor a restore",
            _batch("pl_f5", _op(CurationOperationType.ATTACH_ASSERTION, {"nonsense": True})),
        ),
        (
            "merge into a survivor that does not exist",
            _batch(
                "pl_f6",
                _op(
                    CurationOperationType.MERGE_IDENTITIES,
                    {
                        "survivor_identity": unknown,
                        "merged_identities": [entity.identity_id],
                    },
                ),
            ),
        ),
        (
            "merge a member that does not exist",
            _batch(
                "pl_f7",
                _op(
                    CurationOperationType.MERGE_IDENTITIES,
                    {
                        "survivor_identity": entity.identity_id,
                        "merged_identities": [unknown],
                    },
                ),
            ),
        ),
        (
            "merge with an empty member list",
            _batch(
                "pl_f8",
                _op(
                    CurationOperationType.MERGE_IDENTITIES,
                    {"survivor_identity": entity.identity_id, "merged_identities": []},
                ),
            ),
        ),
        (
            "split an identity that was never merged in",
            _batch(
                "pl_f9",
                _op(
                    CurationOperationType.SPLIT_IDENTITY,
                    {
                        "source_identity": entity.identity_id,
                        "into_identities": [unknown],
                    },
                ),
            ),
        ),
        (
            "reassign against a stale subject",
            _batch(
                "pl_f10",
                _op(
                    CurationOperationType.REASSIGN_ASSERTION,
                    {
                        "assertion_id": assertion.assertion_id,
                        "from_identity": unknown,
                        "to_identity": entity.identity_id,
                    },
                ),
            ),
        ),
    ]


def test_every_non_commit_names_a_reason(make_canonical_store) -> None:
    store = make_canonical_store()
    entity, assertion = _seed(store, key="adr21")
    cases = _failure_cases(store, entity, assertion)
    assert len(cases) >= 10, "the enumeration must not be vacuous"

    for label, batch in cases:
        result = store.apply(batch, preconditions=())
        assert result.committed is False, f"{label}: expected a refusal"
        assert result.error or result.failed_preconditions, (
            f"{label}: committed=False with no reason - exactly the silent "
            f"failure KGIS ADR-0021 narrowed CommitResult to forbid"
        )
        assert result.error is not None
        assert result.error.split(":")[0] in {
            "invalid_payload",
            "unknown_assertion",
            "unknown_identity",
            "unsupported_status",
            "neo4j_error",
        }, f"{label}: unclassified error {result.error!r}"


def test_a_refused_batch_is_an_atomic_no_op(make_canonical_store) -> None:
    """The refusal rolls the whole transaction back, epoch included.

    The batch below has one applicable operation followed by one that fails; a
    store that applied operations as it went would leave the first committed.
    """
    store = make_canonical_store()
    entity, _ = _seed(store, key="atomic")
    epoch_before = store.current_epoch()
    extra = make_assertion(subject_identity=entity.identity_id, predicate="should_not_land")

    result = store.apply(
        _batch(
            "pl_atomic",
            _op(CurationOperationType.ATTACH_ASSERTION, extra.model_dump(mode="json")),
            _op(CurationOperationType.RETRACT_ASSERTION, {"assertion_id": "as_nope"}),
        ),
        preconditions=(),
    )
    assert result.committed is False
    assert result.error is not None
    assert store.current_epoch() == epoch_before
    assert extra.assertion_id not in {
        a.assertion_id
        for a in store.assertions_for(
            entity.identity_id, options=GraphReadOptions(include_superseded=True)
        )
    }


# --- optimistic concurrency: the version counter actually moves ---------------


def test_entity_version_precondition_guards_replay(make_canonical_store) -> None:
    """A *satisfiable* ``entity_version`` guard, and the replay it must block.

    The shared suite only ever uses ``expected="99"`` against a fresh identity,
    which fails whatever the counter does — so conformance says nothing about
    the version *increment*, and a store whose counter is frozen at zero passes
    all seven tests while silently letting a replayed ``CREATE_IDENTITY``
    clobber an existing identity. `kgcs.planner` guards every creation with
    ``entity_version=0`` and the executor's "idempotent replay" property rests
    on the store failing that guard the second time.

    The assertions live in ``scenarios.assert_entity_version_guard`` so that
    ``test_conformance_is_falsifiable.py`` can run *these* assertions — not a
    copy of them — against a store with the counter disabled.
    """
    assert_entity_version_guard(make_canonical_store)


# --- version-bump semantics, pinned so they cannot drift silently -------------
#
# ``entity_version`` is not exposed by any port, so nothing outside the adapter
# could notice these changing. That is precisely why they are written down: a
# reviewer flagged the bump policy as an *unpinned divergence* from the
# reference store - not a defect (monotonicity and staleness both hold, and
# `kgcs.planner` only ever emits ``expected="0"``), but a degree of freedom no
# test constrained.
#
# The reference ``MemoryGraphStore`` bumps once per created entity plus once
# per attached assertion, with **no de-duplication** within a batch. This
# adapter matches that for the two operations upstream implements. The other
# four have no reference semantics at all - ``MemoryGraphStore.apply`` raises
# ``NotImplementedError`` for them - so their rows below are this adapter's own
# decision, recorded rather than inferred.


def test_probe_is_a_read(make_canonical_store) -> None:
    """The measuring instrument must not perturb what it measures.

    Everything below is expressed through ``probe_version``. If probing mutated
    the store, each assertion would be reading a state its own predecessor
    created, and the whole section would be measuring itself.
    """
    store = make_canonical_store()
    entity, _ = _seed(store, key="probe-is-a-read")
    epoch_before = store.current_epoch()

    first = probe_version(store, entity.identity_id)
    second = probe_version(store, entity.identity_id)

    assert first == second
    assert store.current_epoch() == epoch_before
    assert len(store.assertions_for(entity.identity_id)) == 1


def test_unknown_subject_is_version_zero(make_canonical_store) -> None:
    store = make_canonical_store()
    assert probe_version(store, "kg://g1/identity/00000000000000000000000000") == 0


def test_create_and_attach_bump_once_each_with_no_dedup(make_canonical_store) -> None:
    """One bump per *operation*, not one per subject per batch.

    ``CREATE_IDENTITY(X)`` and ``ATTACH_ASSERTION(about X)`` in a single batch
    take X from 0 to **2**, not to 1. That matches ``MemoryGraphStore.apply``,
    which extends one list with the created entities and the attached
    assertions and increments once per element.
    """
    store = make_canonical_store()
    entity = make_entity(key="bump-no-dedup")
    assert probe_version(store, entity.identity_id) == 0

    result = store.apply(
        _batch(
            "pl_bump1",
            _op(CurationOperationType.CREATE_IDENTITY, entity.model_dump(mode="json")),
            _op(
                CurationOperationType.ATTACH_ASSERTION,
                make_assertion(subject_identity=entity.identity_id, predicate="a").model_dump(
                    mode="json"
                ),
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error
    assert probe_version(store, entity.identity_id) == 2

    # Two assertions about the same subject in one batch: +2, again no dedup.
    store.apply(
        _batch(
            "pl_bump2",
            _op(
                CurationOperationType.ATTACH_ASSERTION,
                make_assertion(subject_identity=entity.identity_id, predicate="b").model_dump(
                    mode="json"
                ),
            ),
            _op(
                CurationOperationType.ATTACH_ASSERTION,
                make_assertion(subject_identity=entity.identity_id, predicate="c").model_dump(
                    mode="json"
                ),
            ),
        ),
        preconditions=(),
    )
    assert probe_version(store, entity.identity_id) == 4


def test_retract_bumps_only_the_subject(make_canonical_store) -> None:
    store = make_canonical_store()
    entity, assertion = _seed(store, key="bump-retract")
    other, _ = _seed(store, key="bump-bystander")
    before = probe_version(store, entity.identity_id)
    bystander_before = probe_version(store, other.identity_id)

    store.apply(
        _batch(
            "pl_bump_retract",
            _op(
                CurationOperationType.RETRACT_ASSERTION,
                {
                    "assertion_id": assertion.assertion_id,
                    "superseded_at": "2026-08-01T00:00:00+00:00",
                },
            ),
        ),
        preconditions=(),
    )
    assert probe_version(store, entity.identity_id) == before + 1
    assert probe_version(store, other.identity_id) == bystander_before


def test_merge_bumps_survivor_and_every_member_once(make_canonical_store) -> None:
    """This adapter's own choice - upstream implements no MERGE to match.

    One bump for the survivor and one per merged member, regardless of how many
    assertions actually moved. The alternative (one per moved assertion) would
    make an optimistic guard on the survivor depend on the *size* of the merge,
    which is not something a planner computing against a snapshot can know.
    """
    store = make_canonical_store()
    survivor, _ = _seed(store, key="bump-survivor")
    merged, _ = _seed(store, key="bump-merged")
    survivor_before = probe_version(store, survivor.identity_id)
    merged_before = probe_version(store, merged.identity_id)

    result = store.apply(
        _batch(
            "pl_bump_merge",
            _op(
                CurationOperationType.MERGE_IDENTITIES,
                {
                    "survivor_identity": survivor.identity_id,
                    "merged_identities": [merged.identity_id],
                },
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error
    assert probe_version(store, survivor.identity_id) == survivor_before + 1
    assert probe_version(store, merged.identity_id) == merged_before + 1


def test_reassign_bumps_both_endpoints_once(make_canonical_store) -> None:
    """Also this adapter's choice: the source and the target each move.

    Both endpoints change - one loses an assertion, one gains it - so a plan
    computed against either endpoint's old version is stale and must be
    rejected. Bumping only the target would let a stale plan about the source
    through.
    """
    store = make_canonical_store()
    source, assertion = _seed(store, key="bump-from")
    target, _ = _seed(store, key="bump-to")
    source_before = probe_version(store, source.identity_id)
    target_before = probe_version(store, target.identity_id)

    result = store.apply(
        _batch(
            "pl_bump_reassign",
            _op(
                CurationOperationType.REASSIGN_ASSERTION,
                {
                    "assertion_id": assertion.assertion_id,
                    "from_identity": source.identity_id,
                    "to_identity": target.identity_id,
                },
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error
    assert probe_version(store, source.identity_id) == source_before + 1
    assert probe_version(store, target.identity_id) == target_before + 1


def test_a_refused_batch_bumps_nothing(make_canonical_store) -> None:
    """Atomicity, restated on the counter rather than on the graph."""
    store = make_canonical_store()
    entity, _ = _seed(store, key="bump-refused")
    before = probe_version(store, entity.identity_id)

    result = store.apply(
        _batch(
            "pl_bump_refused",
            _op(
                CurationOperationType.ATTACH_ASSERTION,
                make_assertion(subject_identity=entity.identity_id, predicate="never").model_dump(
                    mode="json"
                ),
            ),
            _op(CurationOperationType.RETRACT_ASSERTION, {"assertion_id": "as_nope"}),
        ),
        preconditions=(),
    )
    assert result.committed is False
    assert probe_version(store, entity.identity_id) == before


def test_a_revoked_assertion_that_is_later_restored(make_canonical_store) -> None:
    """The branch the shared suite never reaches: REVOKED, then restored.

    Found by probe, not by review: an unconditional ``raise`` placed in this
    branch of ``store._reported_status`` left all 585 migration tests green, so
    it was live code with no coverage and a semantic decision nobody had
    written down. It is reachable through the public ``apply()`` surface -
    ``_apply_retract`` accepts ``new_status=REVOKED`` and ``_apply_attach``'s
    restore path accepts ``restore_status=ACTIVE`` - so "the contract suite
    does not exercise it" is not the same as "it cannot happen".

    The decision this pins, stated plainly because it is a judgement and not a
    quotation: ``REVOKED`` is terminal-and-global only while it is the record's
    *latest* status. Once a record has been restored it is live again, so the
    global rule stops applying and the ordinary epoch-versioned rule resumes -
    the record is hidden by default **at the epochs where it was revoked** and
    visible at the epochs where it was not. The alternative (a record that was
    ever revoked stays hidden at every epoch forever) would make a restore
    unobservable on any default read, which is not a restore.

    ``include_superseded`` is asserted not to reveal it, because the
    independence cross-term has to hold on this path too, not only on the one
    upstream tests.
    """
    store = make_canonical_store()
    entity, assertion = _seed(store, key="revoke-then-restore")
    before_revoke = store.current_epoch()

    revoked = store.apply(
        _batch(
            "pl_revoke",
            _op(
                CurationOperationType.RETRACT_ASSERTION,
                {
                    "assertion_id": assertion.assertion_id,
                    "new_status": CurationStatus.REVOKED.value,
                    "superseded_at": "2026-08-01T00:00:00+00:00",
                },
            ),
        ),
        preconditions=(),
    )
    assert revoked.committed is True, revoked.error
    revoked_epoch = revoked.new_epoch
    assert revoked_epoch is not None and revoked_epoch > before_revoke

    restored = store.apply(
        _batch(
            "pl_restore",
            _op(
                CurationOperationType.ATTACH_ASSERTION,
                {
                    "assertion_id": assertion.assertion_id,
                    "subject_identity": entity.identity_id,
                    "restore_status": CurationStatus.ACTIVE.value,
                },
            ),
        ),
        preconditions=(),
    )
    assert restored.committed is True, restored.error

    def ids(**options_kwargs) -> list[str]:
        return [
            a.assertion_id
            for a in store.assertions_for(
                entity.identity_id, options=GraphReadOptions(**options_kwargs)
            )
        ]

    # Latest status is ACTIVE again: an ordinary read sees it, no flags.
    assert ids() == [assertion.assertion_id]

    # At the epoch where it WAS revoked, a default read must not serve it ...
    assert ids(curation_epoch=revoked_epoch) == []
    # ... and the other status's flag must not reveal it either.
    assert ids(curation_epoch=revoked_epoch, include_superseded=True) == [], (
        "include_superseded must not reveal a record that was REVOKED at this epoch"
    )
    # ... but include_revoked must, at its own epoch, with its own status.
    at_revoked = store.assertions_for(
        entity.identity_id,
        options=GraphReadOptions(curation_epoch=revoked_epoch, include_revoked=True),
    )
    assert [a.assertion_id for a in at_revoked] == [assertion.assertion_id]
    assert at_revoked[0].status is CurationStatus.REVOKED

    # And before the revoke it was simply live: visible, ACTIVE, no flags.
    at_start = store.assertions_for(
        entity.identity_id, options=GraphReadOptions(curation_epoch=before_revoke)
    )
    assert [a.assertion_id for a in at_start] == [assertion.assertion_id]
    assert at_start[0].status is CurationStatus.ACTIVE
