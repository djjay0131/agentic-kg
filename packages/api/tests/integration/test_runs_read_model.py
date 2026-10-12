"""Integration test (testcontainers): the Runs API reads :PipelineRun nodes.

Writes runs through the shared core writer (``save_pipeline_run``) exactly as
the T29 nightly job would, then asserts the T30 endpoints surface them through
a real Neo4j. Skips cleanly without Docker.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_TEST_MARKER = "TEST_nightly-"


@pytest.fixture(scope="module")
def neo4j_container():
    """Start an ephemeral Neo4j container (skips without Docker/testcontainers)."""
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers not installed")
        return

    try:
        import docker

        docker.from_env().ping()
    except Exception:
        pytest.skip("Docker not available")
        return

    container = Neo4jContainer("neo4j:5.26-community", password="testpassword")
    try:
        container.start()
        yield container
    finally:
        container.stop()


def _cleanup(repo) -> None:
    with repo.session() as session:
        session.run(
            "MATCH (r:PipelineRun) WHERE r.run_id STARTS WITH $marker DETACH DELETE r",
            marker=_TEST_MARKER,
        )


def _seed_runs(repo) -> dict:
    from agentic_kg.knowledge_graph.models.pipeline_run import (
        PipelineBudget,
        PipelineFailure,
        PipelineQueryResult,
        PipelineRun,
        PipelineTotals,
    )
    from agentic_kg.knowledge_graph.pipeline_runs import PipelineRunRepository

    store = PipelineRunRepository(repo)
    older = PipelineRun(
        run_id=f"{_TEST_MARKER}20260101T010000Z",
        started_at="2026-01-01T01:00:00Z",
        finished_at="2026-01-01T01:10:00Z",
        status="succeeded",
        trigger="schedule",
        namespace="staging",
        totals=PipelineTotals(papers_seen=10, papers_new=2),
        budget=PipelineBudget(max_papers=100, max_llm_usd=1.0, est_llm_usd=0.1),
        review_queue_size=None,
        git_sha="old", image="img:old",
    )
    newer = PipelineRun(
        run_id=f"{_TEST_MARKER}20260102T010000Z",
        started_at="2026-01-02T01:00:00Z",
        finished_at="2026-01-02T01:20:00Z",
        status="partial",
        trigger="manual",
        namespace="staging",
        queries=[
            PipelineQueryResult(
                query_id="q1",
                query="causal inference",
                topic="causal",
                limit=50,
                papers_seen=30,
                papers_new=4,
                status="succeeded",
            )
        ],
        totals=PipelineTotals(
            papers_seen=30, papers_new=4, committed_operations=3, honest_nulls=1
        ),
        deferral_reasons={"low_confidence": 1},
        budget=PipelineBudget(
            max_papers=200, max_llm_usd=2.0, est_llm_usd=0.5, stopped_by_budget=True
        ),
        review_queue_size=None,
        failures=[PipelineFailure(step="cites", message="rate limited", log_url=None)],
        git_sha="new", image="img:new",
    )
    store.save(older)
    store.save(newer)
    return {"older": older, "newer": newer}


@pytest.fixture
def api_client(neo4j_container):
    from unittest.mock import patch

    from agentic_kg.config import Neo4jConfig
    from agentic_kg.knowledge_graph.repository import Neo4jRepository
    from agentic_kg.knowledge_graph.schema import SchemaManager
    from agentic_kg_api.dependencies import get_repo
    from agentic_kg_api.main import app

    config = Neo4jConfig(
        uri=neo4j_container.get_connection_url(),
        username="neo4j",
        password="testpassword",
        database="neo4j",
    )
    repo = Neo4jRepository(config=config)
    repo.verify_connectivity()
    SchemaManager(repository=repo).initialize(force=False)

    _cleanup(repo)
    seeded = _seed_runs(repo)

    app.dependency_overrides[get_repo] = lambda: repo
    patcher = patch("agentic_kg_api.main.get_repo", return_value=repo)
    patcher.start()
    client = TestClient(app)
    try:
        yield client, repo, seeded
    finally:
        client.close()
        patcher.stop()
        app.dependency_overrides.clear()
        _cleanup(repo)
        repo.close()


class TestRunsApiIntegration:
    def test_list_returns_seeded_runs_newest_first(self, api_client):
        client, _repo, seeded = api_client
        response = client.get("/api/runs")
        assert response.status_code == 200
        body = response.json()
        ids = [r["run_id"] for r in body["runs"]]
        assert ids == [seeded["newer"].run_id, seeded["older"].run_id]
        assert body["next_cursor"] is None
        newest = body["runs"][0]
        assert newest["budget_stopped"] is True
        assert newest["failures_count"] == 1
        assert newest["review_queue_size"] is None

    def test_detail_round_trips_nested_fields(self, api_client):
        client, _repo, seeded = api_client
        response = client.get(f"/api/runs/{seeded['newer'].run_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "partial"
        assert body["trigger"] == "manual"
        assert body["queries"][0]["query"] == "causal inference"
        assert body["deferral_reasons"] == {"low_confidence": 1}
        assert body["failures"][0]["message"] == "rate limited"
        assert body["totals"]["papers_new"] == 4

    def test_unknown_run_returns_404(self, api_client):
        client, _repo, _seeded = api_client
        response = client.get(f"/api/runs/{_TEST_MARKER}missing")
        assert response.status_code == 404

    def test_cursor_pagination(self, api_client):
        client, _repo, seeded = api_client
        first = client.get("/api/runs?limit=1")
        assert first.status_code == 200
        page = first.json()
        assert [r["run_id"] for r in page["runs"]] == [seeded["newer"].run_id]
        assert page["next_cursor"] is not None

        second = client.get(f"/api/runs?limit=1&cursor={page['next_cursor']}")
        assert second.status_code == 200
        page2 = second.json()
        assert [r["run_id"] for r in page2["runs"]] == [seeded["older"].run_id]
        assert page2["next_cursor"] is None
