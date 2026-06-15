"""E-8 Unit 6 — parallel orchestrator.

Covers AC-4 (parallel via asyncio.gather) and AC-5 (per-extractor
degradation for both known LLMError and unknown exceptions).

The orchestrator takes three pre-built coroutines so each extractor's
specific call signature stays its own concern.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from agentic_kg.extraction.pipeline import (
    ExtractionFailure,
    PaperExtractionResult,
    extract_all_entities,
)
from agentic_kg.extraction.schemas import (
    ExtractedProblem,
    ExtractedResearchConcept,
    _ExtractedTopicAssignmentBase,
)

# =============================================================================
# Helpers
# =============================================================================


async def _returns(value):
    return value


async def _raises(exc: BaseException):
    raise exc


@pytest.fixture
def sample_problem() -> ExtractedProblem:
    return ExtractedProblem(
        statement="Models struggle with long documents beyond 10000 tokens.",
        quoted_text="struggles with very long documents",
        confidence=0.9,
    )


@pytest.fixture
def sample_topic() -> _ExtractedTopicAssignmentBase:
    return _ExtractedTopicAssignmentBase(level="area", confidence=0.95)


@pytest.fixture
def sample_concept() -> ExtractedResearchConcept:
    return ExtractedResearchConcept(
        name="attention", quoted_text="self-attention is used"
    )


# =============================================================================
# Result types
# =============================================================================


class TestPaperExtractionResult:
    def test_is_partial_false_when_no_failures(
        self, sample_problem, sample_topic, sample_concept
    ):
        r = PaperExtractionResult(
            problems=[sample_problem],
            topics=[sample_topic],
            concepts=[sample_concept],
            failures=[],
        )
        assert r.is_partial is False

    def test_is_partial_true_when_any_failure(self):
        f = ExtractionFailure(
            extractor="topic",
            exception_type="TimeoutError",
            message="took too long",
            traceback="trace",
            occurred_at=datetime.now(timezone.utc),
        )
        r = PaperExtractionResult(
            problems=[], topics=[], concepts=[], failures=[f]
        )
        assert r.is_partial is True


# =============================================================================
# Orchestration
# =============================================================================


class TestExtractAllEntities:
    @pytest.mark.asyncio
    async def test_all_three_extractors_called(
        self, sample_problem, sample_topic, sample_concept
    ):
        result = await extract_all_entities(
            problem_call=_returns([sample_problem]),
            topic_call=_returns([sample_topic]),
            concept_call=_returns([sample_concept]),
            model_call=_returns([]),
            method_call=_returns([]),
            paper_doi="10.1/abc",
        )
        assert result.problems == [sample_problem]
        assert result.topics == [sample_topic]
        assert result.concepts == [sample_concept]
        assert result.failures == []
        assert result.is_partial is False

    @pytest.mark.asyncio
    async def test_extractors_run_concurrently(self):
        """If gather is genuinely parallel, the fastest coroutine finishes
        first regardless of argument order. If awaits were sequential, the
        slow problem call would block topic and concept.
        """
        order: list[str] = []

        async def slow_problem():
            await asyncio.sleep(0.05)
            order.append("problem")
            return []

        async def fast_topic():
            await asyncio.sleep(0.01)
            order.append("topic")
            return []

        async def medium_concept():
            await asyncio.sleep(0.025)
            order.append("concept")
            return []

        async def instant_model():
            order.append("model")
            return []

        async def instant_method():
            order.append("method")
            return []

        await extract_all_entities(
            problem_call=slow_problem(),
            topic_call=fast_topic(),
            concept_call=medium_concept(),
            model_call=instant_model(),
            method_call=instant_method(),
            paper_doi="10.1/abc",
        )
        # Slowest still finishes last; fastest still finishes first relative
        # to the original three. Model/method are instantaneous and slot
        # ahead of anything with a sleep.
        assert order[-1] == "problem"
        assert "topic" in order
        assert "concept" in order

    @pytest.mark.asyncio
    async def test_known_degradation_returns_empty_no_failure(
        self, sample_problem, sample_topic
    ):
        """Extractors that catch LLMError internally return [] and the
        orchestrator does NOT record an ExtractionFailure for them — this
        is expected degradation, not an unhandled crash."""
        result = await extract_all_entities(
            problem_call=_returns([sample_problem]),
            topic_call=_returns([sample_topic]),
            concept_call=_returns([]),  # extractor caught LLMError internally
            model_call=_returns([]),
            method_call=_returns([]),
            paper_doi="10.1/abc",
        )
        assert result.failures == []
        assert result.is_partial is False
        assert result.problems == [sample_problem]
        assert result.topics == [sample_topic]
        assert result.concepts == []

    @pytest.mark.asyncio
    async def test_unknown_exception_recorded_others_preserved(
        self, sample_problem, sample_concept
    ):
        result = await extract_all_entities(
            problem_call=_returns([sample_problem]),
            topic_call=_raises(TimeoutError("topic timeout after 60s")),
            concept_call=_returns([sample_concept]),
            model_call=_returns([]),
            method_call=_returns([]),
            paper_doi="10.1/abc",
        )
        assert result.is_partial is True
        assert len(result.failures) == 1
        f = result.failures[0]
        assert f.extractor == "topic"
        assert f.exception_type == "TimeoutError"
        assert "timeout" in f.message.lower()
        assert f.traceback
        assert result.problems == [sample_problem]
        assert result.concepts == [sample_concept]
        assert result.topics == []

    @pytest.mark.asyncio
    async def test_attribute_error_recorded(self):
        result = await extract_all_entities(
            problem_call=_raises(AttributeError("schema mismatch")),
            topic_call=_returns([]),
            concept_call=_returns([]),
            model_call=_returns([]),
            method_call=_returns([]),
            paper_doi="10.1/abc",
        )
        assert result.is_partial is True
        assert result.failures[0].exception_type == "AttributeError"
        assert result.failures[0].extractor == "problem"

    @pytest.mark.asyncio
    async def test_message_and_traceback_truncated(self):
        result = await extract_all_entities(
            problem_call=_raises(RuntimeError("x" * 1000)),
            topic_call=_returns([]),
            concept_call=_returns([]),
            model_call=_returns([]),
            method_call=_returns([]),
            paper_doi="10.1/abc",
        )
        f = result.failures[0]
        assert len(f.message) <= 500
        assert len(f.traceback) <= 4096

    @pytest.mark.asyncio
    async def test_all_three_fail_each_recorded(self):
        result = await extract_all_entities(
            problem_call=_raises(ValueError("p")),
            topic_call=_raises(TimeoutError("t")),
            concept_call=_raises(RuntimeError("c")),
            model_call=_returns([]),
            method_call=_returns([]),
            paper_doi="10.1/abc",
        )
        names = {f.extractor for f in result.failures}
        assert names == {"problem", "topic", "concept"}
        assert result.problems == []
        assert result.topics == []
        assert result.concepts == []

    @pytest.mark.asyncio
    async def test_occurred_at_is_utc_aware(self):
        result = await extract_all_entities(
            problem_call=_raises(ValueError("x")),
            topic_call=_returns([]),
            concept_call=_returns([]),
            model_call=_returns([]),
            method_call=_returns([]),
            paper_doi="10.1/abc",
        )
        f = result.failures[0]
        assert f.occurred_at.tzinfo is not None
