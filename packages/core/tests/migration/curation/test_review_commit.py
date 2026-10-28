"""A human approval goes through KGCS, not around it.

No Docker and no network: the reference ``MemoryGraphStore`` is the canonical
store, and the same candidate the adviser stage stores is handed back to the
commit path.
"""

from __future__ import annotations

import pytest
from agentic_kg.migration.curation.commit import (
    ReviewCommitError,
    commit_candidate,
)
from kg_contracts.candidates import (
    EntityCandidate,
    RelationCandidate,
    SourceCoordinates,
)
from kg_contracts.evidence import EvidenceRef, EvidenceRelationship
from kg_contracts.identity import EntityRef, new_identity_id
from kg_contracts.testing.factories import make_scores
from kg_contracts.testing.memory import MemoryGraphStore


def _entity_candidate() -> EntityCandidate:
    return EntityCandidate(
        graph_id="research",
        producer="test",
        producer_run_id="nightly-1:q1",
        ontology_version="1",
        source_coordinates=SourceCoordinates(
            source_type="paper", locator="paper://doi/10.1000/x"
        ),
        semantic_key="problem/x",
        scores=make_scores(
            extraction_confidence=0.8, source_reliability=0.75
        ),
        entity_type="Problem",
        aliases=(
            EntityRef(entity_type="Problem", namespace="surface", key="X problem"),
        ),
        display_name="X problem",
        properties={},
        evidence_refs=(
            EvidenceRef(
                evidence_id="ev1",
                relationship=EvidenceRelationship.SUPPORTS,
            ),
        ),
    )


def test_commit_creates_the_identity_through_kgcs():
    candidate = _entity_candidate()
    store = MemoryGraphStore()

    result = commit_candidate(
        candidate.model_dump(mode="json"),
        namespace="staging",
        canonical_store=store,
    )

    assert result.committed is True
    assert result.epoch == 1
    assert len(result.identity_ids) == 1
    entities = store.find_entities(entity_type="Problem")
    assert [entity.identity_id for entity in entities] == list(result.identity_ids)


def test_commit_refuses_a_relation_with_an_alias_endpoint():
    relation = RelationCandidate(
        graph_id="research",
        producer="test",
        producer_run_id="nightly-1:q1",
        ontology_version="1",
        source_coordinates=SourceCoordinates(
            source_type="paper", locator="paper://doi/10.1000/x"
        ),
        semantic_key="relation/x",
        scores=make_scores(
            extraction_confidence=0.8, source_reliability=0.75
        ),
        relation_type="EXTENDS",
        subject=EntityRef(entity_type="Problem", namespace="surface", key="X"),
        object=EntityRef(entity_type="Problem", namespace="surface", key="Y"),
        properties={},
    )
    with pytest.raises(ReviewCommitError, match="alias endpoint"):
        commit_candidate(
            relation.model_dump(mode="json"),
            namespace="staging",
            canonical_store=MemoryGraphStore(),
        )


def test_commit_attaches_a_relation_with_identity_endpoints():
    relation = RelationCandidate(
        graph_id="research",
        producer="test",
        producer_run_id="nightly-1:q1",
        ontology_version="1",
        source_coordinates=SourceCoordinates(
            source_type="paper", locator="paper://doi/10.1000/x"
        ),
        semantic_key="relation/x",
        scores=make_scores(
            extraction_confidence=0.8, source_reliability=0.75
        ),
        relation_type="EXTENDS",
        subject=new_identity_id("research"),
        object=new_identity_id("research"),
        properties={},
        evidence_refs=(
            EvidenceRef(
                evidence_id="ev1",
                relationship=EvidenceRelationship.SUPPORTS,
            ),
        ),
    )
    result = commit_candidate(
        relation.model_dump(mode="json"),
        namespace="staging",
        canonical_store=MemoryGraphStore(),
    )
    assert result.committed is True
    assert result.identity_ids == ()


def test_a_second_commit_is_not_a_duplicate():
    """The store's own guards make a replay refuse rather than duplicate."""
    candidate = _entity_candidate()
    store = MemoryGraphStore()
    commit_candidate(candidate.model_dump(mode="json"), namespace="staging", canonical_store=store)
    with pytest.raises(ReviewCommitError, match="did not commit"):
        commit_candidate(
            candidate.model_dump(mode="json"), namespace="staging", canonical_store=store
        )
    assert len(store.find_entities(entity_type="Problem")) == 1
