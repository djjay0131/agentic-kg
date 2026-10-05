"""
E2E tests for knowledge graph population.

Tests storing and querying data in the staging Neo4j instance.

All nodes written by this module carry a run-unique ``TEST_`` namespace so a
concurrent e2e run against the same staging database cannot see or delete them
(#78). The namespace is applied to ``Problem``/``Author`` ids and to paper
DOIs (``10.<namespace>/...``, which is a legal DOI and still matched by the
TEST_ cleanup predicate).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from agentic_kg.knowledge_graph.models import (
    Author,
    Evidence,
    ExtractionMetadata,
    Paper,
    Problem,
    ProblemStatus,
)
from agentic_kg.knowledge_graph.relations import RelationService
from agentic_kg.knowledge_graph.repository import Neo4jRepository
from agentic_kg.knowledge_graph.search import SearchService

from .conftest import E2E_NAMESPACE, E2EConfig
from .utils import clear_test_data

if TYPE_CHECKING:
    from neo4j import Session


# Every test here asserts on graph contents and needs a live Neo4j session.
# ADR-0006 makes staging Neo4j VPC-private, so these are deselected in the
# GitHub-hosted E2E job (``-m "not requires_db"``) and run only where the
# database is reachable: an in-VPC Cloud Run Job, or locally via an IAP tunnel.
pytestmark = pytest.mark.requires_db


def make_test_id(prefix: str) -> str:
    """Generate a run-namespaced unique test ID."""
    return f"{E2E_NAMESPACE}_{prefix}_{uuid.uuid4().hex[:8]}"


def make_test_doi(prefix: str) -> str:
    """Generate a run-namespaced, validator-legal test DOI.

    ``Paper`` requires the DOI to start with ``10.``; ``10.<ns>/...`` keeps
    the TEST_ namespace visible to cleanup while satisfying that contract.
    """
    return f"10.{E2E_NAMESPACE}/{prefix}_{uuid.uuid4().hex[:8]}"


def make_problem(
    problem_id: str,
    statement: str,
    status: ProblemStatus = ProblemStatus.OPEN,
    source_doi: str | None = None,
) -> Problem:
    """Build a Problem carrying the evidence + extraction metadata the
    repository serializes.

    ``Problem.to_neo4j_properties`` calls ``.model_dump()`` on both nested
    fields, and the read path rebuilds them, so a Problem created through
    ``Neo4jRepository`` must supply valid values (the unit fixtures do the
    same).
    """
    return Problem(
        id=problem_id,
        statement=statement,
        status=status,
        evidence=Evidence(
            source_doi=source_doi or make_test_doi("source"),
            source_title="E2E Source Paper",
            section="introduction",
            quoted_text="A quoted problem statement from the source paper.",
            char_offset_start=0,
            char_offset_end=40,
        ),
        extraction_metadata=ExtractionMetadata(
            extraction_model="gpt-4",
            confidence_score=0.9,
        ),
    )


@pytest.fixture
def repo(e2e_config: E2EConfig):
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
def cleanup_test_data(neo4j_session: "Session"):
    """Clean this run's namespaced nodes before and after each test."""
    clear_test_data(neo4j_session, prefix=E2E_NAMESPACE)
    yield
    clear_test_data(neo4j_session, prefix=E2E_NAMESPACE)


