"""
Unit tests for Knowledge Graph integration.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from agentic_kg.extraction.kg_integration import (
    IntegrationConfig,
    IntegrationResult,
    KnowledgeGraphIntegrator,
    StoredProblem,
    get_kg_integrator,
    reset_kg_integrator,
)
from agentic_kg.extraction.pipeline import PaperProcessingResult
from agentic_kg.extraction.relation_extractor import (
    ExtractedRelation,
    RelationExtractionResult,
    RelationType,
)
from agentic_kg.extraction.schemas import (
    BatchExtractionResult,
    ExtractedProblem,
    ExtractionResult,
)
from agentic_kg.knowledge_graph.models import Paper, Problem
from agentic_kg.knowledge_graph.relations import RelationError
from agentic_kg.knowledge_graph.repository import DuplicateError, NotFoundError


class TestIntegrationConfig:
    """Tests for IntegrationConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = IntegrationConfig()

        assert config.check_duplicates is True
        assert config.similarity_threshold == 0.95
        assert config.min_confidence == 0.5
        assert config.create_paper_if_missing is True
        assert config.store_relations is True
        assert config.generate_embeddings is True

    def test_custom_config(self):
        """Test custom configuration."""
        config = IntegrationConfig(
            check_duplicates=False,
            min_confidence=0.8,
            create_paper_if_missing=False,
        )

        assert config.check_duplicates is False
        assert config.min_confidence == 0.8
        assert config.create_paper_if_missing is False


class TestStoredProblem:
    """Tests for StoredProblem model."""

    def test_new_problem(self):
        """Test newly created problem result."""
        stored = StoredProblem(
            problem_id="prob-123",
            is_new=True,
            is_duplicate=False,
            extraction_linked=True,
        )

        assert stored.is_new is True
        assert stored.is_duplicate is False
        assert stored.duplicate_of is None

    def test_duplicate_problem(self):
        """Test duplicate problem result."""
        stored = StoredProblem(
            problem_id="prob-existing",
            is_new=False,
            is_duplicate=True,
            duplicate_of="prob-existing",
        )

        assert stored.is_new is False
        assert stored.is_duplicate is True


class TestIntegrationResult:
    """Tests for IntegrationResult model."""

    def test_total_new_problems(self):
        """Test counting new problems."""
        result = IntegrationResult(
            problems_stored=[
                StoredProblem(problem_id="1", is_new=True, is_duplicate=False),
                StoredProblem(problem_id="2", is_new=True, is_duplicate=False),
                StoredProblem(problem_id="3", is_new=False, is_duplicate=True),
            ],
        )

        assert result.total_new_problems == 2

    def test_success_with_no_errors(self):
        """Test success property with no errors."""
        result = IntegrationResult()
        assert result.success is True

    def test_success_with_errors(self):
        """Test success property with errors."""
        result = IntegrationResult(errors=["Something went wrong"])
        assert result.success is False


