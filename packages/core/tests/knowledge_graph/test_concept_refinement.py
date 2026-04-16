"""
Tests for ConceptRefinementService.

Tests concept refinement at threshold counts with mocked LLM.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from agentic_kg.knowledge_graph.concept_refinement import (
    ConceptRefinementService,
    ConceptNotFoundError,
    RefinementError,
    RefinementResult,
    get_refinement_service,
    reset_refinement_service,
)
from agentic_kg.knowledge_graph.models import ProblemConcept, ProblemMention


# =============================================================================
# Fixtures and Helpers
# =============================================================================


def make_concept(**kwargs) -> dict:
    """Create a mock concept node dict."""
    return {
        "id": kwargs.get("id", "concept-001"),
        "canonical_statement": kwargs.get(
            "canonical_statement", "How to improve transformer efficiency?"
        ),
        "domain": kwargs.get("domain", "NLP"),
        "status": kwargs.get("status", "open"),
        "assumptions": kwargs.get("assumptions", []),
        "constraints": kwargs.get("constraints", []),
        "datasets": kwargs.get("datasets", []),
        "metrics": kwargs.get("metrics", []),
        "verified_baselines": kwargs.get("verified_baselines", []),
        "claimed_baselines": kwargs.get("claimed_baselines", []),
        "synthesis_method": kwargs.get("synthesis_method", "first_mention"),
        "synthesis_model": kwargs.get("synthesis_model"),
        "synthesized_at": kwargs.get("synthesized_at"),
        "synthesized_by": kwargs.get("synthesized_by"),
        "human_edited": kwargs.get("human_edited", False),
        "mention_count": kwargs.get("mention_count", 1),
        "paper_count": kwargs.get("paper_count", 1),
        "version": kwargs.get("version", 1),
        "last_refined_at_count": kwargs.get("last_refined_at_count"),
    }


def make_mention(**kwargs) -> dict:
    """Create a mock mention node dict."""
    return {
        "id": kwargs.get("id", "mention-001"),
        "statement": kwargs.get("statement", "Test problem statement"),
        "paper_doi": kwargs.get("paper_doi", "10.1234/test.2024"),
        "paper_title": kwargs.get("paper_title", "Test Paper"),
        "section": kwargs.get("section", "Introduction"),
        "quoted_text": kwargs.get("quoted_text", "Test quoted text from the paper."),
        "domain": kwargs.get("domain", "NLP"),
        "assumptions": kwargs.get("assumptions", []),
        "constraints": kwargs.get("constraints", []),
        "datasets": kwargs.get("datasets", []),
        "metrics": kwargs.get("metrics", []),
        "baselines": kwargs.get("baselines", []),
        "confidence_score": kwargs.get("confidence_score", 0.9),
        "concept_id": kwargs.get("concept_id"),
        "review_status": kwargs.get("review_status", "pending"),
        "workflow_state": kwargs.get("workflow_state", "extracted"),
    }


def create_session_context(results_sequence):
    """
    Create a mock session context that returns results in sequence.

    Args:
        results_sequence: List of result values for each run() call
    """
    mock_session = MagicMock()
    mock_results = []

    for result_data in results_sequence:
        mock_result = MagicMock()
        if result_data is None:
            mock_result.single.return_value = None
            mock_result.__iter__ = MagicMock(return_value=iter([]))
        elif isinstance(result_data, dict) and "c" in result_data:
            mock_result.single.return_value = result_data
        elif isinstance(result_data, list):
            mock_result.__iter__ = MagicMock(return_value=iter(result_data))
        else:
            mock_result.single.return_value = result_data
        mock_results.append(mock_result)

    mock_session.run.side_effect = mock_results

    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=mock_session)
    ctx.__exit__ = MagicMock(return_value=False)

    return ctx


@pytest.fixture
def mock_llm():
    """Create mock LLM client."""
    llm = MagicMock()
    llm.extract = AsyncMock()
    return llm


# =============================================================================
# Threshold Checking Tests
# =============================================================================


class TestThresholdChecking:
    """Tests for refinement threshold logic."""

    @pytest.mark.asyncio
    async def test_triggers_at_5_mentions(self, mock_llm):
        """Refinement triggers at 5 mentions."""
        concept_node = make_concept(mention_count=5, last_refined_at_count=None)
        mention_nodes = [make_mention(id=f"m{i}") for i in range(5)]
        updated_node = {**concept_node, "version": 2, "last_refined_at_count": 5}

        mock_repo = MagicMock()
        # Create contexts for each session call
        ctx1 = create_session_context([{"c": concept_node}])
        ctx2 = create_session_context([[{"m": m} for m in mention_nodes]])
        ctx3 = create_session_context([{"c": updated_node}])
        mock_repo.session.side_effect = [ctx1, ctx2, ctx3]

        mock_llm.extract.return_value = MagicMock(
            content=RefinementResult(
                canonical_statement="Refined canonical statement for the problem."
            )
        )

        service = ConceptRefinementService(repository=mock_repo, llm_client=mock_llm)
        result = await service.check_and_refine("concept-001", "trace-001")

        assert result is not None
        assert result.version == 2
        mock_llm.extract.assert_called_once()

    @pytest.mark.asyncio
    async def test_triggers_at_10_mentions(self, mock_llm):
        """Refinement triggers at 10 mentions."""
        concept_node = make_concept(mention_count=10, last_refined_at_count=5)
        mention_nodes = [make_mention(id=f"m{i}") for i in range(10)]
        updated_node = {**concept_node, "version": 2, "last_refined_at_count": 10}

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        ctx2 = create_session_context([[{"m": m} for m in mention_nodes]])
        ctx3 = create_session_context([{"c": updated_node}])
        mock_repo.session.side_effect = [ctx1, ctx2, ctx3]

        mock_llm.extract.return_value = MagicMock(
            content=RefinementResult(canonical_statement="Refined statement at 10.")
        )

        service = ConceptRefinementService(repository=mock_repo, llm_client=mock_llm)
        result = await service.check_and_refine("concept-001", "trace-001")

        assert result is not None
        mock_llm.extract.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_trigger_at_non_threshold(self):
        """No refinement at non-threshold counts (e.g., 7 mentions)."""
        concept_node = make_concept(mention_count=7)

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        mock_repo.session.side_effect = [ctx1]

        service = ConceptRefinementService(repository=mock_repo, llm_client=None)
        result = await service.check_and_refine("concept-001", "trace-001")

        assert result is None

    @pytest.mark.asyncio
    async def test_no_trigger_when_already_refined(self):
        """No refinement when already refined at this threshold."""
        concept_node = make_concept(mention_count=5, last_refined_at_count=5)

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        mock_repo.session.side_effect = [ctx1]

        service = ConceptRefinementService(repository=mock_repo, llm_client=None)
        result = await service.check_and_refine("concept-001", "trace-001")

        assert result is None


# =============================================================================
# Human-Edited Protection Tests
# =============================================================================


class TestHumanEditedProtection:
    """Tests for human-edited concept protection."""

    @pytest.mark.asyncio
    async def test_skips_human_edited_concept(self):
        """Human-edited concepts are never auto-refined."""
        concept_node = make_concept(
            mention_count=5, human_edited=True, last_refined_at_count=None
        )

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        mock_repo.session.side_effect = [ctx1]

        service = ConceptRefinementService(repository=mock_repo, llm_client=None)
        result = await service.check_and_refine("concept-001", "trace-001")

        assert result is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("threshold", [5, 10, 25, 50])
    async def test_skips_human_edited_at_all_thresholds(self, threshold):
        """Human-edited flag blocks refinement at all thresholds."""
        concept_node = make_concept(
            mention_count=threshold, human_edited=True, last_refined_at_count=None
        )

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        mock_repo.session.side_effect = [ctx1]

        service = ConceptRefinementService(repository=mock_repo, llm_client=None)
        result = await service.check_and_refine("concept-001", "trace-001")

        assert result is None


# =============================================================================
# Synthesis Tests
# =============================================================================


class TestSynthesis:
    """Tests for LLM synthesis."""

    @pytest.mark.asyncio
    async def test_synthesis_called_with_all_mentions(self, mock_llm):
        """LLM receives all mention statements for synthesis."""
        concept_node = make_concept(mention_count=5)
        mention_nodes = [
            make_mention(id=f"m{i}", statement=f"This is problem statement number {i} for testing.")
            for i in range(5)
        ]
        updated_node = {**concept_node, "version": 2, "last_refined_at_count": 5}

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        ctx2 = create_session_context([[{"m": m} for m in mention_nodes]])
        ctx3 = create_session_context([{"c": updated_node}])
        mock_repo.session.side_effect = [ctx1, ctx2, ctx3]

        mock_llm.extract.return_value = MagicMock(
            content=RefinementResult(canonical_statement="Synthesized statement.")
        )

        service = ConceptRefinementService(repository=mock_repo, llm_client=mock_llm)
        await service.check_and_refine("concept-001", "trace-001")

        # Check that LLM was called
        mock_llm.extract.assert_called_once()
        call_args = mock_llm.extract.call_args

        # Verify all mentions are in the prompt
        prompt = call_args[1]["prompt"]
        for i in range(5):
            assert f"problem statement number {i}" in prompt

    @pytest.mark.asyncio
    async def test_synthesis_error_raises_refinement_error(self, mock_llm):
        """LLM errors are wrapped in RefinementError."""
        from agentic_kg.extraction.llm_client import LLMError

        concept_node = make_concept(mention_count=5)
        mention_nodes = [make_mention(id="m1")]

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        ctx2 = create_session_context([[{"m": mention_nodes[0]}]])
        mock_repo.session.side_effect = [ctx1, ctx2]

        mock_llm.extract.side_effect = LLMError("API error")

        service = ConceptRefinementService(repository=mock_repo, llm_client=mock_llm)

        with pytest.raises(RefinementError) as exc_info:
            await service.check_and_refine("concept-001", "trace-001")

        assert "LLM synthesis failed" in str(exc_info.value)


# =============================================================================
# Database Update Tests
# =============================================================================


class TestDatabaseUpdates:
    """Tests for database updates after refinement."""

    @pytest.mark.asyncio
    async def test_version_incremented(self, mock_llm):
        """Version is incremented after refinement."""
        concept_node = make_concept(mention_count=5, version=1)
        mention_nodes = [make_mention(id="m1")]
        updated_node = {**concept_node, "version": 2, "last_refined_at_count": 5}

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        ctx2 = create_session_context([[{"m": mention_nodes[0]}]])
        ctx3 = create_session_context([{"c": updated_node}])
        mock_repo.session.side_effect = [ctx1, ctx2, ctx3]

        mock_llm.extract.return_value = MagicMock(
            content=RefinementResult(canonical_statement="New refined canonical statement for version test.")
        )

        service = ConceptRefinementService(repository=mock_repo, llm_client=mock_llm)
        result = await service.check_and_refine("concept-001", "trace-001")

        assert result.version == 2

    @pytest.mark.asyncio
    async def test_last_refined_at_count_updated(self, mock_llm):
        """last_refined_at_count is set to current mention_count."""
        concept_node = make_concept(mention_count=10, last_refined_at_count=5)
        mention_nodes = [make_mention(id="m1")]
        updated_node = {**concept_node, "version": 2, "last_refined_at_count": 10}

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        ctx2 = create_session_context([[{"m": mention_nodes[0]}]])
        ctx3 = create_session_context([{"c": updated_node}])
        mock_repo.session.side_effect = [ctx1, ctx2, ctx3]

        mock_llm.extract.return_value = MagicMock(
            content=RefinementResult(canonical_statement="New refined canonical statement for count test.")
        )

        service = ConceptRefinementService(repository=mock_repo, llm_client=mock_llm)
        result = await service.check_and_refine("concept-001", "trace-001")

        assert result.last_refined_at_count == 10


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_concept_not_found_raises_error(self):
        """ConceptNotFoundError raised when concept doesn't exist."""
        mock_repo = MagicMock()
        ctx1 = create_session_context([None])
        mock_repo.session.side_effect = [ctx1]

        service = ConceptRefinementService(repository=mock_repo, llm_client=None)

        with pytest.raises(ConceptNotFoundError):
            await service.check_and_refine("nonexistent", "trace-001")

    @pytest.mark.asyncio
    async def test_no_llm_client_raises_error(self):
        """RefinementError raised when LLM client not configured."""
        concept_node = make_concept(mention_count=5)
        mention_nodes = [make_mention(id="m1")]

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        ctx2 = create_session_context([[{"m": mention_nodes[0]}]])
        mock_repo.session.side_effect = [ctx1, ctx2]

        service = ConceptRefinementService(repository=mock_repo, llm_client=None)

        with pytest.raises(RefinementError) as exc_info:
            await service.check_and_refine("concept-001", "trace-001")

        assert "LLM client not configured" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_mentions_skips_refinement(self, mock_llm):
        """Refinement skipped when no mentions found."""
        concept_node = make_concept(mention_count=5)

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        ctx2 = create_session_context([[]])  # Empty mentions list
        mock_repo.session.side_effect = [ctx1, ctx2]

        service = ConceptRefinementService(repository=mock_repo, llm_client=mock_llm)
        result = await service.check_and_refine("concept-001", "trace-001")

        assert result is None
        mock_llm.extract.assert_not_called()


