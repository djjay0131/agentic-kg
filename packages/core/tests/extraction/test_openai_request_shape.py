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
from agentic_kg.extraction.llm_client import LLMConfig, LLMProvider, OpenAIClient
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