@pytest.mark.e2e
class TestKGPopulationE2E:
    """E2E tests for KG population and querying."""

    def test_verify_neo4j_connectivity(self, repo: Neo4jRepository):
        """Test that we can connect to staging Neo4j."""
        assert repo.verify_connectivity() is True

    def test_create_and_get_problem(self, repo: Neo4jRepository):
        """Test creating and retrieving a problem."""
        problem_id = make_test_id("problem")

        problem = make_problem(
            problem_id,
            (
                f"{E2E_NAMESPACE} How can we improve the efficiency of "
                "transformer models for long-context understanding?"
            ),
            status=ProblemStatus.OPEN,
        )

        # Create
        created = repo.create_problem(problem, generate_embedding=False)
        assert created.id == problem_id

        # Get
        retrieved = repo.get_problem(problem_id)
        assert retrieved is not None
        assert retrieved.statement == problem.statement
        assert retrieved.status == ProblemStatus.OPEN

    def test_create_paper_with_authors(
        self, repo: Neo4jRepository, neo4j_session: "Session"
    ):
        """Test creating a paper with an author relationship.

        ``Paper.authors`` is a ``list[str]`` of display names in the current
        model; author *nodes* are linked via ``create_author`` +
        ``link_paper_to_author``.
        """
        paper_doi = make_test_doi("paper")
        author_id = make_test_id("author")

        author = Author(
            id=author_id,
            name=f"{E2E_NAMESPACE} Test Author",
            affiliations=["Test University"],
        )
        repo.create_author(author)

        paper = Paper(
            doi=paper_doi,
            title="Test Paper for E2E",
            abstract="This is a test paper abstract.",
            year=2024,
            venue="Test Conference",
            authors=["Test Author"],
        )

        # Create
        created = repo.create_paper(paper)
        assert created.doi == paper_doi

        # Link the author node to the paper
        repo.link_paper_to_author(paper_doi, author_id, position=1)

        # Get
        retrieved = repo.get_paper(paper_doi)
        assert retrieved is not None
        assert retrieved.title == "Test Paper for E2E"
        assert retrieved.authors == ["Test Author"]

        # The AUTHORED_BY edge exists
        result = neo4j_session.run(
            """
            MATCH (p:Paper {doi: $doi})-[:AUTHORED_BY]->(a:Author {id: $author_id})
            RETURN count(a) AS n
            """,
            doi=paper_doi,
            author_id=author_id,
        )
        assert result.single()["n"] == 1

    def test_link_problem_to_paper(self, repo: Neo4jRepository):
        """Test creating problem-paper EXTRACTED_FROM relationships."""
        problem_id = make_test_id("problem")
        paper_doi = make_test_doi("paper")

        # Create paper first
        paper = Paper(
            doi=paper_doi,
            title="Source Paper",
            abstract="Paper from which problem was extracted.",
            year=2024,
        )
        repo.create_paper(paper)

        # Create problem
        problem = make_problem(
            problem_id,
            (
                f"{E2E_NAMESPACE} Problem extracted from a source paper for "
                "relationship testing."
            ),
            status=ProblemStatus.OPEN,
            source_doi=paper_doi,
        )
        repo.create_problem(problem, generate_embedding=False)

        # Link via the current relation service contract
        relation_service = RelationService(repository=repo)
        relation_service.link_problem_to_paper(
            problem_id=problem_id,
            paper_doi=paper_doi,
            section="introduction",
        )

        # Verify relationship exists
        source = relation_service.get_source_paper(problem_id)
        assert source is not None
        assert source["doi"] == paper_doi

    def test_list_problems_with_filters(self, repo: Neo4jRepository):
        """Test listing problems with status filters."""
        statuses = [
            ProblemStatus.OPEN,
            ProblemStatus.IN_PROGRESS,
            ProblemStatus.OPEN,
        ]
        for i, status in enumerate(statuses):
            problem = make_problem(
                make_test_id(f"problem_{i}"),
                (
                    f"{E2E_NAMESPACE} Test problem number {i} used to verify "
                    "status-filtered listing."
                ),
                status=status,
            )
            repo.create_problem(problem, generate_embedding=False)

        # All namespaced problems were created
        all_problems = [
            p for p in repo.list_problems(limit=500) if p.id.startswith(E2E_NAMESPACE)
        ]
        assert len(all_problems) >= 3

        # Filter by status
        open_problems = [
            p
            for p in repo.list_problems(status=ProblemStatus.OPEN, limit=500)
            if p.id.startswith(E2E_NAMESPACE)
        ]
        assert len(open_problems) >= 2


