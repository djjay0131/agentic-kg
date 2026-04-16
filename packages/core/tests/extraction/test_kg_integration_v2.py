"""
Unit tests for Knowledge Graph Integration V2 with agent workflows.

Tests Phase 2 functionality:
- MEDIUM/LOW confidence routing to agent workflows
- Human review queue escalation
- Concept refinement after linking
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from agentic_kg.extraction.kg_integration_v2 import (
    KGIntegratorV2,
    MentionIntegrationResult,
    IntegrationResultV2,
)
from agentic_kg.extraction.schemas import (
    ExtractedProblem,
    ExtractedAssumption,
    ExtractedConstraint,
    ExtractedDataset,
    ExtractedMetric,
    ExtractedBaseline,
)
from agentic_kg.knowledge_graph.models import (
    MatchCandidate,
    MatchConfidence,
    ProblemConcept,
    ProblemMention,
    ProblemStatus,
)


# =============================================================================
# Fixtures
# =============================================================================


def make_extracted_problem(**kwargs) -> ExtractedProblem:
    """Create an ExtractedProblem for testing."""
    return ExtractedProblem(
        statement=kwargs.get(
            "statement", "How to improve transformer efficiency for long sequences?"
        ),
        domain=kwargs.get("domain", "NLP"),
        quoted_text=kwargs.get("quoted_text", "The challenge of handling long sequences requires new approaches to attention."),
        assumptions=[
            ExtractedAssumption(text="Transformers are the standard architecture", implicit=False, confidence=0.9)
        ],
        constraints=[ExtractedConstraint(text="Memory limited for long sequences", constraint_type="computational", confidence=0.8)],
        datasets=[ExtractedDataset(name="WikiText-103", url=None, available=True)],
        metrics=[ExtractedMetric(name="Perplexity", description="Lower is better", baseline_value=None)],
        baselines=[ExtractedBaseline(name="Transformer-XL", paper_reference=None, performance_notes="ppl=24.0")],
        confidence=0.9,
    )


def make_mock_concept(**kwargs) -> ProblemConcept:
    """Create a mock ProblemConcept."""
    return ProblemConcept(
        id=kwargs.get("id", "concept-001"),
        canonical_statement=kwargs.get(
            "canonical_statement", "How to improve transformer efficiency?"
        ),
        domain=kwargs.get("domain", "NLP"),
        status=ProblemStatus.OPEN,
        mention_count=kwargs.get("mention_count", 5),
        paper_count=1,
    )


def make_mock_candidate(confidence: MatchConfidence, **kwargs) -> MatchCandidate:
    """Create a mock MatchCandidate with specified confidence."""
    score_map = {
        MatchConfidence.HIGH: 0.97,
        MatchConfidence.MEDIUM: 0.87,
        MatchConfidence.LOW: 0.65,
        MatchConfidence.REJECTED: 0.30,
    }
    return MatchCandidate(
        concept_id=kwargs.get("concept_id", "concept-001"),
        concept_statement=kwargs.get("statement", "How to improve transformer efficiency?"),
        similarity_score=kwargs.get("score", score_map.get(confidence, 0.5)),
        confidence=confidence,
    )


@pytest.fixture
def mock_repo():
    """Create mock repository."""
    repo = MagicMock()
    return repo


@pytest.fixture
def mock_embedder():
    """Create mock embedding service."""
    embedder = MagicMock()
    embedder.generate_embedding.return_value = [0.1] * 1536
    return embedder


@pytest.fixture
def mock_matcher():
    """Create mock concept matcher."""
    return MagicMock()


@pytest.fixture
def mock_linker():
    """Create mock auto-linker."""
    linker = MagicMock()
    return linker


# =============================================================================
# HIGH Confidence Tests (Auto-linking)
# =============================================================================


class TestHighConfidenceRouting:
    """Tests for HIGH confidence auto-linking."""

    def test_high_confidence_auto_links(
        self, mock_repo, mock_embedder, mock_matcher, mock_linker
    ):
        """HIGH confidence match auto-links to concept."""
        # Setup
        candidate = make_mock_candidate(MatchConfidence.HIGH)
        concept = make_mock_concept()

        mock_matcher.match_mention_to_concept.return_value = candidate
        mock_linker.auto_link_high_confidence.return_value = concept

        integrator = KGIntegratorV2(
            repository=mock_repo,
            embedding_service=mock_embedder,
            concept_matcher=mock_matcher,
            auto_linker=mock_linker,
            enable_agent_workflow=False,  # Simplify test
            enable_concept_refinement=False,
        )

        problem = make_extracted_problem()

        # Execute
        result = integrator.integrate_extracted_problems(
            extracted_problems=[problem],
            paper_doi="10.1234/test.2024",
        )

        # Assert
        assert result.mentions_created == 1
        assert result.mentions_linked == 1
        assert result.mentions_new_concepts == 0

        mention_result = result.mention_results[0]
        assert mention_result.auto_linked is True
        assert mention_result.concept_id == "concept-001"
        assert mention_result.match_confidence == "high"

    def test_high_confidence_triggers_refinement(
        self, mock_repo, mock_embedder, mock_matcher, mock_linker
    ):
        """HIGH confidence linking triggers concept refinement."""
        # Setup
        candidate = make_mock_candidate(MatchConfidence.HIGH)
        concept = make_mock_concept()

        mock_matcher.match_mention_to_concept.return_value = candidate
        mock_linker.auto_link_high_confidence.return_value = concept

        mock_refinement = MagicMock()
        mock_refinement.check_and_refine = AsyncMock(return_value=concept)

        integrator = KGIntegratorV2(
            repository=mock_repo,
            embedding_service=mock_embedder,
            concept_matcher=mock_matcher,
            auto_linker=mock_linker,
            refinement_service=mock_refinement,
            enable_agent_workflow=False,
            enable_concept_refinement=True,
        )

        problem = make_extracted_problem()

        # Execute
        result = integrator.integrate_extracted_problems(
            extracted_problems=[problem],
            paper_doi="10.1234/test.2024",
        )

        # Assert
        assert result.mention_results[0].concept_refined is True
        mock_refinement.check_and_refine.assert_called_once()


# =============================================================================
# NO_MATCH Tests (New Concept Creation)
# =============================================================================


class TestNoMatchRouting:
    """Tests for NO_MATCH case creating new concepts."""

    def test_no_match_creates_new_concept(
        self, mock_repo, mock_embedder, mock_matcher, mock_linker
    ):
        """NO_MATCH case creates new concept."""
        # Setup
        mock_matcher.match_mention_to_concept.return_value = None
        concept = make_mock_concept(id="new-concept")
        mock_linker.create_new_concept.return_value = concept

        integrator = KGIntegratorV2(
            repository=mock_repo,
            embedding_service=mock_embedder,
            concept_matcher=mock_matcher,
            auto_linker=mock_linker,
            enable_agent_workflow=False,
            enable_concept_refinement=False,
        )

        problem = make_extracted_problem()

        # Execute
        result = integrator.integrate_extracted_problems(
            extracted_problems=[problem],
            paper_doi="10.1234/test.2024",
        )

        # Assert
        assert result.mentions_new_concepts == 1
        assert result.mention_results[0].is_new_concept is True
        assert result.mention_results[0].concept_id == "new-concept"
        mock_linker.create_new_concept.assert_called_once()


# =============================================================================
# MEDIUM Confidence Tests (Agent Workflow)
# =============================================================================


class TestMediumConfidenceRouting:
    """Tests for MEDIUM confidence agent workflow routing."""

    def test_medium_confidence_routes_to_workflow(
        self, mock_repo, mock_embedder, mock_matcher, mock_linker
    ):
        """MEDIUM confidence routes to agent workflow."""
        # Setup
        candidate = make_mock_candidate(MatchConfidence.MEDIUM)
        mock_matcher.match_mention_to_concept.return_value = candidate

        # Mock workflow to return "linked" decision
        with patch(
            "agentic_kg.extraction.kg_integration_v2.process_medium_low_confidence"
        ) as mock_workflow:
            mock_workflow.return_value = {
                "final_decision": "linked",
                "final_concept_id": "concept-001",
                "final_confidence": 0.88,
            }

            concept = make_mock_concept()
            mock_linker._create_instance_of_relationship.return_value = concept

            integrator = KGIntegratorV2(
                repository=mock_repo,
                embedding_service=mock_embedder,
                concept_matcher=mock_matcher,
                auto_linker=mock_linker,
                enable_agent_workflow=True,
                enable_concept_refinement=False,
            )

            problem = make_extracted_problem()

            # Execute
            result = integrator.integrate_extracted_problems(
                extracted_problems=[problem],
                paper_doi="10.1234/test.2024",
            )

            # Assert
            assert result.mention_results[0].agent_workflow_used is True
            assert result.mention_results[0].workflow_decision == "linked"
            mock_workflow.assert_called_once()

    def test_medium_confidence_workflow_creates_new(
        self, mock_repo, mock_embedder, mock_matcher, mock_linker
    ):
        """MEDIUM confidence workflow can decide to create new concept."""
        # Setup
        candidate = make_mock_candidate(MatchConfidence.MEDIUM)
        mock_matcher.match_mention_to_concept.return_value = candidate

        with patch(
            "agentic_kg.extraction.kg_integration_v2.process_medium_low_confidence"
        ) as mock_workflow:
            mock_workflow.return_value = {
                "final_decision": "created_new",
                "final_concept_id": None,
                "final_confidence": 0.75,
            }

            concept = make_mock_concept(id="new-from-workflow")
            mock_linker.create_new_concept.return_value = concept

            integrator = KGIntegratorV2(
                repository=mock_repo,
                embedding_service=mock_embedder,
                concept_matcher=mock_matcher,
                auto_linker=mock_linker,
                enable_agent_workflow=True,
                enable_concept_refinement=False,
            )

            problem = make_extracted_problem()

            # Execute
            result = integrator.integrate_extracted_problems(
                extracted_problems=[problem],
                paper_doi="10.1234/test.2024",
            )

            # Assert
            assert result.mention_results[0].is_new_concept is True
            assert result.mention_results[0].workflow_decision == "created_new"


# =============================================================================
# LOW Confidence Tests (Agent Workflow)
# =============================================================================


class TestLowConfidenceRouting:
    """Tests for LOW confidence agent workflow routing."""

    def test_low_confidence_routes_to_workflow(
        self, mock_repo, mock_embedder, mock_matcher, mock_linker
    ):
        """LOW confidence routes to agent workflow."""
        # Setup
        candidate = make_mock_candidate(MatchConfidence.LOW)
        mock_matcher.match_mention_to_concept.return_value = candidate

        with patch(
            "agentic_kg.extraction.kg_integration_v2.process_medium_low_confidence"
        ) as mock_workflow:
            mock_workflow.return_value = {
                "final_decision": "escalated",
                "escalation_reason": "consensus_failed",
                "final_confidence": 0.55,
                "evaluator_result": {},
                "maker_results": [],
                "hater_results": [],
                "arbiter_results": [],
                "current_round": 3,
                "decision_reasoning": "No consensus after 3 rounds",
            }

            integrator = KGIntegratorV2(
                repository=mock_repo,
                embedding_service=mock_embedder,
                concept_matcher=mock_matcher,
                auto_linker=mock_linker,
                enable_agent_workflow=True,
                enable_concept_refinement=False,
            )

            problem = make_extracted_problem()

            # Execute
            result = integrator.integrate_extracted_problems(
                extracted_problems=[problem],
                paper_doi="10.1234/test.2024",
            )

            # Assert
            mention_result = result.mention_results[0]
            assert mention_result.agent_workflow_used is True
            assert mention_result.workflow_decision == "escalated"


# =============================================================================
# Human Review Escalation Tests
# =============================================================================


class TestHumanReviewEscalation:
    """Tests for human review queue escalation."""

    def test_escalation_enqueues_review(
        self, mock_repo, mock_embedder, mock_matcher, mock_linker
    ):
        """Escalated matches are enqueued to human review."""
        # Setup
        candidate = make_mock_candidate(MatchConfidence.LOW)
        mock_matcher.match_mention_to_concept.return_value = candidate

        mock_review_queue = MagicMock()
        pending_review = MagicMock()
        pending_review.id = "review-123"
        mock_review_queue.enqueue = AsyncMock(return_value=pending_review)

        with patch(
            "agentic_kg.extraction.kg_integration_v2.process_medium_low_confidence"
        ) as mock_workflow:
            mock_workflow.return_value = {
                "final_decision": "escalated",
                "escalation_reason": "consensus_failed",
                "final_confidence": 0.55,
                "evaluator_result": {},
                "maker_results": [],
                "hater_results": [],
                "arbiter_results": [],
                "current_round": 3,
                "decision_reasoning": "No consensus",
            }

            integrator = KGIntegratorV2(
                repository=mock_repo,
                embedding_service=mock_embedder,
                concept_matcher=mock_matcher,
                auto_linker=mock_linker,
                review_queue_service=mock_review_queue,
                enable_agent_workflow=True,
                enable_concept_refinement=False,
            )

            problem = make_extracted_problem()

            # Execute
            result = integrator.integrate_extracted_problems(
                extracted_problems=[problem],
                paper_doi="10.1234/test.2024",
            )

            # Assert
            assert result.mention_results[0].human_review_id == "review-123"
            mock_review_queue.enqueue.assert_called_once()


# =============================================================================
# Workflow Disabled Tests
# =============================================================================


class TestWorkflowDisabled:
    """Tests for when agent workflow is disabled."""

    def test_medium_confidence_creates_new_when_workflow_disabled(
        self, mock_repo, mock_embedder, mock_matcher, mock_linker
    ):
        """MEDIUM confidence creates new concept when workflow disabled."""
        # Setup
        candidate = make_mock_candidate(MatchConfidence.MEDIUM)
        mock_matcher.match_mention_to_concept.return_value = candidate

        concept = make_mock_concept(id="new-concept")
        mock_linker.create_new_concept.return_value = concept

        integrator = KGIntegratorV2(
            repository=mock_repo,
            embedding_service=mock_embedder,
            concept_matcher=mock_matcher,
            auto_linker=mock_linker,
            enable_agent_workflow=False,  # Workflow disabled
            enable_concept_refinement=False,
        )

        problem = make_extracted_problem()

        # Execute
        result = integrator.integrate_extracted_problems(
            extracted_problems=[problem],
            paper_doi="10.1234/test.2024",
        )

        # Assert
        assert result.mention_results[0].is_new_concept is True
        assert result.mention_results[0].agent_workflow_used is False


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling scenarios."""

    def test_workflow_failure_falls_back_to_new_concept(
        self, mock_repo, mock_embedder, mock_matcher, mock_linker
    ):
        """Workflow failure falls back to creating new concept."""
        # Setup
        candidate = make_mock_candidate(MatchConfidence.MEDIUM)
        mock_matcher.match_mention_to_concept.return_value = candidate

        with patch(
            "agentic_kg.extraction.kg_integration_v2.process_medium_low_confidence"
        ) as mock_workflow:
            mock_workflow.side_effect = Exception("Workflow error")

            concept = make_mock_concept(id="fallback-concept")
            mock_linker.create_new_concept.return_value = concept

            integrator = KGIntegratorV2(
                repository=mock_repo,
                embedding_service=mock_embedder,
                concept_matcher=mock_matcher,
                auto_linker=mock_linker,
                enable_agent_workflow=True,
                enable_concept_refinement=False,
            )

            problem = make_extracted_problem()

            # Execute
            result = integrator.integrate_extracted_problems(
                extracted_problems=[problem],
                paper_doi="10.1234/test.2024",
            )

            # Assert - should fallback to new concept
            assert result.mention_results[0].is_new_concept is True
            assert result.mention_results[0].concept_id == "fallback-concept"

    def test_embedding_failure_returns_error(
        self, mock_repo, mock_embedder, mock_matcher, mock_linker
    ):
        """Embedding failure returns error result."""
        # Setup
        mock_embedder.generate_embedding.side_effect = Exception("Embedding API error")

        integrator = KGIntegratorV2(
            repository=mock_repo,
            embedding_service=mock_embedder,
            concept_matcher=mock_matcher,
            auto_linker=mock_linker,
            enable_agent_workflow=False,
            enable_concept_refinement=False,
        )

        problem = make_extracted_problem()

        # Execute
        result = integrator.integrate_extracted_problems(
            extracted_problems=[problem],
            paper_doi="10.1234/test.2024",
        )

        # Assert
        assert result.mention_results[0].error is not None
        assert "Embedding" in result.mention_results[0].error


