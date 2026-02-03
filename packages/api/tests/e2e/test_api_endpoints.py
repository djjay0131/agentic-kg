"""
E2E tests for API endpoints against staging.

Tests real HTTP requests to the deployed staging API.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import APITestConfig


@pytest.mark.e2e
class TestHealthEndpoint:
    """E2E tests for health endpoint."""

    def test_health_returns_ok(self, api_client: httpx.Client):
        """Test health endpoint returns OK status."""
        response = api_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "neo4j_connected" in data

    def test_health_neo4j_connected(self, api_client: httpx.Client):
        """Test that Neo4j is connected in staging."""
        response = api_client.get("/health")

        data = response.json()
        assert data["neo4j_connected"] is True


@pytest.mark.e2e
class TestStatsEndpoint:
    """E2E tests for stats endpoint."""

    def test_stats_returns_counts(self, api_client: httpx.Client):
        """Test stats endpoint returns entity counts."""
        response = api_client.get("/api/stats")

        assert response.status_code == 200
        data = response.json()

        # Should have count fields
        assert "problem_count" in data or "problems" in data
        assert "paper_count" in data or "papers" in data


@pytest.mark.e2e
class TestProblemsEndpoint:
    """E2E tests for problems endpoint."""

    def test_list_problems(self, api_client: httpx.Client):
        """Test listing problems."""
        response = api_client.get("/api/problems", params={"limit": 10})

        assert response.status_code == 200
        data = response.json()

        # Should be a list (possibly empty)
        assert isinstance(data, list)

    def test_list_problems_with_pagination(self, api_client: httpx.Client):
        """Test problems pagination."""
        response = api_client.get(
            "/api/problems",
            params={"limit": 5, "offset": 0},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 5

    def test_get_problem_not_found(self, api_client: httpx.Client):
        """Test getting a non-existent problem returns 404."""
        response = api_client.get("/api/problems/nonexistent-id-12345")

        assert response.status_code == 404


@pytest.mark.e2e
class TestPapersEndpoint:
    """E2E tests for papers endpoint."""

    def test_list_papers(self, api_client: httpx.Client):
        """Test listing papers."""
        response = api_client.get("/api/papers", params={"limit": 10})

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_paper_not_found(self, api_client: httpx.Client):
        """Test getting a non-existent paper returns 404."""
        response = api_client.get("/api/papers/nonexistent-id-12345")

        assert response.status_code == 404


@pytest.mark.e2e
class TestSearchEndpoint:
    """E2E tests for search endpoint."""

    def test_search_returns_results(self, api_client: httpx.Client):
        """Test search endpoint returns results structure."""
        response = api_client.get(
            "/api/search",
            params={"q": "machine learning", "limit": 5},
        )

        assert response.status_code == 200
        data = response.json()

        # Should be a list
        assert isinstance(data, list)

    def test_search_empty_query_handled(self, api_client: httpx.Client):
        """Test search with empty query is handled gracefully."""
        response = api_client.get(
            "/api/search",
            params={"q": "", "limit": 5},
        )

        # Should either return empty results or error gracefully
        assert response.status_code in [200, 400, 422]


@pytest.mark.e2e
class TestGraphEndpoint:
    """E2E tests for graph visualization endpoint."""

    def test_graph_returns_structure(self, api_client: httpx.Client):
        """Test graph endpoint returns nodes and edges."""
        response = api_client.get("/api/graph", params={"limit": 10})

        assert response.status_code == 200
        data = response.json()

        # Should have nodes and edges/links
        assert "nodes" in data or "vertices" in data
        assert "edges" in data or "links" in data


@pytest.mark.e2e
class TestWorkflowEndpoints:
    """E2E tests for agent workflow endpoints."""

    def test_list_workflows(self, api_client: httpx.Client):
        """Test listing workflows."""
        response = api_client.get("/api/agents/workflows")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_workflow_not_found(self, api_client: httpx.Client):
        """Test getting a non-existent workflow returns 404."""
        response = api_client.get("/api/agents/workflows/nonexistent-run-id")

        assert response.status_code == 404


@pytest.mark.e2e
class TestAPIResponseSchemas:
    """E2E tests verifying API response schemas."""

    def test_problem_schema(self, api_client: httpx.Client):
        """Test that problem responses have expected fields."""
        response = api_client.get("/api/problems", params={"limit": 1})

        if response.status_code == 200 and response.json():
            problem = response.json()[0]

            # Required fields
            assert "id" in problem
            assert "title" in problem
            assert "description" in problem

    def test_paper_schema(self, api_client: httpx.Client):
        """Test that paper responses have expected fields."""
        response = api_client.get("/api/papers", params={"limit": 1})

        if response.status_code == 200 and response.json():
            paper = response.json()[0]

            # Required fields
            assert "id" in paper
            assert "title" in paper

    def test_error_response_schema(self, api_client: httpx.Client):
        """Test that error responses have expected structure."""
        response = api_client.get("/api/problems/nonexistent-id")

        assert response.status_code == 404
        data = response.json()

        # Should have detail field (FastAPI standard)
        assert "detail" in data
