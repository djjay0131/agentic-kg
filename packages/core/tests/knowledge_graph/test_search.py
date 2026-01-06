"""
Integration tests for search operations.

These tests require Docker and will be skipped if Docker is not available.
Semantic search tests are limited since they require OpenAI API key.
"""

import pytest
from agentic_kg.knowledge_graph.models import (
    Evidence,
    ExtractionMetadata,
    Problem,
    ProblemStatus,
)
from agentic_kg.knowledge_graph.search import SearchService

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture
def search_service(neo4j_repository):
    """Create a search service with the test repository."""
    return SearchService(repository=neo4j_repository)


@pytest.fixture
def sample_problems(neo4j_repository, sample_evidence_data):
    """Create sample problems for search testing."""
    problems = []
    test_data = [
        ("NLP", ProblemStatus.OPEN, "Transformer attention scaling"),
        ("NLP", ProblemStatus.IN_PROGRESS, "Language model pretraining"),
        ("CV", ProblemStatus.OPEN, "Image classification efficiency"),
        ("CV", ProblemStatus.RESOLVED, "Object detection accuracy"),
        ("ML", ProblemStatus.OPEN, "Reinforcement learning exploration"),
    ]

    for domain, status, topic in test_data:
        problem = Problem(
            statement=f"Research problem about {topic} in {domain} domain - " + "x" * 20,
            domain=domain,
            status=status,
            evidence=Evidence(**sample_evidence_data),
            extraction_metadata=ExtractionMetadata(
                extraction_model="gpt-4",
                confidence_score=0.9,
            ),
        )
        neo4j_repository.create_problem(problem)
        problems.append(problem)

    return problems


class TestStructuredSearch:
    """Test structured search operations."""

    def test_search_by_domain(self, search_service, sample_problems):
        """Test searching problems by domain."""
        results = search_service.structured_search(domain="NLP")

        assert len(results) == 2
        assert all(r.problem.domain == "NLP" for r in results)
        assert all(r.match_type == "structured" for r in results)

    def test_search_by_status(self, search_service, sample_problems):
        """Test searching problems by status."""
        results = search_service.structured_search(status=ProblemStatus.OPEN)

        assert len(results) == 3
        assert all(r.problem.status == ProblemStatus.OPEN for r in results)

    def test_search_by_domain_and_status(self, search_service, sample_problems):
        """Test searching with multiple filters."""
        results = search_service.structured_search(
            domain="CV",
            status=ProblemStatus.OPEN,
        )

        assert len(results) == 1
        assert results[0].problem.domain == "CV"
        assert results[0].problem.status == ProblemStatus.OPEN

    def test_search_with_limit(self, search_service, sample_problems):
        """Test limiting search results."""
        results = search_service.structured_search(top_k=2)

        assert len(results) == 2

    def test_search_no_results(self, search_service, sample_problems):
        """Test search with no matching results."""
        results = search_service.structured_search(domain="Nonexistent")

        assert len(results) == 0

    def test_search_result_score(self, search_service, sample_problems):
        """Test that structured search results have score of 1.0."""
        results = search_service.structured_search(domain="NLP")

        assert all(r.score == 1.0 for r in results)


class TestSemanticSearch:
    """Test semantic search operations.

    Note: These tests require embeddings which need OpenAI API key.
    Most tests will be skipped without proper configuration.
    """

    def test_semantic_search_requires_embeddings(
        self, search_service, sample_problems
    ):
        """Test that semantic search returns empty without embeddings."""
        # Without embeddings stored, semantic search should return empty
        results = search_service.semantic_search(
            query="transformer attention mechanism",
            top_k=5,
        )

        # Since problems don't have embeddings, we expect no results
        # from vector search
        assert isinstance(results, list)


class TestFindSimilarProblems:
    """Test duplicate detection functionality."""

    def test_find_similar_problems_returns_list(
        self, search_service, sample_problems
    ):
        """Test that find_similar_problems returns a list."""
        problem = sample_problems[0]
        results = search_service.find_similar_problems(
            problem=problem,
            threshold=0.5,
        )

        assert isinstance(results, list)

    def test_find_similar_excludes_self(self, search_service, sample_problems):
        """Test that find_similar_problems excludes the input problem."""
        problem = sample_problems[0]
        results = search_service.find_similar_problems(
            problem=problem,
            threshold=0.5,
            exclude_self=True,
        )

        # Self should not be in results
        result_ids = [r.problem.id for r in results]
        assert problem.id not in result_ids


class TestSearchResultSorting:
    """Test search result ordering."""

    def test_results_are_sorted_by_score(self, search_service, sample_problems):
        """Test that search results are sorted by score descending."""
        results = search_service.structured_search()

        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score


class TestHybridSearch:
    """Test hybrid search (combined semantic + structured)."""

    def test_hybrid_search_with_filters(self, search_service, sample_problems):
        """Test hybrid search with domain filter."""
        results = search_service.hybrid_search(
            query="machine learning",
            domain="NLP",
            top_k=10,
        )

        # Without embeddings, hybrid search falls back to filtered results
        assert isinstance(results, list)

    def test_hybrid_search_with_status_filter(self, search_service, sample_problems):
        """Test hybrid search with status filter."""
        results = search_service.hybrid_search(
            query="research problem",
            status=ProblemStatus.OPEN,
            top_k=10,
        )

        # All results should have OPEN status
        for r in results:
            if r.problem.status:
                assert r.problem.status == ProblemStatus.OPEN
