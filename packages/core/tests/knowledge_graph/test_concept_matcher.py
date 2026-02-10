"""
Unit tests for ConceptMatcher service.

Tests similarity matching, confidence classification, and citation boost logic.
"""

import pytest
import math
from unittest.mock import Mock, MagicMock, patch
from neo4j.exceptions import ServiceUnavailable, TransientError, ClientError

from agentic_kg.knowledge_graph.concept_matcher import (
    ConceptMatcher,
    MatcherError,
)
from agentic_kg.knowledge_graph.models import (
    MatchCandidate,
    MatchConfidence,
    ProblemMention,
)


class TestConceptMatcher:
    """Test ConceptMatcher service."""

    @pytest.fixture
    def mock_repo(self):
        """Mock Neo4j repository."""
        return Mock()

    @pytest.fixture
    def mock_embedder(self):
        """Mock embedding service."""
        embedder = Mock()
        embedder.generate_embedding.return_value = [0.1] * 1536  # 1536-dim vector
        return embedder

    @pytest.fixture
    def matcher(self, mock_repo, mock_embedder):
        """Create ConceptMatcher with mocked dependencies."""
        return ConceptMatcher(
            repository=mock_repo,
            embedding_service=mock_embedder,
        )

    @pytest.fixture
    def sample_mention(self):
        """Create sample ProblemMention."""
        return ProblemMention(
            id="mention-1",
            statement="How can we improve neural network training efficiency?",
            paper_doi="10.1234/test",
            section="Introduction",
            domain="Machine Learning",
            quoted_text="Training large neural networks is computationally expensive.",
            embedding=[0.1] * 1536,
        )

    def test_generate_embedding_success(self, matcher, mock_embedder):
        """Test successful embedding generation."""
        text = "Test problem statement"
        embedding = matcher.generate_embedding(text)

        assert embedding == [0.1] * 1536
        mock_embedder.generate_embedding.assert_called_once_with(text)

    def test_generate_embedding_failure(self, matcher, mock_embedder):
        """Test embedding generation failure."""
        mock_embedder.generate_embedding.side_effect = Exception("API Error")

        with pytest.raises(MatcherError) as exc_info:
            matcher.generate_embedding("Test text")

        assert "Failed to generate embedding" in str(exc_info.value)

    # =============================================================================
    # Confidence Classification Tests - Boundary Values (TEST-MAJ-002)
    # =============================================================================

    def test_classify_confidence_high(self, matcher):
        """Test HIGH confidence classification (>95%)."""
        assert matcher.classify_confidence(0.96) == MatchConfidence.HIGH
        assert matcher.classify_confidence(0.99) == MatchConfidence.HIGH
        assert matcher.classify_confidence(1.0) == MatchConfidence.HIGH

    def test_classify_confidence_exact_threshold_high(self, matcher):
        """Test exact HIGH confidence threshold (0.95)."""
        # At exact threshold, should be HIGH (>= check)
        assert matcher.classify_confidence(0.95) == MatchConfidence.HIGH

    def test_classify_confidence_just_below_high(self, matcher):
        """Test just below HIGH threshold."""
        assert matcher.classify_confidence(0.949) == MatchConfidence.MEDIUM

    def test_classify_confidence_medium(self, matcher):
        """Test MEDIUM confidence classification (80-95%)."""
        assert matcher.classify_confidence(0.85) == MatchConfidence.MEDIUM
        assert matcher.classify_confidence(0.90) == MatchConfidence.MEDIUM
        assert matcher.classify_confidence(0.94) == MatchConfidence.MEDIUM

    def test_classify_confidence_exact_threshold_medium(self, matcher):
        """Test exact MEDIUM confidence threshold (0.80)."""
        # At exact threshold, should be MEDIUM
        assert matcher.classify_confidence(0.80) == MatchConfidence.MEDIUM

    def test_classify_confidence_just_below_medium(self, matcher):
        """Test just below MEDIUM threshold."""
        assert matcher.classify_confidence(0.799) == MatchConfidence.LOW

    def test_classify_confidence_low(self, matcher):
        """Test LOW confidence classification (50-80%)."""
        assert matcher.classify_confidence(0.50) == MatchConfidence.LOW
        assert matcher.classify_confidence(0.65) == MatchConfidence.LOW
        assert matcher.classify_confidence(0.79) == MatchConfidence.LOW

    def test_classify_confidence_exact_threshold_low(self, matcher):
        """Test exact LOW confidence threshold (0.50)."""
        # At exact threshold, should be LOW
        assert matcher.classify_confidence(0.50) == MatchConfidence.LOW

    def test_classify_confidence_just_below_low(self, matcher):
        """Test just below LOW threshold."""
        assert matcher.classify_confidence(0.499) == MatchConfidence.REJECTED

    def test_classify_confidence_rejected(self, matcher):
        """Test REJECTED confidence classification (<50%)."""
        assert matcher.classify_confidence(0.49) == MatchConfidence.REJECTED
        assert matcher.classify_confidence(0.30) == MatchConfidence.REJECTED
        assert matcher.classify_confidence(0.0) == MatchConfidence.REJECTED

    def test_classify_confidence_invalid_negative(self, matcher):
        """Test classification with negative score (invalid input)."""
        # Should classify as REJECTED (below threshold)
        assert matcher.classify_confidence(-0.1) == MatchConfidence.REJECTED
        assert matcher.classify_confidence(-1.0) == MatchConfidence.REJECTED

    def test_classify_confidence_invalid_above_one(self, matcher):
        """Test classification with score >1.0 (invalid input)."""
        # Should classify as HIGH (above all thresholds)
        assert matcher.classify_confidence(1.5) == MatchConfidence.HIGH
        assert matcher.classify_confidence(2.0) == MatchConfidence.HIGH

    def test_classify_confidence_nan(self, matcher):
        """Test classification with NaN value."""
        # NaN comparisons always return False, so will fall through to REJECTED
        result = matcher.classify_confidence(float('nan'))
        # NaN is not >= any threshold, so should be REJECTED
        assert result == MatchConfidence.REJECTED

    def test_classify_confidence_infinity(self, matcher):
        """Test classification with infinity values."""
        assert matcher.classify_confidence(float('inf')) == MatchConfidence.HIGH
        assert matcher.classify_confidence(float('-inf')) == MatchConfidence.REJECTED

    # =============================================================================
    # Neo4j Connection Failure Tests (TEST-CRIT-002)
    # =============================================================================

    def test_find_candidate_concepts_neo4j_connection_failure(
        self, matcher, sample_mention, mock_repo
    ):
        """Test vector search when Neo4j connection fails."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Simulate Neo4j connection failure
        mock_session.execute_read.side_effect = ServiceUnavailable("Connection lost")

        with pytest.raises(MatcherError) as exc_info:
            matcher.find_candidate_concepts(sample_mention)

        assert "Vector similarity search failed" in str(exc_info.value)

    def test_find_candidate_concepts_vector_index_missing(
        self, matcher, sample_mention, mock_repo
    ):
        """Test when vector index is unavailable."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Simulate missing index error
        mock_session.execute_read.side_effect = ClientError(
            "Unable to find index: concept_embedding_idx"
        )

        with pytest.raises(MatcherError) as exc_info:
            matcher.find_candidate_concepts(sample_mention)

        assert "Vector similarity search failed" in str(exc_info.value)

    def test_find_candidate_concepts_query_timeout(
        self, matcher, sample_mention, mock_repo
    ):
        """Test when Neo4j query times out."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Simulate timeout
        mock_session.execute_read.side_effect = TransientError("Query timeout")

        with pytest.raises(MatcherError) as exc_info:
            matcher.find_candidate_concepts(sample_mention)

        assert "Vector similarity search failed" in str(exc_info.value)

    def test_find_candidate_concepts_session_creation_failure(
        self, matcher, sample_mention, mock_repo
    ):
        """Test when Neo4j session cannot be created."""
        # Session creation fails
        mock_repo.session.side_effect = ServiceUnavailable("Cannot connect to database")

        with pytest.raises(MatcherError) as exc_info:
            matcher.find_candidate_concepts(sample_mention)

        assert "Vector similarity search failed" in str(exc_info.value)

    # =============================================================================
    # Citation Boost Error Handling Tests (TEST-MAJ-004)
    # =============================================================================

    def test_citation_boost_query_failure(self, matcher, sample_mention, mock_repo):
        """Test when citation query fails."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Citation query fails
        mock_session.execute_read.side_effect = ServiceUnavailable("Connection lost")

        # Should return 0.0 boost and log warning (not raise exception)
        boost = matcher._calculate_citation_boost(sample_mention, "concept-1")

        assert boost == 0.0

    def test_citation_boost_query_timeout(self, matcher, sample_mention, mock_repo):
        """Test when citation query times out."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Query timeout
        mock_session.execute_read.side_effect = TransientError("Query timeout")

        # Should return 0.0 boost (graceful degradation)
        boost = matcher._calculate_citation_boost(sample_mention, "concept-1")

        assert boost == 0.0

    def test_citation_boost_missing_paper_doi(self, matcher, mock_repo):
        """Test citation boost with missing paper_doi."""
        mention_no_doi = ProblemMention(
            id="mention-2",
            statement="Test problem",
            paper_doi="",  # Empty DOI
            section="Intro",
            domain="AI",
            quoted_text="Test",
            embedding=[0.1] * 1536,
        )

        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session
        mock_session.execute_read.return_value = False

        # Should handle gracefully
        boost = matcher._calculate_citation_boost(mention_no_doi, "concept-1")

        assert boost == 0.0

    def test_citation_boost_malformed_response(self, matcher, sample_mention, mock_repo):
        """Test citation boost with malformed Neo4j response."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Return None instead of expected boolean
        mock_session.execute_read.return_value = None

        # Should handle gracefully and treat as no citations
        boost = matcher._calculate_citation_boost(sample_mention, "concept-1")

        assert boost == 0.0

    def test_citation_boost_exception_in_transaction(self, matcher, sample_mention, mock_repo):
        """Test citation boost when transaction function raises exception."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Transaction function raises exception
        mock_session.execute_read.side_effect = Exception("Unexpected error in transaction")

        # Should catch exception and return 0.0
        boost = matcher._calculate_citation_boost(sample_mention, "concept-1")

        assert boost == 0.0

    # =============================================================================
    # Existing Tests (preserved)
    # =============================================================================

    def test_find_candidate_concepts_success(self, matcher, sample_mention, mock_repo):
        """Test successful candidate concept search."""
        # Mock Neo4j vector search results
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        mock_result = [
            {
                "concept_id": "concept-1",
                "statement": "Improving neural network training",
                "domain": "Machine Learning",
                "mention_count": 5,
                "similarity_score": 0.97,
            },
            {
                "concept_id": "concept-2",
                "statement": "Efficient deep learning",
                "domain": "Machine Learning",
                "mention_count": 3,
                "similarity_score": 0.85,
            },
        ]
        mock_session.execute_read.return_value = mock_result

        candidates = matcher.find_candidate_concepts(sample_mention, top_k=10)

        assert len(candidates) == 2
        assert candidates[0].concept_id == "concept-1"
        assert candidates[0].confidence == MatchConfidence.HIGH  # 0.97 > 0.95
        assert candidates[0].similarity_score == 0.97
        assert candidates[1].confidence == MatchConfidence.MEDIUM  # 0.85 in 0.80-0.95
        assert candidates[0].domain_match is True  # Both "Machine Learning"

    def test_find_candidate_concepts_no_embedding(self, matcher, sample_mention):
        """Test error when mention has no embedding."""
        sample_mention.embedding = None

        with pytest.raises(MatcherError) as exc_info:
            matcher.find_candidate_concepts(sample_mention)

        assert "must have embedding" in str(exc_info.value)

    def test_find_candidate_concepts_with_citation_boost(
        self, matcher, sample_mention, mock_repo
    ):
        """Test candidate search with citation boost enabled."""
        # Mock Neo4j responses
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Mock vector search result
        mock_search_result = [
            {
                "concept_id": "concept-1",
                "statement": "Neural network training",
                "domain": "Machine Learning",
                "mention_count": 5,
                "similarity_score": 0.90,
            }
        ]

        # Mock citation check (return True for has_citations)
        def mock_execute_read(func):
            if "has_citations" in func.__name__ or len(mock_execute_read.call_count_list) > 0:
                return True  # Has citations
            mock_execute_read.call_count_list.append(1)
            return mock_search_result

        mock_execute_read.call_count_list = []
        mock_session.execute_read.side_effect = mock_execute_read

        candidates = matcher.find_candidate_concepts(
            sample_mention, top_k=10, include_citation_boost=True
        )

        assert len(candidates) == 1
        assert candidates[0].citation_boost == 0.20  # Max boost
        assert candidates[0].final_score == 0.90 + 0.20  # Boosted score

    def test_match_mention_to_concept_high_confidence(
        self, matcher, sample_mention, mock_repo
    ):
        """Test matching with HIGH confidence result."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        mock_result = [
            {
                "concept_id": "concept-1",
                "statement": "Neural network training efficiency",
                "domain": "Machine Learning",
                "mention_count": 5,
                "similarity_score": 0.97,
            }
        ]
        mock_session.execute_read.return_value = mock_result

        best_candidate = matcher.match_mention_to_concept(sample_mention)

        assert best_candidate is not None
        assert best_candidate.concept_id == "concept-1"
        assert best_candidate.confidence == MatchConfidence.HIGH

    def test_match_mention_to_concept_rejected(
        self, matcher, sample_mention, mock_repo
    ):
        """Test matching with REJECTED confidence (no suitable match)."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        mock_result = [
            {
                "concept_id": "concept-1",
                "statement": "Completely unrelated topic",
                "domain": "Physics",
                "mention_count": 1,
                "similarity_score": 0.30,  # Below threshold
            }
        ]
        mock_session.execute_read.return_value = mock_result

        best_candidate = matcher.match_mention_to_concept(sample_mention)

        assert best_candidate is None  # Rejected

    def test_match_mention_to_concept_no_candidates(
        self, matcher, sample_mention, mock_repo
    ):
        """Test matching with no candidates found."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session
        mock_session.execute_read.return_value = []  # No results

        best_candidate = matcher.match_mention_to_concept(sample_mention)

        assert best_candidate is None

    def test_citation_boost_calculation(self, matcher, sample_mention, mock_repo):
        """Test citation boost calculation logic."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Mock citation relationship exists
        mock_session.execute_read.return_value = True

        boost = matcher._calculate_citation_boost(sample_mention, "concept-1")

        assert boost == 0.20  # MAX_CITATION_BOOST

    def test_citation_boost_no_relationship(self, matcher, sample_mention, mock_repo):
        """Test citation boost when no citation relationship exists."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Mock no citation relationship
        mock_session.execute_read.return_value = False

        boost = matcher._calculate_citation_boost(sample_mention, "concept-1")

        assert boost == 0.0  # No boost

    def test_domain_matching_detection(self, matcher, sample_mention, mock_repo):
        """Test domain matching is correctly detected."""
        mock_session = MagicMock()
        mock_repo.session.return_value.__enter__.return_value = mock_session

        # Same domain
        mock_result = [
            {
                "concept_id": "concept-1",
                "statement": "Test",
                "domain": "Machine Learning",  # Matches sample_mention.domain
                "mention_count": 1,
                "similarity_score": 0.85,
            }
        ]
        mock_session.execute_read.return_value = mock_result

        candidates = matcher.find_candidate_concepts(sample_mention)

        assert candidates[0].domain_match is True

    def test_final_score_calculation(self):
        """Test MatchCandidate final_score property."""
        candidate = MatchCandidate(
            concept_id="concept-1",
            concept_statement="Test",
            similarity_score=0.85,
            confidence=MatchConfidence.MEDIUM,
            citation_boost=0.15,
        )

        assert candidate.final_score == 1.0  # min(1.0, 0.85 + 0.15)

    def test_final_score_capped_at_one(self):
        """Test final_score is capped at 1.0."""
        candidate = MatchCandidate(
            concept_id="concept-1",
            concept_statement="Test",
            similarity_score=0.95,
            confidence=MatchConfidence.HIGH,
            citation_boost=0.20,
        )

        assert candidate.final_score == 1.0  # Capped, not 1.15
