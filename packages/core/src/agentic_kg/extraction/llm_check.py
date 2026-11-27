"""Deploy-time proof that the configured models still work.

One tiny real structured call per configured model, using the same client and
env surface the application uses. A retired or mistyped model therefore fails
the deploy (or the nightly preflight) with the provider's own message instead
of surfacing as a 502 to a user. Keys are never printed.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Optional

from pydantic import BaseModel

from agentic_kg.config import EmbeddingConfig
from agentic_kg.extraction.llm_client import create_llm_client
from agentic_kg.llm_config import resolve_llm_model, resolve_llm_provider


class LLMCheckError(Exception):
    """A configured model did not answer successfully."""


class _Ping(BaseModel):
    """The smallest structured response that proves the call path works."""

    ok: bool


@dataclass
class LLMCheckResult:
    kind: str
    provider: str
    model: str
    ok: bool = True


async def run_llm_check(
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    embedding_model: Optional[str] = None,
    check_embedding: bool = True,
) -> list[LLMCheckResult]:
    """Check the configured chat model and (by default) the embedding model.

    Raises:
        LLMCheckError: Carries the provider's message; the CLI/nightly code
            turns it into a non-zero exit.
    """
    resolved_provider = resolve_llm_provider(provider)
    resolved_model = resolve_llm_model(resolved_provider, explicit=model)
    results: list[LLMCheckResult] = []

    client = create_llm_client(provider=resolved_provider, model=resolved_model)
    try:
        response = await client.extract(
            prompt="Return ok=true. This is a deployment health check.",
            response_model=_Ping,
            system_prompt="Reply with the requested structured JSON only.",
        )
    except Exception as exc:  # noqa: BLE001 - re-raised with the label
        raise LLMCheckError(
            f"{resolved_provider} chat model {resolved_model!r}: {exc}"
        ) from exc
    if getattr(response.content, "ok", None) is not True:
        raise LLMCheckError(
            f"{resolved_provider} chat model {resolved_model!r} returned an "
            f"unexpected response"
        )
    results.append(
        LLMCheckResult(kind="chat", provider=resolved_provider, model=resolved_model)
    )

    if check_embedding:
        config = EmbeddingConfig()
        if embedding_model:
            config.model = embedding_model
        if not config.is_configured:
            raise LLMCheckError(
                f"embeddings {config.model!r}: OPENAI_API_KEY is not configured"
            )
        # Imported lazily: the chat check must not need the graph package.
        from agentic_kg.knowledge_graph.embeddings import EmbeddingService

        try:
            vector = EmbeddingService(config).generate_embedding("agentic-kg llm-check")
        except Exception as exc:  # noqa: BLE001 - re-raised with the label
            raise LLMCheckError(f"embedding model {config.model!r}: {exc}") from exc
        if len(vector) != config.dimensions:
            raise LLMCheckError(
                f"embedding model {config.model!r} returned {len(vector)} "
                f"dimensions; the vector index expects {config.dimensions}. "
                "Refusing to change the index dimension implicitly."
            )
        results.append(
            LLMCheckResult(kind="embedding", provider="openai", model=config.model)
        )

    return results


def run_llm_check_sync(
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    embedding_model: Optional[str] = None,
    check_embedding: bool = True,
) -> list[LLMCheckResult]:
    """Synchronous wrapper for the nightly Job and CLI."""
    return asyncio.run(
        run_llm_check(
            provider=provider,
            model=model,
            embedding_model=embedding_model,
            check_embedding=check_embedding,
        )
    )


def results_as_dicts(results: list[LLMCheckResult]) -> list[dict[str, object]]:
    return [asdict(result) for result in results]


__all__ = [
    "LLMCheckError",
    "LLMCheckResult",
    "results_as_dicts",
    "run_llm_check",
    "run_llm_check_sync",
]
