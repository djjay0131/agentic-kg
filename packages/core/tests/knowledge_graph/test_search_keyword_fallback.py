"""Keyword fallback when vector search yields nothing (F2).

The staging graph was populated but ``POST /api/search`` returned ``[]`` for
every query: the embedding key was absent, so ``semantic_search`` swallowed the
error. These tests pin the fallback without needing Neo4j or an embedding key.
"""

from __future__ import annotations

from agentic_kg.config import SearchConfig
from agentic_kg.knowledge_graph.embeddings import EmbeddingError
from agentic_kg.knowledge_graph.models import Problem, ProblemStatus
from agentic_kg.knowledge_graph.search import SearchService


class FailingEmbeddings:
    def generate_embedding(self, text):
        raise EmbeddingError("OpenAI API key not configured")


class FakeRepository:
    def __init__(self):
        self.keyword_queries = []

    def semantic_problem_views(self, embedding, top_k=10, min_score=None):
        return []

    def keyword_problem_views(self, query, *, status=None, limit=50):
        self.keyword_queries.append((query, status, limit))
        return [
            {
                "id": "p-graph",
                "kind": "concept",
                "statement": "How can knowledge graph construction scale?",
                "status": "open",
                "assumptions": [],
                "constraints": [],
                "datasets": [],
                "metrics": [],
                "baselines": [],
                "evidence": None,
                "extraction_metadata": None,
                "mentions": [],
                "papers": [],
                "mention_count": 1,
                "paper_count": 1,
                "confidence": 0.9,
                "origin": "extracted",
                "derived_from": [],
                "created_at": None,
                "updated_at": None,
                "score": 1.0,
            },
            {
                "id": "p-other",
                "kind": "concept",
                "statement": "How can language model pretraining scale?",
                "status": "open",
                "assumptions": [],
                "constraints": [],
                "datasets": [],
                "metrics": [],
                "baselines": [],
                "evidence": None,
                "extraction_metadata": None,
                "mentions": [],
                "papers": [],
                "mention_count": 1,
                "paper_count": 1,
                "confidence": 0.8,
                "origin": "extracted",
                "derived_from": [],
                "created_at": None,
                "updated_at": None,
                "score": 0.5,
            },
        ][:limit]

    def problem_view_to_problem(self, view):
        return Problem(
            id=view["id"],
            statement=view["statement"],
            status=ProblemStatus(view.get("status") or "open"),
        )


def _service(repo):
    return SearchService(
        repository=repo,
        embedding_service=FailingEmbeddings(),
        config=SearchConfig(),
    )


def test_keyword_search_returns_matches_when_embeddings_fail():
    repo = FakeRepository()
    results = _service(repo).keyword_search("graph")

    assert [r.problem.id for r in results] == ["p-graph", "p-other"]
    assert results[0].match_type == "keyword"
    assert results[0].score == 1.0


def test_hybrid_search_falls_back_to_keyword():
    repo = FakeRepository()
    results = _service(repo).hybrid_search("graph")

    assert results, "a populated graph must not report an empty search"
    assert results[0].problem.id == "p-graph"
    assert results[0].match_type == "keyword"
    assert repo.keyword_queries, "keyword fallback was never consulted"
    assert repo.keyword_queries[0][0] == "graph"


def test_keyword_search_blank_query_is_empty():
    assert _service(FakeRepository()).keyword_search("   ") == []
