"""Tests for the Method router (E-4, Unit 7).

Mocks the repository via the shared ``client`` + ``mock_repo`` fixtures;
no Neo4j required. Covers AC-7 happy paths and error responses.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agentic_kg.knowledge_graph.models import Method
from agentic_kg.knowledge_graph.repository import NotFoundError


def _make_method(**overrides) -> Method:
    defaults = {
        "id": "method-uuid-1",
        "name": "fine-tuning",
        "description": None,
        "aliases": [],
        "method_type": "training",
        "usage_count": 0,
    }
    defaults.update(overrides)
    return Method(**defaults)


def _wire_session(mock_repo, records: list[dict]) -> None:
    session = MagicMock()
    session.execute_read.return_value = records
    mock_repo.session.return_value.__enter__ = MagicMock(return_value=session)
    mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)


class TestListMethods:
    def test_returns_methods(self, client, mock_repo):
        _wire_session(
            mock_repo,
            [{"id": "m1", "name": "fine-tuning", "aliases": "[]"}],
        )
        mock_repo._method_from_neo4j.return_value = _make_method(
            id="m1", name="fine-tuning"
        )

        response = client.get("/api/methods")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["methods"][0]["id"] == "m1"
        assert data["methods"][0]["name"] == "fine-tuning"

    def test_name_filter_forwarded(self, client, mock_repo):
        tx = MagicMock()
        tx.run.return_value = []
        session = MagicMock()
        session.execute_read.side_effect = lambda fn: fn(tx)
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        response = client.get("/api/methods?name=fine")
        assert response.status_code == 200
        params = tx.run.call_args.kwargs
        assert params["name"] == "fine"

    def test_method_type_filter_forwarded(self, client, mock_repo):
        tx = MagicMock()
        tx.run.return_value = []
        session = MagicMock()
        session.execute_read.side_effect = lambda fn: fn(tx)
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        response = client.get("/api/methods?method_type=training")
        assert response.status_code == 200
        params = tx.run.call_args.kwargs
        assert params["method_type"] == "training"


class TestGetMethod:
    def test_detail(self, client, mock_repo):
        mock_repo.get_method.return_value = _make_method(
            id="m1",
            name="fine-tuning",
            description="parameter-efficient adaptation",
            aliases=["FT", "PEFT"],
            usage_count=5,
        )
        response = client.get("/api/methods/m1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "m1"
        assert data["usage_count"] == 5
        assert "FT" in data["aliases"]

    def test_not_found(self, client, mock_repo):
        mock_repo.get_method.side_effect = NotFoundError("missing")
        response = client.get("/api/methods/missing")
        assert response.status_code == 404


class TestSearchMethods:
    def test_returns_results_with_scores(self, client, mock_repo, monkeypatch):
        method = _make_method(name="fine-tuning")
        mock_repo.search_methods_by_embedding.return_value = [(method, 0.94)]

        from agentic_kg_api.routers import methods as router_mod

        monkeypatch.setattr(
            router_mod, "generate_method_embedding", lambda q: [0.1] * 1536
        )

        response = client.get("/api/methods/search?q=adaptation")
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["method"]["name"] == "fine-tuning"
        assert data["results"][0]["score"] == 0.94

    def test_embedding_service_unavailable_returns_500(
        self, client, mock_repo, monkeypatch
    ):
        from agentic_kg_api.routers import methods as router_mod

        def boom(q):
            raise RuntimeError("openai down")

        monkeypatch.setattr(router_mod, "generate_method_embedding", boom)

        response = client.get("/api/methods/search?q=anything")
        assert response.status_code == 500


class TestCreateMethod:
    def test_creates_new(self, client, mock_repo):
        new_method = _make_method(id="m-new", name="new technique")
        mock_repo.create_or_merge_method.return_value = (new_method, True)

        response = client.post("/api/methods", json={"name": "new technique"})
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is True
        assert data["method"]["name"] == "new technique"

    def test_merges_existing(self, client, mock_repo):
        existing = _make_method(id="m-exist", name="fine-tuning")
        mock_repo.create_or_merge_method.return_value = (existing, False)

        response = client.post(
            "/api/methods", json={"name": "fine tuning"},  # near-duplicate
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is False
        assert data["method"]["name"] == "fine-tuning"

    def test_threshold_override_forwarded(self, client, mock_repo):
        """QA Q2: threshold=1.01 is the operator escape valve. Confirm
        the API passes it through to create_or_merge_method."""
        new_method = _make_method(id="m-distinct", name="distinct method")
        mock_repo.create_or_merge_method.return_value = (new_method, True)

        response = client.post(
            "/api/methods",
            json={"name": "distinct method", "threshold": 1.01},
        )
        assert response.status_code == 200
        kwargs = mock_repo.create_or_merge_method.call_args.kwargs
        assert kwargs["threshold"] == 1.01


class TestGetMethodPapers:
    def test_returns_papers(self, client, mock_repo):
        mock_repo.get_method.return_value = _make_method(id="m1")
        mock_repo.get_papers_for_method.return_value = [
            {"doi": "10.1/p1", "title": "P1"},
            {"doi": "10.1/p2", "title": "P2"},
        ]
        response = client.get("/api/methods/m1/papers")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    def test_method_missing_returns_404(self, client, mock_repo):
        mock_repo.get_method.side_effect = NotFoundError("m1 missing")
        response = client.get("/api/methods/m1/papers")
        assert response.status_code == 404


class TestLinkPaper:
    def test_link_created(self, client, mock_repo):
        mock_repo.link_paper_to_method.return_value = True
        response = client.post(
            "/api/methods/m1/link-paper", json={"entity_id": "10.1/p1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["relationship"] == "APPLIES_METHOD"
        assert data["created"] is True

    def test_missing_paper_or_method_returns_404(self, client, mock_repo):
        mock_repo.link_paper_to_method.side_effect = NotFoundError("p1 missing")
        response = client.post(
            "/api/methods/m1/link-paper", json={"entity_id": "10.1/p1"},
        )
        assert response.status_code == 404


class TestDeleteMethod:
    def test_delete_succeeds(self, client, mock_repo):
        response = client.delete("/api/methods/m1")
        assert response.status_code == 200
        mock_repo.delete_method.assert_called_once_with("m1")

    def test_delete_nonexistent_returns_404(self, client, mock_repo):
        mock_repo.delete_method.side_effect = NotFoundError("missing")
        response = client.delete("/api/methods/missing")
        assert response.status_code == 404
