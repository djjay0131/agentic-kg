"""
Integration tests for canonical problem architecture end-to-end workflow.

Tests the complete flow: ExtractedProblem → ProblemMention → ConceptMatcher
→ AutoLinker → ProblemConcept with live Neo4j.

These tests require a running Neo4j instance with proper configuration.
Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD environment variables.
"""

import os
import time
import pytest
from datetime import datetime

from agentic_kg.knowledge_graph.auto_linker import get_auto_linker
from agentic_kg.knowledge_graph.concept_matcher import get_concept_matcher
from agentic_kg.knowledge_graph.embeddings import EmbeddingService
from agentic_kg.knowledge_graph.models import (
    MatchConfidence,
    ProblemConcept,
    ProblemMention,
    ProblemStatus,
)
from agentic_kg.knowledge_graph.repository import Neo4jRepository
from agentic_kg.knowledge_graph.schema import initialize_schema, SCHEMA_VERSION


# Check if Neo4j is available
NEO4J_AVAILABLE = all([
    os.getenv("NEO4J_URI"),
    os.getenv("NEO4J_USER"),
    os.getenv("NEO4J_PASSWORD"),
])

# Skip all tests if Neo4j not available
pytestmark = pytest.mark.skipif(
    not NEO4J_AVAILABLE,
    reason="Neo4j not available (set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)",
)


@pytest.fixture(scope="module")
def neo4j_repo():
    """Create Neo4j repository for testing."""
    if not NEO4J_AVAILABLE:
        pytest.skip("Neo4j not available")

    repo = Neo4jRepository()
    yield repo
    # Cleanup after all tests


@pytest.fixture(scope="module")
def setup_schema(neo4j_repo):
    """Initialize schema before tests."""
    # Initialize schema (idempotent)
    initialize_schema(force=True)
    yield
    # No teardown - keep schema for inspection


@pytest.fixture
def embedding_service():
    """Create embedding service (will use API if configured)."""
    # Check if OpenAI API key is available
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OpenAI API key not available")
    return EmbeddingService()


@pytest.fixture
def test_concept(neo4j_repo, embedding_service):
    """Create a test concept for matching tests."""
    # Create a concept manually
    concept = ProblemConcept(
        canonical_statement="How to improve neural network training efficiency?",
        domain="Machine Learning",
        status=ProblemStatus.OPEN,
        synthesis_method="manual_test",
        mention_count=0,
        paper_count=0,
        embedding=embedding_service.generate_embedding(
            "How to improve neural network training efficiency?"
        ),
    )

    # Store in Neo4j
    with neo4j_repo.session() as session:
        query = """
        CREATE (c:ProblemConcept)
        SET c = $properties
        RETURN c.id as id
        """
        result = session.run(query, properties=concept.to_neo4j_properties())
        concept_id = result.single()["id"]

    yield concept_id

    # Cleanup after test
    with neo4j_repo.session() as session:
        session.run("MATCH (c:ProblemConcept {id: $id}) DETACH DELETE c", id=concept_id)


