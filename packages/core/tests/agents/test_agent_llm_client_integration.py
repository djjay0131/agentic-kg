"""The research agents must work with the *real* LLM client classes.

The original bug was invisible because every agent test injected a
``MagicMock`` that happened to define ``structured_extract``; the real
``OpenAIClient`` did not. These tests construct the real client, mock only the
underlying instructor SDK call, and drive the agent call path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agentic_kg.agents.continuation import ContinuationAgent
from agentic_kg.agents.ranking import RankingAgent
from agentic_kg.agents.schemas import (
    ContinuationProposal,
    ExperimentalStep,
    RankedProblem,
    RankingResult,
)
from agentic_kg.agents.synthesis import SynthesisAgent
from agentic_kg.extraction.llm_client import (
    AnthropicClient,
    LLMConfig,
    LLMProvider,
    OpenAIClient,
    reset_llm_clients,
)


def _completion(prompt_tokens=10, completion_tokens=5):
    completion = MagicMock()
    completion.usage = MagicMock(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    completion.choices = [MagicMock(finish_reason="stop")]
    return completion


def _instruction_client(response):
    client = MagicMock()
    client.chat.completions.create_with_completion = AsyncMock(
        return_value=(response, _completion())
    )
    client.messages.create_with_completion = AsyncMock(
        return_value=(response, _completion())
    )
    return client


def _openai_client():
    return OpenAIClient(LLMConfig(provider=LLMProvider.OPENAI, api_key="test-key"))


def _anthropic_client():
    return AnthropicClient(
        LLMConfig(
            provider=LLMProvider.ANTHROPIC,
            model="claude-3-5-sonnet-20241022",
            api_key="test-key",
        )
    )


@pytest.fixture(autouse=True)
def _reset_clients():
    reset_llm_clients()
    yield
    reset_llm_clients()


@pytest.mark.asyncio
async def test_real_openai_client_has_structured_extract():
    """A missing method on the real client is a CI failure, not a mock pass."""
    client = _openai_client()
    result = RankingResult(
        ranked_problems=[
            RankedProblem(
                problem_id="p1",
                statement="How can systems scale?",
                score=0.7,
                tractability=0.7,
                data_availability=0.7,
                cross_domain_impact=0.7,
                rationale="A sufficiently long rationale.",
            )
        ],
        total_candidates=1,
    )
    with patch.object(
        client, "_get_instructor_client", return_value=_instruction_client(result)
    ):
        response = await client.structured_extract(
            response_model=RankingResult,
            system_prompt="rank",
            user_prompt="candidates",
        )

    assert isinstance(response.content, RankingResult)
    assert response.content.ranked_problems[0].problem_id == "p1"


@pytest.mark.asyncio
async def test_real_anthropic_client_has_structured_extract():
    client = _anthropic_client()
    result = RankingResult(ranked_problems=[], total_candidates=0)
    with patch.object(
        client, "_get_instructor_client", return_value=_instruction_client(result)
    ):
        response = await client.structured_extract(
            response_model=RankingResult,
            system_prompt="rank",
            user_prompt="candidates",
        )

    assert isinstance(response.content, RankingResult)


@pytest.mark.asyncio
async def test_ranking_agent_uses_real_openai_client():
    llm = _openai_client()
    result = RankingResult(
        ranked_problems=[
            RankedProblem(
                problem_id="p1",
                statement="How can systems scale?",
                score=0.7,
                tractability=0.7,
                data_availability=0.7,
                cross_domain_impact=0.7,
                rationale="A sufficiently long rationale.",
            )
        ],
        total_candidates=1,
    )
    agent = RankingAgent(llm_client=llm, repository=MagicMock())

    with patch.object(
        llm, "_get_instructor_client", return_value=_instruction_client(result)
    ):
        ranked = await agent._rank_with_llm("1. (ID: p1) ...", 1, None)

    assert ranked.total_candidates == 1
    assert ranked.ranked_problems[0].problem_id == "p1"


@pytest.mark.asyncio
async def test_continuation_agent_uses_real_openai_client():
    llm = _openai_client()
    proposal = ContinuationProposal(
        problem_id="p1",
        title="Scale it",
        methodology="A methodology that is long enough to pass validation.",
        expected_outcome="A measurable improvement.",
        experimental_steps=[
            ExperimentalStep(
                step_number=1,
                description="Run the experiment",
                expected_output="A result",
            )
        ],
    )
    agent = ContinuationAgent(llm_client=llm, repository=MagicMock())
    context = MagicMock()
    context.problem_id = "p1"
    context.statement = "How can systems scale?"
    context.topic = "systems"
    context.status = "open"
    context.constraints = []
    context.datasets = []
    context.metrics = []
    context.baselines = []
    context.related_problems = []

    with patch.object(
        llm, "_get_instructor_client", return_value=_instruction_client(proposal)
    ):
        generated = await agent._generate_proposal(context)

    assert generated.problem_id == "p1"
    assert generated.title == "Scale it"


@pytest.mark.asyncio
async def test_synthesis_agent_uses_real_openai_client():
    from agentic_kg.agents.schemas import EvaluationResult, SynthesisReport

    llm = _openai_client()
    report = SynthesisReport(
        summary="The experiment produced a clear and reproducible improvement.",
        source_problem_id="p1",
    )
    agent = SynthesisAgent(llm_client=llm, repository=MagicMock())
    context = MagicMock()
    context.statement = "How can systems scale?"
    context.topic = "systems"
    proposal = ContinuationProposal(
        problem_id="p1",
        title="Scale it",
        methodology="A methodology that is long enough to pass validation.",
        expected_outcome="A measurable improvement.",
        experimental_steps=[],
    )
    evaluation = EvaluationResult(
        proposal_id="p1",
        feasibility_score=0.8,
        verdict="promising",
    )

    with patch.object(
        llm, "_get_instructor_client", return_value=_instruction_client(report)
    ):
        generated = await agent._generate_report(context, proposal, evaluation)

    assert generated.summary == report.summary
