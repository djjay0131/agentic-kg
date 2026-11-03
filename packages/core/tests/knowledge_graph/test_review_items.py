"""Docker-free tests for review-queue models, storage and decision semantics.

The real Neo4j round-trip lives in ``test_review_items_integration.py``
(testcontainers, CI only). These tests pin the things that can silently drift
without a database: the idempotency key, the JSON storage shape, the
"a decision is never overwritten" guard, and the approve/reject orchestration
(approve commits, reject does not).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from agentic_kg.knowledge_graph.models.review_item import (
    ReviewAgreement,
    ReviewEvidenceRef,
    ReviewItem,
    is_merge_verdict,
    review_item_id,
    review_item_uid,
)
from agentic_kg.knowledge_graph.review_items import (
    CandidateReviewService,
    ReviewCommitOutcome,
    ReviewItemBlockedError,
    ReviewItemNotFoundError,
    ReviewItemNotPendingError,
    ReviewItemRepository,
)


def make_item(**overrides) -> ReviewItem:
    data = dict(
        namespace="staging",
        run_id="nightly-20260101T023000Z",
        candidate_id="cand_1",
        now="2026-01-01T02:31:00Z",
        candidate_kind="entity",
        entity_type="Problem",
        candidate_summary="X is hard",
        candidate_payload={"candidate_id": "cand_1", "candidate_kind": "entity"},
        evidence_refs=[
            ReviewEvidenceRef(
                evidence_id="ev1",
                relationship="SUPPORTS",
                content="X is hard because Y.",
            )
        ],
        adviser_verdict="admit",
        confidence=0.9,
        rationale="the evidence supports admission",
        adviser_type="review_admission",
        adviser_version="review_admission/1",
        adviser_model_id="fake",
        adviser_model_version="1",
        prompt_version="2+evidence",
        committable=True,
    )
    data.update(overrides)
    return ReviewItem.build(**data)


def make_fake_repo():
    repo = MagicMock()
    session = MagicMock()
    repo.session.return_value.__enter__.return_value = session
    return repo, session


class TestModelAndKeys:
    def test_idempotency_key_is_run_and_candidate(self):
        uid = review_item_uid("staging", "nightly-1", "cand-1")
        assert uid == "staging\x1fnightly-1\x1fcand-1"
        assert review_item_id("staging", "nightly-1", "cand-1") == review_item_id(
            "staging", "nightly-1", "cand-1"
        )
        assert review_item_id("staging", "nightly-1", "cand-1") != review_item_id(
            "staging", "nightly-2", "cand-1"
        )

    def test_merge_verdict_predicate(self):
        assert is_merge_verdict("merge-into:kg://x/identity/1")
        assert not is_merge_verdict("merge-into:")
        assert not is_merge_verdict("admit")

    def test_properties_round_trip(self):
        item = make_item()
        props = item.to_neo4j_properties()
        assert props["uid"] == item.uid
        assert props["ns"] == "staging"
        assert isinstance(props["candidate_payload"], str)
        assert isinstance(props["evidence_refs"], str)
        restored = ReviewItem.from_neo4j_properties(props)
        assert restored == item

    def test_agreement_rate_is_null_when_nothing_decided(self):
        agreement = ReviewAgreement(entity_type="Problem")
        assert agreement.agreement_rate is None


class TestRepositoryWrites:
    def test_save_merges_and_refreshes_only_while_pending(self):
        repo, session = make_fake_repo()
        store = ReviewItemRepository(repo, namespace="staging")
        item = make_item()
        store.save(item)
        tx = MagicMock()
        session.execute_write.call_args.args[0](tx)
        text = tx.run.call_args.args[0]
        assert "MERGE (r:Canon__ReviewItem" in text
        assert "WHERE r.status = 'pending'" in text
        assert tx.run.call_args.kwargs["uid"] == item.uid

    def test_save_rejects_a_foreign_namespace(self):
        repo, _ = make_fake_repo()
        store = ReviewItemRepository(repo, namespace="staging")
        with pytest.raises(Exception):
            store.save(make_item(namespace="other"))

    def test_record_decision_appends_and_updates(self):
        repo, session = make_fake_repo()
        item = make_item()
        result_record = MagicMock()
        result_record.single.return_value = {"r": item.to_neo4j_properties()}
        tx = MagicMock()
        tx.run.return_value = result_record
        session.execute_write.side_effect = lambda work: work(tx)

        store = ReviewItemRepository(repo, namespace="staging")
        updated = store.record_decision(
            item,
            decision="approve",
            user_id="alice",
            note="good",
            committed_epoch=3,
            committed_identity_ids=("kg://research/identity/1",),
        )
        assert updated.status == "pending"  # the fake returns the pre-decision props
        call = tx.run.call_args
        text = call.args[0]
        assert "CREATE (d:Canon__ReviewDecision" in text
        assert call.kwargs["status"] == "approved"
        assert call.kwargs["user_id"] == "alice"
        assert call.kwargs["committed_epoch"] == 3

    def test_record_decision_refuses_a_non_pending_item(self):
        repo, session = make_fake_repo()
        tx = MagicMock()
        result_record = MagicMock()
        result_record.single.return_value = None
        tx.run.return_value = result_record
        session.execute_write.side_effect = lambda work: work(tx)
        store = ReviewItemRepository(repo, namespace="staging")
        with pytest.raises(ReviewItemNotPendingError):
            store.record_decision(
                make_item(), decision="reject", user_id="alice"
            )


class TestRepositoryReads:
    def test_list_returns_items_and_total(self):
        repo, session = make_fake_repo()
        item = make_item()
        list_result = MagicMock()
        list_result.__iter__ = MagicMock(
            return_value=iter([{"r": item.to_neo4j_properties()}])
        )
        count_result = MagicMock()
        count_result.single.return_value = {"count": 1}
        session.execute_read.side_effect = lambda work: (
            work(_tx(list_result)) if work.__name__ == "_list" else work(_tx(count_result))
        )
        store = ReviewItemRepository(repo, namespace="staging")
        items, total = store.list(status="pending", candidate_type="Problem")
        assert total == 1
        assert items == [item]

    def test_get_unknown_raises(self):
        repo, session = make_fake_repo()
        result = MagicMock()
        result.single.return_value = None
        session.execute_read.side_effect = lambda work: work(_tx(result))
        store = ReviewItemRepository(repo, namespace="staging")
        with pytest.raises(ReviewItemNotFoundError):
            store.get("missing")

    def test_agreement_counts_admit_then_approve(self):
        repo, session = make_fake_repo()
        result = MagicMock()
        result.__iter__ = MagicMock(
            return_value=iter(
                [
                    {
                        "entity_type": "Problem",
                        "candidate_kind": "entity",
                        "adviser_verdict": "admit",
                        "status": "approved",
                    },
                    {
                        "entity_type": "Problem",
                        "candidate_kind": "entity",
                        "adviser_verdict": "reject",
                        "status": "approved",
                    },
                ]
            )
        )
        session.execute_read.side_effect = lambda work: work(_tx(result))
        store = ReviewItemRepository(repo, namespace="staging")
        agreement = store.agreement_by_entity_type()
        assert agreement["Problem"].decided == 2
        assert agreement["Problem"].agreed == 1
        assert agreement["Problem"].agreement_rate == 0.5


class TestCandidateReviewService:
    def _service(self, item, committer=None):
        repo = MagicMock()
        repo.get.return_value = item
        repo.record_decision.side_effect = lambda item, **kwargs: item.model_copy(
            update={
                "status": "approved" if kwargs["decision"] == "approve" else "rejected",
                "decided_by": kwargs["user_id"],
                "decision_note": kwargs.get("note"),
            }
        )
        return CandidateReviewService(repo, committer=committer), repo

    def test_approve_calls_the_committer(self):
        calls = []

        def committer(item):
            calls.append(item.id)
            return ReviewCommitOutcome(epoch=7, identity_ids=("kg://x/1",))

        service, repo = self._service(make_item(), committer=committer)
        updated = service.approve("id", user_id="alice", note="yes")
        assert calls == [repo.get.return_value.id]
        assert updated.status == "approved"
        repo.record_decision.assert_called_once()
        assert repo.record_decision.call_args.kwargs["committed_epoch"] == 7

    def test_reject_never_calls_the_committer(self):
        calls = []

        def committer(item):  # pragma: no cover - must not run
            calls.append(item.id)
            return ReviewCommitOutcome(epoch=1)

        service, repo = self._service(make_item(), committer=committer)
        updated = service.reject("id", user_id="alice", note="no")
        assert calls == []
        assert updated.status == "rejected"
        assert repo.record_decision.call_args.kwargs["decision"] == "reject"

    def test_approve_refuses_a_non_committable_item(self):
        service, repo = self._service(
            make_item(committable=False, commit_blocker="needs ER")
        )
        with pytest.raises(ReviewItemBlockedError, match="needs ER"):
            service.approve("id", user_id="alice")
        repo.record_decision.assert_not_called()

    def test_approve_refuses_an_already_decided_item(self):
        service, repo = self._service(make_item(status="rejected"))
        with pytest.raises(ReviewItemNotPendingError):
            service.approve("id", user_id="alice")
        repo.record_decision.assert_not_called()


# -- tiny helpers -----------------------------------------------------------


def _tx(result):
    tx = MagicMock()
    tx.run.return_value = result
    return tx