@pytest.mark.e2e
class TestHybridSearchE2E:
    """E2E tests for hybrid search functionality."""

    @pytest.fixture
    def search_service(self, repo: Neo4jRepository):
        """Create search service for staging Neo4j."""
        return SearchService(repository=repo)

    def test_keyword_search(
        self,
        search_service: SearchService,
        repo: Neo4jRepository,
    ):
        """Test structured (keyword-filtered) search."""
        unique_keyword = f"uniquekeyword{uuid.uuid4().hex[:6]}"
        problem = make_problem(
            make_test_id("searchable"),
            (
                f"{E2E_NAMESPACE} A problem about {unique_keyword} and related "
                "research directions."
            ),
            status=ProblemStatus.OPEN,
        )
        repo.create_problem(problem, generate_embedding=False)

        # Structured search by status (the current contract; the old
        # `domain` argument no longer exists on Problem or structured_search).
        results = search_service.structured_search(
            status=ProblemStatus.OPEN,
            top_k=500,
        )

        matching = [
            r for r in results if r.problem.id.startswith(E2E_NAMESPACE)
        ]
        assert len(matching) >= 1


@pytest.mark.e2e
class TestRelationshipsE2E:
    """E2E tests for knowledge graph relationships."""

    def test_problem_paper_author_chain(
        self,
        repo: Neo4jRepository,
        neo4j_session: "Session",
    ):
        """Test complete chain: Problem → Paper → Author."""
        author_id = make_test_id("author")
        paper_doi = make_test_doi("paper")
        problem_id = make_test_id("problem")

        # Create author
        author = Author(
            id=author_id,
            name=f"{E2E_NAMESPACE} Chain Test Author",
            affiliations=["Test Institute"],
        )
        repo.create_author(author)

        # Create paper and link the author
        paper = Paper(
            doi=paper_doi,
            title="Chain Test Paper",
            abstract="Paper for testing relationship chains.",
            year=2024,
        )
        repo.create_paper(paper)
        repo.link_paper_to_author(paper_doi, author_id, position=1)

        # Create problem linked to paper
        problem = make_problem(
            problem_id,
            (
                f"{E2E_NAMESPACE} Problem from a chain-test paper used to "
                "verify graph traversal."
            ),
            status=ProblemStatus.OPEN,
            source_doi=paper_doi,
        )
        repo.create_problem(problem, generate_embedding=False)
        RelationService(repository=repo).link_problem_to_paper(
            problem_id=problem_id,
            paper_doi=paper_doi,
            section="introduction",
        )

        # Query the chain: Problem → Paper → Author
        result = neo4j_session.run(
            """
            MATCH (prob:Problem {id: $problem_id})
                  -[:EXTRACTED_FROM]->(paper:Paper)
                  -[:AUTHORED_BY]->(author:Author)
            RETURN prob.statement as statement, paper.title as paper,
                   author.name as author
            """,
            problem_id=problem_id,
        )
        record = result.single()

        assert record is not None
        assert record["paper"] == "Chain Test Paper"
        assert record["author"] == f"{E2E_NAMESPACE} Chain Test Author"

    def test_count_test_nodes(self, neo4j_session: "Session", repo: Neo4jRepository):
        """Test that we can count the run's namespaced nodes."""
        for i in range(3):
            problem = make_problem(
                make_test_id(f"count_{i}"),
                (
                    f"{E2E_NAMESPACE} Count test problem number {i} for node "
                    "counting."
                ),
                status=ProblemStatus.OPEN,
            )
            repo.create_problem(problem, generate_embedding=False)

        # Count only this run's nodes, not the whole shared database.
        result = neo4j_session.run(
            "MATCH (p:Problem) WHERE p.id STARTS WITH $ns RETURN count(p) AS n",
            ns=E2E_NAMESPACE,
        )
        assert result.single()["n"] >= 3
