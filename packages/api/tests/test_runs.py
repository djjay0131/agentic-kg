"""Tests for the nightly PipelineRun endpoints (``/api/runs``).

The router is tested against a fake store injected via the
``get_pipeline_runs`` dependency. The real Neo4j path is covered by
``tests/integration/test_runs_read_model.py``.
"""

from __future__ import annotations

import pytest
from agentic_kg.knowledge_graph.models.pipeline_run import (
    PipelineBudget,
    PipelineFailure,
    PipelineQueryResult,
    PipelineRun,
    PipelineTotals,
)
from agentic_kg.knowledge_graph.pipeline_runs import (
    InvalidCursorError,
    PipelineRunNotFoundError,
)
from agentic_kg_api.dependencies import get_pipeline_runs
from agentic_kg_api.main import app
from fastapi.testclient import TestClient


def make_run(run_id: str = "nightly-20260101T023000Z", **overrides) -> PipelineRun:
    data = {
        "run_id": run_id,
        "started_at": "2026-01-01T02:30:00Z",
        "finished_at": "2026-01-01T02:41:00Z",
        "status": "succeeded",
        "trigger": "schedule",
        "namespace": "staging",
        "queries": [
            PipelineQueryResult(
                query_id="q1",
                query="transformer efficiency",
                topic="NLP",
                limit=50,
                papers_seen=120,
                papers_new=7,
                status="succeeded",
            )
        ],
        "totals": PipelineTotals(
            papers_seen=120,
            papers_new=7,
            committed_operations=5,
            deferred_candidates=2,
            honest_nulls=1,
        ),
        "deferral_reasons": {"low_confidence": 2},
        "budget": PipelineBudget(
            max_papers=500,
            max_llm_usd=5.0,
            est_llm_usd=1.25,
            stopped_by_budget=True,
        ),
        "review_queue_size": None,
        "proposed_queries": [{"query": "new idea", "topic": "P3"}],
        "failures": [PipelineFailure(step="cites", message="rate limited", log_url=None)],
        "git_sha": "abc123",
        "image": "gcr.io/vt-gcp-00042/agentic-kg:1",
    }
    data.update(overrides)
    return PipelineRun(**data)


class FakeRunStore:
    """In-memory stand-in for ``PipelineRunRepository``."""

    def __init__(self, runs=None):
        self.runs = runs or []
        self.next_cursor = None
        self.list_error: Exception | None = None
        self.last_list_kwargs: dict | None = None

    def list(self, limit=50, cursor=None):
        self.last_list_kwargs = {"limit": limit, "cursor": cursor}
        if self.list_error is not None:
            raise self.list_error
        return self.runs, self.next_cursor

    def get(self, run_id):
        for run in self.runs:
            if run.run_id == run_id:
                return run
        raise PipelineRunNotFoundError(run_id)


@pytest.fixture
def store() -> FakeRunStore:
    return FakeRunStore()


@pytest.fixture
def runs_client(store):
    app.dependency_overrides[get_pipeline_runs] = lambda: store
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestListRuns:
    def test_empty_state(self, runs_client):
        response = runs_client.get("/api/runs")
        assert response.status_code == 200
        assert response.json() == {"runs": [], "next_cursor": None}

    def test_returns_summaries(self, runs_client, store):
        store.runs = [make_run()]
        response = runs_client.get("/api/runs")
        assert response.status_code == 200
        body = response.json()
        assert len(body["runs"]) == 1
        row = body["runs"][0]
        assert row["run_id"] == "nightly-20260101T023000Z"
        assert row["status"] == "succeeded"
        assert row["trigger"] == "schedule"
        assert row["totals"]["papers_seen"] == 120
        assert row["totals"]["papers_new"] == 7
        assert row["totals"]["committed_operations"] == 5
        assert row["totals"]["deferred_candidates"] == 2
        assert row["budget_stopped"] is True
        assert row["failures_count"] == 1
        assert row["review_queue_size"] is None
        # Summary is a projection: no per-query detail.
        assert "queries" not in row

    def test_passes_pagination_params(self, runs_client, store):
        store.next_cursor = "abc"
        response = runs_client.get("/api/runs?limit=10&cursor=tok")
        assert response.status_code == 200
        assert response.json()["next_cursor"] == "abc"
        assert store.last_list_kwargs == {"limit": 10, "cursor": "tok"}

    def test_invalid_cursor_returns_400(self, runs_client, store):
        store.list_error = InvalidCursorError("bad")
        response = runs_client.get("/api/runs?cursor=garbage")
        assert response.status_code == 400

    @pytest.mark.parametrize("limit", [0, -1, 201, "nope"])
    def test_invalid_limit_returns_422(self, runs_client, limit):
        response = runs_client.get(f"/api/runs?limit={limit}")
        assert response.status_code == 422


class TestGetRun:
    def test_returns_full_run(self, runs_client, store):
        store.runs = [make_run()]
        response = runs_client.get("/api/runs/nightly-20260101T023000Z")
        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == "nightly-20260101T023000Z"
        assert body["queries"][0]["query_id"] == "q1"
        assert body["deferral_reasons"] == {"low_confidence": 2}
        assert body["budget"]["stopped_by_budget"] is True
        assert body["proposed_queries"] == [{"query": "new idea", "topic": "P3"}]
        assert body["failures"][0]["step"] == "cites"
        assert body["review_queue_size"] is None
        assert body["git_sha"] == "abc123"
        assert body["image"] == "gcr.io/vt-gcp-00042/agentic-kg:1"

    def test_unknown_run_returns_404(self, runs_client):
        response = runs_client.get("/api/runs/nightly-does-not-exist")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
