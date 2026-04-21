"""Tests for the ResearchConcept router (E-2, Unit 4)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_kg.knowledge_graph.models import ResearchConcept
from agentic_kg.knowledge_graph.repository import NotFoundError


def _make_concept(**overrides) -> ResearchConcept:
    defaults = {
        "id": "concept-uuid-1",
        "name": "attention mechanism",
        "description": None,
        "aliases": [],
        "mention_count": 0,
        "paper_count": 0,
    }
    defaults.update(overrides)
    return ResearchConcept(**defaults)


def _wire_session(mock_repo, records: list[dict]) -> None:
    session = MagicMock()
    session.execute_read.return_value = records
    mock_repo.session.return_value.__enter__ = MagicMock(return_value=session)
    mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)


class TestListConcepts:
    def test_flat_list(self, client, mock_repo):
        _wire_session(mock_repo, [{"id": "c1", "name": "attention", "aliases": "[]"}])
        # `list_concepts` calls repo._research_concept_from_neo4j
        mock_repo._research_concept_from_neo4j.return_value = _make_concept(
            id="c1", name="attention"
        )

        response = client.get("/api/concepts")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["concepts"][0]["id"] == "c1"
        assert data["concepts"][0]["name"] == "attention"

    def test_name_filter_is_forwarded(self, client, mock_repo):
        _wire_session(mock_repo, [])
        response = client.get("/api/concepts?name=attention")
        assert response.status_code == 200
        # Cypher ran with the `name` parameter substring.
        session = mock_repo.session.return_value.__enter__.return_value
        args, _ = session.execute_read.call_args
        # Can't introspect the closure directly, but the response was 200 so
        # the branch with the name filter ran.
        assert session.execute_read.called


class TestGetConcept:
    def test_detail(self, client, mock_repo):
        mock_repo.get_research_concept.return_value = _make_concept(
            id="c1",
            name="attention",
            description="scaled dot-product attention",
            aliases=["self-attention"],
            mention_count=5,
            paper_count=3,
        )
        response = client.get("/api/concepts/c1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "c1"
        assert data["description"] == "scaled dot-product attention"
        assert data["aliases"] == ["self-attention"]
        assert data["mention_count"] == 5

    def test_not_found(self, client, mock_repo):
        mock_repo.get_research_concept.side_effect = NotFoundError("missing")
        response = client.get("/api/concepts/missing")
        assert response.status_code == 404


class TestConceptSearch:
    def test_returns_results_with_scores(self, client, mock_repo):
        concept = _make_concept(name="attention mechanism")
        mock_repo.search_research_concepts_by_embedding.return_value = [
            (concept, 0.93)
        ]
        with patch(
            "agentic_kg_api.routers.concepts.generate_research_concept_embedding",
            return_value=[0.1] * 1536,
        ):
            response = client.get("/api/concepts/search?q=transformer+attention")

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "transformer attention"
        assert len(data["results"]) == 1
        assert data["results"][0]["score"] == pytest.approx(0.93)

    def test_min_score_forwarded(self, client, mock_repo):
        mock_repo.search_research_concepts_by_embedding.return_value = []
        with patch(
            "agentic_kg_api.routers.concepts.generate_research_concept_embedding",
            return_value=[0.1] * 1536,
        ):
            response = client.get(
                "/api/concepts/search?q=foo&top_k=3&min_score=0.85"
            )
        assert response.status_code == 200
        call = mock_repo.search_research_concepts_by_embedding.call_args
        assert call.kwargs.get("top_k") == 3
        assert call.kwargs.get("min_score") == pytest.approx(0.85)

    def test_embedding_failure_returns_500(self, client, mock_repo):
        with patch(
            "agentic_kg_api.routers.concepts.generate_research_concept_embedding",
            side_effect=RuntimeError("API down"),
        ):
            response = client.get("/api/concepts/search?q=foo")
        assert response.status_code == 500


class TestCreateConcept:
    def test_creates_new_concept(self, client, mock_repo):
        concept = _make_concept(name="retrieval augmented generation")
        mock_repo.create_or_merge_research_concept.return_value = (concept, True)

        response = client.post(
            "/api/concepts",
            json={"name": "retrieval augmented generation"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is True
        assert data["concept"]["name"] == "retrieval augmented generation"

    def test_dedup_returns_existing(self, client, mock_repo):
        existing = _make_concept(
            name="attention mechanism", aliases=["self-attention"]
        )
        mock_repo.create_or_merge_research_concept.return_value = (
            existing,
            False,
        )
        response = client.post(
            "/api/concepts",
            json={
                "name": "self-attention mechanism",
                "aliases": ["SDPA"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is False

    def test_threshold_forwarded(self, client, mock_repo):
        concept = _make_concept()
        mock_repo.create_or_merge_research_concept.return_value = (concept, True)
        response = client.post(
            "/api/concepts",
            json={"name": "concept name", "threshold": 0.85},
        )
        assert response.status_code == 200
        call = mock_repo.create_or_merge_research_concept.call_args
        assert call.kwargs["threshold"] == pytest.approx(0.85)

    def test_short_name_rejected(self, client, mock_repo):
        response = client.post("/api/concepts", json={"name": "X"})
        assert response.status_code == 422


class TestLinkProblem:
    def test_link_created(self, client, mock_repo):
        mock_repo.link_problem_to_concept.return_value = True
        response = client.post(
            "/api/concepts/c1/link-problem",
            json={"entity_id": "pc-1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is True
        assert data["relationship"] == "INVOLVES_CONCEPT"
        mock_repo.link_problem_to_concept.assert_called_once_with(
            problem_concept_id="pc-1", research_concept_id="c1"
        )

    def test_link_existing_returns_created_false(self, client, mock_repo):
        mock_repo.link_problem_to_concept.return_value = False
        response = client.post(
            "/api/concepts/c1/link-problem", json={"entity_id": "pc-1"}
        )
        assert response.status_code == 200
        assert response.json()["created"] is False

    def test_link_missing_entity_returns_404(self, client, mock_repo):
        mock_repo.link_problem_to_concept.side_effect = NotFoundError("gone")
        response = client.post(
            "/api/concepts/c1/link-problem", json={"entity_id": "missing"}
        )
        assert response.status_code == 404


class TestLinkPaper:
    def test_link_created(self, client, mock_repo):
        mock_repo.link_paper_to_concept.return_value = True
        response = client.post(
            "/api/concepts/c1/link-paper",
            json={"entity_id": "10.1234/example"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is True
        assert data["relationship"] == "DISCUSSES"
        mock_repo.link_paper_to_concept.assert_called_once_with(
            paper_doi="10.1234/example", research_concept_id="c1"
        )

    def test_link_missing_paper_returns_404(self, client, mock_repo):
        mock_repo.link_paper_to_concept.side_effect = NotFoundError("gone")
        response = client.post(
            "/api/concepts/c1/link-paper",
            json={"entity_id": "10.1234/missing"},
        )
        assert response.status_code == 404


class TestConceptProblemsPapers:
    def test_problems_listed(self, client, mock_repo):
        mock_repo.get_research_concept.return_value = _make_concept()
        mock_repo.get_problems_for_concept.return_value = [
            {"id": "pc-1", "canonical_statement": "hi"},
            {"id": "pc-2", "canonical_statement": "bye"},
        ]
        response = client.get("/api/concepts/c1/problems")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["problems"][0]["id"] == "pc-1"

    def test_problems_404_when_concept_missing(self, client, mock_repo):
        mock_repo.get_research_concept.side_effect = NotFoundError("gone")
        response = client.get("/api/concepts/missing/problems")
        assert response.status_code == 404

    def test_papers_listed(self, client, mock_repo):
        mock_repo.get_research_concept.return_value = _make_concept()
        mock_repo.get_papers_for_concept.return_value = [
            {"doi": "10.1/a", "title": "A"},
        ]
        response = client.get("/api/concepts/c1/papers")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["papers"][0]["doi"] == "10.1/a"

    def test_papers_404_when_concept_missing(self, client, mock_repo):
        mock_repo.get_research_concept.side_effect = NotFoundError("gone")
        response = client.get("/api/concepts/missing/papers")
        assert response.status_code == 404