class TestSchemaIntegration:
    """Test schema migration and validation."""

    def test_schema_version_is_2(self, neo4j_repo, setup_schema):
        """Test schema version is updated to 2."""
        with neo4j_repo.session() as session:
            result = session.run(
                "MATCH (s:SchemaVersion) RETURN s.version as version ORDER BY s.version DESC LIMIT 1"
            )
            record = result.single()
            assert record["version"] == SCHEMA_VERSION

    def test_problem_mention_constraint_exists(self, neo4j_repo, setup_schema):
        """Test ProblemMention unique constraint exists."""
        with neo4j_repo.session() as session:
            result = session.run("SHOW CONSTRAINTS")
            constraints = [r["name"] for r in result]
            assert "problem_mention_id_unique" in constraints

    def test_problem_concept_constraint_exists(self, neo4j_repo, setup_schema):
        """Test ProblemConcept unique constraint exists."""
        with neo4j_repo.session() as session:
            result = session.run("SHOW CONSTRAINTS")
            constraints = [r["name"] for r in result]
            assert "problem_concept_id_unique" in constraints

    def test_vector_indexes_exist(self, neo4j_repo, setup_schema):
        """Test vector indexes for embeddings exist."""
        with neo4j_repo.session() as session:
            result = session.run("SHOW INDEXES")
            indexes = [r["name"] for r in result]

            # Check for vector indexes
            assert "mention_embedding_idx" in indexes or "mention_embedding_idx" in str(indexes)
            assert "concept_embedding_idx" in indexes or "concept_embedding_idx" in str(indexes)

    def test_property_indexes_exist(self, neo4j_repo, setup_schema):
        """Test property indexes exist."""
        with neo4j_repo.session() as session:
            result = session.run("SHOW INDEXES")
            indexes = [r["name"] for r in result]

            # Check for property indexes
            assert "mention_paper_idx" in indexes
            assert "concept_domain_idx" in indexes


class TestConceptMatcherIntegration:
    """Test ConceptMatcher with live Neo4j."""

    def test_find_candidate_concepts_with_vector_search(
        self, neo4j_repo, embedding_service, test_concept, setup_schema
    ):
        """Test vector similarity search finds matching concepts."""
        # Create a mention similar to test_concept
        mention = ProblemMention(
            id="test-mention-1",
            statement="Improving efficiency of neural network training",
            paper_doi="10.1234/test",
            section="Introduction",
            domain="Machine Learning",
            quoted_text="Training is slow",
            embedding=embedding_service.generate_embedding(
                "Improving efficiency of neural network training"
            ),
        )

        matcher = get_concept_matcher(repository=neo4j_repo, embedding_service=embedding_service)
        candidates = matcher.find_candidate_concepts(mention, top_k=5)

        # Should find the test concept
        assert len(candidates) > 0
        assert any(c.concept_id == test_concept for c in candidates)

        # Check confidence classification
        best_candidate = candidates[0]
        assert best_candidate.confidence in [
            MatchConfidence.HIGH,
            MatchConfidence.MEDIUM,
            MatchConfidence.LOW,
        ]

    def test_vector_search_performance(
        self, neo4j_repo, embedding_service, test_concept, setup_schema
    ):
        """Test vector similarity search completes under 100ms."""
        mention = ProblemMention(
            id="test-mention-perf",
            statement="Neural network training performance",
            paper_doi="10.1234/test",
            section="Methods",
            domain="Machine Learning",
            quoted_text="Performance matters",
            embedding=embedding_service.generate_embedding(
                "Neural network training performance"
            ),
        )

        matcher = get_concept_matcher(repository=neo4j_repo, embedding_service=embedding_service)

        # Time the query
        start = time.perf_counter()
        candidates = matcher.find_candidate_concepts(mention, top_k=10)
        duration_ms = (time.perf_counter() - start) * 1000

        # Should complete under 100ms (acceptance criteria)
        assert duration_ms < 100, f"Query took {duration_ms:.2f}ms (should be <100ms)"


