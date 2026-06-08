"""Tests for the Model router (E-3, Unit 7).

Mocks the repository via the shared ``client`` + ``mock_repo`` fixtures;
no Neo4j required. Covers AC-8 happy paths and error responses.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from agentic_kg.knowledge_graph.models import Model
from agentic_kg.knowledge_graph.repository import NotFoundError


def _make_model(**overrides) -> Model:
    defaults = {
        "id": "model-uuid-1",
        "name": "BERT",
        "description": None,
        "aliases": [],
        "architecture": "transformer",
        "model_type": "language_model",
        "year_introduced": 2018,
        "introducing_paper_doi": None,
        "is_canonical": False,
        "usage_count": 0,
    }
    defaults.update(overrides)
    return Model(**defaults)


def _wire_session(mock_repo, records: list[dict]) -> None:
    session = MagicMock()
    session.execute_read.return_value = records
    mock_repo.session.return_value.__enter__ = MagicMock(return_value=session)
    mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)


class TestListModels:
    def test_returns_models(self, client, mock_repo):
        _wire_session(mock_repo, [{"id": "m1", "name": "BERT", "aliases": "[]"}])
        mock_repo._model_from_neo4j.return_value = _make_model(id="m1", name="BERT")

        response = client.get("/api/models")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["models"][0]["id"] == "m1"
        assert data["models"][0]["name"] == "BERT"

    def test_name_filter_forwarded(self, client, mock_repo):
        tx = MagicMock()
        tx.run.return_value = []
        session = MagicMock()
        session.execute_read.side_effect = lambda fn: fn(tx)
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        response = client.get("/api/models?name=bert")
        assert response.status_code == 200
        params = tx.run.call_args.kwargs
        assert params["name"] == "bert"

    def test_architecture_filter_forwarded(self, client, mock_repo):
        tx = MagicMock()
        tx.run.return_value = []
        session = MagicMock()
        session.execute_read.side_effect = lambda fn: fn(tx)
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        response = client.get("/api/models?architecture=transformer")
        assert response.status_code == 200
        params = tx.run.call_args.kwargs
        assert params["architecture"] == "transformer"

    def test_is_canonical_filter_forwarded(self, client, mock_repo):
        tx = MagicMock()
        tx.run.return_value = []
        session = MagicMock()
        session.execute_read.side_effect = lambda fn: fn(tx)
        mock_repo.session.return_value.__enter__ = MagicMock(return_value=session)
        mock_repo.session.return_value.__exit__ = MagicMock(return_value=False)

        response = client.get("/api/models?is_canonical=true")
        assert response.status_code == 200
        params = tx.run.call_args.kwargs
        assert params["is_canonical"] is True


class TestGetModel:
    def test_detail(self, client, mock_repo):
        mock_repo.get_model.return_value = _make_model(
            id="m1",
            name="BERT",
            description="A transformer-based language model",
            aliases=["bert-base", "bert-large"],
            is_canonical=True,
            usage_count=5,
        )
        response = client.get("/api/models/m1")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "m1"
        assert data["is_canonical"] is True
        assert data["usage_count"] == 5

    def test_not_found(self, client, mock_repo):
        mock_repo.get_model.side_effect = NotFoundError("missing")
        response = client.get("/api/models/missing")
        assert response.status_code == 404


class TestSearchModels:
    def test_returns_results_with_scores(self, client, mock_repo, monkeypatch):
        model = _make_model(name="BERT")
        mock_repo.search_models_by_embedding.return_value = [
            (model, 0.93),
        ]
        # Stub embedding so we don't hit OpenAI.
        from agentic_kg_api.routers import models as router_mod

        monkeypatch.setattr(
            router_mod, "generate_model_embedding", lambda q: [0.1] * 1536
        )

        response = client.get("/api/models/search?q=transformer")
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["model"]["name"] == "BERT"
        assert data["results"][0]["score"] == 0.93

    def test_embedding_service_unavailable_returns_500(
        self, client, mock_repo, monkeypatch
    ):
        from agentic_kg_api.routers import models as router_mod

        def boom(q):
            raise RuntimeError("openai down")

        monkeypatch.setattr(router_mod, "generate_model_embedding", boom)

        response = client.get("/api/models/search?q=anything")
        assert response.status_code == 500


class TestCreateModel:
    def test_creates_new(self, client, mock_repo):
        new_model = _make_model(id="m-new", name="NewModel", is_canonical=False)
        mock_repo.create_or_merge_model.return_value = (new_model, True)

        response = client.post(
            "/api/models",
            json={"name": "NewModel"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is True
        assert data["model"]["name"] == "NewModel"

    def test_merges_existing(self, client, mock_repo):
        existing = _make_model(id="m-existing", name="BERT", is_canonical=True)
        mock_repo.create_or_merge_model.return_value = (existing, False)

        response = client.post(
            "/api/models",
            json={"name": "bert"},  # lowercase variant
        )
        assert response.status_code == 200
        data = response.json()
        assert data["created"] is False
        assert data["model"]["name"] == "BERT"


class TestGetModelPapers:
    def test_returns_papers(self, client, mock_repo):
        mock_repo.get_model.return_value = _make_model(id="m1")
        mock_repo.get_papers_for_model.return_value = [
            {"doi": "10.1/p1", "title": "P1"},
            {"doi": "10.1/p2", "title": "P2"},
        ]
        response = client.get("/api/models/m1/papers")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    def test_model_missing_returns_404(self, client, mock_repo):
        mock_repo.get_model.side_effect = NotFoundError("m1 missing")
        response = client.get("/api/models/m1/papers")
        assert response.status_code == 404


class TestLinkPaper:
    def test_link_created(self, client, mock_repo):
        mock_repo.link_paper_to_model.return_value = True
        response = client.post(
            "/api/models/m1/link-paper",
            json={"entity_id": "10.1/p1"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["relationship"] == "USES_MODEL"
        assert data["created"] is True

    def test_missing_paper_or_model_returns_404(self, client, mock_repo):
        mock_repo.link_paper_to_model.side_effect = NotFoundError("p1 missing")
        response = client.post(
            "/api/models/m1/link-paper",
            json={"entity_id": "10.1/p1"},
        )
        assert response.status_code == 404


class TestDeleteModel:
    def test_delete_succeeds(self, client, mock_repo):
        response = client.delete("/api/models/m1")
        assert response.status_code == 200
        mock_repo.delete_model.assert_called_once_with("m1", force=False)

    def test_delete_canonical_without_force_returns_409(
        self, client, mock_repo
    ):
        mock_repo.delete_model.side_effect = ValueError("canonical")
        response = client.delete("/api/models/m1")
        assert response.status_code == 409

    def test_delete_canonical_with_force_succeeds(self, client, mock_repo):
        response = client.delete("/api/models/m1?force=true")
        assert response.status_code == 200
        mock_repo.delete_model.assert_called_once_with("m1", force=True)

    def test_delete_nonexistent_returns_404(self, client, mock_repo):
        mock_repo.delete_model.side_effect = NotFoundError("missing")
        response = client.delete("/api/models/missing")
        assert response.status_code == 404