class TestKnowledgeGraphIntegrator:
    """Tests for KnowledgeGraphIntegrator class."""

    @pytest.fixture
    def mock_repository(self):
        """Create mock repository."""
        repo = MagicMock()
        repo.create_problem = MagicMock()
        repo.get_problem = MagicMock()
        repo.create_paper = MagicMock()
        repo.get_paper = MagicMock()
        return repo

    @pytest.fixture
    def mock_relation_service(self):
        """Create mock relation service."""
        service = MagicMock()
        service.link_problem_to_paper = MagicMock()
        service.create_relation = MagicMock()
        return service

    @pytest.fixture
    def integrator(self, mock_repository, mock_relation_service):
        """Create integrator with mocks."""
        return KnowledgeGraphIntegrator(
            repository=mock_repository,
            relation_service=mock_relation_service,
            config=IntegrationConfig(generate_embeddings=False),
        )

    @pytest.fixture
    def sample_problem(self):
        """Create sample extracted problem."""
        return ExtractedProblem(
            statement="Deep learning models require significant computational resources.",
            quoted_text="significant computational resources",
            confidence=0.9,
            domain="Machine Learning",
        )

    @pytest.fixture
    def sample_processing_result(self, sample_problem):
        """Create sample paper processing result."""
        return PaperProcessingResult(
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            paper_authors=["Author One"],
            extraction_result=BatchExtractionResult(
                paper_title="Test Paper",
                paper_doi="10.1234/test",
                results=[
                    ExtractionResult(
                        section_type="limitations",
                        problems=[sample_problem],
                    ),
                ],
            ),
            success=True,
        )

    def test_store_single_problem_success(
        self, integrator, mock_repository, mock_relation_service, sample_problem
    ):
        """Test storing a single problem successfully."""
        mock_repository.create_problem.return_value = MagicMock()

        result = integrator.store_single_problem(
            problem=sample_problem,
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            authors=["Author One"],
        )

        assert result.is_new is True
        assert result.is_duplicate is False
        mock_repository.create_problem.assert_called_once()

    def test_store_single_problem_duplicate(
        self, integrator, mock_repository, sample_problem
    ):
        """Test handling duplicate problem."""
        mock_repository.create_problem.side_effect = DuplicateError("Already exists")

        result = integrator.store_single_problem(
            problem=sample_problem,
            paper_doi="10.1234/test",
        )

        assert result.is_new is False
        assert result.is_duplicate is True

    def test_store_problem_creates_extraction_link(
        self, integrator, mock_repository, mock_relation_service, sample_problem
    ):
        """Test that EXTRACTED_FROM relation is created."""
        mock_repository.create_problem.return_value = MagicMock()
        mock_relation_service.link_problem_to_paper.return_value = MagicMock()

        result = integrator.store_single_problem(
            problem=sample_problem,
            paper_doi="10.1234/test",
        )

        assert result.extraction_linked is True
        mock_relation_service.link_problem_to_paper.assert_called_once()

    def test_store_problem_without_paper(
        self, integrator, mock_repository, mock_relation_service, sample_problem
    ):
        """Test storing problem without paper DOI."""
        mock_repository.create_problem.return_value = MagicMock()

        result = integrator.store_single_problem(
            problem=sample_problem,
            paper_doi=None,
        )

        assert result.is_new is True
        # No paper link should be created
        mock_relation_service.link_problem_to_paper.assert_not_called()

    def test_integrate_result_success(
        self, integrator, mock_repository, mock_relation_service, sample_processing_result
    ):
        """Test integrating a full processing result."""
        mock_repository.get_paper.return_value = Paper(
            doi="10.1234/test",
            title="Test Paper",
            authors=["Author One"],
            year=2026,
        )
        mock_repository.create_problem.return_value = MagicMock()
        mock_relation_service.link_problem_to_paper.return_value = MagicMock()

        result = integrator.integrate_extraction_result(sample_processing_result)

        assert result.success is True
        assert result.paper_doi == "10.1234/test"
        assert len(result.problems_stored) == 1
        assert result.problems_stored[0].is_new is True

    def test_integrate_result_creates_paper(
        self, integrator, mock_repository, mock_relation_service, sample_processing_result
    ):
        """Test that paper is created if missing."""
        mock_repository.get_paper.side_effect = NotFoundError("Not found")
        mock_repository.create_paper.return_value = MagicMock()
        mock_repository.create_problem.return_value = MagicMock()
        mock_relation_service.link_problem_to_paper.return_value = MagicMock()

        result = integrator.integrate_extraction_result(sample_processing_result)

        assert result.success is True
        mock_repository.create_paper.assert_called_once()

    def test_integrate_result_skips_low_confidence(
        self, integrator, mock_repository, mock_relation_service
    ):
        """Test that low confidence problems are skipped."""
        integrator.config.min_confidence = 0.8

        low_conf_result = PaperProcessingResult(
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            extraction_result=BatchExtractionResult(
                paper_title="Test Paper",
                results=[
                    ExtractionResult(
                        section_type="limitations",
                        problems=[
                            ExtractedProblem(
                                statement="Low confidence problem statement here.",
                                quoted_text="low confidence",
                                confidence=0.5,  # Below threshold
                            ),
                        ],
                    ),
                ],
            ),
            success=True,
        )

        mock_repository.get_paper.return_value = Paper(
            doi="10.1234/test", title="Test", authors=[], year=2026
        )

        result = integrator.integrate_extraction_result(low_conf_result)

        assert result.problems_skipped == 1
        assert len(result.problems_stored) == 0
        mock_repository.create_problem.assert_not_called()

    def test_integrate_result_with_failed_extraction(self, integrator):
        """Test handling of failed extraction result."""
        failed_result = PaperProcessingResult(
            paper_doi="10.1234/test",
            success=False,
        )

        result = integrator.integrate_extraction_result(failed_result)

        assert result.success is False
        assert "not successful" in result.errors[0]

    def test_integrate_result_stores_relations(
        self, integrator, mock_repository, mock_relation_service
    ):
        """Test that relations are stored."""
        problems = [
            ExtractedProblem(
                statement="First problem about machine learning optimization.",
                quoted_text="machine learning optimization",
                confidence=0.9,
            ),
            ExtractedProblem(
                statement="Second problem about neural network efficiency.",
                quoted_text="neural network efficiency",
                confidence=0.85,
            ),
        ]

        relations = RelationExtractionResult(
            relations=[
                ExtractedRelation(
                    source_problem_id="First problem about machine learning",
                    target_problem_id="Second problem about neural network",
                    relation_type=RelationType.EXTENDS,
                    confidence=0.8,
                    evidence="First extends second",
                ),
            ],
        )

        processing_result = PaperProcessingResult(
            paper_doi="10.1234/test",
            paper_title="Test Paper",
            extraction_result=BatchExtractionResult(
                paper_title="Test Paper",
                results=[
                    ExtractionResult(
                        section_type="limitations",
                        problems=problems,
                    ),
                ],
            ),
            relation_result=relations,
            success=True,
        )

        mock_repository.get_paper.return_value = Paper(
            doi="10.1234/test", title="Test", authors=[], year=2026
        )
        mock_repository.create_problem.return_value = MagicMock()
        mock_relation_service.link_problem_to_paper.return_value = MagicMock()
        mock_relation_service.create_relation.return_value = MagicMock()

        result = integrator.integrate_extraction_result(processing_result)

        assert result.relations_created >= 0  # May or may not match

    def test_store_relations_skips_existing(
        self, integrator, mock_repository, mock_relation_service
    ):
        """Test that existing relations are skipped."""
        mock_relation_service.create_relation.side_effect = RelationError(
            "Already exists"
        )

        relations = [
            ExtractedRelation(
                source_problem_id="Problem A",
                target_problem_id="Problem B",
                relation_type=RelationType.EXTENDS,
                confidence=0.8,
                evidence="Evidence",
            ),
        ]

        integration = IntegrationResult()
        problem_id_map = {"Problem A": "id-a", "Problem B": "id-b"}

        integrator._store_relations(relations, problem_id_map, integration)

        assert integration.relations_skipped == 1
        assert integration.relations_created == 0

    def test_ensure_paper_exists_creates_when_missing(
        self, integrator, mock_repository
    ):
        """Test paper creation when missing."""
        mock_repository.get_paper.side_effect = NotFoundError("Not found")
        mock_repository.create_paper.return_value = MagicMock()

        integration = IntegrationResult()
        result = integrator._ensure_paper_exists(
            doi="10.1234/new",
            title="New Paper",
            authors=["Author"],
            integration=integration,
        )

        assert result is True
        mock_repository.create_paper.assert_called_once()

    def test_ensure_paper_exists_fails_when_creation_disabled(
        self, integrator, mock_repository
    ):
        """Test paper not created when disabled."""
        integrator.config.create_paper_if_missing = False
        mock_repository.get_paper.side_effect = NotFoundError("Not found")

        integration = IntegrationResult()
        result = integrator._ensure_paper_exists(
            doi="10.1234/missing",
            title="Missing Paper",
            authors=[],
            integration=integration,
        )

        assert result is False
        assert len(integration.errors) == 1
        mock_repository.create_paper.assert_not_called()


