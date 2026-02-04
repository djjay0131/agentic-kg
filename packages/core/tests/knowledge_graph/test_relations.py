"""
Integration tests for relation operations.

These tests require Docker and will be skipped if Docker is not available.
"""

import pytest
from agentic_kg.knowledge_graph.models import (
    Author,
    ContradictionType,
    DependencyType,
    Evidence,
    ExtractionMetadata,
    Paper,
    Problem,
    ProblemStatus,
    RelationType,
)
from agentic_kg.knowledge_graph.relations import RelationError, RelationService
from agentic_kg.knowledge_graph.repository import NotFoundError

# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


@pytest.fixture
def relation_service(neo4j_repository):
    """Create a relation service with the test repository."""
    return RelationService(repository=neo4j_repository)


@pytest.fixture
def two_problems(neo4j_repository, sample_evidence_data):
    """Create two problems for relation testing."""
    import uuid as uuid_mod
    problems = []
    for i in range(2):
        problem = Problem(
            id=f"TEST_{uuid_mod.uuid4().hex[:16]}",
            statement=f"Research problem {i} - " + "x" * 20,
            domain="NLP",
            status=ProblemStatus.OPEN,
            evidence=Evidence(**sample_evidence_data),
            extraction_metadata=ExtractionMetadata(
                extraction_model="gpt-4",
                confidence_score=0.9,
            ),
        )
        neo4j_repository.create_problem(problem)
        problems.append(problem)
    return problems


@pytest.fixture
def paper_and_author(neo4j_repository, sample_paper_data, sample_author_data):
    """Create a paper and author for relation testing."""
    import uuid as uuid_mod
    paper = Paper(**sample_paper_data)
    # Override author name with TEST_ prefix for cleanup isolation
    author_data = {**sample_author_data, "name": f"TEST_{uuid_mod.uuid4().hex[:8]}_Author"}
    author = Author(**author_data)
    neo4j_repository.create_paper(paper)
    neo4j_repository.create_author(author)
    return paper, author


class TestProblemRelations:
    """Test problem-to-problem relations."""

    def test_create_extends_relation(self, relation_service, two_problems):
        """Test creating an EXTENDS relation."""
        p1, p2 = two_problems
        relation = relation_service.create_extends_relation(
            from_problem_id=p1.id,
            to_problem_id=p2.id,
            confidence=0.85,
            inferred_by="gpt-4",
        )

        assert relation.from_problem_id == p1.id
        assert relation.to_problem_id == p2.id
        assert relation.relation_type == RelationType.EXTENDS
        assert relation.confidence == 0.85
        assert relation.inferred_by == "gpt-4"

    def test_create_contradicts_relation(self, relation_service, two_problems):
        """Test creating a CONTRADICTS relation."""
        p1, p2 = two_problems
        relation = relation_service.create_contradicts_relation(
            from_problem_id=p1.id,
            to_problem_id=p2.id,
            contradiction_type=ContradictionType.EMPIRICAL,
            confidence=0.75,
        )

        assert relation.relation_type == RelationType.CONTRADICTS
        assert relation.contradiction_type == ContradictionType.EMPIRICAL

    def test_create_depends_on_relation(self, relation_service, two_problems):
        """Test creating a DEPENDS_ON relation."""
        p1, p2 = two_problems
        relation = relation_service.create_depends_on_relation(
            from_problem_id=p1.id,
            to_problem_id=p2.id,
            dependency_type=DependencyType.PREREQUISITE,
            confidence=0.9,
        )

        assert relation.relation_type == RelationType.DEPENDS_ON
        assert relation.dependency_type == DependencyType.PREREQUISITE

    def test_create_reframes_relation(self, relation_service, two_problems):
        """Test creating a REFRAMES relation."""
        p1, p2 = two_problems
        relation = relation_service.create_reframes_relation(
            from_problem_id=p1.id,
            to_problem_id=p2.id,
            confidence=0.8,
        )

        assert relation.relation_type == RelationType.REFRAMES

    def test_create_relation_with_nonexistent_problem_raises_error(
        self, relation_service, two_problems
    ):
        """Test that creating relation with nonexistent problem raises error."""
        p1, _ = two_problems

        with pytest.raises(NotFoundError):
            relation_service.create_relation(
                from_problem_id=p1.id,
                to_problem_id="nonexistent-id",
                relation_type=RelationType.EXTENDS,
            )

    def test_create_duplicate_relation_raises_error(
        self, relation_service, two_problems
    ):
        """Test that creating duplicate relation raises RelationError."""
        p1, p2 = two_problems
        relation_service.create_extends_relation(p1.id, p2.id)

        with pytest.raises(RelationError):
            relation_service.create_extends_relation(p1.id, p2.id)

    def test_get_related_problems_outgoing(self, relation_service, two_problems):
        """Test getting related problems (outgoing direction)."""
        p1, p2 = two_problems
        relation_service.create_extends_relation(p1.id, p2.id)

        related = relation_service.get_related_problems(p1.id, direction="outgoing")

        assert len(related) == 1
        problem, relation = related[0]
        assert problem.id == p2.id
        assert relation.relation_type == RelationType.EXTENDS

    def test_get_related_problems_incoming(self, relation_service, two_problems):
        """Test getting related problems (incoming direction)."""
        p1, p2 = two_problems
        relation_service.create_extends_relation(p1.id, p2.id)

        related = relation_service.get_related_problems(p2.id, direction="incoming")

        assert len(related) == 1
        problem, relation = related[0]
        assert problem.id == p1.id

    def test_get_related_problems_both_directions(self, relation_service, two_problems):
        """Test getting related problems (both directions)."""
        p1, p2 = two_problems
        relation_service.create_extends_relation(p1.id, p2.id)

        # From p1's perspective
        related_p1 = relation_service.get_related_problems(p1.id, direction="both")
        assert len(related_p1) == 1

        # From p2's perspective
        related_p2 = relation_service.get_related_problems(p2.id, direction="both")
        assert len(related_p2) == 1

    def test_get_related_problems_filter_by_type(
        self, relation_service, two_problems, neo4j_repository, sample_evidence_data
    ):
        """Test filtering related problems by relation type."""
        p1, p2 = two_problems

        # Create a third problem
        p3 = Problem(
            statement="Third research problem - " + "x" * 20,
            domain="NLP",
            status=ProblemStatus.OPEN,
            evidence=Evidence(**sample_evidence_data),
            extraction_metadata=ExtractionMetadata(
                extraction_model="gpt-4",
                confidence_score=0.9,
            ),
        )
        neo4j_repository.create_problem(p3)

        # Create different relation types
        relation_service.create_extends_relation(p1.id, p2.id)
        relation_service.create_contradicts_relation(
            p1.id, p3.id, ContradictionType.THEORETICAL
        )

        # Filter by EXTENDS
        extends_related = relation_service.get_related_problems(
            p1.id, relation_type=RelationType.EXTENDS
        )
        assert len(extends_related) == 1
        assert extends_related[0][0].id == p2.id

        # Filter by CONTRADICTS
        contradicts_related = relation_service.get_related_problems(
            p1.id, relation_type=RelationType.CONTRADICTS
        )
        assert len(contradicts_related) == 1
        assert contradicts_related[0][0].id == p3.id


