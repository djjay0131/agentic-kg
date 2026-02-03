"""
E2E tests for knowledge graph population.

Tests storing and querying data in the staging Neo4j instance.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from agentic_kg.knowledge_graph.models import (
    Author,
    Paper,
    Problem,
    ProblemStatus,
)
from agentic_kg.knowledge_graph.repository import Neo4jRepository
from agentic_kg.knowledge_graph.search import SearchService

from .conftest import E2EConfig
from .utils import clear_test_data, count_nodes, count_relationships

if TYPE_CHECKING:
    from neo4j import Session


def make_test_id(prefix: str) -> str:
    """Generate a unique test ID."""
    return f"TEST_{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.e2e
class TestKGPopulationE2E:
    """E2E tests for KG population and querying."""

    @pytest.fixture
    def repo(self, e2e_config: E2EConfig):
        """Create repository for staging Neo4j."""
        from agentic_kg.config import Neo4jConfig

        config = Neo4jConfig(
            uri=e2e_config.neo4j_uri,
            username=e2e_config.neo4j_user,
            password=e2e_config.neo4j_password,
        )
        repo = Neo4jRepository(config=config)
        yield repo
        repo.close()

    @pytest.fixture(autouse=True)
    def cleanup_test_data(self, neo4j_session: "Session"):
        """Clean up test data before and after each test."""
        clear_test_data(neo4j_session, prefix="TEST_")
        yield
        clear_test_data(neo4j_session, prefix="TEST_")

    def test_verify_neo4j_connectivity(self, repo: Neo4jRepository):
        """Test that we can connect to staging Neo4j."""
        assert repo.verify_connectivity() is True

    def test_create_and_get_problem(self, repo: Neo4jRepository):
        """Test creating and retrieving a problem."""
        problem_id = make_test_id("problem")

        problem = Problem(
            id=problem_id,
            title="Test Research Problem",
            description="This is a test problem for E2E testing.",
            domain="testing",
            status=ProblemStatus.OPEN,
            importance_score=0.75,
        )

        # Create
        created = repo.create_problem(problem, generate_embedding=False)
        assert created.id == problem_id

        # Get
        retrieved = repo.get_problem(problem_id)
        assert retrieved is not None
        assert retrieved.title == "Test Research Problem"
        assert retrieved.domain == "testing"
        assert retrieved.importance_score == 0.75

    def test_create_paper_with_authors(self, repo: Neo4jRepository):
        """Test creating a paper with author relationships."""
        paper_id = make_test_id("paper")
        author_id = make_test_id("author")

        author = Author(
            id=author_id,
            name="Test Author",
            affiliations=["Test University"],
        )

        paper = Paper(
            id=paper_id,
            title="Test Paper for E2E",
            abstract="This is a test paper abstract.",
            year=2024,
            venue="Test Conference",
            authors=[author],
        )

        # Create
        created = repo.create_paper(paper)
        assert created.id == paper_id

        # Get
        retrieved = repo.get_paper(paper_id)
        assert retrieved is not None
        assert retrieved.title == "Test Paper for E2E"
        assert len(retrieved.authors) == 1
        assert retrieved.authors[0].name == "Test Author"

    def test_link_problem_to_paper(self, repo: Neo4jRepository):
        """Test creating problem-paper relationships."""
        problem_id = make_test_id("problem")
        paper_id = make_test_id("paper")

        # Create paper first
        paper = Paper(
            id=paper_id,
            title="Source Paper",
            abstract="Paper from which problem was extracted.",
            year=2024,
        )
        repo.create_paper(paper)

        # Create problem linked to paper
        problem = Problem(
            id=problem_id,
            title="Extracted Problem",
            description="Problem extracted from source paper.",
            domain="testing",
            source_paper_ids=[paper_id],
        )
        created = repo.create_problem(problem, generate_embedding=False)

        # Create the relationship explicitly
        repo.link_problem_to_paper(problem_id, paper_id)

        # Verify relationship exists
        retrieved = repo.get_problem(problem_id)
        assert paper_id in retrieved.source_paper_ids

    def test_list_problems_with_filters(self, repo: Neo4jRepository):
        """Test listing problems with filters."""
        # Create multiple problems
        for i, (domain, status) in enumerate([
            ("NLP", ProblemStatus.OPEN),
            ("NLP", ProblemStatus.ACTIVE),
            ("ML", ProblemStatus.OPEN),
        ]):
            problem = Problem(
                id=make_test_id(f"problem_{i}"),
                title=f"Test Problem {i}",
                description=f"Description {i}",
                domain=domain,
                status=status,
            )
            repo.create_problem(problem, generate_embedding=False)

        # List all TEST_ problems
        all_problems = repo.list_problems(limit=100)
        test_problems = [p for p in all_problems if p.id.startswith("TEST_")]
        assert len(test_problems) >= 3

        # Filter by domain
        nlp_problems = repo.list_problems(domain="NLP", limit=100)
        test_nlp = [p for p in nlp_problems if p.id.startswith("TEST_")]
        assert len(test_nlp) >= 2

        # Filter by status
        open_problems = repo.list_problems(status=ProblemStatus.OPEN, limit=100)
        test_open = [p for p in open_problems if p.id.startswith("TEST_")]
        assert len(test_open) >= 2


@pytest.mark.e2e
class TestHybridSearchE2E:
    """E2E tests for hybrid search functionality."""

    @pytest.fixture
    def search_service(self, e2e_config: E2EConfig, repo: Neo4jRepository):
        """Create search service for staging Neo4j."""
        # SearchService uses repository, not direct neo4j_config
        service = SearchService(repository=repo)
        yield service

    @pytest.fixture
    def repo(self, e2e_config: E2EConfig):
        """Create repository for test data setup."""
        from agentic_kg.config import Neo4jConfig

        config = Neo4jConfig(
            uri=e2e_config.neo4j_uri,
            username=e2e_config.neo4j_user,
            password=e2e_config.neo4j_password,
        )
        repo = Neo4jRepository(config=config)
        yield repo
        repo.close()

    @pytest.fixture(autouse=True)
    def cleanup_test_data(self, neo4j_session: "Session"):
        """Clean up test data before and after each test."""
        clear_test_data(neo4j_session, prefix="TEST_")
        yield
        clear_test_data(neo4j_session, prefix="TEST_")

    def test_keyword_search(
        self,
        search_service: SearchService,
        repo: Neo4jRepository,
    ):
        """Test keyword-based search."""
        # Create test problem with unique keyword
        unique_keyword = f"uniquekeyword{uuid.uuid4().hex[:6]}"
        problem = Problem(
            id=make_test_id("searchable"),
            title=f"Problem about {unique_keyword}",
            description=f"This problem involves {unique_keyword} research.",
            domain="testing",
        )
        repo.create_problem(problem, generate_embedding=False)

        # Search using structured search (keyword-based)
        results = search_service.structured_search(
            domain="testing",
            top_k=10,
        )

        # Should find our problem
        matching = [r for r in results if r.problem.id.startswith("TEST_")]
        assert len(matching) >= 1


@pytest.mark.e2e
class TestRelationshipsE2E:
    """E2E tests for knowledge graph relationships."""

    @pytest.fixture
    def repo(self, e2e_config: E2EConfig):
        """Create repository for staging Neo4j."""
        from agentic_kg.config import Neo4jConfig

        config = Neo4jConfig(
            uri=e2e_config.neo4j_uri,
            username=e2e_config.neo4j_user,
            password=e2e_config.neo4j_password,
        )
        repo = Neo4jRepository(config=config)
        yield repo
        repo.close()

    @pytest.fixture(autouse=True)
    def cleanup_test_data(self, neo4j_session: "Session"):
        """Clean up test data before and after each test."""
        clear_test_data(neo4j_session, prefix="TEST_")
        yield
        clear_test_data(neo4j_session, prefix="TEST_")

    def test_problem_paper_author_chain(
        self,
        repo: Neo4jRepository,
        neo4j_session: "Session",
    ):
        """Test complete chain: Author → Paper → Problem."""
        author_id = make_test_id("author")
        paper_id = make_test_id("paper")
        problem_id = make_test_id("problem")

        # Create author
        author = Author(
            id=author_id,
            name="Chain Test Author",
            affiliations=["Test Institute"],
        )

        # Create paper with author
        paper = Paper(
            id=paper_id,
            title="Chain Test Paper",
            abstract="Paper for testing relationship chains.",
            year=2024,
            authors=[author],
        )
        repo.create_paper(paper)

        # Create problem linked to paper
        problem = Problem(
            id=problem_id,
            title="Chain Test Problem",
            description="Problem from chain test paper.",
            domain="testing",
            source_paper_ids=[paper_id],
        )
        repo.create_problem(problem, generate_embedding=False)
        repo.link_problem_to_paper(problem_id, paper_id)

        # Query the chain: Problem → Paper → Author
        result = neo4j_session.run(
            """
            MATCH (prob:Problem {id: $problem_id})
                  -[:EXTRACTED_FROM]->(paper:Paper)
                  -[:AUTHORED_BY]->(author:Author)
            RETURN prob.title as problem, paper.title as paper, author.name as author
            """,
            problem_id=problem_id,
        )
        record = result.single()

        assert record is not None
        assert record["problem"] == "Chain Test Problem"
        assert record["paper"] == "Chain Test Paper"
        assert record["author"] == "Chain Test Author"

    def test_count_test_nodes(self, neo4j_session: "Session", repo: Neo4jRepository):
        """Test that we can count nodes created during tests."""
        # Create some test data
        for i in range(3):
            problem = Problem(
                id=make_test_id(f"count_{i}"),
                title=f"Count Test Problem {i}",
                description=f"Description {i}",
                domain="testing",
            )
            repo.create_problem(problem, generate_embedding=False)

        # Count using our utility
        # Note: This counts ALL problems, not just TEST_ ones
        total = count_nodes(neo4j_session, "Problem")
        assert total >= 3  # At least our 3 test problems
