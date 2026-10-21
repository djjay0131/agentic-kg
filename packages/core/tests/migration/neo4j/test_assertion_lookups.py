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
from agentic_kg.migration.neo4j import store as store_module
from agentic_kg.migration.neo4j.schema import LABEL_ASSERTION, UID_SEP
from kg_contracts.assertions import CurationStatus
from kg_contracts.curation import CurationOperation, CurationOperationType
from kg_contracts.stores import GraphMutationBatch, GraphReadOptions
from kg_contracts.testing.factories import make_assertion, make_entity
from kgps.ports import ReaderAssertionCatalog, catalog_for, supports_native_lookup

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
    """AC-2: the store *and* the reader KGPS actually holds advertise the lookup.

    ``kgps.ports.supports_native_lookup`` reads ``reader.capabilities()``; a
    reader without that method silently forced a full ``find_entities`` +
    ``assertions_for`` scan on every ``explain``.
    """
    store = make_canonical_store()
    reader = store.read_only()
    assert store.capabilities().supports_assertion_lookup is True
    assert reader.capabilities() == store.capabilities()
    assert supports_native_lookup(store) is True
    assert supports_native_lookup(reader) is True


def test_kgps_catalog_uses_get_assertion_not_a_scan(
    make_canonical_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The capability is only useful if KGPS's catalog takes the indexed path."""
    store = make_canonical_store()
    entity = make_entity(key="catalog-native")
    assertion = make_assertion(subject_identity=entity.identity_id, object_value=1)
    _commit(store, "pl_catalog", _create(entity), _attach(assertion))
    reader = store.read_only()

    lookups: list[str] = []
    real_get = reader.get_assertion

    def spy_get(assertion_id, options=GraphReadOptions()):
        lookups.append(assertion_id)
        return real_get(assertion_id, options)

    def fail_scan(*args, **kwargs):
        raise AssertionError("KGPS fell back to a full find_entities scan")

    monkeypatch.setattr(reader, "get_assertion", spy_get)
    monkeypatch.setattr(reader, "find_entities", fail_scan)

    catalog = catalog_for(reader)
    assert isinstance(catalog, ReaderAssertionCatalog)
    found = catalog.get_assertion(assertion.assertion_id)
    assert found is not None
    assert found.assertion_id == assertion.assertion_id
    assert lookups == [assertion.assertion_id]


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "not-an-assertion-id",
        "as_" + "x" * 26 + UID_SEP + "extra",
    ],
)
def test_get_assertion_rejects_malformed_ids_without_querying(
    make_canonical_store, monkeypatch: pytest.MonkeyPatch, bad_id: str
) -> None:
    store = make_canonical_store()

    def fail_query(*args, **kwargs):
        raise AssertionError("a malformed assertion id reached the database")

    monkeypatch.setattr(store_module, "_read_assertion", fail_query)
    assert store.get_assertion(bad_id) is None
    assert store.read_only().get_assertion(bad_id) is None


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


def test_retract_to_revoked_clears_an_inherited_superseded_by(
    make_canonical_store,
) -> None:
    """A REVOKED record may not carry a successor pointer (review finding 2).

    ``superseded_by`` is a partial invariant: set => status SUPERSEDED. Retiring
    an already-SUPERSEDED record as REVOKED must clear the pointer it inherited,
    or ``Assertion.model_validate`` refuses the update and a valid RETRACT
    becomes a ``CommitRefused`` regression against ``master``.
    """
    store = make_canonical_store()
    entity = make_entity(key="revoke-superseded")
    old = make_assertion(
        subject_identity=entity.identity_id, predicate="height_cm", object_value=195
    )
    new = make_assertion(
        subject_identity=entity.identity_id, predicate="height_cm", object_value=200
    )
    _commit(store, "pl_attach", _create(entity), _attach(old), _attach(new))
    _commit(store, "pl_supersede", _retract(old.assertion_id, superseded_by=new.assertion_id))

    retired = store.get_assertion(
        old.assertion_id, options=GraphReadOptions(include_superseded=True)
    )
    assert retired is not None
    assert retired.superseded_by == new.assertion_id

    result = store.apply(
        GraphMutationBatch(
            plan_id="pl_revoke_superseded",
            operations=(
                CurationOperation(
                    type=CurationOperationType.RETRACT_ASSERTION,
                    payload={
                        "assertion_id": old.assertion_id,
                        "new_status": CurationStatus.REVOKED.value,
                        "superseded_at": NOW.isoformat(),
                    },
                ),
            ),
        ),
        preconditions=(),
    )
    assert result.committed is True, result.error

    revoked = store.get_assertion(
        old.assertion_id, options=GraphReadOptions(include_revoked=True)
    )
    assert revoked is not None
    assert revoked.status is CurationStatus.REVOKED
    assert revoked.superseded_by is None
    [streamed] = [
        a
        for a in store.read_only().iter_assertions(
            GraphReadOptions(include_revoked=True)
        )
        if a.assertion_id == old.assertion_id
    ]
    assert streamed.superseded_by is None


def test_iter_assertions_honours_the_subject_revoke_shield(
    make_canonical_store,
) -> None:
    """ADR-0026's shield is a read rule on the *stream* too, not just the list."""
    store = make_canonical_store()
    entity = make_entity(key="iterate-shield")
    assertion = make_assertion(subject_identity=entity.identity_id, object_value=1)
    _commit(store, "pl_iter_shield", _create(entity), _attach(assertion))
    _commit(store, "pl_iter_revoke", _revoke(entity.identity_id))

    reader = store.read_only()
    assert list(reader.iter_assertions()) == []
    assert [a.assertion_id for a in reader.iter_assertions(
        GraphReadOptions(include_revoked=True)
    )] == [assertion.assertion_id]


