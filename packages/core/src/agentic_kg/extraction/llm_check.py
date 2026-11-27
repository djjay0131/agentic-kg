"""Deploy-time proof that the configured models still work.

One tiny real structured call per configured model, using the same client and
env surface the application uses. A retired or mistyped model therefore fails
the deploy (or the nightly preflight) with the provider's own message instead
of surfacing as a 502 to a user.

A second probe mirrors the largest real call shape: a bounded ranking batch
with the compact batch schema. That is the call that truncated staging when
reasoning tokens were counted against an output-sized budget, so the deploy
now fails on truncation rather than a user discovering it. Keys are never
printed.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Optional

from pydantic import BaseModel

from agentic_kg.agents.graph_port import GraphProblem, format_problems
from agentic_kg.agents.prompts import (
    RANKING_BATCH_SYSTEM_PROMPT,
    RANKING_BATCH_USER_PROMPT,
)
from agentic_kg.agents.schemas import RankingBatchResult
from agentic_kg.config import EmbeddingConfig
from agentic_kg.extraction.llm_client import create_llm_client
from agentic_kg.llm_config import (
    resolve_llm_model,
    resolve_llm_provider,
    resolve_max_output_tokens,
    resolve_ranking_batch_size,
)


class LLMCheckError(Exception):
    """A configured model did not answer successfully."""


class _Ping(BaseModel):
    """The smallest structured response that proves the call path works."""

    ok: bool


def _synthetic_candidates(count: int) -> list[GraphProblem]:
    """Synthetic candidates shaped like the real ranking input."""
    return [
        GraphProblem(
            problem_id=f"synthetic-problem-{index}",
            statement=(
                "Can the synthetic research problem "
                f"{index} be advanced with a tractable, data-available method "
                "that also benefits an adjacent field?"
            ),
            status="open",
        )
        for index in range(1, count + 1)
    ]


@dataclass
class LLMCheckResult:
    kind: str
    provider: str
    model: str
    ok: bool = True


async def _run_ranking_probe(
    client,
    *,
    provider: str,
    model: str,
) -> None:
    """Exercise the largest real call shape: one bounded ranking batch.

    This is the probe that would have failed the deploy instead of the staging
    run: the compact ranking schema plus a reasoning effort has to fit in the
    configured completion budget. A ``finish_reason=length`` or a truncation
    exception is reported with the call site and budget (the client already
    retries once at a larger budget).
    """
    try:
        batch_size = resolve_ranking_batch_size()
        budget = resolve_max_output_tokens()
    except ValueError as exc:
        raise LLMCheckError(f"ranking probe configuration: {exc}") from exc
    candidates = _synthetic_candidates(batch_size)
    user_prompt = RANKING_BATCH_USER_PROMPT.format(
        count=len(candidates),
        topic="deployment health check",
        problems_text=format_problems(candidates),
    )
    try:
        response = await client.structured_extract(
            response_model=RankingBatchResult,
            system_prompt=RANKING_BATCH_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=budget,
            call_site="llm-check.ranking_batch",
        )
    except Exception as exc:  # noqa: BLE001 - re-raised with the label
        raise LLMCheckError(
            f"{provider} ranking probe ({batch_size} candidates, "
            f"max_completion_tokens={budget}): {exc}"
        ) from exc

    if getattr(response, "finish_reason", None) == "length":
        raise LLMCheckError(
            f"{provider} ranking probe ({batch_size} candidates) hit "
            f"max_completion_tokens={budget}; the deploy would truncate"
        )
    result = getattr(response, "content", None)
    if not isinstance(result, RankingBatchResult) or not result.scores:
        raise LLMCheckError(
            f"{provider} ranking probe returned no parsed scores "
            f"(model {model!r})"
        )


async def run_llm_check(
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    embedding_model: Optional[str] = None,
    check_embedding: bool = True,
    check_ranking: bool = True,
) -> list[LLMCheckResult]:
    """Check the configured chat model and (by default) the embedding model.

    Also runs the ranking-batch probe by default, because a tiny ping does not
    prove the largest real call fits its budget.

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
            call_site="llm-check.chat",
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

    if check_ranking:
        await _run_ranking_probe(
            client, provider=resolved_provider, model=resolved_model
        )
        results.append(
            LLMCheckResult(
                kind="ranking", provider=resolved_provider, model=resolved_model
            )
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
    check_ranking: bool = True,
) -> list[LLMCheckResult]:
    """Synchronous wrapper for the nightly Job and CLI."""
    return asyncio.run(
        run_llm_check(
            provider=provider,
            model=model,
            embedding_model=embedding_model,
            check_embedding=check_embedding,
            check_ranking=check_ranking,
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
