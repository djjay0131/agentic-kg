"""The deploy-time model check: one real call per configured model."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agentic_kg.extraction import llm_check
from agentic_kg.extraction.llm_check import LLMCheckError, run_llm_check


class _Client:
    def __init__(self, *, ok: bool = True, error: Exception | None = None):
        self._ok = ok
        self._error = error

    async def extract(self, **_kwargs):
        if self._error is not None:
            raise self._error
        return SimpleNamespace(content=SimpleNamespace(ok=self._ok))


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
async def test_successful_chat_and_embedding_check(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gpt-6.2-sol")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        llm_check, "create_llm_client", lambda **_kwargs: _Client(ok=True)
    )

    service = MagicMock()
    service.generate_embedding.return_value = [0.0] * 1536
    monkeypatch.setattr(
        "agentic_kg.knowledge_graph.embeddings.EmbeddingService",
        lambda _config: service,
    )

    results = await run_llm_check()

    assert [result.kind for result in results] == ["chat", "embedding"]
    assert all(result.ok for result in results)


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