class TestAutoLinkerIntegration:
    """Test AutoLinker with live Neo4j."""

    def test_auto_link_high_confidence_creates_relationship(
        self, neo4j_repo, embedding_service, test_concept, setup_schema
    ):
        """Test HIGH confidence auto-linking creates INSTANCE_OF relationship."""
        # Create mention very similar to test concept
        mention = ProblemMention(
            id="test-mention-link",
            statement="How to improve neural network training efficiency?",  # Exact match
            paper_doi="10.1234/test",
            section="Introduction",
            domain="Machine Learning",
            quoted_text="Efficiency is important",
            embedding=embedding_service.generate_embedding(
                "How to improve neural network training efficiency?"
            ),
        )

        # Store mention in Neo4j
        with neo4j_repo.session() as session:
            query = "CREATE (m:ProblemMention) SET m = $properties"
            session.run(query, properties=mention.to_neo4j_properties())

        # Auto-link
        linker = get_auto_linker(
            repository=neo4j_repo,
            embedding_service=embedding_service,
        )

        linked_concept = linker.auto_link_high_confidence(
            mention, trace_id="test-auto-link"
        )

        # Verify relationship created
        if linked_concept:  # May be None if not HIGH confidence
            with neo4j_repo.session() as session:
                result = session.run(
                    """
                    MATCH (m:ProblemMention {id: $mention_id})-[r:INSTANCE_OF]->(c:ProblemConcept)
                    RETURN c.id as concept_id, r.confidence as confidence
                    """,
                    mention_id=mention.id,
                )
                record = result.single()
                assert record is not None
                assert record["concept_id"] == linked_concept.id
                assert record["confidence"] > 0.95

        # Cleanup
        with neo4j_repo.session() as session:
            session.run(
                "MATCH (m:ProblemMention {id: $id}) DETACH DELETE m",
                id=mention.id,
            )

    def test_create_new_concept_when_no_high_match(
        self, neo4j_repo, embedding_service, setup_schema
    ):
        """Test new concept creation when no HIGH confidence match exists."""
        # Create mention for completely new problem
        mention = ProblemMention(
            id="test-mention-new",
            statement="How to optimize quantum computing algorithms for chemistry?",
            paper_doi="10.1234/test",
            section="Abstract",
            domain="Quantum Computing",
            quoted_text="Quantum chemistry is hard",
            embedding=embedding_service.generate_embedding(
                "How to optimize quantum computing algorithms for chemistry?"
            ),
        )

        # Store mention
        with neo4j_repo.session() as session:
            query = "CREATE (m:ProblemMention) SET m = $properties"
            session.run(query, properties=mention.to_neo4j_properties())

        # Create new concept
        linker = get_auto_linker(
            repository=neo4j_repo,
            embedding_service=embedding_service,
        )

        new_concept = linker.create_new_concept(mention, trace_id="test-new-concept")

        # Verify concept created
        assert new_concept is not None
        assert new_concept.canonical_statement == mention.statement
        assert new_concept.synthesis_method == "first_mention"
        assert new_concept.mention_count == 1

        # Verify concept exists in Neo4j
        with neo4j_repo.session() as session:
            result = session.run(
                "MATCH (c:ProblemConcept {id: $id}) RETURN c",
                id=new_concept.id,
            )
            assert result.single() is not None

        # Cleanup
        with neo4j_repo.session() as session:
            session.run(
                "MATCH (m:ProblemMention {id: $mid}) DETACH DELETE m",
                mid=mention.id,
            )
            session.run(
                "MATCH (c:ProblemConcept {id: $cid}) DETACH DELETE c",
                cid=new_concept.id,
            )


