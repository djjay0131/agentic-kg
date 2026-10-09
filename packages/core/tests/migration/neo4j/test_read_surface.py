"""`GraphReader` methods the shared suite never exercises.

``GraphMutationStoreContract`` drives ``current_epoch``, ``get_entity`` and
``assertions_for``. It never calls ``find_entities`` or ``neighborhood``, so
conformance says nothing about them — and those two are exactly what the
projector will use to walk canonical state. Covered here rather than left to the
first projection PR to discover.

Also pinned: the read-only façade and the store return the *same* answers. They
share the visibility helpers by construction, but "by construction" is a claim
about today's code, and the projector reads through the façade while every test
above reads through the store.
"""

from __future__ import annotations

import pytest
from kg_contracts.assertions import CurationStatus
from kg_contracts.curation import CurationOperation, CurationOperationType
from kg_contracts.identity import EntityRef
from kg_contracts.stores import GraphMutationBatch, GraphReadOptions
from kg_contracts.testing.factories import make_assertion, make_entity

pytestmark = pytest.mark.integration


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


@pytest.fixture
def populated(make_canonical_store):
    """Two papers and an author, with a relation edge between two of them."""
    store = make_canonical_store()
    paper = make_entity(entity_type="Paper", key="p1")
    other = make_entity(entity_type="Paper", key="p2")
    author = make_entity(entity_type="Author", key="a1")
    epoch_1 = _commit(store, "pl_1", _create(paper), _create(other), _create(author))

    edge = make_assertion(
        subject_identity=paper.identity_id,
        predicate="AUTHORED_BY",
        object_value=None,
        object_identity=author.identity_id,
    )
    epoch_2 = _commit(store, "pl_2", _attach(edge))
    return {
        "store": store,
        "paper": paper,
        "other": other,
        "author": author,
        "edge": edge,
        "epoch_1": epoch_1,
        "epoch_2": epoch_2,
    }


def test_find_entities_filters_by_type(populated) -> None:
    store = populated["store"]
    papers = store.find_entities(entity_type="Paper")
    assert {e.identity_id for e in papers} == {
        populated["paper"].identity_id,
        populated["other"].identity_id,
    }
    assert [e.identity_id for e in store.find_entities(entity_type="Author")] == [
        populated["author"].identity_id
    ]
    assert store.find_entities(entity_type="NoSuchType") == []


def test_find_entities_filters_by_alias(populated) -> None:
    store = populated["store"]
    alias = EntityRef(entity_type="Paper", namespace="test", key="p2")
    assert [e.identity_id for e in store.find_entities(alias=alias)] == [
        populated["other"].identity_id
    ]
    missing = EntityRef(entity_type="Paper", namespace="test", key="nope")
    assert store.find_entities(alias=missing) == []


def test_find_entities_honours_the_epoch_snapshot(make_canonical_store) -> None:
    store = make_canonical_store()
    first = make_entity(entity_type="Paper", key="early")
    epoch_1 = _commit(store, "pl_a", _create(first))
    later = make_entity(entity_type="Paper", key="late")
    _commit(store, "pl_b", _create(later))

    at_1 = store.find_entities(
        entity_type="Paper", options=GraphReadOptions(curation_epoch=epoch_1)
    )
    assert [e.identity_id for e in at_1] == [first.identity_id]
    assert len(store.find_entities(entity_type="Paper")) == 2


def test_find_entities_hides_superseded_identities_by_default(populated) -> None:
    """A merged-away identity drops out of ``find_entities`` at the later epoch.

    The projector's sweep depends on this: an identity absent from a published
    epoch must not be projected (spec §4.3).
    """
    store = populated["store"]
    _commit(
        store,
        "pl_merge",
        CurationOperation(
            type=CurationOperationType.MERGE_IDENTITIES,
            payload={
                "survivor_identity": populated["paper"].identity_id,
                "merged_identities": [populated["other"].identity_id],
            },
        ),
    )
    visible = {e.identity_id for e in store.find_entities(entity_type="Paper")}
    assert visible == {populated["paper"].identity_id}

    with_superseded = store.find_entities(
        entity_type="Paper", options=GraphReadOptions(include_superseded=True)
    )
    merged = {e.identity_id: e for e in with_superseded}[populated["other"].identity_id]
    assert merged.status is CurationStatus.SUPERSEDED


def test_neighborhood_follows_object_identity(populated) -> None:
    store = populated["store"]
    neighbours = store.neighborhood(populated["paper"].identity_id)
    assert [e.identity_id for e in neighbours] == [populated["author"].identity_id]
    assert store.neighborhood(populated["author"].identity_id) == []


def test_neighborhood_respects_the_epoch_the_edge_was_written_at(populated) -> None:
    """At epoch 1 the identities exist but the edge does not, so no neighbours."""
    store = populated["store"]
    at_1 = store.neighborhood(
        populated["paper"].identity_id,
        options=GraphReadOptions(curation_epoch=populated["epoch_1"]),
    )
    assert at_1 == []


def test_read_only_facade_agrees_with_the_store(populated) -> None:
    store = populated["store"]
    reader = store.read_only()
    paper_id = populated["paper"].identity_id

    assert reader.current_epoch() == store.current_epoch()
    assert reader.get_entity(paper_id) == store.get_entity(paper_id)
    assert reader.assertions_for(paper_id) == store.assertions_for(paper_id)
    assert reader.find_entities(entity_type="Paper") == store.find_entities(entity_type="Paper")
    assert reader.neighborhood(paper_id) == store.neighborhood(paper_id)

    # And the comparisons above are not all empty-to-empty (§9.0 obligation 2).
    assert reader.get_entity(paper_id) is not None
    assert reader.assertions_for(paper_id)
    assert reader.find_entities(entity_type="Paper")
    assert reader.neighborhood(paper_id)


def test_namespaces_are_isolated_from_each_other(make_canonical_store) -> None:
    """Two stores over one physical database cannot see each other's data.

    This is what makes a pristine store per ``make_store()`` achievable without
    a destructive statement, and it is the Community-edition answer to spec
    §4.2's UNDETERMINED U-1 (one user database, so separation is by namespace
    plus label prefix rather than by engine).
    """
    left = make_canonical_store()
    right = make_canonical_store()
    assert left.namespace != right.namespace

    entity = make_entity(entity_type="Paper", key="isolated")
    _commit(left, "pl_iso", _create(entity))

    assert left.get_entity(entity.identity_id) is not None
    assert right.get_entity(entity.identity_id) is None
    assert right.find_entities(entity_type="Paper") == []
    assert right.current_epoch() == 0