# =============================================================================
# Singleton Tests
# =============================================================================


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_refinement_service_returns_same_instance(self):
        """get_refinement_service returns singleton instance."""
        reset_refinement_service()
        mock_repo = MagicMock()

        svc1 = get_refinement_service(repository=mock_repo)
        svc2 = get_refinement_service()

        assert svc1 is svc2
        reset_refinement_service()

    def test_reset_refinement_service(self):
        """reset_refinement_service clears the singleton."""
        reset_refinement_service()
        mock_repo = MagicMock()

        svc1 = get_refinement_service(repository=mock_repo)
        reset_refinement_service()
        svc2 = get_refinement_service(repository=mock_repo)

        assert svc1 is not svc2
        reset_refinement_service()


# =============================================================================
# Threshold Constants Tests
# =============================================================================


class TestThresholdConstants:
    """Tests for threshold configuration."""

    def test_thresholds_are_correct(self):
        """Verify threshold values match design spec."""
        assert ConceptRefinementService.REFINEMENT_THRESHOLDS == [5, 10, 25, 50]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("threshold", [5, 10, 25, 50])
    async def test_all_thresholds_trigger_refinement(self, threshold, mock_llm):
        """All defined thresholds trigger refinement."""
        concept_node = make_concept(mention_count=threshold, last_refined_at_count=None)
        mention_nodes = [make_mention(id="m1")]
        updated_node = {
            **concept_node,
            "version": 2,
            "last_refined_at_count": threshold,
        }

        mock_repo = MagicMock()
        ctx1 = create_session_context([{"c": concept_node}])
        ctx2 = create_session_context([[{"m": mention_nodes[0]}]])
        ctx3 = create_session_context([{"c": updated_node}])
        mock_repo.session.side_effect = [ctx1, ctx2, ctx3]

        mock_llm.extract.return_value = MagicMock(
            content=RefinementResult(canonical_statement="A refined canonical statement for testing purposes.")
        )

        service = ConceptRefinementService(repository=mock_repo, llm_client=mock_llm)
        result = await service.check_and_refine("concept-001", "trace-001")

        assert result is not None
        assert result.last_refined_at_count == threshold
