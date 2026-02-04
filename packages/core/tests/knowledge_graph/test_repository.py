"""
Integration tests for Neo4j repository.

These tests require Docker and will be skipped if Docker is not available.
"""

import uuid

import pytest
from agentic_kg.knowledge_graph.models import (
    Author,
    Evidence,
    ExtractionMetadata,
    Paper,
    Problem,
    ProblemStatus,
)
from agentic_kg.knowledge_graph.repository import (
    DuplicateError,
    NotFoundError,
)


def _test_id() -> str:
    """Generate a TEST_ prefixed unique ID for test isolation."""
    return f"TEST_{uuid.uuid4().hex[:16]}"

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


class TestProblemCRUD:
    """Test Problem CRUD operations."""

    def test_create_problem(self, neo4j_repository, sample_problem_data):
        """Test creating a problem."""
        problem = Problem(**sample_problem_data)
        created = neo4j_repository.create_problem(problem)

        assert created.id == problem.id
        assert created.statement == problem.statement
        assert created.domain == problem.domain

    def test_create_duplicate_problem_raises_error(
        self, neo4j_repository, sample_problem_data
    ):
        """Test that creating duplicate problem raises DuplicateError."""
        problem = Problem(**sample_problem_data)
        neo4j_repository.create_problem(problem)

        with pytest.raises(DuplicateError):
            neo4j_repository.create_problem(problem)

    def test_get_problem(self, neo4j_repository, sample_problem_data):
        """Test retrieving a problem by ID."""
        problem = Problem(**sample_problem_data)
        neo4j_repository.create_problem(problem)

        retrieved = neo4j_repository.get_problem(problem.id)

        assert retrieved.id == problem.id
        assert retrieved.statement == problem.statement
        assert retrieved.status == problem.status

    def test_get_nonexistent_problem_raises_error(self, neo4j_repository):
        """Test that getting nonexistent problem raises NotFoundError."""
        with pytest.raises(NotFoundError):
            neo4j_repository.get_problem("nonexistent-id")

    def test_update_problem(self, neo4j_repository, sample_problem_data):
        """Test updating a problem."""
        problem = Problem(**sample_problem_data)
        neo4j_repository.create_problem(problem)

        problem.status = ProblemStatus.IN_PROGRESS
        problem.domain = "Updated Domain"
        updated = neo4j_repository.update_problem(problem)

        assert updated.status == ProblemStatus.IN_PROGRESS
        assert updated.domain == "Updated Domain"
        assert updated.version == 2

    def test_update_nonexistent_problem_raises_error(
        self, neo4j_repository, sample_problem_data
    ):
        """Test that updating nonexistent problem raises NotFoundError."""
        problem = Problem(**sample_problem_data)

        with pytest.raises(NotFoundError):
            neo4j_repository.update_problem(problem)

    def test_soft_delete_problem(self, neo4j_repository, sample_problem_data):
        """Test soft deleting a problem."""
        problem = Problem(**sample_problem_data)
        neo4j_repository.create_problem(problem)

        result = neo4j_repository.delete_problem(problem.id, soft=True)

        assert result is True
        retrieved = neo4j_repository.get_problem(problem.id)
        assert retrieved.status == ProblemStatus.DEPRECATED

    def test_hard_delete_problem(self, neo4j_repository, sample_problem_data):
        """Test hard deleting a problem."""
        problem = Problem(**sample_problem_data)
        neo4j_repository.create_problem(problem)

        result = neo4j_repository.delete_problem(problem.id, soft=False)

        assert result is True
        with pytest.raises(NotFoundError):
            neo4j_repository.get_problem(problem.id)

    def test_list_problems(self, neo4j_repository, sample_evidence_data):
        """Test listing problems with filters."""
        # Use a unique domain prefix to avoid interference from other tests
        test_run_id = uuid.uuid4().hex[:8]
        nlp_domain = f"TEST_NLP_{test_run_id}"
        cv_domain = f"TEST_CV_{test_run_id}"

        # Create multiple problems with TEST_ prefixed IDs
        created_ids = []
        for i, domain in enumerate([nlp_domain, nlp_domain, cv_domain]):
            problem = Problem(
                id=_test_id(),
                statement=f"Problem {i} - " + "x" * 20,
                domain=domain,
                status=ProblemStatus.OPEN,
                evidence=Evidence(**sample_evidence_data),
                extraction_metadata=ExtractionMetadata(
                    extraction_model="gpt-4",
                    confidence_score=0.9,
                ),
            )
            neo4j_repository.create_problem(problem)
            created_ids.append(problem.id)

        # Filter by domain (unique to this test run)
        nlp_problems = neo4j_repository.list_problems(domain=nlp_domain)
        assert len(nlp_problems) == 2

        cv_problems = neo4j_repository.list_problems(domain=cv_domain)
        assert len(cv_problems) == 1

        # Verify the created problems are returned
        all_problems = neo4j_repository.list_problems()
        found_ids = [p.id for p in all_problems if p.id in created_ids]
        assert len(found_ids) == 3


