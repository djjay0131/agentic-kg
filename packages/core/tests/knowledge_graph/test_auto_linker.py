"""
Unit tests for AutoLinker service.

Tests automatic linking, concept creation, and transaction handling.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone
from neo4j.exceptions import ServiceUnavailable, TransientError, ClientError

from agentic_kg.knowledge_graph.auto_linker import (
    AutoLinker,
    AutoLinkerError,
)
from agentic_kg.knowledge_graph.models import (
    MatchCandidate,
    MatchConfidence,
    ProblemConcept,
    ProblemMention,
    ProblemStatus,
)


class TestAutoLinker:
    """Test AutoLinker service."""

    @pytest.fixture
    def mock_repo(self):
        """Mock Neo4j repository with context manager support."""
        repo = Mock()
        # Configure session() to return a MagicMock for context manager support
        repo.session.return_value = MagicMock()
        return repo

    @pytest.fixture
    def mock_matcher(self):
        """Mock ConceptMatcher."""
        return Mock()

    @pytest.fixture
    def mock_embedder(self):
        """Mock EmbeddingService."""
        embedder = Mock()
        embedder.generate_embedding.return_value = [0.1] * 1536
        return embedder

    @pytest.fixture
    def linker(self, mock_repo, mock_matcher, mock_embedder):
        """Create AutoLinker with mocked dependencies."""
        return AutoLinker(
            repository=mock_repo,
            concept_matcher=mock_matcher,
            embedding_service=mock_embedder,
        )

    @pytest.fixture
    def sample_mention(self):
        """Create sample ProblemMention."""
        return ProblemMention(
            id="mention-1",
            statement="How can we improve neural network training?",
            paper_doi="10.1234/test",
            section="Introduction",
            domain="Machine Learning",
            quoted_text="Training is expensive.",
            embedding=[0.1] * 1536,
        )

    @pytest.fixture
    def high_confidence_candidate(self):
        """Create HIGH confidence MatchCandidate."""
        return MatchCandidate(
            concept_id="concept-1",
            concept_statement="Improving neural network training",
            similarity_score=0.97,
            confidence=MatchConfidence.HIGH,
        )

    @pytest.fixture
    def medium_confidence_candidate(self):
        """Create MEDIUM confidence MatchCandidate."""
        return MatchCandidate(
            concept_id="concept-2",
            concept_statement="Neural networks",
            similarity_score=0.85,
            confidence=MatchConfidence.MEDIUM,
        )

    def test_auto_link_high_confidence_success(
        self, linker, sample_mention, high_confidence_candidate, mock_matcher, mock_repo
    ):
        """Test successful auto-linking with HIGH confidence."""
        # Mock matcher returns HIGH confidence candidate
        mock_matcher.match_mention_to_concept.return_value = high_confidence_candidate

        # Mock Neo4j transaction
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        mock_concept_data = {
            "id": "concept-1",
            "canonical_statement": "Improving neural network training",
            "domain": "Machine Learning",
            "status": "open",
            "mention_count": 6,
            "paper_count": 3,
        }
        mock_session.execute_write.return_value = mock_concept_data

        # Execute
        concept = linker.auto_link_high_confidence(sample_mention, trace_id="test-trace")

        # Verify
        assert concept is not None
        assert concept.id == "concept-1"
        assert concept.mention_count == 6
        mock_matcher.match_mention_to_concept.assert_called_once()
        mock_session.execute_write.assert_called_once()

    def test_auto_link_medium_confidence_returns_none(
        self, linker, sample_mention, medium_confidence_candidate, mock_matcher
    ):
        """Test auto-linking returns None for MEDIUM confidence (not HIGH)."""
        # Mock matcher returns MEDIUM confidence
        mock_matcher.match_mention_to_concept.return_value = medium_confidence_candidate

        concept = linker.auto_link_high_confidence(sample_mention, trace_id="test-trace")

        assert concept is None  # Should not link MEDIUM confidence

    def test_auto_link_no_match_returns_none(
        self, linker, sample_mention, mock_matcher
    ):
        """Test auto-linking returns None when no match found."""
        # Mock matcher returns None (no match)
        mock_matcher.match_mention_to_concept.return_value = None

        concept = linker.auto_link_high_confidence(sample_mention, trace_id="test-trace")

        assert concept is None

    def test_auto_link_matcher_failure(
        self, linker, sample_mention, mock_matcher
    ):
        """Test auto-linking handles matcher failure."""
        # Mock matcher raises exception
        mock_matcher.match_mention_to_concept.side_effect = Exception("Matcher error")

        with pytest.raises(AutoLinkerError) as exc_info:
            linker.auto_link_high_confidence(sample_mention, trace_id="test-trace")

        assert "Failed to find matching concept" in str(exc_info.value)

    def test_auto_link_transaction_failure(
        self, linker, sample_mention, high_confidence_candidate, mock_matcher, mock_repo
    ):
        """Test auto-linking handles transaction failure."""
        # Mock matcher returns HIGH confidence
        mock_matcher.match_mention_to_concept.return_value = high_confidence_candidate

        # Mock transaction failure
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session
        mock_session.execute_write.side_effect = Exception("Transaction failed")

        with pytest.raises(AutoLinkerError) as exc_info:
            linker.auto_link_high_confidence(sample_mention, trace_id="test-trace")

        assert "Auto-linking failed" in str(exc_info.value)

    # =============================================================================
    # Relationship Creation Error Tests (TEST-MAJ-003)
    # =============================================================================

    def test_create_relationship_mention_not_found(
        self, linker, sample_mention, high_confidence_candidate, mock_matcher, mock_repo
    ):
        """Test when mention node not found in Neo4j during relationship creation."""
        mock_matcher.match_mention_to_concept.return_value = high_confidence_candidate

        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Query returns None (mention not found)
        mock_session.execute_write.return_value = None

        with pytest.raises(AutoLinkerError) as exc_info:
            linker.auto_link_high_confidence(sample_mention, trace_id="test-trace")

        assert "Auto-linking failed" in str(exc_info.value)

    def test_create_relationship_concept_not_found(
        self, linker, sample_mention, high_confidence_candidate, mock_matcher, mock_repo
    ):
        """Test when concept node not found in Neo4j during relationship creation."""
        mock_matcher.match_mention_to_concept.return_value = high_confidence_candidate

        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Simulate concept not found (execute_write raises exception)
        mock_session.execute_write.side_effect = ClientError(
            "Node not found: ProblemConcept {id: 'concept-1'}"
        )

        with pytest.raises(AutoLinkerError) as exc_info:
            linker.auto_link_high_confidence(sample_mention, trace_id="test-trace")

        assert "Auto-linking failed" in str(exc_info.value)

    def test_create_relationship_transaction_rollback(
        self, linker, sample_mention, high_confidence_candidate, mock_matcher, mock_repo
    ):
        """Test transaction rollback on relationship creation failure."""
        mock_matcher.match_mention_to_concept.return_value = high_confidence_candidate

        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Simulate transaction rollback
        mock_session.execute_write.side_effect = TransientError("Transaction rolled back")

        with pytest.raises(AutoLinkerError) as exc_info:
            linker.auto_link_high_confidence(sample_mention, trace_id="test-trace")

        assert "Auto-linking failed" in str(exc_info.value)

    def test_create_relationship_constraint_violation(
        self, linker, sample_mention, high_confidence_candidate, mock_matcher, mock_repo
    ):
        """Test handling of constraint violation during relationship creation."""
        mock_matcher.match_mention_to_concept.return_value = high_confidence_candidate

        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Simulate constraint violation
        mock_session.execute_write.side_effect = ClientError(
            "Constraint violation: INSTANCE_OF relationship already exists"
        )

        with pytest.raises(AutoLinkerError) as exc_info:
            linker.auto_link_high_confidence(sample_mention, trace_id="test-trace")

        assert "Auto-linking failed" in str(exc_info.value)

    def test_create_relationship_neo4j_unavailable(
        self, linker, sample_mention, high_confidence_candidate, mock_matcher, mock_repo
    ):
        """Test handling of Neo4j service unavailable during relationship creation."""
        mock_matcher.match_mention_to_concept.return_value = high_confidence_candidate

        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Simulate Neo4j unavailable
        mock_session.execute_write.side_effect = ServiceUnavailable("Database not reachable")

        with pytest.raises(AutoLinkerError) as exc_info:
            linker.auto_link_high_confidence(sample_mention, trace_id="test-trace")

        assert "Auto-linking failed" in str(exc_info.value)

    # =============================================================================
    # Concept Creation Tests
    # =============================================================================

    def test_create_new_concept_success(
        self, linker, sample_mention, mock_embedder, mock_repo
    ):
        """Test successful new concept creation."""
        # Mock embedding generation
        mock_embedder.generate_embedding.return_value = [0.2] * 1536

        # Mock Neo4j transaction
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Execute
        concept = linker.create_new_concept(sample_mention, trace_id="test-trace")

        # Verify concept properties
        assert concept.canonical_statement == sample_mention.statement
        assert concept.domain == sample_mention.domain
        assert concept.status == ProblemStatus.OPEN
        assert concept.mention_count == 1
        assert concept.paper_count == 1
        assert concept.synthesis_method == "first_mention"
        assert concept.synthesized_by == "auto_linker"
        assert concept.human_edited is False
        assert len(concept.embedding) == 1536

        # Verify Neo4j interaction
        mock_embedder.generate_embedding.assert_called_once_with(sample_mention.statement)
        mock_session.execute_write.assert_called_once()

    def test_create_new_concept_embedding_failure(
        self, linker, sample_mention, mock_embedder
    ):
        """Test concept creation handles embedding failure."""
        # Mock embedding failure
        mock_embedder.generate_embedding.side_effect = Exception("Embedding error")

        with pytest.raises(AutoLinkerError) as exc_info:
            linker.create_new_concept(sample_mention, trace_id="test-trace")

        assert "Failed to generate concept embedding" in str(exc_info.value)

    def test_create_new_concept_transaction_failure(
        self, linker, sample_mention, mock_embedder, mock_repo
    ):
        """Test concept creation handles transaction failure."""
        # Mock embedding success
        mock_embedder.generate_embedding.return_value = [0.2] * 1536

        # Mock transaction failure
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session
        mock_session.execute_write.side_effect = Exception("Transaction failed")

        with pytest.raises(AutoLinkerError) as exc_info:
            linker.create_new_concept(sample_mention, trace_id="test-trace")

        assert "Concept creation failed" in str(exc_info.value)

    def test_create_new_concept_neo4j_unavailable(
        self, linker, sample_mention, mock_embedder, mock_repo
    ):
        """Test concept creation when Neo4j is unavailable."""
        mock_embedder.generate_embedding.return_value = [0.2] * 1536

        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session
        mock_session.execute_write.side_effect = ServiceUnavailable("Database down")

        with pytest.raises(AutoLinkerError) as exc_info:
            linker.create_new_concept(sample_mention, trace_id="test-trace")

        assert "Concept creation failed" in str(exc_info.value)

    def test_create_new_concept_preserves_metadata(
        self, linker, mock_embedder, mock_repo
    ):
        """Test new concept preserves all metadata from mention."""
        from agentic_kg.knowledge_graph.models import (
            Assumption,
            Constraint,
            Dataset,
            ConstraintType,
        )

        # Create mention with rich metadata
        mention = ProblemMention(
            id="mention-1",
            statement="How to test problem metadata preservation effectively?",
            paper_doi="10.1234/test",
            section="Methods",
            domain="AI",
            quoted_text="Test quote with sufficient length for validation",
            embedding=[0.1] * 1536,
            assumptions=[Assumption(text="Assumption 1", implicit=False, confidence=0.9)],
            constraints=[Constraint(text="Constraint 1", type=ConstraintType.COMPUTATIONAL, confidence=0.8)],
            datasets=[Dataset(name="Dataset 1", available=True)],
        )

        # Mock services
        mock_embedder.generate_embedding.return_value = [0.2] * 1536
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Execute
        concept = linker.create_new_concept(mention, trace_id="test-trace")

        # Verify metadata preserved
        assert len(concept.assumptions) == 1
        assert concept.assumptions[0].text == "Assumption 1"
        assert len(concept.constraints) == 1
        assert concept.constraints[0].text == "Constraint 1"
        assert len(concept.datasets) == 1
        assert concept.datasets[0].name == "Dataset 1"

    def test_trace_id_propagation(
        self, linker, sample_mention, high_confidence_candidate, mock_matcher, mock_repo
    ):
        """Test trace ID is propagated through operations."""
        trace_id = "custom-trace-123"
        mock_matcher.match_mention_to_concept.return_value = high_confidence_candidate

        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session
        mock_concept_data = {
            "id": "concept-1",
            "canonical_statement": "How to test trace ID propagation in auto-linking?",
            "domain": "AI",
            "status": "open",
            "mention_count": 1,
            "paper_count": 1,
        }
        mock_session.execute_write.return_value = mock_concept_data

        # Execute with custom trace ID
        linker.auto_link_high_confidence(sample_mention, trace_id=trace_id)

        # Verify trace ID was passed to matcher
        mock_matcher.match_mention_to_concept.assert_called_once()

    def test_auto_link_generates_trace_id_if_not_provided(
        self, linker, sample_mention, medium_confidence_candidate, mock_matcher
    ):
        """Test trace ID is auto-generated if not provided."""
        mock_matcher.match_mention_to_concept.return_value = medium_confidence_candidate

        # Call without trace_id
        concept = linker.auto_link_high_confidence(sample_mention)  # trace_id=None

        # Should complete without error (trace_id generated internally)
        assert concept is None  # MEDIUM confidence returns None

    def test_create_new_concept_metadata_fields(
        self, linker, sample_mention, mock_embedder, mock_repo
    ):
        """Test new concept has correct metadata fields."""
        mock_embedder.generate_embedding.return_value = [0.2] * 1536
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        concept = linker.create_new_concept(sample_mention, trace_id="test-trace")

        # Verify metadata fields
        assert concept.synthesis_method == "first_mention"
        assert concept.synthesized_by == "auto_linker"
        assert concept.human_edited is False
        assert concept.version == 1
        assert concept.mention_count == 1
        assert concept.paper_count == 1
        assert isinstance(concept.synthesized_at, datetime)