def test_iter_assertions_at_a_historical_epoch_still_honours_the_shield(
    make_canonical_store,
) -> None:
    """A historical ``curation_epoch`` does not time-travel a revoke away.

    Revocation is terminal and global (ADR-0025); the shield reads the latest
    identity status, so asking for the epoch that created the assertion still
    hides it. ``include_revoked`` is the one switch that reveals it, and it
    reports the assertion at its status *at that epoch*.
    """
    store = make_canonical_store()
    entity = make_entity(key="iterate-historical-shield")
    assertion = make_assertion(subject_identity=entity.identity_id, object_value=1)
    creation_epoch = _commit(store, "pl_hist", _create(entity), _attach(assertion))
    _commit(store, "pl_hist_revoke", _revoke(entity.identity_id))

    reader = store.read_only()
    historical = GraphReadOptions(curation_epoch=creation_epoch)
    assert list(reader.iter_assertions(historical)) == []
    [surfaced] = list(
        reader.iter_assertions(
            GraphReadOptions(curation_epoch=creation_epoch, include_revoked=True)
        )
    )
    assert surfaced.assertion_id == assertion.assertion_id
    assert surfaced.status is CurationStatus.ACTIVE


def test_iter_assertions_pins_the_epoch_for_the_whole_stream(
    make_canonical_store,
) -> None:
    """A commit mid-stream must not leak into a later page (review finding 6).

    Each page has its own transaction; without pinning, the each-page "latest
    epoch" could advance between pages. The stream pins ``current_epoch()`` once
    at start, so the second assertion — committed after the first item was
    consumed — is not in the stream's snapshot.
    """
    store = make_canonical_store()
    entity = make_entity(key="iterate-pinned")
    first = make_assertion(
        subject_identity=entity.identity_id, predicate="p0", object_value=0
    )
    second = make_assertion(
        subject_identity=entity.identity_id, predicate="p1", object_value=1
    )
    _commit(store, "pl_pin_1", _create(entity), _attach(first))

    stream = store.read_only().iter_assertions(batch_size=1)
    assert next(stream).assertion_id == first.assertion_id

    _commit(store, "pl_pin_2", _attach(second))
    assert list(stream) == []
    assert {a.assertion_id for a in store.read_only().iter_assertions()} == {
        first.assertion_id,
        second.assertion_id,
    }


def test_ensure_schema_creates_the_seq_index_idempotently(
    make_canonical_store, canonical_driver
) -> None:
    """Keyset pagination needs ``(ns, seq)``; the DDL is additive and re-runnable."""
    store = make_canonical_store()
    store.ensure_schema()
    store.ensure_schema()

    with canonical_driver.session() as session:
        rows = session.run(
            "SHOW INDEXES YIELD name, labelsOrTypes, properties, state "
            "WHERE name = 'canon_assertion_seq' "
            "RETURN labelsOrTypes, properties, state"
        ).data()

    assert len(rows) == 1
    assert rows[0]["labelsOrTypes"] == [LABEL_ASSERTION]
    assert rows[0]["properties"] == ["ns", "seq"]
    assert rows[0]["state"] in {"ONLINE", "POPULATING"}


# --- falsifiability: each new read rule can actually go red -------------------


def test_get_assertion_shield_mutant_is_caught(make_canonical_store, monkeypatch) -> None:
    """Neutralise the shield and the get-path shield test must fail."""
    monkeypatch.setattr(store_module, "_subject_revoked", lambda history, options: False)
    with pytest.raises(AssertionError):
        test_get_assertion_honours_the_subject_revoke_shield(make_canonical_store)


def test_iter_assertions_join_mutant_is_caught(make_canonical_store, monkeypatch) -> None:
    """Drop the identity join from the page query and the stream shield test fails.

    The ``OPTIONAL MATCH`` is the only thing that carries the subject history
    into the stream; without it every assertion looks like it has a live subject.
    """
    real_page = store_module._read_assertion_page

    def without_join(tx, namespace, after, limit):
        rows = real_page(tx, namespace, after, limit)
        for row in rows:
            row.pop("subject_status_history", None)
        return rows

    monkeypatch.setattr(store_module, "_read_assertion_page", without_join)
    with pytest.raises(AssertionError):
        test_iter_assertions_honours_the_subject_revoke_shield(make_canonical_store)


def test_unscoped_assertion_lookup_mutant_is_caught(
    two_namespaces, monkeypatch
) -> None:
    """An id lookup that ignores the namespace must fail the isolation test."""
    real_read = store_module._read_assertion

    def unscoped(tx, namespace, assertion_id):
        record = tx.run(
            f"MATCH (a:{LABEL_ASSERTION} {{assertion_id: $id}}) "
            f"RETURN a.payload AS payload, a.status_history AS status_history, "
            f"       a.curation_epoch AS curation_epoch, "
            f"       a.subject_identity AS subject_identity",
            id=assertion_id,
        ).single()
        return None if record is None else dict(record)

    monkeypatch.setattr(store_module, "_read_assertion", unscoped)
    with pytest.raises(AssertionError):
        test_get_assertion_is_namespace_scoped(two_namespaces)
    # The real helper is restored by monkeypatch; prove it is not the mutant.
    assert real_read is not unscoped
