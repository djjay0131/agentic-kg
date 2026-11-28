"""The deploy-time model check: real calls for the configured models.

Two probes matter: a tiny chat ping proves the model id and request shape, and
a bounded ranking batch proves the largest real structured call still fits the
completion budget when reasoning tokens count against it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agentic_kg.agents.schemas import (
    GeneratedCode,
    RankedProblemScore,
    RankingBatchResult,
)
from agentic_kg.extraction import llm_check
from agentic_kg.extraction.llm_check import LLMCheckError, run_llm_check


def _ranking_result(count: int = 1) -> RankingBatchResult:
    return RankingBatchResult(
        scores=[
            RankedProblemScore(
                problem_id=f"synthetic-problem-{index}",
                score=0.5,
                rationale="A synthetic deployment-check rationale.",
            )
            for index in range(1, count + 1)
        ]
    )


class _Client:
    def __init__(
        self,
        *,
        ok: bool = True,
        error: Exception | None = None,
        ranking_error: Exception | None = None,
        ranking_finish_reason: str = "stop",
        ranking_scores: RankingBatchResult | None = None,
        codegen_error: Exception | None = None,
        codegen_code: str = "print('{\"metrics\": {\"accuracy\": 0.5}}')\n",
    ):
        self._ok = ok
        self._error = error
        self._ranking_error = ranking_error
        self._ranking_finish_reason = ranking_finish_reason
        self._ranking_scores = ranking_scores
        self._codegen_error = codegen_error
        self._codegen_code = codegen_code
        self.ranking_calls: list[dict] = []
        self.codegen_calls: list[dict] = []

    async def extract(self, **_kwargs):
        if self._error is not None:
            raise self._error
        return SimpleNamespace(content=SimpleNamespace(ok=self._ok))

    async def structured_extract(self, **kwargs):
        if kwargs.get("response_model") is GeneratedCode:
            self.codegen_calls.append(kwargs)
            if self._codegen_error is not None:
                raise self._codegen_error
            return SimpleNamespace(
                content=GeneratedCode(
                    code=self._codegen_code,
                    plan="- simulate\n- report metrics",
                ),
                finish_reason="stop",
            )
        self.ranking_calls.append(kwargs)
        if self._ranking_error is not None:
            raise self._ranking_error
        scores = self._ranking_scores or _ranking_result()
        return SimpleNamespace(
            content=scores, finish_reason=self._ranking_finish_reason
        )


@pytest.mark.asyncio
async def test_chat_failure_carries_the_provider_message(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-6.2-sol")
    monkeypatch.setattr(
        llm_check,
        "create_llm_client",
        lambda **_kwargs: _Client(error=RuntimeError("Error code: 404 - model_not_found")),
    )

    with pytest.raises(LLMCheckError, match="model_not_found"):
        await run_llm_check(check_embedding=False)


@pytest.mark.asyncio
async def test_successful_chat_ranking_and_embedding_check(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-6.2-sol")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = _Client(ok=True)
    monkeypatch.setattr(llm_check, "create_llm_client", lambda **_kwargs: client)

    service = MagicMock()
    service.generate_embedding.return_value = [0.0] * 1536
    monkeypatch.setattr(
        "agentic_kg.knowledge_graph.embeddings.EmbeddingService",
        lambda _config: service,
    )

    results = await run_llm_check()

    assert [result.kind for result in results] == [
        "chat",
        "ranking",
        "code_generation",
        "embedding",
    ]
    assert all(result.ok for result in results)
    # The ranking probe must use the compact batch schema and the configured
    # completion budget, not a tiny ping.
    assert client.ranking_calls
    call = client.ranking_calls[0]
    assert call["response_model"] is RankingBatchResult
    assert call["call_site"] == "llm-check.ranking_batch"
    assert call["max_tokens"] >= 1
    # The code-generation probe must mirror the real call shape and effort.
    assert client.codegen_calls
    codegen = client.codegen_calls[0]
    assert codegen["response_model"] is GeneratedCode
    assert codegen["call_site"] == "llm-check.code_generation"
    assert codegen["reasoning_effort"] == "low"
    assert codegen["max_tokens"] >= 24576


@pytest.mark.asyncio
async def test_ranking_probe_can_be_skipped(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-6.2-sol")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = _Client(ok=True)
    monkeypatch.setattr(llm_check, "create_llm_client", lambda **_kwargs: client)

    service = MagicMock()
    service.generate_embedding.return_value = [0.0] * 1536
    monkeypatch.setattr(
        "agentic_kg.knowledge_graph.embeddings.EmbeddingService",
        lambda _config: service,
    )

    results = await run_llm_check(check_ranking=False)

    assert [result.kind for result in results] == [
        "chat",
        "code_generation",
        "embedding",
    ]
    assert client.ranking_calls == []


@pytest.mark.asyncio
async def test_ranking_probe_failure_is_reported(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-6.2-sol")
    monkeypatch.setattr(
        llm_check,
        "create_llm_client",
        lambda **_kwargs: _Client(
            ranking_error=RuntimeError(
                "The output is incomplete due to a max_tokens length limit."
            )
        ),
    )

    with pytest.raises(LLMCheckError, match="ranking probe"):
        await run_llm_check(check_embedding=False)


@pytest.mark.asyncio
async def test_ranking_probe_length_finish_reason_is_reported(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-6.2-sol")
    monkeypatch.setattr(
        llm_check,
        "create_llm_client",
        lambda **_kwargs: _Client(ranking_finish_reason="length"),
    )

    with pytest.raises(LLMCheckError, match="would truncate"):
        await run_llm_check(check_embedding=False)


@pytest.mark.asyncio
async def test_code_generation_probe_failure_is_reported(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-6.2-sol")
    monkeypatch.setattr(
        llm_check,
        "create_llm_client",
        lambda **_kwargs: _Client(
            codegen_error=RuntimeError("LLM call 'x' timed out after 600s")
        ),
    )

    with pytest.raises(LLMCheckError, match="code-generation probe"):
        await run_llm_check(check_embedding=False)


@pytest.mark.asyncio
async def test_code_generation_probe_without_a_script_is_refused(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-6.2-sol")
    monkeypatch.setattr(
        llm_check,
        "create_llm_client",
        lambda **_kwargs: _Client(ok=True, codegen_code="   "),
    )

    with pytest.raises(LLMCheckError, match="returned no script"):
        await run_llm_check(check_embedding=False)


@pytest.mark.asyncio
async def test_embedding_dimension_change_is_refused(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-6.2-sol")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_check, "create_llm_client", lambda **_kwargs: _Client(ok=True)
    )

    service = MagicMock()
    service.generate_embedding.return_value = [0.0] * 3072
    monkeypatch.setattr(
        "agentic_kg.knowledge_graph.embeddings.EmbeddingService",
        lambda _config: service,
    )

    with pytest.raises(LLMCheckError, match="vector index expects 1536"):
        await run_llm_check()
