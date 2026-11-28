"""The OpenAI Chat Completions request shape instructors actually sends.

These tests drive the real ``OpenAIClient`` and the real instructor wrapper,
with only the HTTP transport faked, so a mode or parameter regression fails on
the wire body rather than on a mock's call arguments.

The failure this pins (staging, 2026-11-27): function tools plus a non-``none``
reasoning effort are rejected by the GPT-6.2 family on
``/v1/chat/completions``. Structured extraction therefore uses
``response_format`` with a JSON schema (instructor's ``JSON_SCHEMA`` mode) and
sends the configured reasoning effort explicitly, never function tools.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from agentic_kg.extraction.llm_client import (
    LLMConfig,
    LLMProvider,
    LLMTimeoutError,
    LLMTruncationError,
    OpenAIClient,
)
from openai import AsyncOpenAI
from pydantic import BaseModel


class _Ping(BaseModel):
    ok: bool


class _FreeForm(BaseModel):
    # An arbitrary mapping cannot be expressed in OpenAI's strict subset.
    fields: dict[str, str]


def _completion_body(model: str) -> dict[str, Any]:
    """A minimal valid Chat Completions response carrying the requested JSON."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"ok": true}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


async def _extract_and_capture(
    client: OpenAIClient,
    response_model: type[BaseModel] = _Ping,
    content: str = '{"ok": true}',
) -> tuple[Any, dict[str, Any]]:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        body = _completion_body(client.config.model)
        body["choices"][0]["message"]["content"] = content
        return httpx.Response(200, json=body)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._client = AsyncOpenAI(api_key="test-key", http_client=http_client)
    try:
        response = await client.extract(
            prompt="Return the requested structured JSON.",
            response_model=response_model,
            system_prompt="Reply with the requested structured JSON only.",
        )
    finally:
        await http_client.aclose()

    assert captured["url"].endswith("/v1/chat/completions")
    return response, captured["body"]


def _client(monkeypatch: pytest.MonkeyPatch, **config_kwargs: Any) -> OpenAIClient:
    # Keep the model decision explicit: the env-first config surface would
    # otherwise let a developer's shell change what this test asserts.
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_EXTRACTION_MODEL", raising=False)
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    config = LLMConfig(
        provider=LLMProvider.OPENAI,
        model="gpt-6.2-sol",
        api_key="test-key",
        **config_kwargs,
    )
    return OpenAIClient(config)


