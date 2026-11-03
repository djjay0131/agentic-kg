"""The ReviewItem store against a real Neo4j (testcontainers, CI only).

Uses the curation suite's declared throwaway container and a fresh namespace,
so it cannot observe or disturb any other suite's data. Locally Docker is not
available and the whole curation conftest skips this module; the GitHub
``migration-canonical-adapter`` job runs it.
"""

from __future__ import annotations

import uuid

from agentic_kg.knowledge_graph.models.review_item import (
    ReviewEvidenceRef,
    ReviewItem,
)
from agentic_kg.knowledge_graph.review_items import ReviewItemRepository

RUN_ID = "nightly-20260101T023000Z"


def _item(namespace: str) -> ReviewItem:
    return ReviewItem.build(
        namespace=namespace,
        run_id=RUN_ID,
        candidate_id="cand_1",
        now="2026-01-01T02:31:00Z",
        candidate_kind="entity",
        entity_type="Problem",
        candidate_summary="X is hard",
        candidate_payload={"candidate_id": "cand_1"},
        evidence_refs=[
            ReviewEvidenceRef(
                evidence_id="ev1",
                relationship="SUPPORTS",
                content="X is hard because Y.",
            )
        ],
        adviser_verdict="admit",
        confidence=0.9,
        rationale="supports admission",
        adviser_type="review_admission",
        adviser_version="review_admission/1",
        adviser_model_id="fake",
        adviser_model_version="1",
        prompt_version="2+evidence",
    )


def _repository(uri: str, namespace: str) -> ReviewItemRepository:
    return ReviewItemRepository.from_uri(
        uri,
        username="neo4j",
        password="testpassword",
        namespace=namespace,
    )


def test_round_trip_and_idempotency(curation_neo4j):
    namespace = f"review-{uuid.uuid4().hex}"
    store = _repository(curation_neo4j, namespace)
    try:
        item = _item(namespace)
        store.save(item)
        store.save(item)  # idempotent on (run_id, candidate_id)
        fetched = store.get(item.id)
        assert fetched.candidate_id == item.candidate_id
        assert fetched.evidence_refs[0].content == "X is hard because Y."
        items, total = store.list(run_id=RUN_ID)
        assert total == 1
        assert len(items) == 1
        assert store.candidate_ids_for_run(RUN_ID) == {item.candidate_id}
    finally:
        store.close()


def test_decision_is_append_only_and_agreement_is_computed(curation_neo4j):
    namespace = f"review-{uuid.uuid4().hex}"
    store = _repository(curation_neo4j, namespace)
    try:
        item = _item(namespace)
        store.save(item)
        decided = store.record_decision(
            item,
            decision="approve",
            user_id="alice",
            note="good",
            committed_epoch=4,
            committed_identity_ids=("kg://research/identity/1",),
        )
        assert decided.status == "approved"
        assert decided.decided_by == "alice"

        # A second decision must not overwrite the first.
        try:
            store.record_decision(item, decision="reject", user_id="bob")
        except Exception as exc:
            assert "not pending" in str(exc).lower()
        else:  # pragma: no cover - the store must refuse
            raise AssertionError("a decided item accepted a second decision")

        agreement = store.agreement_by_entity_type()
        assert agreement["Problem"].decided == 1
        assert agreement["Problem"].agreed == 1
    finally:
        store.close()
