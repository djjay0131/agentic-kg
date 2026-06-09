"""E-3 AC-11 — integration "done demo" against testcontainers Neo4j.

Replaces the human-driven staging demo with an automated regression that
exercises the full path: seed load → fake Papers → CLI/repo linking →
API-shaped query. Uses deterministic embeddings (slot-based one-hot
vectors) so the dedup decisions are reproducible and OpenAI is not
required.

Setup mirrors the spec's AC-11 wording:
- Fresh testcontainers Neo4j with schema initialized (provided by the
  shared ``neo4j_repository`` fixture)
- The bundled seed YAML loaded
- 5 synthetic Paper nodes
- Linking via ``repo.link_paper_to_model``
- Query via ``repo.get_papers_for_model`` (the same call ``GET
  /api/models/{id}/papers`` makes under the hood)
"""

from __future__ import annotations

import uuid

import pytest
from agentic_kg.knowledge_graph.models import Paper
from agentic_kg.knowledge_graph.seed_models import load_seed_models

pytestmark = pytest.mark.integration


def _test_doi() -> str:
    return f"10.1/TEST-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def _patch_embeddings(monkeypatch):
    """Deterministic embeddings — same name → same slot, different names →
    orthogonal vectors. Keeps the seed load reproducible without OpenAI."""
    from agentic_kg.knowledge_graph import embeddings, repository  # noqa: F401

    seen: dict[str, int] = {}

    def _det_emb(name: str, description=None) -> list[float]:
        slot = seen.setdefault(name, len(seen))
        v = [0.0] * 1536
        v[slot % 1536] = 1.0
        return v

    monkeypatch.setattr(embeddings, "generate_model_embedding", _det_emb)


@pytest.fixture
def loaded_repo(neo4j_repository, _patch_embeddings):
    """Repository with the canonical Model seed YAML loaded.

    Teardown drops every Model node afterwards — the bundled seed Models
    don't carry a TEST_ prefix, so the shared cleanup query wouldn't
    catch them, and subsequent tests would see leftover BERT / GPT-4 /
    etc. nodes with deterministic embeddings that collide with their own
    inputs.
    """
    load_seed_models(neo4j_repository)
    yield neo4j_repository

    with neo4j_repository.session() as session:
        session.run("MATCH (m:Model) DETACH DELETE m")


def _make_paper(repo, doi: str, title_suffix: str) -> Paper:
    paper = Paper(
        doi=doi,
        title=f"TEST {title_suffix}",
        authors=[],
        year=2024,
    )
    repo.create_paper(paper)
    return paper


class TestDoneDemo:
    """AC-11: 5 fake Papers, each linked to a different seed Model, queried
    back via the API-shaped repository call.
    """

    def test_link_five_papers_and_query_back(self, loaded_repo):
        # Pick five well-known seed entries that exist after the bundled
        # seed is loaded. (These names match the seed YAML.)
        targets = [
            ("BERT", "transformer LM paper"),
            ("ResNet", "image classification paper"),
            ("CLIP", "multimodal contrastive paper"),
            ("XGBoost", "gradient boosting paper"),
            ("GAT", "graph attention paper"),
        ]

        for model_name, title_suffix in targets:
            doi = _test_doi()
            _make_paper(loaded_repo, doi, title_suffix)
            model = loaded_repo.get_model_by_name(model_name)
            created = loaded_repo.link_paper_to_model(
                paper_doi=doi, model_id=model.id
            )
            assert created is True

            # The same query path /api/models/{id}/papers uses.
            papers = loaded_repo.get_papers_for_model(model.id, limit=20)
            assert any(p["doi"] == doi for p in papers), (
                f"linked Paper {doi} not returned by get_papers_for_model "
                f"for Model {model_name!r}"
            )

    def test_usage_count_increments_per_link(self, loaded_repo):
        """Each link tick increments the model's denormalized usage_count."""
        model = loaded_repo.get_model_by_name("BERT")
        baseline = model.usage_count

        doi = _test_doi()
        _make_paper(loaded_repo, doi, "BERT user")
        loaded_repo.link_paper_to_model(paper_doi=doi, model_id=model.id)

        refreshed = loaded_repo.get_model_by_name("BERT")
        assert refreshed.usage_count == baseline + 1

    def test_canonical_models_present_after_seed_load(self, loaded_repo):
        """AC-5 / AC-11 prereq: load_seed_models populated the well-known set."""
        for name in ("BERT", "GPT-4", "ResNet", "Stable Diffusion", "Mistral"):
            model = loaded_repo.get_model_by_name(name)
            assert model.is_canonical is True
