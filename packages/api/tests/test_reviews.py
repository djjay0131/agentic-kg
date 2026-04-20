"""
Tests for agentic_kg_api.routers.reviews -- Review Queue API endpoints.

Tests for human-in-the-loop review queue functionality.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock

from agentic_kg.knowledge_graph.review_queue import ReviewNotFoundError
from agentic_kg.knowledge_graph.models import ReviewResolution

from agentic_kg_api.dependencies import get_review_queue
from agentic_kg_api.main import app


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_review_queue_service():
    """Create mock ReviewQueueService with async methods."""
    svc = MagicMock()
    svc.get_pending = AsyncMock(return_value=[])
    svc.count_pending = AsyncMock(return_value=0)
    svc.get_by_id = AsyncMock()
    svc.assign = AsyncMock()
    svc.unassign = AsyncMock()
    svc.resolve = AsyncMock()
    return svc


@pytest.fixture
def client_with_reviews(mock_repo, mock_search_service, mock_relation_service, mock_review_queue_service):
    """Create TestClient with review queue service mocked."""
    from fastapi.testclient import TestClient
    from agentic_kg_api.dependencies import get_repo, get_search, get_relations

    app.dependency_overrides[get_repo] = lambda: mock_repo
    app.dependency_overrides[get_search] = lambda: mock_search_service
    app.dependency_overrides[get_relations] = lambda: mock_relation_service
    app.dependency_overrides[get_review_queue] = lambda: mock_review_queue_service

    yield TestClient(app)
    app.dependency_overrides.clear()


def make_agent_context():
    """Create mock AgentContextForReview."""
    ctx = MagicMock()
    ctx.escalation_reason = MagicMock(value="evaluator_uncertain")
    ctx.evaluator_decision = "escalate"
    ctx.evaluator_confidence = 0.6
    ctx.maker_arguments = ["semantic similarity is high", "domains match"]
    ctx.hater_arguments = ["scope differs slightly"]
    ctx.arbiter_decision = None
    ctx.rounds_attempted = 1
    ctx.final_confidence = 0.55
    return ctx


def make_suggested_concept(**kwargs):
    """Create mock SuggestedConceptForReview."""
    c = MagicMock()
    c.concept_id = kwargs.get("concept_id", "concept-001")
    c.canonical_statement = kwargs.get("canonical_statement", "Canonical problem statement")
    c.similarity_score = kwargs.get("similarity_score", 0.85)
    c.final_score = kwargs.get("final_score", 0.87)
    c.agent_reasoning = kwargs.get("agent_reasoning", "High semantic overlap")
    c.mention_count = kwargs.get("mention_count", 3)
    return c


def make_pending_review(**kwargs):
    """Create a mock PendingReview."""
    r = MagicMock()
    r.id = kwargs.get("id", "review-001")
    r.trace_id = kwargs.get("trace_id", "trace-001")
    r.mention_id = kwargs.get("mention_id", "mention-001")
    r.mention_statement = kwargs.get("mention_statement", "How to improve transformer efficiency?")
    r.paper_doi = kwargs.get("paper_doi", "10.1234/test.2024")
    r.paper_title = kwargs.get("paper_title", "Test Paper Title")
    r.priority = kwargs.get("priority", MagicMock(value=5))
    r.status = kwargs.get("status", MagicMock(value="pending"))
    r.assigned_to = kwargs.get("assigned_to", None)
    r.assigned_at = kwargs.get("assigned_at", None)
    r.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
    r.sla_deadline = kwargs.get("sla_deadline", datetime.now(timezone.utc))
    r.suggested_concepts = kwargs.get("suggested_concepts", [make_suggested_concept()])
    r.agent_context = kwargs.get("agent_context", make_agent_context())
    r.resolution = kwargs.get("resolution", None)
    r.resolved_concept_id = kwargs.get("resolved_concept_id", None)
    r.resolved_by = kwargs.get("resolved_by", None)
    r.resolved_at = kwargs.get("resolved_at", None)
    r.resolution_notes = kwargs.get("resolution_notes", None)
    return r


# =============================================================================
# GET /api/reviews/pending -- List Pending Reviews
# =============================================================================


class TestListPendingReviews:
    """Tests for GET /api/reviews/pending."""

    def test_returns_empty_list(self, client_with_reviews, mock_review_queue_service):
        """Returns empty list when no pending reviews."""
        mock_review_queue_service.get_pending.return_value = []
        mock_review_queue_service.count_pending.return_value = 0

        response = client_with_reviews.get("/api/reviews/pending")

        assert response.status_code == 200
        data = response.json()
        assert data["reviews"] == []
        assert data["total"] == 0
        assert data["limit"] == 20

    def test_returns_pending_reviews(self, client_with_reviews, mock_review_queue_service):
        """Returns list of pending reviews."""
        reviews = [
            make_pending_review(id="r1", mention_statement="Problem 1"),
            make_pending_review(id="r2", mention_statement="Problem 2"),
        ]
        mock_review_queue_service.get_pending.return_value = reviews
        mock_review_queue_service.count_pending.return_value = 2

        response = client_with_reviews.get("/api/reviews/pending")

        assert response.status_code == 200
        data = response.json()
        assert len(data["reviews"]) == 2
        assert data["reviews"][0]["id"] == "r1"
        assert data["reviews"][1]["id"] == "r2"
        assert data["total"] == 2

    def test_filters_by_priority(self, client_with_reviews, mock_review_queue_service):
        """Passes priority filter to service."""
        mock_review_queue_service.get_pending.return_value = []
        mock_review_queue_service.count_pending.return_value = 0

        response = client_with_reviews.get("/api/reviews/pending?priority=3")

        assert response.status_code == 200
        mock_review_queue_service.get_pending.assert_called_with(
            limit=20, priority_filter=3
        )

    def test_respects_limit(self, client_with_reviews, mock_review_queue_service):
        """Respects limit parameter."""
        mock_review_queue_service.get_pending.return_value = []
        mock_review_queue_service.count_pending.return_value = 0

        response = client_with_reviews.get("/api/reviews/pending?limit=5")

        assert response.status_code == 200
        assert mock_review_queue_service.get_pending.call_args[1]["limit"] == 5

    @pytest.mark.parametrize("limit", [0, -1, 101])
    def test_invalid_limit_returns_422(self, client_with_reviews, limit):
        """Rejects invalid limit values."""
        response = client_with_reviews.get(f"/api/reviews/pending?limit={limit}")
        assert response.status_code == 422

    @pytest.mark.parametrize("priority", [0, -1, 11])
    def test_invalid_priority_returns_422(self, client_with_reviews, priority):
        """Rejects invalid priority values."""
        response = client_with_reviews.get(f"/api/reviews/pending?priority={priority}")
        assert response.status_code == 422


# =============================================================================
# GET /api/reviews/{review_id} -- Get Review Detail
# =============================================================================


class TestGetReview:
    """Tests for GET /api/reviews/{review_id}."""

    def test_returns_review_detail(self, client_with_reviews, mock_review_queue_service):
        """Returns full review detail with agent context."""
        review = make_pending_review(id="r1")
        mock_review_queue_service.get_by_id.return_value = review

        response = client_with_reviews.get("/api/reviews/r1")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "r1"
        assert "agent_context" in data
        assert "suggested_concepts" in data
        assert data["agent_context"]["escalation_reason"] == "evaluator_uncertain"

    def test_includes_suggested_concepts(self, client_with_reviews, mock_review_queue_service):
        """Returns suggested concepts in detail."""
        concept = make_suggested_concept(concept_id="c1", similarity_score=0.92)
        review = make_pending_review(id="r1", suggested_concepts=[concept])
        mock_review_queue_service.get_by_id.return_value = review

        response = client_with_reviews.get("/api/reviews/r1")

        assert response.status_code == 200
        data = response.json()
        assert len(data["suggested_concepts"]) == 1
        assert data["suggested_concepts"][0]["concept_id"] == "c1"
        assert data["suggested_concepts"][0]["similarity_score"] == 0.92

    def test_not_found_returns_404(self, client_with_reviews, mock_review_queue_service):
        """Returns 404 for nonexistent review."""
        mock_review_queue_service.get_by_id.side_effect = ReviewNotFoundError("not found")

        response = client_with_reviews.get("/api/reviews/nonexistent")

        assert response.status_code == 404
        assert "Review not found" in response.json()["detail"]


# =============================================================================
# POST /api/reviews/{review_id}/assign -- Assign Review
# =============================================================================


class TestAssignReview:
    """Tests for POST /api/reviews/{review_id}/assign."""

    def test_assigns_review(self, client_with_reviews, mock_review_queue_service):
        """Assigns review to user from header."""
        review = make_pending_review(id="r1", assigned_to="user123")
        mock_review_queue_service.assign.return_value = review

        response = client_with_reviews.post(
            "/api/reviews/r1/assign",
            headers={"X-User-ID": "user123"},
        )

        assert response.status_code == 200
        mock_review_queue_service.assign.assert_called_with("r1", "user123")

    def test_requires_user_header(self, client_with_reviews):
        """Returns 401 without X-User-ID header."""
        response = client_with_reviews.post("/api/reviews/r1/assign")

        assert response.status_code == 401
        assert "X-User-ID header required" in response.json()["detail"]

    def test_not_found_returns_404(self, client_with_reviews, mock_review_queue_service):
        """Returns 404 for nonexistent review."""
        mock_review_queue_service.assign.side_effect = ReviewNotFoundError("not found")

        response = client_with_reviews.post(
            "/api/reviews/nonexistent/assign",
            headers={"X-User-ID": "user123"},
        )

        assert response.status_code == 404


# =============================================================================
# POST /api/reviews/{review_id}/unassign -- Unassign Review
# =============================================================================


class TestUnassignReview:
    """Tests for POST /api/reviews/{review_id}/unassign."""

    def test_unassigns_review(self, client_with_reviews, mock_review_queue_service):
        """Unassigns review successfully."""
        review = make_pending_review(id="r1", assigned_to=None)
        mock_review_queue_service.unassign.return_value = review

        response = client_with_reviews.post(
            "/api/reviews/r1/unassign",
            headers={"X-User-ID": "user123"},
        )

        assert response.status_code == 200
        mock_review_queue_service.unassign.assert_called_with("r1")

    def test_requires_user_header(self, client_with_reviews):
        """Returns 401 without X-User-ID header."""
        response = client_with_reviews.post("/api/reviews/r1/unassign")

        assert response.status_code == 401

    def test_not_found_returns_404(self, client_with_reviews, mock_review_queue_service):
        """Returns 404 for nonexistent review."""
        mock_review_queue_service.unassign.side_effect = ReviewNotFoundError("not found")

        response = client_with_reviews.post(
            "/api/reviews/nonexistent/unassign",
            headers={"X-User-ID": "user123"},
        )

        assert response.status_code == 404


# =============================================================================
# POST /api/reviews/{review_id}/resolve -- Resolve Review
# =============================================================================


class TestResolveReview:
    """Tests for POST /api/reviews/{review_id}/resolve."""

    def test_resolves_with_linked(self, client_with_reviews, mock_review_queue_service):
        """Resolves review with linked decision."""
        review = make_pending_review(id="r1")
        review.resolution = MagicMock(value="linked")
        mock_review_queue_service.resolve.return_value = review

        response = client_with_reviews.post(
            "/api/reviews/r1/resolve",
            headers={"X-User-ID": "user123"},
            json={"resolution": "linked", "concept_id": "concept-1"},
        )

        assert response.status_code == 200
        mock_review_queue_service.resolve.assert_called_with(
            review_id="r1",
            resolution=ReviewResolution.LINKED,
            concept_id="concept-1",
            user_id="user123",
            notes=None,
        )

    def test_linked_requires_concept_id(self, client_with_reviews):
        """Returns 400 when linked resolution missing concept_id."""
        response = client_with_reviews.post(
            "/api/reviews/r1/resolve",
            headers={"X-User-ID": "user123"},
            json={"resolution": "linked"},  # Missing concept_id
        )

        assert response.status_code == 400
        assert "concept_id is required" in response.json()["detail"]

    def test_resolves_with_created_new(self, client_with_reviews, mock_review_queue_service):
        """Resolves review with created_new decision."""
        review = make_pending_review(id="r1")
        review.resolution = MagicMock(value="created_new")
        mock_review_queue_service.resolve.return_value = review

        response = client_with_reviews.post(
            "/api/reviews/r1/resolve",
            headers={"X-User-ID": "user123"},
            json={"resolution": "created_new"},
        )

        assert response.status_code == 200
        mock_review_queue_service.resolve.assert_called_with(
            review_id="r1",
            resolution=ReviewResolution.CREATED_NEW,
            concept_id=None,
            user_id="user123",
            notes=None,
        )

    def test_resolves_with_blacklisted(self, client_with_reviews, mock_review_queue_service):
        """Resolves review with blacklisted decision."""
        review = make_pending_review(id="r1")
        review.resolution = MagicMock(value="blacklisted")
        mock_review_queue_service.resolve.return_value = review

        response = client_with_reviews.post(
            "/api/reviews/r1/resolve",
            headers={"X-User-ID": "user123"},
            json={"resolution": "blacklisted", "notes": "Spam content"},
        )

        assert response.status_code == 200
        mock_review_queue_service.resolve.assert_called_with(
            review_id="r1",
            resolution=ReviewResolution.BLACKLISTED,
            concept_id=None,
            user_id="user123",
            notes="Spam content",
        )

    def test_invalid_resolution_returns_400(self, client_with_reviews):
        """Returns 400 for invalid resolution value."""
        response = client_with_reviews.post(
            "/api/reviews/r1/resolve",
            headers={"X-User-ID": "user123"},
            json={"resolution": "invalid_value"},
        )

        assert response.status_code == 400
        assert "Invalid resolution" in response.json()["detail"]

    def test_requires_user_header(self, client_with_reviews):
        """Returns 401 without X-User-ID header."""
        response = client_with_reviews.post(
            "/api/reviews/r1/resolve",
            json={"resolution": "created_new"},
        )

        assert response.status_code == 401

    def test_not_found_returns_404(self, client_with_reviews, mock_review_queue_service):
        """Returns 404 for nonexistent review."""
        mock_review_queue_service.resolve.side_effect = ReviewNotFoundError("not found")

        response = client_with_reviews.post(
            "/api/reviews/nonexistent/resolve",
            headers={"X-User-ID": "user123"},
            json={"resolution": "created_new"},
        )

        assert response.status_code == 404
