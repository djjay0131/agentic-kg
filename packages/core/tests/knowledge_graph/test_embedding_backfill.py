"""Embedding backfill path (F2): old nodes without embeddings become searchable."""

from __future__ import annotations

from agentic_kg.knowledge_graph import embedding_backfill
from agentic_kg.knowledge_graph.embeddings import EmbeddingError


class FakeEmbeddings:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or set()

    def generate_embedding(self, text):
        self.calls.append(text)
        if text in self.fail_on:
            raise EmbeddingError("provider down")
        return [float(len(text)), 0.0]


def test_backfill_updates_concepts_and_problems(monkeypatch):
    written = []
    monkeypatch.setattr(
        embedding_backfill,
        "_missing_concepts",
        lambda repo, limit: [{"id": "c1", "canonical_statement": "A graph problem"}],
    )
    monkeypatch.setattr(
        embedding_backfill,
        "_missing_problems",
        lambda repo, limit: [{"id": "p1", "statement": "A legacy problem"}],
    )
    monkeypatch.setattr(
        embedding_backfill,
        "_set_concept_embedding",
        lambda repo, node_id, vector: written.append((node_id, vector)),
    )
    monkeypatch.setattr(
        embedding_backfill,
        "_set_problem_embedding",
        lambda repo, node_id, vector: written.append((node_id, vector)),
    )

    service = FakeEmbeddings()
    report = embedding_backfill.backfill_problem_embeddings(
        repository=object(), embedding_service=service
    )

    assert report.concepts_updated == 1
    assert report.problems_updated == 1
    assert report.failures == []
    assert [node_id for node_id, _ in written] == ["c1", "p1"]


def test_backfill_records_provider_failure_and_keeps_going(monkeypatch):
    written = []
    monkeypatch.setattr(
        embedding_backfill,
        "_missing_concepts",
        lambda repo, limit: [
            {"id": "bad", "canonical_statement": "fails"},
            {"id": "good", "canonical_statement": "works"},
        ],
    )
    monkeypatch.setattr(
        embedding_backfill, "_missing_problems", lambda repo, limit: []
    )
    monkeypatch.setattr(
        embedding_backfill,
        "_set_concept_embedding",
        lambda repo, node_id, vector: written.append(node_id),
    )

    report = embedding_backfill.backfill_problem_embeddings(
        repository=object(), embedding_service=FakeEmbeddings(fail_on={"fails"})
    )

    assert report.concepts_updated == 1
    assert written == ["good"]
    assert len(report.failures) == 1
    assert "ProblemConcept bad" in report.failures[0]


def test_backfill_dry_run_does_not_call_the_provider(monkeypatch):
    monkeypatch.setattr(
        embedding_backfill,
        "_missing_concepts",
        lambda repo, limit: [{"id": "c1", "canonical_statement": "A graph problem"}],
    )
    monkeypatch.setattr(
        embedding_backfill, "_missing_problems", lambda repo, limit: []
    )
    service = FakeEmbeddings()

    report = embedding_backfill.backfill_problem_embeddings(
        repository=object(), embedding_service=service, dry_run=True
    )

    assert report.concepts_updated == 1
    assert service.calls == []


def test_backfill_skips_empty_text_without_calling_the_provider(monkeypatch):
    monkeypatch.setattr(
        embedding_backfill,
        "_missing_concepts",
        lambda repo, limit: [{"id": "c1", "canonical_statement": "  "}],
    )
    monkeypatch.setattr(
        embedding_backfill, "_missing_problems", lambda repo, limit: []
    )
    service = FakeEmbeddings()

    report = embedding_backfill.backfill_problem_embeddings(
        repository=object(), embedding_service=service
    )

    assert report.concepts_updated == 0
    assert service.calls == []
    assert "empty canonical_statement" in report.failures[0]