@pytest.mark.asyncio
async def test_default_request_uses_json_schema_not_function_tools(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _client(monkeypatch)

    response, body = await _extract_and_capture(client)

    assert response.content.ok is True
    assert body["model"] == "gpt-6.2-sol"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["reasoning_effort"] == "medium"
    # No function tools of either generation on chat completions.
    assert "tools" not in body
    assert "tool_choice" not in body
    assert "functions" not in body
    assert "function_call" not in body
    # Reasoning requests reject sampling parameters.
    assert "temperature" not in body
    assert body["max_completion_tokens"] == client.config.max_tokens
    assert "max_tokens" not in body


@pytest.mark.asyncio
async def test_free_form_schema_falls_back_to_best_effort_json_schema(
    monkeypatch: pytest.MonkeyPatch,
):
    """An arbitrary mapping cannot be strict, but must still not use tools."""
    client = _client(monkeypatch)

    response, body = await _extract_and_capture(
        client, _FreeForm, '{"fields": {"a": "b"}}'
    )

    assert response.content.fields == {"a": "b"}
    assert body["response_format"]["type"] == "json_schema"
    assert "strict" not in body["response_format"]["json_schema"]
    assert "tools" not in body
    assert "functions" not in body


@pytest.mark.asyncio
async def test_configured_reasoning_effort_is_sent_explicitly(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _client(monkeypatch, reasoning_effort="high")

    _, body = await _extract_and_capture(client)

    assert body["reasoning_effort"] == "high"
    assert body["response_format"]["type"] == "json_schema"
    assert "tools" not in body
    assert "functions" not in body
    assert "temperature" not in body


@pytest.mark.asyncio
async def test_non_reasoning_effort_keeps_temperature(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _client(monkeypatch, reasoning_effort="none")

    _, body = await _extract_and_capture(client)

    assert body["reasoning_effort"] == "none"
    assert body["temperature"] == client.config.temperature
    assert "tools" not in body
    assert "functions" not in body


async def _extract_with_budget_sequence(
    client: OpenAIClient,
    *,
    finish_reasons: list[str],
    reasoning_tokens: int = 0,
) -> tuple[Any, list[dict[str, Any]]]:
    """Drive a real request through the fake transport, one response per call.

    ``finish_reasons`` is consumed in order; the last entry repeats if the
    client makes more requests than responses supplied. Each captured body is
    returned so the test can assert the retry raised ``max_completion_tokens``.
    """
    bodies: list[dict[str, Any]] = []
    responses = list(finish_reasons)

    async def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        reason = responses.pop(0) if len(responses) > 1 else responses[0]
        body = _completion_body(client.config.model)
        body["choices"][0]["finish_reason"] = reason
        usage = body["usage"]
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
        return httpx.Response(200, json=body)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._client = AsyncOpenAI(api_key="test-key", http_client=http_client)
    try:
        response = await client.extract(
            prompt="Return the requested structured JSON.",
            response_model=_Ping,
            system_prompt="Reply with the requested structured JSON only.",
            call_site="test.truncation",
        )
    finally:
        await http_client.aclose()
    return response, bodies


@pytest.mark.asyncio
async def test_length_finish_reason_retries_once_with_a_larger_budget(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _client(monkeypatch, max_tokens=1000)

    response, bodies = await _extract_with_budget_sequence(
        client, finish_reasons=["length", "stop"]
    )

    assert response.content.ok is True
    assert [body["max_completion_tokens"] for body in bodies] == [1000, 2000]
    assert bodies[0]["reasoning_effort"] == "medium"


@pytest.mark.asyncio
async def test_truncation_after_one_retry_names_call_site_budget_and_effort(
    monkeypatch: pytest.MonkeyPatch,
):
    client = _client(monkeypatch, max_tokens=1000)

    with pytest.raises(LLMTruncationError) as exc_info:
        await _extract_with_budget_sequence(
            client, finish_reasons=["length", "length"]
        )

    message = str(exc_info.value)
    assert "test.truncation" in message
    assert "max_completion_tokens=2000" in message
    assert "reasoning_effort='medium'" in message
    assert exc_info.value.call_site == "test.truncation"
    assert exc_info.value.budget == 2000


@pytest.mark.asyncio
async def test_reasoning_tokens_are_recorded(monkeypatch: pytest.MonkeyPatch):
    client = _client(monkeypatch, max_tokens=1000)

    response, _ = await _extract_with_budget_sequence(
        client, finish_reasons=["stop"], reasoning_tokens=7
    )

    assert response.usage.reasoning_tokens == 7
    assert response.usage.total_tokens == 8


@pytest.mark.asyncio
async def test_ceiling_budget_does_not_retry_without_a_larger_value(
    monkeypatch: pytest.MonkeyPatch,
):
    from agentic_kg.llm_config import OUTPUT_TOKEN_CEILING

    client = _client(monkeypatch, max_tokens=OUTPUT_TOKEN_CEILING)

    with pytest.raises(LLMTruncationError) as exc_info:
        await _extract_with_budget_sequence(
            client, finish_reasons=["length", "length"]
        )

    message = str(exc_info.value)
    assert "no larger budget was available within the ceiling" in message
    assert exc_info.value.budget == OUTPUT_TOKEN_CEILING


async def _extract_with_transport_errors(
    client: OpenAIClient,
    errors: list[Exception | None],
    *,
    call_site: str = "evaluation.code_generation",
    timeout: float | None = None,
) -> Any:
    pending = list(errors)

    async def handler(request: httpx.Request) -> httpx.Response:
        error = pending.pop(0) if pending else None
        if error is not None:
            raise error
        return httpx.Response(200, json=_completion_body(client.config.model))

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client._client = AsyncOpenAI(
        api_key="test-key", http_client=http_client, max_retries=0
    )
    try:
        return await client.extract(
            prompt="Return the requested structured JSON.",
            response_model=_Ping,
            call_site=call_site,
            timeout=timeout,
        )
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_timeout_retries_once_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "agentic_kg.extraction.llm_client.TIMEOUT_RETRY_BACKOFF_SECONDS", 0
    )
    client = _client(monkeypatch)

    response = await _extract_with_transport_errors(
        client, [httpx.ReadTimeout("Request timed out"), None]
    )

    assert response.content.ok is True


@pytest.mark.asyncio
async def test_timeout_after_one_retry_names_call_site_timeout_and_budget(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "agentic_kg.extraction.llm_client.TIMEOUT_RETRY_BACKOFF_SECONDS", 0
    )
    client = _client(monkeypatch, max_tokens=24576)

    with pytest.raises(LLMTimeoutError) as exc_info:
        await _extract_with_transport_errors(
            client,
            [httpx.ReadTimeout("Request timed out")] * 2,
            timeout=600,
        )

    error = exc_info.value
    assert error.call_site == "evaluation.code_generation"
    assert error.timeout == 600
    assert error.budget == 24576
    message = str(error)
    assert "evaluation.code_generation" in message
    assert "timed out after 600s" in message
    assert "max_completion_tokens=24576" in message


@pytest.mark.asyncio
async def test_code_generation_call_site_uses_the_long_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    from agentic_kg.llm_config import (
        CALL_SITE_REQUEST_TIMEOUTS,
        CODE_GENERATION_TIMEOUT_SECONDS,
        DEFAULT_REQUEST_TIMEOUT_SECONDS,
        resolve_request_timeout,
    )

    assert CALL_SITE_REQUEST_TIMEOUTS["evaluation.code_generation"] == (
        CODE_GENERATION_TIMEOUT_SECONDS
    )
    assert (
        resolve_request_timeout(call_site="evaluation.code_generation", env={})
        == CODE_GENERATION_TIMEOUT_SECONDS
    )
    assert (
        resolve_request_timeout(call_site="ranking.batch", env={})
        == DEFAULT_REQUEST_TIMEOUT_SECONDS
    )
    assert (
        resolve_request_timeout(
            30, call_site="evaluation.code_generation", env={}
        )
        == 30
    )