class TestPaperCRUD:
    """Test Paper CRUD operations."""

    def test_create_paper(self, neo4j_repository, sample_paper_data):
        """Test creating a paper."""
        paper = Paper(**sample_paper_data)
        created = neo4j_repository.create_paper(paper)

        assert created.doi == paper.doi
        assert created.title == paper.title

    def test_create_duplicate_paper_raises_error(
        self, neo4j_repository, sample_paper_data
    ):
        """Test that creating duplicate paper raises DuplicateError."""
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_paper(paper)

        with pytest.raises(DuplicateError):
            neo4j_repository.create_paper(paper)

    def test_get_paper(self, neo4j_repository, sample_paper_data):
        """Test retrieving a paper by DOI."""
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_paper(paper)

        retrieved = neo4j_repository.get_paper(paper.doi)

        assert retrieved.doi == paper.doi
        assert retrieved.title == paper.title
        assert retrieved.year == paper.year

    def test_get_nonexistent_paper_raises_error(self, neo4j_repository):
        """Test that getting nonexistent paper raises NotFoundError."""
        with pytest.raises(NotFoundError):
            neo4j_repository.get_paper("10.9999/nonexistent")

    def test_update_paper(self, neo4j_repository, sample_paper_data):
        """Test updating a paper."""
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_paper(paper)

        paper.abstract = "Updated abstract"
        updated = neo4j_repository.update_paper(paper)

        assert updated.abstract == "Updated abstract"

    def test_delete_paper(self, neo4j_repository, sample_paper_data):
        """Test deleting a paper."""
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_paper(paper)

        result = neo4j_repository.delete_paper(paper.doi)

        assert result is True
        with pytest.raises(NotFoundError):
            neo4j_repository.get_paper(paper.doi)


class TestAuthorCRUD:
    """Test Author CRUD operations."""

    def test_create_author(self, neo4j_repository, sample_author_data):
        """Test creating an author."""
        author = Author(**sample_author_data)
        created = neo4j_repository.create_author(author)

        assert created.id == author.id
        assert created.name == author.name

    def test_create_duplicate_author_raises_error(
        self, neo4j_repository, sample_author_data
    ):
        """Test that creating duplicate author raises DuplicateError."""
        author = Author(**sample_author_data)
        neo4j_repository.create_author(author)

        with pytest.raises(DuplicateError):
            neo4j_repository.create_author(author)

    def test_get_author(self, neo4j_repository, sample_author_data):
        """Test retrieving an author by ID."""
        author = Author(**sample_author_data)
        neo4j_repository.create_author(author)

        retrieved = neo4j_repository.get_author(author.id)

        assert retrieved.id == author.id
        assert retrieved.name == author.name
        assert retrieved.orcid == author.orcid

    def test_get_nonexistent_author_raises_error(self, neo4j_repository):
        """Test that getting nonexistent author raises NotFoundError."""
        with pytest.raises(NotFoundError):
            neo4j_repository.get_author("nonexistent-id")

    def test_update_author(self, neo4j_repository, sample_author_data):
        """Test updating an author."""
        author = Author(**sample_author_data)
        neo4j_repository.create_author(author)

        author.affiliations = ["Stanford", "OpenAI"]
        updated = neo4j_repository.update_author(author)

        assert updated.affiliations == ["Stanford", "OpenAI"]


class TestRepositoryConnection:
    """Test repository connection handling."""

    def test_verify_connectivity(self, neo4j_repository):
        """Test that connectivity verification works."""
        result = neo4j_repository.verify_connectivity()
        assert result is True

    def test_session_context_manager(self, neo4j_repository):
        """Test session context manager."""
        with neo4j_repository.session() as session:
            result = session.run("RETURN 1 as n")
            record = result.single()
            assert record["n"] == 1
