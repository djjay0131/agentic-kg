"""The ADR-0029 id-keyed lookups on the Neo4j canonical surface.

``GraphMutationStoreContract`` (kg_contracts 2.3.0) covers ``get_assertion``'s
Active/superseded/unknown cases and the ADR-0028 lineage round trips against
the store. What it cannot cover is this adapter's two extra promises:

* the same answer through the read-only façade
  (``Neo4jCanonicalGraphReader``), which applications actually hold;
* ``iter_assertions`` — a batched, namespace-scoped stream that is not part of
  the GraphReader protocol and therefore not in the shared suite.

These are testcontainers tests: they run in CI's canonical-adapter job, not on
a Docker-less workstation. The namespace cases use two freshly minted stores,
which is how every fixture here achieves pristine state without deleting.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from kg_contracts.assertions import CurationStatus
from kg_contracts.curation import CurationOperation, CurationOperationType
from kg_contracts.stores import GraphMutationBatch, GraphReadOptions
from kg_contracts.testing.factories import make_assertion, make_entity

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 20, tzinfo=UTC)


def _commit(store, plan_id, *operations):
    result = store.apply(
        GraphMutationBatch(plan_id=plan_id, operations=operations), preconditions=()
    )
    assert result.committed is True, result.error
    return result.new_epoch


def _create(entity):
    return CurationOperation(
        type=CurationOperationType.CREATE_IDENTITY, payload=entity.model_dump(mode="json")
    )


def _attach(assertion):
    return CurationOperation(
        type=CurationOperationType.ATTACH_ASSERTION, payload=assertion.model_dump(mode="json")
    )


def _retract(assertion_id, *, superseded_by=None):
    payload = {
        "assertion_id": assertion_id,
        "new_status": CurationStatus.SUPERSEDED.value,
        "superseded_at": NOW.isoformat(),
    }
    if superseded_by is not None:
        payload["superseded_by"] = superseded_by
    return CurationOperation(type=CurationOperationType.RETRACT_ASSERTION, payload=payload)


def _revoke(identity_id):
    return CurationOperation(
        type=CurationOperationType.REVOKE_IDENTITY, payload={"identity_id": identity_id}
    )


@pytest.fixture
def two_namespaces(make_canonical_store):
    left = make_canonical_store()
    right = make_canonical_store()
    assert left.namespace != right.namespace
    return left, right


def test_capabilities_declare_assertion_lookup(make_canonical_store) -> None:
    store = make_canonical_store()
    assert store.capabilities().supports_assertion_lookup is True


def test_get_assertion_is_namespace_scoped(two_namespaces) -> None:
    left, right = two_namespaces
    entity = make_entity(key="lookup-isolation")
    assertion = make_assertion(subject_identity=entity.identity_id, object_value=1)
    _commit(left, "pl_left", _create(entity), _attach(assertion))

    assert left.get_assertion(assertion.assertion_id) is not None
    assert right.get_assertion(assertion.assertion_id) is None
    assert right.read_only().get_assertion(assertion.assertion_id) is None


def test_get_assertion_unknown_id_returns_none(make_canonical_store) -> None:
    store = make_canonical_store()
    assert store.get_assertion("assertion_does_not_exist") is None
    assert store.read_only().get_assertion("assertion_does_not_exist") is None


def test_get_assertion_honours_both_history_switches(make_canonical_store) -> None:
    store = make_canonical_store()
    entity = make_entity(key="lookup-history")
    active = make_assertion(
        subject_identity=entity.identity_id, predicate="height_cm", object_value=200
    )
    superseded = make_assertion(
        subject_identity=entity.identity_id,
        predicate="height_cm",
        object_value=195,
        status=CurationStatus.SUPERSEDED,
        superseded_at=NOW,
    )
    revoked = make_assertion(
        subject_identity=entity.identity_id,
        predicate="height_cm",
        object_value=190,
        status=CurationStatus.REVOKED,
    )
    _commit(
        store,
        "pl_history",
        _create(entity),
        _attach(active),
        _attach(superseded),
        _attach(revoked),
    )

    assert store.get_assertion(active.assertion_id) is not None
    assert store.get_assertion(superseded.assertion_id) is None
    assert store.get_assertion(revoked.assertion_id) is None

    assert store.get_assertion(
        superseded.assertion_id, options=GraphReadOptions(include_superseded=True)
    ) is not None
    assert store.get_assertion(
        superseded.assertion_id, options=GraphReadOptions(include_revoked=True)
    ) is None
    assert store.get_assertion(
        revoked.assertion_id, options=GraphReadOptions(include_revoked=True)
    ) is not None
    assert store.get_assertion(
        revoked.assertion_id, options=GraphReadOptions(include_superseded=True)
    ) is None
    both = GraphReadOptions(include_superseded=True, include_revoked=True)
    assert {
        a.assertion_id
        for a in (
            store.get_assertion(active.assertion_id, options=both),
            store.get_assertion(superseded.assertion_id, options=both),
            store.get_assertion(revoked.assertion_id, options=both),
        )
        if a is not None
    } == {active.assertion_id, superseded.assertion_id, revoked.assertion_id}


def test_get_assertion_honours_the_subject_revoke_shield(make_canonical_store) -> None:
    store = make_canonical_store()
    entity = make_entity(key="lookup-shield")
    assertion = make_assertion(subject_identity=entity.identity_id, object_value=1)
    _commit(store, "pl_shield", _create(entity), _attach(assertion))
    _commit(store, "pl_revoke", _revoke(entity.identity_id))

    assert store.get_assertion(assertion.assertion_id) is None
    assert store.read_only().get_assertion(assertion.assertion_id) is None
    surfaced = store.get_assertion(
        assertion.assertion_id, options=GraphReadOptions(include_revoked=True)
    )
    assert surfaced is not None
    assert surfaced.status is CurationStatus.ACTIVE


def test_iter_assertions_covers_all_and_only_the_namespace(two_namespaces) -> None:
    left, right = two_namespaces
    left_entity = make_entity(key="iterate-left")
    right_entity = make_entity(key="iterate-right")
    assertions = [
        make_assertion(subject_identity=left_entity.identity_id, predicate=f"p{i}", object_value=i)
        for i in range(5)
    ]
    foreign = make_assertion(subject_identity=right_entity.identity_id, object_value=99)
    _commit(
        left,
        "pl_left",
        _create(left_entity),
        *(_attach(a) for a in assertions),
    )
    _commit(right, "pl_right", _create(right_entity), _attach(foreign))

    # batch_size=2 forces three keyset pages over five records.
    streamed = list(left.read_only().iter_assertions(batch_size=2))
    assert {a.assertion_id for a in streamed} == {a.assertion_id for a in assertions}
    assert foreign.assertion_id not in {a.assertion_id for a in streamed}
    assert [a.assertion_id for a in right.read_only().iter_assertions()] == [
        foreign.assertion_id
    ]


def test_iter_assertions_honours_history_switches(make_canonical_store) -> None:
    store = make_canonical_store()
    entity = make_entity(key="iterate-history")
    active = make_assertion(
        subject_identity=entity.identity_id, predicate="height_cm", object_value=200
    )
    superseded = make_assertion(
        subject_identity=entity.identity_id,
        predicate="height_cm",
        object_value=195,
        status=CurationStatus.SUPERSEDED,
        superseded_at=NOW,
    )
    revoked = make_assertion(
        subject_identity=entity.identity_id,
        predicate="height_cm",
        object_value=190,
        status=CurationStatus.REVOKED,
    )
    _commit(
        store,
        "pl_iter_history",
        _create(entity),
        _attach(active),
        _attach(superseded),
        _attach(revoked),
    )

    def ids(options: GraphReadOptions) -> set[str]:
        return {a.assertion_id for a in store.read_only().iter_assertions(options)}

    assert ids(GraphReadOptions()) == {active.assertion_id}
    assert ids(GraphReadOptions(include_superseded=True)) == {
        active.assertion_id,
        superseded.assertion_id,
    }
    assert ids(GraphReadOptions(include_revoked=True)) == {
        active.assertion_id,
        revoked.assertion_id,
    }
    assert ids(GraphReadOptions(include_superseded=True, include_revoked=True)) == {
        active.assertion_id,
        superseded.assertion_id,
        revoked.assertion_id,
    }


def test_reader_and_store_agree_on_the_lookups(make_canonical_store) -> None:
    store = make_canonical_store()
    reader = store.read_only()
    entity = make_entity(key="lookup-parity")
    assertions = [
        make_assertion(subject_identity=entity.identity_id, predicate=f"p{i}", object_value=i)
        for i in range(3)
    ]
    _commit(store, "pl_parity", _create(entity), *(_attach(a) for a in assertions))

    for assertion in assertions:
        assert reader.get_assertion(assertion.assertion_id) == store.get_assertion(
            assertion.assertion_id
        )
    assert {a.assertion_id for a in reader.iter_assertions()} == {
        a.assertion_id for a in store.iter_assertions()
    }


def test_superseded_by_and_source_candidate_ids_round_trip(make_canonical_store) -> None:
    """ADR-0028's RETRACT transport becomes a typed field on the retired record.

    The shared suite round-trips both fields through ATTACH; this pins the other
    write path — a forward supersession whose RETRACT payload names its
    successor — and the visibility gate that hides the pointer by default.
    """
    store = make_canonical_store()
    entity = make_entity(key="lineage")
    old = make_assertion(
        subject_identity=entity.identity_id,
        predicate="height_cm",
        object_value=195,
        source_candidate_ids=("cand_old",),
    )
    new = make_assertion(
        subject_identity=entity.identity_id, predicate="height_cm", object_value=200
    )
    attach_epoch = _commit(
        store, "pl_attach", _create(entity), _attach(old), _attach(new)
    )
    _commit(store, "pl_supersede", _retract(old.assertion_id, superseded_by=new.assertion_id))

    # The pointer belongs to the retired record: the epoch before the
    # supersession reports ACTIVE with no successor, so the historical view
    # does not violate the contract's partial invariant.
    historical = store.get_assertion(
        old.assertion_id,
        options=GraphReadOptions(curation_epoch=attach_epoch, include_superseded=True),
    )
    assert historical is not None
    assert historical.status is CurationStatus.ACTIVE
    assert historical.superseded_by is None

    assert store.get_assertion(old.assertion_id) is None
    retired = store.get_assertion(
        old.assertion_id, options=GraphReadOptions(include_superseded=True)
    )
    assert retired is not None
    assert retired.superseded_by == new.assertion_id
    assert retired.source_candidate_ids == ("cand_old",)
    [streamed] = [
        a
        for a in store.read_only().iter_assertions(
            GraphReadOptions(include_superseded=True)
        )
        if a.assertion_id == old.assertion_id
    ]
    assert streamed.superseded_by == new.assertion_id
