"""API tests for the nightly review queue (``/api/review``).

The router is exercised against a fake service injected through
``get_review_items``. The KGCS commit path itself is covered in
``packages/core/tests/migration/curation/test_review_commit.py``; here the
committer is a fake, which is exactly what proves approval calls it and
rejection does not.
"""

from __future__ import annotations

import pytest
from agentic_kg.knowledge_graph.models.pipeline_run import PipelineRun
from agentic_kg.knowledge_graph.models.review_item import (
    ReviewAgreement,
    ReviewEvidenceRef,
    ReviewItem,
)
from agentic_kg.knowledge_graph.pipeline_runs import PipelineRunNotFoundError
from agentic_kg.knowledge_graph.review_items import (
    ReviewItemBlockedError,
    ReviewItemNotFoundError,
    ReviewItemNotPendingError,
)
from agentic_kg_api.dependencies import get_pipeline_runs, get_review_items
from agentic_kg_api.main import app
from fastapi.testclient import TestClient


def make_item(**overrides) -> ReviewItem:
    data = dict(
        namespace="staging",
        run_id="nightly-20260101T023000Z",
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
        rationale="evidence supports admission",
        adviser_type="review_admission",
        adviser_version="review_admission/1",
        adviser_model_id="fake",
        adviser_model_version="1",
        prompt_version="2+evidence",
    )
    data.update(overrides)
    return ReviewItem.build(**data)


class FakeReviewService:
    def __init__(self, items=None):
        self.items = {item.id: item for item in (items or [])}
        self.committer_calls = 0
        self.last_list = None
        self.agreement = {}

    def list(self, **kwargs):
        self.last_list = kwargs
        items = list(self.items.values())
        if kwargs.get("status"):
            items = [item for item in items if item.status == kwargs["status"]]
        if kwargs.get("run_id"):
            items = [item for item in items if item.run_id == kwargs["run_id"]]
        if kwargs.get("candidate_type"):
            wanted = kwargs["candidate_type"]
            items = [
                item
                for item in items
                if item.candidate_kind == wanted or item.entity_type == wanted
            ]
        total = len(items)
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit", 50)
        return items[offset : offset + limit], total

    def get(self, item_id):
        if item_id not in self.items:
            raise ReviewItemNotFoundError(item_id)
        return self.items[item_id]

    def _set_status(self, item, status, user_id, note):
        updated = item.model_copy(
            update={
                "status": status,
                "decided_by": user_id,
                "decision_note": note,
            }
        )
        self.items[item.id] = updated
        return updated

    def approve(self, item_id, *, user_id, note=None):
        item = self.get(item_id)
        if item.status != "pending":
            raise ReviewItemNotPendingError(item_id)
        if not item.committable:
            raise ReviewItemBlockedError(item.commit_blocker or "blocked")
        self.committer_calls += 1
        return self._set_status(item, "approved", user_id, note)

    def reject(self, item_id, *, user_id, note=None):
        item = self.get(item_id)
        if item.status != "pending":
            raise ReviewItemNotPendingError(item_id)
        return self._set_status(item, "rejected", user_id, note)

    def agreement_by_entity_type(self):
        return self.agreement


class FakeRunStore:
    def __init__(self, run=None):
        self.run = run

    def get(self, run_id):
        if self.run is None or self.run.run_id != run_id:
            raise PipelineRunNotFoundError(run_id)
        return self.run


@pytest.fixture
def service() -> FakeReviewService:
    return FakeReviewService()


@pytest.fixture
def client(service):
    app.dependency_overrides[get_review_items] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestList:
    def test_empty_queue(self, client):
        response = client.get("/api/review")
        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    def test_returns_summaries_and_passes_filters(self, client, service):
        item = make_item()
        service.items[item.id] = item
        response = client.get(
            "/api/review?status=pending&type=Problem&run_id=nightly-20260101T023000Z"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        row = body["items"][0]
        assert row["adviser_verdict"] == "admit"
        assert row["candidate_summary"] == "X is hard"
        assert "candidate_payload" not in row
        assert service.last_list["candidate_type"] == "Problem"

    def test_invalid_status_is_422(self, client):
        assert client.get("/api/review?status=nope").status_code == 422

    def test_pagination_params_are_forwarded(self, client, service):
        client.get("/api/review?limit=10&offset=20")
        assert service.last_list["limit"] == 10
        assert service.last_list["offset"] == 20


class TestDetail:
    def test_returns_evidence_text_and_rationale(self, client, service):
        item = make_item()
        service.items[item.id] = item
        response = client.get(f"/api/review/{item.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["evidence_refs"][0]["content"] == "X is hard because Y."
        assert body["rationale"] == "evidence supports admission"
        assert body["adviser_model_id"] == "fake"

    def test_unknown_is_404(self, client):
        assert client.get("/api/review/missing").status_code == 404


class TestDecision:
    def test_requires_x_user_id(self, client, service):
        item = make_item()
        service.items[item.id] = item
        response = client.post(
            f"/api/review/{item.id}", json={"decision": "approve"}
        )
        assert response.status_code == 401
        assert service.committer_calls == 0

    def test_approve_calls_the_committer(self, client, service):
        item = make_item()
        service.items[item.id] = item
        response = client.post(
            f"/api/review/{item.id}",
            json={"decision": "approve", "note": "good"},
            headers={"X-User-ID": "alice"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["decided_by"] == "alice"
        assert body["decision_note"] == "good"
        assert service.committer_calls == 1

    def test_reject_does_not_commit(self, client, service):
        item = make_item()
        service.items[item.id] = item
        response = client.post(
            f"/api/review/{item.id}",
            json={"decision": "reject", "note": "not enough evidence"},
            headers={"X-User-ID": "alice"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert service.committer_calls == 0

    def test_blocked_item_is_409(self, client, service):
        item = make_item(committable=False, commit_blocker="needs ER")
        service.items[item.id] = item
        response = client.post(
            f"/api/review/{item.id}",
            json={"decision": "approve"},
            headers={"X-User-ID": "alice"},
        )
        assert response.status_code == 409
        assert "needs ER" in response.json()["detail"]

    def test_already_decided_is_409(self, client, service):
        item = make_item(status="rejected")
        service.items[item.id] = item
        response = client.post(
            f"/api/review/{item.id}",
            json={"decision": "approve"},
            headers={"X-User-ID": "alice"},
        )
        assert response.status_code == 409

    def test_invalid_decision_is_422(self, client, service):
        item = make_item()
        service.items[item.id] = item
        response = client.post(
            f"/api/review/{item.id}",
            json={"decision": "maybe"},
            headers={"X-User-ID": "alice"},
        )
        assert response.status_code == 422


class TestRunAgreement:
    def test_run_detail_includes_to_date_agreement(self, service):
        run = PipelineRun(
            run_id="nightly-20260101T023000Z",
            started_at="2026-01-01T02:30:00Z",
            status="succeeded",
        )
        service.agreement = {
            "Problem": ReviewAgreement(
                entity_type="Problem", decided=2, agreed=1, agreement_rate=0.5
            )
        }
        app.dependency_overrides[get_review_items] = lambda: service
        app.dependency_overrides[get_pipeline_runs] = lambda: FakeRunStore(run)
        try:
            response = TestClient(app).get(
                "/api/runs/nightly-20260101T023000Z"
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["adviser_human_agreement"]["Problem"]["decided"] == 2
        assert body["adviser_human_agreement"]["Problem"]["agreement_rate"] == 0.5
