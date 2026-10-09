"""Tests for the Topic router (E-1, Unit 7)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentic_kg.knowledge_graph.models import Topic, TopicLevel
from agentic_kg.knowledge_graph.repository import NotFoundError


def _make_topic(**kwargs) -> Topic:
    defaults = {
        "id": "topic-uuid-1",
        "name": "Natural Language Processing",
        "level": TopicLevel.AREA,
        "parent_id": None,
        "source": "manual",
        "description": None,
        "problem_count": 0,
        "paper_count": 0,
    }
    defaults.update(kwargs)
    return Topic(**defaults)


class TestListTopics:
    def test_flat_list(self, client, mock_repo):
        domain = _make_topic(level=TopicLevel.DOMAIN, name="Computer Science")
        area = _make_topic(level=TopicLevel.AREA, parent_id=domain.id)
        subtopic = _make_topic(level=TopicLevel.SUBTOPIC, parent_id=area.id, name="Machine Translation")

        def _by_level(level):
            return {
                TopicLevel.DOMAIN: [domain],
                TopicLevel.AREA: [area],
                TopicLevel.SUBTOPIC: [subtopic],
            }[level]

        mock_repo.get_topics_by_level.side_effect = _by_level

        response = client.get("/api/topics")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        names = {t["name"] for t in data["topics"]}
        assert names == {"Computer Science", "Natural Language Processing", "Machine Translation"}

    def test_filter_by_level(self, client, mock_repo):
        mock_repo.get_topics_by_level.return_value = [_make_topic()]
        response = client.get("/api/topics?level=area")
        assert response.status_code == 200
        assert len(response.json()["topics"]) == 1
        mock_repo.get_topics_by_level.assert_called_once()

    def test_invalid_level(self, client, mock_repo):
        response = client.get("/api/topics?level=nonexistent")
        assert response.status_code == 400

    def test_tree_response(self, client, mock_repo):
        mock_repo.get_topic_tree.return_value = [
            {
                "id": "d1",
                "name": "Computer Science",
                "level": "domain",
                "parent_id": None,
                "source": "manual",
                "description": None,
                "problem_count": 0,
                "paper_count": 0,
                "children": [
                    {
                        "id": "a1",
                        "name": "NLP",
                        "level": "area",
                        "parent_id": "d1",
                        "source": "manual",
                        "description": None,
                        "problem_count": 0,
                        "paper_count": 0,
                        "children": [],
                    }
                ],
            }
        ]
        response = client.get("/api/topics?tree=true")
        assert response.status_code == 200
        data = response.json()
        assert len(data["roots"]) == 1
        assert data["roots"][0]["children"][0]["name"] == "NLP"

    def test_tree_with_level_rejected(self, client, mock_repo):
        response = client.get("/api/topics?tree=true&level=area")
        assert response.status_code == 400


class TestGetTopic:
    def test_detail(self, client, mock_repo):
        domain = _make_topic(id="d1", level=TopicLevel.DOMAIN, name="Computer Science")
        area = _make_topic(id="a1", level=TopicLevel.AREA, parent_id="d1", name="NLP")
        subtopic = _make_topic(id="s1", level=TopicLevel.SUBTOPIC, parent_id="a1", name="Machine Translation")

        def _get_topic(tid):
            return {"d1": domain, "a1": area, "s1": subtopic}[tid]

        mock_repo.get_topic.side_effect = _get_topic
        mock_repo.get_topic_children.return_value = [subtopic]

        response = client.get("/api/topics/a1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "a1"
        assert data["parent"]["id"] == "d1"
        assert len(data["children"]) == 1
        assert data["children"][0]["id"] == "s1"

    def test_not_found(self, client, mock_repo):
        mock_repo.get_topic.side_effect = NotFoundError("missing")
        response = client.get("/api/topics/missing")
        assert response.status_code == 404


class TestTopicSearch:
    def test_search_returns_results(self, client, mock_repo):
        topic = _make_topic(name="NLP")
        mock_repo.search_topics_by_embedding.return_value = [(topic, 0.95)]

        with patch(
            "agentic_kg_api.routers.topics.generate_topic_embedding",
            return_value=[0.1] * 1536,
        ):
            response = client.get("/api/topics/search?q=language")

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "language"
        assert len(data["results"]) == 1
        assert data["results"][0]["score"] == pytest.approx(0.95)

    def test_search_respects_level(self, client, mock_repo):
        mock_repo.search_topics_by_embedding.return_value = []
        with patch(
            "agentic_kg_api.routers.topics.generate_topic_embedding",
            return_value=[0.1] * 1536,
        ):
            response = client.get("/api/topics/search?q=test&level=area")
        assert response.status_code == 200
        call = mock_repo.search_topics_by_embedding.call_args
        assert call.kwargs.get("level") == TopicLevel.AREA

    def test_search_invalid_level(self, client, mock_repo):
        with patch(
            "agentic_kg_api.routers.topics.generate_topic_embedding",
            return_value=[0.1] * 1536,
        ):
            response = client.get("/api/topics/search?q=test&level=bogus")
        assert response.status_code == 400


class TestGetTopicProblems:
    def test_returns_problems(self, client, mock_repo):
        from tests.conftest import make_problem_view

        mock_repo.get_topic.return_value = _make_topic()
        mock_repo.list_problem_views_for_topic.return_value = [
            make_problem_view(id="p1", statement="Test statement long enough")
        ]

        response = client.get("/api/topics/topic-uuid-1/problems")
        assert response.status_code == 200
        data = response.json()
        assert data["topic_id"] == "topic-uuid-1"
        assert len(data["problems"]) == 1
        assert data["problems"][0]["id"] == "p1"
        assert data["include_subtopics"] is True
        call_kwargs = mock_repo.list_problem_views_for_topic.call_args[1]
        assert call_kwargs["include_subtopics"] is True

    def test_returns_concept_problems(self, client, mock_repo):
        """A canonical ProblemConcept is returned for its Topic.

        Regression: ingestion no longer writes :Problem, so the old
        ``MATCH (p:Problem)-[:BELONGS_TO]`` query returned nothing.
        """
        from tests.conftest import make_problem_view

        mock_repo.get_topic.return_value = _make_topic()
        mock_repo.list_problem_views_for_topic.return_value = [
            make_problem_view(
                id="concept-1",
                kind="concept",
                statement="Canonical topic problem statement",
                canonical_statement="Canonical topic problem statement",
                mention_count=2,
                paper_count=1,
            )
        ]

        response = client.get("/api/topics/topic-uuid-1/problems")
        assert response.status_code == 200
        problem = response.json()["problems"][0]
        assert problem["id"] == "concept-1"
        assert problem["canonical_statement"] == "Canonical topic problem statement"
        assert problem["mention_count"] == 2

    def test_topic_not_found(self, client, mock_repo):
        mock_repo.get_topic.side_effect = NotFoundError("missing")
        response = client.get("/api/topics/missing/problems")
        assert response.status_code == 404

    def test_returns_problem_origin(self, client, mock_repo):
        """The topic-problems summary carries the provenance origin."""
        from tests.conftest import make_problem_view

        mock_repo.get_topic.return_value = _make_topic()
        mock_repo.list_problem_views_for_topic.return_value = [
            make_problem_view(
                id="synth-1",
                statement="Agent-derived topic problem",
                origin="agent:synthesis",
            )
        ]

        response = client.get("/api/topics/topic-uuid-1/problems")
        assert response.status_code == 200
        assert response.json()["problems"][0]["origin"] == "agent:synthesis"


class TestAssignTopic:
    def test_assign_created(self, client, mock_repo):
        mock_repo.assign_entity_to_topic.return_value = True

        response = client.post(
            "/api/topics/topic-uuid-1/assign",
            json={"entity_id": "prob-1", "entity_label": "Problem"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is True
        mock_repo.assign_entity_to_topic.assert_called_once_with(
            entity_id="prob-1", topic_id="topic-uuid-1", entity_label="Problem"
        )

    def test_assign_existing(self, client, mock_repo):
        mock_repo.assign_entity_to_topic.return_value = False

        response = client.post(
            "/api/topics/topic-uuid-1/assign",
            json={"entity_id": "prob-1", "entity_label": "Problem"},
        )
        assert response.status_code == 200
        assert response.json()["created"] is False

    def test_assign_bad_label(self, client, mock_repo):
        mock_repo.assign_entity_to_topic.side_effect = ValueError("bad label")
        response = client.post(
            "/api/topics/topic-uuid-1/assign",
            json={"entity_id": "prob-1", "entity_label": "Author"},
        )
        assert response.status_code == 400

    def test_assign_missing_entity(self, client, mock_repo):
        mock_repo.assign_entity_to_topic.side_effect = NotFoundError("gone")
        response = client.post(
            "/api/topics/topic-uuid-1/assign",
            json={"entity_id": "prob-1", "entity_label": "Problem"},
        )
        assert response.status_code == 404
