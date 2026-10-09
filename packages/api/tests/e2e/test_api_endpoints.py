"""
E2E tests for API endpoints against staging.

Tests real HTTP requests to the deployed staging API.
"""

from __future__ import annotations

import httpx
import pytest


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

        # StatsResponse: total_* counts plus two breakdown maps.
        assert {
            "total_problems",
            "total_papers",
            "total_topics",
            "problems_by_status",
            "problems_by_topic",
        } <= set(data)
        assert isinstance(data["total_problems"], int)
        assert isinstance(data["total_papers"], int)
        assert isinstance(data["total_topics"], int)
        assert isinstance(data["problems_by_status"], dict)
        assert isinstance(data["problems_by_topic"], dict)


@pytest.mark.e2e
class TestProblemsEndpoint:
    """E2E tests for problems endpoint."""

    def test_list_problems(self, api_client: httpx.Client):
        """Test listing problems."""
        response = api_client.get("/api/problems", params={"limit": 10})

        assert response.status_code == 200
        data = response.json()

        # Paginated envelope (ProblemListResponse), not a bare list.
        assert {"problems", "total", "limit", "offset"} <= set(data)
        assert isinstance(data["problems"], list)
        assert data["limit"] == 10
        assert data["offset"] == 0

    def test_list_problems_with_pagination(self, api_client: httpx.Client):
        """Test problems pagination."""
        response = api_client.get(
            "/api/problems",
            params={"limit": 5, "offset": 0},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["problems"]) <= 5
        assert data["limit"] == 5

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

        # Paginated envelope (PaperListResponse), not a bare list.
        assert {"papers", "total", "limit", "offset"} <= set(data)
        assert isinstance(data["papers"], list)
        assert data["limit"] == 10
        assert data["offset"] == 0

    def test_get_paper_not_found(self, api_client: httpx.Client):
        """Test getting a non-existent paper returns 404."""
        response = api_client.get("/api/papers/nonexistent-id-12345")

        assert response.status_code == 404


@pytest.mark.e2e
class TestSearchEndpoint:
    """E2E tests for search endpoint."""

    def test_search_returns_results(self, api_client: httpx.Client):
        """Test search endpoint returns results structure."""
        query = "machine learning"
        # Search is POST /api/search with a JSON body, not GET with query
        # params (which is a 405 against the real router).
        response = api_client.post(
            "/api/search",
            json={"query": query, "top_k": 5},
        )

        assert response.status_code == 200
        data = response.json()

        # SearchResponse envelope.
        assert {"results", "query", "total"} <= set(data)
        assert data["query"] == query
        assert isinstance(data["results"], list)
        assert data["total"] == len(data["results"])
        for item in data["results"]:
            assert {"problem", "score", "match_type"} <= set(item)

    def test_search_empty_query_handled(self, api_client: httpx.Client):
        """Test search with empty query is handled gracefully."""
        # SearchRequest.query has min_length=1, so an empty query is a
        # request-validation error (422) rather than a 200 with no results.
        response = api_client.post(
            "/api/search",
            json={"query": "", "top_k": 5},
        )

        assert response.status_code == 422


@pytest.mark.e2e
class TestGraphEndpoint:
    """E2E tests for graph visualization endpoint."""

    def test_graph_returns_structure(self, api_client: httpx.Client):
        """Test graph endpoint returns nodes and edges."""
        response = api_client.get("/api/graph", params={"limit": 10})

        assert response.status_code == 200
        data = response.json()

        # GraphResponse: nodes + links.
        assert {"nodes", "links"} <= set(data)
        assert isinstance(data["nodes"], list)
        assert isinstance(data["links"], list)


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

        assert response.status_code == 200
        data = response.json()
        assert "problems" in data

        problems = data["problems"]
        if not problems:
            pytest.skip("staging has no problems to validate against")

        # ProblemSummary fields.
        problem = problems[0]
        assert {"id", "statement", "status"} <= set(problem)
        assert isinstance(problem["id"], str) and problem["id"]
        assert isinstance(problem["statement"], str)
        assert isinstance(problem["status"], str)

    def test_paper_schema(self, api_client: httpx.Client):
        """Test that paper responses have expected fields."""
        response = api_client.get("/api/papers", params={"limit": 1})

        assert response.status_code == 200
        data = response.json()
        assert "papers" in data

        papers = data["papers"]
        if not papers:
            pytest.skip("staging has no papers to validate against")

        # PaperSummary fields (papers are keyed by ``doi``, not ``id``).
        paper = papers[0]
        assert {"doi", "title"} <= set(paper)
        assert isinstance(paper["doi"], str) and paper["doi"]
        assert isinstance(paper["title"], str)

    def test_error_response_schema(self, api_client: httpx.Client):
        """Test that error responses have expected structure."""
        response = api_client.get("/api/problems/nonexistent-id")

        assert response.status_code == 404
        data = response.json()

        # Should have detail field (FastAPI standard)
        assert "detail" in data