class TestProblemPaperRelations:
    """Test problem-to-paper relations."""

    def test_link_problem_to_paper(
        self, relation_service, neo4j_repository, sample_problem_data, sample_paper_data
    ):
        """Test linking a problem to its source paper."""
        problem = Problem(**sample_problem_data)
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_problem(problem)
        neo4j_repository.create_paper(paper)

        relation = relation_service.link_problem_to_paper(
            problem_id=problem.id,
            paper_doi=paper.doi,
            section="Introduction",
        )

        assert relation.problem_id == problem.id
        assert relation.paper_doi == paper.doi
        assert relation.section == "Introduction"

    def test_link_problem_to_nonexistent_paper_raises_error(
        self, relation_service, neo4j_repository, sample_problem_data
    ):
        """Test that linking to nonexistent paper raises error."""
        problem = Problem(**sample_problem_data)
        neo4j_repository.create_problem(problem)

        with pytest.raises(NotFoundError):
            relation_service.link_problem_to_paper(
                problem_id=problem.id,
                paper_doi="10.9999/nonexistent",
                section="Introduction",
            )

    def test_get_source_paper(
        self, relation_service, neo4j_repository, sample_problem_data, sample_paper_data
    ):
        """Test getting the source paper for a problem."""
        problem = Problem(**sample_problem_data)
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_problem(problem)
        neo4j_repository.create_paper(paper)

        relation_service.link_problem_to_paper(
            problem.id, paper.doi, "Introduction"
        )

        source = relation_service.get_source_paper(problem.id)

        assert source is not None
        assert source["doi"] == paper.doi


class TestPaperAuthorRelations:
    """Test paper-to-author relations."""

    def test_link_paper_to_author(self, relation_service, paper_and_author):
        """Test linking a paper to an author."""
        paper, author = paper_and_author

        relation = relation_service.link_paper_to_author(
            paper_doi=paper.doi,
            author_id=author.id,
            author_position=1,
        )

        assert relation.paper_doi == paper.doi
        assert relation.author_id == author.id
        assert relation.author_position == 1

    def test_link_paper_to_nonexistent_author_raises_error(
        self, relation_service, neo4j_repository, sample_paper_data
    ):
        """Test that linking to nonexistent author raises error."""
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_paper(paper)

        with pytest.raises(NotFoundError):
            relation_service.link_paper_to_author(
                paper_doi=paper.doi,
                author_id="nonexistent-id",
                author_position=1,
            )

    def test_get_paper_authors(
        self, relation_service, neo4j_repository, sample_paper_data
    ):
        """Test getting all authors of a paper."""
        paper = Paper(**sample_paper_data)
        neo4j_repository.create_paper(paper)

        # Create multiple authors
        authors = []
        for i, name in enumerate(["First Author", "Second Author"]):
            author = Author(name=name)
            neo4j_repository.create_author(author)
            authors.append(author)
            relation_service.link_paper_to_author(
                paper.doi, author.id, author_position=i + 1
            )

        paper_authors = relation_service.get_paper_authors(paper.doi)

        assert len(paper_authors) == 2
        assert paper_authors[0]["position"] == 1
        assert paper_authors[1]["position"] == 2