# =============================================================================
# Integration Result Tests
# =============================================================================


class TestIntegrationResultV2:
    """Tests for IntegrationResultV2 model."""

    def test_success_property(self):
        """Test success property when no errors."""
        result = IntegrationResultV2(
            trace_id="test-trace",
            mentions_created=2,
            mentions_linked=1,
            mentions_new_concepts=1,
        )

        assert result.success is True

    def test_success_property_with_errors(self):
        """Test success property when errors present."""
        result = IntegrationResultV2(
            trace_id="test-trace",
            errors=["Some error occurred"],
        )

        assert result.success is False

    def test_total_concepts_created(self):
        """Test total_concepts_created property."""
        result = IntegrationResultV2(
            trace_id="test-trace",
            mentions_new_concepts=3,
        )

        assert result.total_concepts_created == 3


class TestMentionIntegrationResult:
    """Tests for MentionIntegrationResult model."""

    def test_agent_workflow_fields(self):
        """Test agent workflow tracking fields."""
        result = MentionIntegrationResult(
            mention_id="mention-001",
            trace_id="trace-001",
            agent_workflow_used=True,
            workflow_decision="linked",
            human_review_id=None,
        )

        assert result.agent_workflow_used is True
        assert result.workflow_decision == "linked"

    def test_human_review_tracking(self):
        """Test human review tracking field."""
        result = MentionIntegrationResult(
            mention_id="mention-001",
            trace_id="trace-001",
            agent_workflow_used=True,
            workflow_decision="escalated",
            human_review_id="review-123",
        )

        assert result.human_review_id == "review-123"

    def test_concept_refined_field(self):
        """Test concept refinement tracking field."""
        result = MentionIntegrationResult(
            mention_id="mention-001",
            concept_id="concept-001",
            trace_id="trace-001",
            auto_linked=True,
            concept_refined=True,
        )

        assert result.concept_refined is True