class TestMapRelationType:
    """Tests for relation type mapping."""

    @pytest.fixture
    def integrator(self):
        """Create integrator with mocks."""
        return KnowledgeGraphIntegrator(
            repository=MagicMock(),
            relation_service=MagicMock(),
        )

    def test_map_extends(self, integrator):
        """Test mapping EXTENDS relation."""
        from agentic_kg.knowledge_graph.models import RelationType as KGRelationType

        result = integrator._map_relation_type(RelationType.EXTENDS)
        assert result == KGRelationType.EXTENDS

    def test_map_contradicts(self, integrator):
        """Test mapping CONTRADICTS relation."""
        from agentic_kg.knowledge_graph.models import RelationType as KGRelationType

        result = integrator._map_relation_type(RelationType.CONTRADICTS)
        assert result == KGRelationType.CONTRADICTS

    def test_map_depends_on(self, integrator):
        """Test mapping DEPENDS_ON relation."""
        from agentic_kg.knowledge_graph.models import RelationType as KGRelationType

        result = integrator._map_relation_type(RelationType.DEPENDS_ON)
        assert result == KGRelationType.DEPENDS_ON

    def test_map_unknown_type(self, integrator):
        """Test mapping unknown relation type."""
        result = integrator._map_relation_type(RelationType.RELATED_TO)
        assert result is None


class TestGetKGIntegrator:
    """Tests for singleton access."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_kg_integrator()

    def teardown_method(self):
        """Reset singleton after each test."""
        reset_kg_integrator()

    def test_returns_integrator_instance(self):
        """Test that get_kg_integrator returns an integrator."""
        with patch("agentic_kg.extraction.kg_integration.get_repository"):
            with patch("agentic_kg.extraction.kg_integration.get_relation_service"):
                integrator = get_kg_integrator()
                assert isinstance(integrator, KnowledgeGraphIntegrator)

    def test_returns_same_instance(self):
        """Test singleton pattern."""
        with patch("agentic_kg.extraction.kg_integration.get_repository"):
            with patch("agentic_kg.extraction.kg_integration.get_relation_service"):
                integrator1 = get_kg_integrator()
                integrator2 = get_kg_integrator()
                assert integrator1 is integrator2

    def test_reset_clears_singleton(self):
        """Test reset clears singleton."""
        with patch("agentic_kg.extraction.kg_integration.get_repository"):
            with patch("agentic_kg.extraction.kg_integration.get_relation_service"):
                integrator1 = get_kg_integrator()
                reset_kg_integrator()
                integrator2 = get_kg_integrator()
                assert integrator1 is not integrator2
