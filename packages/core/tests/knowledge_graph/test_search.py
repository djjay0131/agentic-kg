"""
Integration tests for search operations.

These tests require Docker and will be skipped if Docker is not available.
Semantic search tests are limited since they require OpenAI API key.
"""

import uuid

import pytest
from agentic_kg.knowledge_graph.models import (
    Evidence,
    ExtractionMetadata,
    Problem,
    ProblemStatus,
    Topic,
    TopicLevel,
)
from agentic_kg.knowledge_graph.search import SearchService

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


def _test_id() -> str:
    """Generate a TEST_ prefixed unique ID for test isolation."""
    return f"TEST_{uuid.uuid4().hex[:16]}"


@pytest.fixture
def search_service(neo4j_repository):
    """Create a search service with the test repository."""
    return SearchService(repository=neo4j_repository)


@pytest.fixture
def test_topics(neo4j_repository):
    """Create three Topic nodes (NLP, CV, ML) for search filtering.

    After the E-1 domain→Topic rename, Problems no longer carry a domain
    string; they BELONG_TO a Topic node instead. Each topic name is
    TEST_-prefixed so the per-test cleanup catches it.
    """
    run_id = uuid.uuid4().hex[:8]
    names = {
        "NLP": f"TEST_NLP_{run_id}",
        "CV": f"TEST_CV_{run_id}",
        "ML": f"TEST_ML_{run_id}",
    }
    topics: dict[str, Topic] = {}
    for key, name in names.items():
        topic = Topic(name=name, level=TopicLevel.AREA)
        neo4j_repository.create_topic(topic, generate_embedding=False)
        topics[key] = topic
    return topics


@pytest.fixture
def sample_problems(neo4j_repository, sample_evidence_data, test_topics):
    """Create sample problems for search testing.

    Each problem is linked to one of the three test topics via BELONGS_TO
    so the structured-search topic_id filter has data to find.
    """
    problems = []
    test_data = [
        ("NLP", ProblemStatus.OPEN, "Transformer attention scaling"),
        ("NLP", ProblemStatus.IN_PROGRESS, "Language model pretraining"),
        ("CV", ProblemStatus.OPEN, "Image classification efficiency"),
        ("CV", ProblemStatus.RESOLVED, "Object detection accuracy"),
        ("ML", ProblemStatus.OPEN, "Reinforcement learning exploration"),
    ]

    for topic_key, status, topic_label in test_data:
        problem = Problem(
            id=_test_id(),
            statement=(
                f"TEST_{uuid.uuid4().hex[:8]} Research problem about {topic_label} "
                f"in {topic_key} area - " + "x" * 20
            ),
            status=status,
            evidence=Evidence(**sample_evidence_data),
            extraction_metadata=ExtractionMetadata(
                extraction_model="gpt-4",
                confidence_score=0.9,
            ),
        )
        neo4j_repository.create_problem(problem, generate_embedding=False)
        neo4j_repository.assign_entity_to_topic(
            problem.id, test_topics[topic_key].id, entity_label="Problem"
        )
        problems.append(problem)

    return problems


class TestStructuredSearch:
    """Test structured search operations."""

    def test_search_by_topic(self, search_service, sample_problems, test_topics):
        """Searching problems by Topic id returns BELONGS_TO neighbors."""
        results = search_service.structured_search(topic_id=test_topics["NLP"].id)

        assert len(results) == 2
        assert all(r.match_type == "structured" for r in results)

    def test_search_by_status(self, search_service, sample_problems, test_topics):
        """Combine a topic filter with a status filter."""
        results = search_service.structured_search(
            topic_id=test_topics["NLP"].id,
            status=ProblemStatus.OPEN,
        )
        assert len(results) == 1  # Only 1 NLP problem is OPEN

        results = search_service.structured_search(
            topic_id=test_topics["ML"].id,
            status=ProblemStatus.OPEN,
        )
        assert len(results) == 1  # Only 1 ML problem exists and is OPEN
        assert all(r.problem.status == ProblemStatus.OPEN for r in results)

    def test_search_by_topic_and_status(
        self, search_service, sample_problems, test_topics
    ):
        """Test searching with multiple filters."""
        results = search_service.structured_search(
            topic_id=test_topics["CV"].id,
            status=ProblemStatus.OPEN,
        )

        assert len(results) == 1
        assert results[0].problem.status == ProblemStatus.OPEN

    def test_search_with_limit(self, search_service, sample_problems, test_topics):
        """Test limiting search results."""
        results = search_service.structured_search(
            topic_id=test_topics["NLP"].id, top_k=1
        )

        assert len(results) == 1

    def test_search_no_results(self, search_service, sample_problems):
        """Test search with no matching results."""
        results = search_service.structured_search(
            topic_id="TEST_nonexistent_topic_12345"
        )

        assert len(results) == 0

    def test_search_result_score(self, search_service, sample_problems, test_topics):
        """Test that structured search results have score of 1.0."""
        results = search_service.structured_search(topic_id=test_topics["NLP"].id)

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

    def test_hybrid_search_with_filters(
        self, search_service, sample_problems, test_topics
    ):
        """Test hybrid search with a topic filter."""
        results = search_service.hybrid_search(
            query="machine learning",
            topic_id=test_topics["NLP"].id,
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
