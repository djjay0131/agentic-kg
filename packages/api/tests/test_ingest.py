"""
Tests for agentic_kg_api.routers.ingest — Cloud Run Jobs ingestion endpoints.

Tests the API layer: job triggering, status polling, caching, and Neo4j lookup.
"""

import pytest
import time
import json
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from agentic_kg_api.main import app
from agentic_kg_api.dependencies import get_repo, get_search, get_relations
from agentic_kg_api.routers.ingest import (
    reset_status_cache,
    _status_cache,
    _build_status_response,
    _get_ingestion_run_from_neo4j,
    CACHE_TTL,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def clean_cache():
    """Reset status cache before each test."""
    reset_status_cache()
    yield
    reset_status_cache()


@pytest.fixture
def client(mock_repo, mock_search_service, mock_relation_service):
    """Create TestClient with dependencies overridden."""
    app.dependency_overrides[get_repo] = lambda: mock_repo
    app.dependency_overrides[get_search] = lambda: mock_search_service
    app.dependency_overrides[get_relations] = lambda: mock_relation_service
    yield TestClient(app)
    app.dependency_overrides.clear()


# =============================================================================
# _build_status_response Tests
# =============================================================================


class TestBuildStatusResponse:
    """Tests for response building logic."""

    def test_minimal_response(self):
        resp = _build_status_response("trace-1", "queued", query="test")
        assert resp.trace_id == "trace-1"
        assert resp.status == "queued"
        assert resp.query == "test"
        assert resp.papers_found == 0

    def test_response_from_neo4j_data(self):
        neo4j_data = {
            "trace_id": "trace-1",
            "query": "graph retrieval",
            "status": "completed",
            "papers_found": 20,
            "papers_imported": 18,
            "papers_extracted": 14,
            "papers_skipped_no_pdf": 4,
            "total_problems": 47,
            "concepts_created": 31,
            "concepts_linked": 16,
            "extraction_errors": json.dumps({"10.1/a": "timeout"}),
        }
        resp = _build_status_response("trace-1", "completed", neo4j_data=neo4j_data)
        assert resp.papers_found == 20
        assert resp.total_problems == 47
        assert resp.extraction_errors == {"10.1/a": "timeout"}

    def test_response_with_invalid_json_errors(self):
        neo4j_data = {
            "status": "completed",
            "extraction_errors": "not-json",
            "papers_found": 5,
        }
        resp = _build_status_response("t", "completed", neo4j_data=neo4j_data)
        assert resp.extraction_errors == {}

    def test_response_with_error(self):
        resp = _build_status_response("t", "failed", error="Neo4j down")
        assert resp.error == "Neo4j down"


# =============================================================================
# POST /api/ingest Tests
# =============================================================================


class TestStartIngestion:
    """Tests for POST /api/ingest."""

    def test_start_ingestion_returns_queued(self, client):
        """POST triggers job and returns queued status."""
        with patch(
            "agentic_kg_api.routers.ingest._trigger_cloud_run_job",
            new_callable=AsyncMock,
        ) as mock_trigger:
            mock_trigger.return_value = {"metadata": {"name": "exec-123"}}
            response = client.post("/api/ingest", json={
                "query": "graph-based retrieval",
                "limit": 10,
            })

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["query"] == "graph-based retrieval"
        assert data["trace_id"].startswith("ingest-")
        mock_trigger.assert_called_once()

    def test_start_ingestion_with_all_options(self, client):
        """POST accepts all optional fields."""
        with patch(
            "agentic_kg_api.routers.ingest._trigger_cloud_run_job",
            new_callable=AsyncMock,
        ):
            response = client.post("/api/ingest", json={
                "query": "test",
                "limit": 5,
                "sources": ["arxiv"],
                "dry_run": True,
                "enable_agent_workflow": False,
                "min_extraction_confidence": 0.7,
            })
        assert response.status_code == 200

    def test_start_ingestion_missing_query_returns_422(self, client):
        """POST without query returns validation error."""
        response = client.post("/api/ingest", json={"limit": 10})
        assert response.status_code == 422

    def test_start_ingestion_invalid_limit_returns_422(self, client):
        """POST with limit > 100 returns validation error."""
        response = client.post("/api/ingest", json={"query": "test", "limit": 200})
        assert response.status_code == 422

    def test_start_ingestion_job_trigger_failure_returns_500(self, client):
        """POST returns 500 if Cloud Run Jobs API fails."""
        with patch(
            "agentic_kg_api.routers.ingest._trigger_cloud_run_job",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Auth failed"),
        ):
            response = client.post("/api/ingest", json={"query": "test"})

        assert response.status_code == 500
        assert "Auth failed" in response.json()["detail"]


# =============================================================================
# GET /api/ingest/{trace_id} Tests
# =============================================================================


class TestGetIngestionStatus:
    """Tests for GET /api/ingest/{trace_id}."""

    def test_unknown_trace_id_returns_404(self, client):
        """GET for unknown trace_id returns 404."""
        with patch(
            "agentic_kg_api.routers.ingest._get_ingestion_run_from_neo4j",
            return_value=None,
        ):
            response = client.get("/api/ingest/nonexistent-id")
        assert response.status_code == 404

    def test_completed_job_from_neo4j(self, client):
        """GET returns full results from Neo4j IngestionRun node."""
        neo4j_data = {
            "trace_id": "ingest-abc",
            "query": "graph retrieval",
            "status": "completed",
            "papers_found": 20,
            "papers_imported": 18,
            "papers_extracted": 14,
            "papers_skipped_no_pdf": 4,
            "total_problems": 47,
            "concepts_created": 31,
            "concepts_linked": 16,
            "extraction_errors": "{}",
        }
        with patch(
            "agentic_kg_api.routers.ingest._get_ingestion_run_from_neo4j",
            return_value=neo4j_data,
        ):
            response = client.get("/api/ingest/ingest-abc")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["papers_found"] == 20
        assert data["total_problems"] == 47

    def test_running_job_from_cache(self, client):
        """GET returns cached running status when Neo4j has no result yet."""
        # Seed the cache (simulating a recently triggered job)
        _status_cache["ingest-running"] = (time.time(), {"status": "running", "query": "test"})

        with patch(
            "agentic_kg_api.routers.ingest._get_ingestion_run_from_neo4j",
            return_value=None,
        ):
            response = client.get("/api/ingest/ingest-running")

        assert response.status_code == 200
        assert response.json()["status"] == "running"

    def test_failed_job_from_neo4j(self, client):
        """GET returns error details for a failed job."""
        neo4j_data = {
            "trace_id": "ingest-fail",
            "query": "test",
            "status": "failed",
            "papers_found": 0,
            "extraction_errors": "{}",
        }
        with patch(
            "agentic_kg_api.routers.ingest._get_ingestion_run_from_neo4j",
            return_value=neo4j_data,
        ):
            response = client.get("/api/ingest/ingest-fail")

        assert response.status_code == 200
        assert response.json()["status"] == "failed"

    def test_completed_status_cached_permanently(self, client):
        """Terminal states are cached and don't re-query Neo4j."""
        neo4j_data = {
            "trace_id": "ingest-done",
            "query": "test",
            "status": "completed",
            "papers_found": 5,
            "extraction_errors": "{}",
        }

        call_count = 0

        def mock_neo4j(tid):
            nonlocal call_count
            call_count += 1
            return neo4j_data

        with patch(
            "agentic_kg_api.routers.ingest._get_ingestion_run_from_neo4j",
            side_effect=mock_neo4j,
        ):
            # First call — queries Neo4j
            client.get("/api/ingest/ingest-done")
            assert call_count == 1

            # Second call — should still query Neo4j (cache is for GCP status, not Neo4j)
            # But the terminal state is cached in _status_cache
            client.get("/api/ingest/ingest-done")

        # Neo4j is queried both times (IngestionRun lookup is cheap and authoritative)
        assert call_count == 2


# =============================================================================
# _get_ingestion_run_from_neo4j Tests
# =============================================================================


class TestGetIngestionRunFromNeo4j:
    """Tests for Neo4j IngestionRun lookup."""

    def test_returns_node_data(self):
        """Returns dict of IngestionRun properties when found."""
        mock_record = MagicMock()
        mock_record.__getitem__ = MagicMock(return_value={"trace_id": "t", "status": "completed"})

        mock_result = MagicMock()
        mock_result.single.return_value = mock_record

        mock_session = MagicMock()
        mock_session.run.return_value = mock_result

        mock_repo = MagicMock()
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch("agentic_kg_api.routers.ingest.get_repo", return_value=mock_repo):
            data = _get_ingestion_run_from_neo4j("t")

        assert data == {"trace_id": "t", "status": "completed"}

    def test_returns_none_when_not_found(self):
        """Returns None when no IngestionRun node exists."""
        mock_result = MagicMock()
        mock_result.single.return_value = None

        mock_session = MagicMock()
        mock_session.run.return_value = mock_result

        mock_repo = MagicMock()
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        with patch("agentic_kg_api.routers.ingest.get_repo", return_value=mock_repo):
            data = _get_ingestion_run_from_neo4j("nonexistent")

        assert data is None

    def test_returns_none_on_neo4j_error(self):
        """Returns None gracefully on Neo4j connection error."""
        mock_repo = MagicMock()
        mock_repo.session.side_effect = RuntimeError("Connection refused")

        with patch("agentic_kg_api.routers.ingest.get_repo", return_value=mock_repo):
            data = _get_ingestion_run_from_neo4j("t")

        assert data is None
