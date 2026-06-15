"""E-8 V2 Unit 4 — orchestrator extension.

Covers AC-5 (5-way parallel with new model_call/method_call kwargs and
the TypeError contract for V1 callers omitting them) and AC-6 (per-
extractor degradation continues for both new extractors).
"""

import asyncio

import pytest
from agentic_kg.extraction.pipeline import (
    PaperExtractionResult,
    extract_all_entities,
)
from agentic_kg.extraction.schemas import (
    ExtractedMethod,
    ExtractedModel,
    ExtractedProblem,
    ExtractedResearchConcept,
    _ExtractedTopicAssignmentBase,
)


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
        name="attention", quoted_text="self-attention is used",
    )


@pytest.fixture
def sample_model() -> ExtractedModel:
    return ExtractedModel(
        name="BERT", quoted_text="we use BERT-base", confidence=0.95,
    )


@pytest.fixture
def sample_method() -> ExtractedMethod:
    return ExtractedMethod(
        name="fine-tuning",
        quoted_text="we fine-tune the encoder",
        confidence=0.9,
    )


# =============================================================================
# PaperExtractionResult — V2 fields
# =============================================================================


class TestPaperExtractionResultV2Fields:
    def test_models_default_empty(self):
        r = PaperExtractionResult()
        assert r.models == []

    def test_methods_default_empty(self):
        r = PaperExtractionResult()
        assert r.methods == []

    def test_carries_models_and_methods(self, sample_model, sample_method):
        r = PaperExtractionResult(
            models=[sample_model], methods=[sample_method],
        )
        assert r.models == [sample_model]
        assert r.methods == [sample_method]


# =============================================================================
# 5-way orchestration happy path
# =============================================================================


class TestExtractAllEntitiesFiveWay:
    @pytest.mark.asyncio
    async def test_all_five_extractors_called_and_returned(
        self, sample_problem, sample_topic, sample_concept,
        sample_model, sample_method,
    ):
        r = await extract_all_entities(
            problem_call=_returns([sample_problem]),
            topic_call=_returns([sample_topic]),
            concept_call=_returns([sample_concept]),
            model_call=_returns([sample_model]),
            method_call=_returns([sample_method]),
            paper_doi="10.1/abc",
        )
        assert r.problems == [sample_problem]
        assert r.topics == [sample_topic]
        assert r.concepts == [sample_concept]
        assert r.models == [sample_model]
        assert r.methods == [sample_method]
        assert r.failures == []
        assert r.is_partial is False

    @pytest.mark.asyncio
    async def test_v1_callsite_missing_model_method_raises_type_error(
        self, sample_problem, sample_topic, sample_concept,
    ):
        """AC-5: V1 callers passing only 3 kwargs must raise TypeError.

        No silent regression — the migration step is loud. We build the
        coroutines first and explicitly close them after the assertion
        so pytest doesn't warn about un-awaited coroutines (the TypeError
        fires at the function-call boundary, before any of these are
        awaited).
        """
        p_call = _returns([sample_problem])
        t_call = _returns([sample_topic])
        c_call = _returns([sample_concept])
        try:
            with pytest.raises(TypeError):
                await extract_all_entities(
                    problem_call=p_call,
                    topic_call=t_call,
                    concept_call=c_call,
                    paper_doi="10.1/abc",
                )
        finally:
            p_call.close()
            t_call.close()
            c_call.close()

    @pytest.mark.asyncio
    async def test_runs_concurrently(self):
        order: list[str] = []

        async def t(name: str, delay: float):
            await asyncio.sleep(delay)
            order.append(name)
            return []

        await extract_all_entities(
            problem_call=t("problem", 0.04),
            topic_call=t("topic", 0.01),
            concept_call=t("concept", 0.02),
            model_call=t("model", 0.03),
            method_call=t("method", 0.005),
        )
        # Fastest finishes first; slowest finishes last; gather is parallel.
        assert order[0] == "method"
        assert order[-1] == "problem"


# =============================================================================
# AC-6 — Per-extractor degradation for the new extractors
# =============================================================================


class TestModelExtractorDegradation:
    @pytest.mark.asyncio
    async def test_model_raises_runtime_others_preserved(
        self, sample_problem, sample_concept,
    ):
        r = await extract_all_entities(
            problem_call=_returns([sample_problem]),
            topic_call=_returns([]),
            concept_call=_returns([sample_concept]),
            model_call=_raises(RuntimeError("model bombs")),
            method_call=_returns([]),
            paper_doi="10.1/abc",
        )
        assert r.is_partial is True
        assert len(r.failures) == 1
        assert r.failures[0].extractor == "model"
        assert r.failures[0].exception_type == "RuntimeError"
        # Siblings untouched.
        assert r.problems == [sample_problem]
        assert r.concepts == [sample_concept]
        assert r.models == []


class TestMethodExtractorDegradation:
    @pytest.mark.asyncio
    async def test_method_raises_attribute_error_others_preserved(
        self, sample_problem,
    ):
        r = await extract_all_entities(
            problem_call=_returns([sample_problem]),
            topic_call=_returns([]),
            concept_call=_returns([]),
            model_call=_returns([]),
            method_call=_raises(AttributeError("schema bug")),
            paper_doi="10.1/abc",
        )
        assert r.is_partial is True
        assert r.failures[0].extractor == "method"
        assert r.failures[0].exception_type == "AttributeError"
        assert r.problems == [sample_problem]
        assert r.methods == []


class TestModelAndMethodSilentDegradation:
    """LLMError caught inside the extractor returns [] and does NOT
    appear in failures (mirror of V1 contract)."""

    @pytest.mark.asyncio
    async def test_both_silent_empty_no_failure(self, sample_topic):
        r = await extract_all_entities(
            problem_call=_returns([]),
            topic_call=_returns([sample_topic]),
            concept_call=_returns([]),
            model_call=_returns([]),  # LLMError caught internally
            method_call=_returns([]),  # LLMError caught internally
            paper_doi="10.1/abc",
        )
        assert r.failures == []
        assert r.is_partial is False
        assert r.models == []
        assert r.methods == []