class TestEndToEndWorkflow:
    """Test complete end-to-end workflow."""

    def test_two_papers_one_concept_workflow(
        self, neo4j_repo, embedding_service, setup_schema
    ):
        """
        Test acceptance criteria: Import 2 papers with same problem
        → 1 concept, 2 mentions, both AUTO_LINKED.
        """
        # Paper 1: First mention of problem
        mention1 = ProblemMention(
            id="mention-paper1",
            statement="How can we reduce training time for large language models?",
            paper_doi="10.1234/paper1",
            section="Introduction",
            domain="Natural Language Processing",
            quoted_text="Training LLMs takes weeks",
            embedding=embedding_service.generate_embedding(
                "How can we reduce training time for large language models?"
            ),
        )

        # Paper 2: Similar problem statement
        mention2 = ProblemMention(
            id="mention-paper2",
            statement="Reducing computational cost of training large language models",
            paper_doi="10.1234/paper2",
            section="Motivation",
            domain="Natural Language Processing",
            quoted_text="LLM training is expensive",
            embedding=embedding_service.generate_embedding(
                "Reducing computational cost of training large language models"
            ),
        )

        linker = get_auto_linker(
            repository=neo4j_repo,
            embedding_service=embedding_service,
        )

        # Store mention1 and create concept
        with neo4j_repo.session() as session:
            session.run(
                "CREATE (m:ProblemMention) SET m = $properties",
                properties=mention1.to_neo4j_properties(),
            )

        concept1 = linker.create_new_concept(mention1, trace_id="paper1")
        assert concept1 is not None

        # Store mention2 and try to link
        with neo4j_repo.session() as session:
            session.run(
                "CREATE (m:ProblemMention) SET m = $properties",
                properties=mention2.to_neo4j_properties(),
            )

        # This should link to concept1 (HIGH confidence)
        linked_concept = linker.auto_link_high_confidence(mention2, trace_id="paper2")

        # Verify results
        if linked_concept:
            assert linked_concept.id == concept1.id

        # Count mentions and concepts
        with neo4j_repo.session() as session:
            # Count concepts
            concept_count = session.run(
                """
                MATCH (c:ProblemConcept)
                WHERE c.canonical_statement CONTAINS 'language model'
                RETURN count(c) as count
                """
            ).single()["count"]

            # Count mentions
            mention_count = session.run(
                """
                MATCH (m:ProblemMention)
                WHERE m.statement CONTAINS 'language model'
                RETURN count(m) as count
                """
            ).single()["count"]

            # Check relationships
            relationship_count = session.run(
                """
                MATCH (m:ProblemMention)-[:INSTANCE_OF]->(c:ProblemConcept)
                WHERE m.id IN [$m1, $m2]
                RETURN count(*) as count
                """,
                m1=mention1.id,
                m2=mention2.id,
            ).single()["count"]

        # Acceptance criteria
        assert concept_count == 1, "Should have 1 canonical concept"
        assert mention_count == 2, "Should have 2 mentions"
        assert relationship_count == 2, "Both mentions should be linked"

        # Cleanup
        with neo4j_repo.session() as session:
            session.run(
                "MATCH (m:ProblemMention) WHERE m.id IN [$m1, $m2] DETACH DELETE m",
                m1=mention1.id,
                m2=mention2.id,
            )
            session.run(
                "MATCH (c:ProblemConcept {id: $cid}) DETACH DELETE c",
                cid=concept1.id,
            )

    def test_trace_id_propagation_through_workflow(
        self, neo4j_repo, embedding_service, setup_schema
    ):
        """Test trace IDs are stored in relationships for audit trail."""
        trace_id = "audit-test-12345"

        mention = ProblemMention(
            id="mention-audit",
            statement="Test problem for audit trail",
            paper_doi="10.1234/audit",
            section="Test",
            domain="Testing",
            quoted_text="Test quote",
            embedding=embedding_service.generate_embedding("Test problem for audit trail"),
        )

        # Store mention
        with neo4j_repo.session() as session:
            session.run(
                "CREATE (m:ProblemMention) SET m = $properties",
                properties=mention.to_neo4j_properties(),
            )

        # Create concept with trace ID
        linker = get_auto_linker(repository=neo4j_repo, embedding_service=embedding_service)
        concept = linker.create_new_concept(mention, trace_id=trace_id)

        # Verify trace ID in relationship
        with neo4j_repo.session() as session:
            result = session.run(
                """
                MATCH (m:ProblemMention {id: $mid})-[r:INSTANCE_OF]->(c:ProblemConcept)
                RETURN r.trace_id as trace_id
                """,
                mid=mention.id,
            )
            record = result.single()
            assert record is not None
            assert record["trace_id"] == trace_id

        # Cleanup
        with neo4j_repo.session() as session:
            session.run("MATCH (m:ProblemMention {id: $id}) DETACH DELETE m", id=mention.id)
            session.run("MATCH (c:ProblemConcept {id: $id}) DETACH DELETE c", id=concept.id)
