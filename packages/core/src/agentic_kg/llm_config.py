"""The one configuration surface for LLM and embedding model selection.

Every model id and token budget used at runtime resolves here. Operators set
these environment variables:

* ``LLM_PROVIDER`` -- ``openai`` (default) or ``anthropic``;
* ``LLM_MODEL`` -- the chat/extraction model for that provider;
* ``LLM_REASONING_EFFORT`` -- the reasoning effort for models that expose one
  (default ``medium``, the GPT-6.2 default; OpenAI's Chat Completions accepts it
  as ``reasoning_effort``). Set this together with ``LLM_MODEL``: a value the
  model rejects is a provider error, and GPT-6.2 Sol/Astra reject ``none``;
* ``LLM_MAX_OUTPUT_TOKENS`` -- the completion budget for every chat call.
  Reasoning models count reasoning tokens *and* visible output against this
  one ceiling, so an output-sized budget (the old 4096) truncates a reasoning
  call. Default :data:`DEFAULT_MAX_OUTPUT_TOKENS`; per-call needs are expressed
  as explicit ``max_tokens`` arguments at the call site;
* ``LLM_REQUEST_TIMEOUT_SECONDS`` -- the HTTP timeout for one chat call, in
  seconds (default :data:`DEFAULT_REQUEST_TIMEOUT_SECONDS`). A reasoning call
  can think for minutes before its first visible token, so the historical 60 s
  timeout aborted healthy calls. Code generation gets the longer per-call-site
  default in :data:`CALL_SITE_REQUEST_TIMEOUTS`; an explicit per-call timeout
  wins over both;
* ``LLM_RANKING_BATCH_SIZE`` -- how many candidates one ranking call scores
  (default :data:`DEFAULT_RANKING_BATCH_SIZE`), so ranking output does not grow
  without bound with the candidate list;
* ``EMBEDDING_MODEL`` -- the OpenAI embedding model (default
  ``text-embedding-3-small``, 1536 dimensions, matching the existing vector
  index).

``OPENAI_EXTRACTION_MODEL`` is a legacy alias for ``LLM_MODEL`` when the
provider is OpenAI; ``LLM_MODEL`` wins when both are set. The canonical
surface is what Terraform sets on the API service and every Job, so a retired
model can be changed in one place without a code change.

Defaults are current generally-available models, checked against the
providers' published model/deprecation pages when this module was written:

* OpenAI model catalog: https://platform.openai.com/docs/models
  (``gpt-6.2-sol`` is the documented go-to model for everyday work and agents)
* OpenAI deprecations: https://platform.openai.com/docs/deprecations
* OpenAI embeddings: https://platform.openai.com/docs/guides/embeddings
* OpenAI structured outputs: https://platform.openai.com/docs/guides/structured-outputs
* Anthropic models: https://docs.anthropic.com/en/docs/about-claude/models/overview
  (``claude-sonnet-5-5`` is the current speed/intelligence model)
* Anthropic deprecations: https://docs.anthropic.com/en/docs/about-claude/model-deprecations

No retired model id is spelled in this module (or any other source file); the
denylist test in ``packages/core/tests/test_llm_config.py`` enforces that.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

LLM_PROVIDER_ENV = "LLM_PROVIDER"
LLM_MODEL_ENV = "LLM_MODEL"
REASONING_EFFORT_ENV = "LLM_REASONING_EFFORT"
MAX_OUTPUT_TOKENS_ENV = "LLM_MAX_OUTPUT_TOKENS"
REQUEST_TIMEOUT_ENV = "LLM_REQUEST_TIMEOUT_SECONDS"
RANKING_BATCH_SIZE_ENV = "LLM_RANKING_BATCH_SIZE"
EMBEDDING_MODEL_ENV = "EMBEDDING_MODEL"
LEGACY_OPENAI_MODEL_ENV = "OPENAI_EXTRACTION_MODEL"

DEFAULT_LLM_PROVIDER = "openai"
DEFAULT_OPENAI_MODEL = "gpt-6.2-sol"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# The reasoning effort values OpenAI documents for reasoning models. Not every
# model accepts every value: GPT-6.2 Sol/Astra reject ``none`` (and GPT-6.1 Sol
# rejects ``none``/``minimal``). The default is the GPT-6.2 default, so sending
# it explicitly does not change model behaviour; a value the configured model
# rejects surfaces as a provider error from ``agentic-kg llm-check``.
DEFAULT_REASONING_EFFORT = "medium"
SUPPORTED_REASONING_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

SUPPORTED_PROVIDERS = ("openai", "anthropic")

# ---------------------------------------------------------------------------
# Completion budgets
# ---------------------------------------------------------------------------
# A reasoning model counts reasoning tokens AND visible output against
# ``max_completion_tokens``. 4096 (the historical output-only budget) was spent
# on reasoning alone by a 20-candidate ranking call, so the response arrived
# ``finish_reason="length"`` and instructor raised "The output is incomplete due
# to a max_tokens length limit". The default below leaves room for reasoning
# plus a normal structured response; calls whose visible output is legitimately
# larger (e.g. evaluation code generation) pass an explicit ``max_tokens``.
#
# The ceiling bounds a truncation retry. It is deliberately below the documented
# maximum completion tokens of current OpenAI reasoning families; a configured
# model with a smaller ceiling will reject an over-large request with its own
# provider error, which is preferable to an unbounded retry. The default is
# intentionally conservative: ranking is batched, so no single response needs a
# model's full output window.
DEFAULT_MAX_OUTPUT_TOKENS = 16384
OUTPUT_TOKEN_CEILING = 32768

#: Larger fixed budgets for call sites whose *visible* output can legitimately
#: exceed a normal structured response. These are overrides, not defaults.
EVALUATION_CODE_MAX_OUTPUT_TOKENS = 24576
SYNTHESIS_MAX_OUTPUT_TOKENS = 16384
CONTINUATION_MAX_OUTPUT_TOKENS = 16384

#: Ranking scores candidates in bounded batches so the response size is a
#: function of the batch, never of the whole candidate set.
DEFAULT_RANKING_BATCH_SIZE = 8

# ---------------------------------------------------------------------------
# Request timeouts
# ---------------------------------------------------------------------------
# A reasoning model can spend minutes on hidden reasoning before the first
# visible token arrives. The historical 60 s client timeout therefore aborted
# healthy calls; staging surfaced it as "OpenAI extraction failed: Request
# timed out" after the gate request had already been waiting for minutes. The
# default below is long enough for a normal reasoning response. A call site
# whose visible output is a complete program gets a longer default; the
# operator can raise the base with LLM_REQUEST_TIMEOUT_SECONDS.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 180.0

#: Per-call-site timeout floors. ``evaluation.code_generation`` returns a whole
#: Python program at a 24576-token budget, so it is the longest call in the
#: pipeline. An operator may raise the base above these, but not lower a call
#: site below its floor unless the call passes an explicit timeout.
CODE_GENERATION_TIMEOUT_SECONDS = 600.0
CALL_SITE_REQUEST_TIMEOUTS: dict[str, float] = {
    "evaluation.code_generation": CODE_GENERATION_TIMEOUT_SECONDS,
    "llm-check.code_generation": CODE_GENERATION_TIMEOUT_SECONDS,
}


def _value(env: Mapping[str, str], name: str) -> Optional[str]:
    """Return a stripped, non-empty env value, or ``None``."""
    raw = env.get(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def resolve_llm_provider(
    explicit: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the provider, validating it against the supported set.

    Precedence: an explicit provider argument, then ``LLM_PROVIDER``, then
    ``openai``. An unknown value raises rather than silently calling the wrong
    provider.
    """
    source = os.environ if env is None else env
    raw = explicit if explicit is not None else _value(source, LLM_PROVIDER_ENV)
    provider = (raw or DEFAULT_LLM_PROVIDER).strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"unsupported LLM provider {raw!r}; expected one of "
            f"{', '.join(SUPPORTED_PROVIDERS)}"
        )
    return provider


def default_model_for(provider: str) -> str:
    """The current generally-available default for ``provider``."""
    if provider == "anthropic":
        return DEFAULT_ANTHROPIC_MODEL
    return DEFAULT_OPENAI_MODEL


def resolve_llm_model(
    provider: str,
    explicit: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the chat/extraction model for ``provider``.

    Precedence (highest first):

    1. ``LLM_MODEL`` -- the canonical operator lever; it deliberately wins
       over a caller's explicit model so one env flip reaches every extractor.
    2. ``OPENAI_EXTRACTION_MODEL`` for OpenAI -- the legacy alias kept for
       existing runbooks and deployments.
    3. the explicit ``model`` argument.
    4. the provider default.
    """
    source = os.environ if env is None else env
    configured = _value(source, LLM_MODEL_ENV)
    if configured:
        return configured
    if provider == "openai":
        legacy = _value(source, LEGACY_OPENAI_MODEL_ENV)
        if legacy:
            return legacy
    if explicit and explicit.strip():
        return explicit.strip()
    return default_model_for(provider)


def resolve_reasoning_effort(
    explicit: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the reasoning effort for a chat-completion request.

    Precedence: an explicit argument, then ``LLM_REASONING_EFFORT``, then
    :data:`DEFAULT_REASONING_EFFORT`. An unknown value raises rather than being
    forwarded to a provider, because a typo would otherwise surface only at the
    first real call.
    """
    source = os.environ if env is None else env
    raw = explicit if explicit is not None else _value(source, REASONING_EFFORT_ENV)
    effort = (raw or DEFAULT_REASONING_EFFORT).strip().lower()
    if effort not in SUPPORTED_REASONING_EFFORTS:
        raise ValueError(
            f"unsupported reasoning effort {raw!r}; expected one of "
            f"{', '.join(SUPPORTED_REASONING_EFFORTS)}"
        )
    return effort


def _positive_int(raw: str, name: str) -> int:
    """Parse a positive integer configuration value or raise with its name."""
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer; got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer; got {value}")
    return value


def resolve_max_output_tokens(
    explicit: Optional[int] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> int:
    """Resolve the completion budget for a chat call.

    Precedence: an explicit per-call value, then ``LLM_MAX_OUTPUT_TOKENS``, then
    :data:`DEFAULT_MAX_OUTPUT_TOKENS`. The result may not exceed
    :data:`OUTPUT_TOKEN_CEILING`; an over-large value raises instead of being
    forwarded, because silently clamping would make an operator's setting look
    applied when it was not.
    """
    source = os.environ if env is None else env
    if explicit is not None:
        budget = _positive_int(str(explicit), "max_tokens")
    else:
        raw = _value(source, MAX_OUTPUT_TOKENS_ENV)
        budget = (
            _positive_int(raw, MAX_OUTPUT_TOKENS_ENV)
            if raw is not None
            else DEFAULT_MAX_OUTPUT_TOKENS
        )
    if budget > OUTPUT_TOKEN_CEILING:
        raise ValueError(
            f"max output tokens {budget} exceeds the supported ceiling "
            f"{OUTPUT_TOKEN_CEILING}; lower {MAX_OUTPUT_TOKENS_ENV} or the "
            "call-site budget"
        )
    return budget


def _positive_float(raw: str, name: str) -> float:
    """Parse a positive float configuration value or raise with its name."""
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number; got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number; got {value}")
    return value


def resolve_request_timeout(
    explicit: Optional[float] = None,
    *,
    call_site: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> float:
    """Resolve the HTTP timeout for one chat call, in seconds.

    Precedence (highest first):

    1. an explicit per-call value (a caller that knows its own shape);
    2. the call site's floor in :data:`CALL_SITE_REQUEST_TIMEOUTS` when the
       operator's base is shorter than it (code generation is the long case);
    3. ``LLM_REQUEST_TIMEOUT_SECONDS``;
    4. :data:`DEFAULT_REQUEST_TIMEOUT_SECONDS`.

    A non-positive or malformed value raises instead of silently reverting to
    a default, so an operator sees the misconfiguration immediately.
    """
    if explicit is not None:
        return _positive_float(str(explicit), "request timeout")
    source = os.environ if env is None else env
    raw = _value(source, REQUEST_TIMEOUT_ENV)
    base = (
        _positive_float(raw, REQUEST_TIMEOUT_ENV)
        if raw is not None
        else DEFAULT_REQUEST_TIMEOUT_SECONDS
    )
    floor = CALL_SITE_REQUEST_TIMEOUTS.get(call_site or "")
    if floor is not None:
        return max(base, floor)
    return base


def resolve_ranking_batch_size(
    explicit: Optional[int] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> int:
    """Resolve how many candidates one ranking call may score.

    Precedence: an explicit value, then ``LLM_RANKING_BATCH_SIZE``, then
    :data:`DEFAULT_RANKING_BATCH_SIZE`. Batching is what keeps a ranking
    response bounded by the batch rather than by the whole candidate list.
    """
    source = os.environ if env is None else env
    if explicit is not None:
        return _positive_int(str(explicit), "ranking batch size")
    raw = _value(source, RANKING_BATCH_SIZE_ENV)
    if raw is not None:
        return _positive_int(raw, RANKING_BATCH_SIZE_ENV)
    return DEFAULT_RANKING_BATCH_SIZE


def resolve_embedding_model(
    explicit: Optional[str] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the embedding model, env-first (same reason as the chat model)."""
    source = os.environ if env is None else env
    return (
        _value(source, EMBEDDING_MODEL_ENV)
        or (explicit.strip() if explicit and explicit.strip() else None)
        or DEFAULT_EMBEDDING_MODEL
    )


def default_chat_model(*, env: Optional[Mapping[str, str]] = None) -> str:
    """Resolve the configured chat model from the environment."""
    provider = resolve_llm_provider(env=env)
    return resolve_llm_model(provider, env=env)


__all__ = [
    "CALL_SITE_REQUEST_TIMEOUTS",
    "CODE_GENERATION_TIMEOUT_SECONDS",
    "CONTINUATION_MAX_OUTPUT_TOKENS",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_LLM_PROVIDER",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_RANKING_BATCH_SIZE",
    "DEFAULT_REASONING_EFFORT",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "EMBEDDING_MODEL_ENV",
    "EVALUATION_CODE_MAX_OUTPUT_TOKENS",
    "LEGACY_OPENAI_MODEL_ENV",
    "LLM_MODEL_ENV",
    "LLM_PROVIDER_ENV",
    "MAX_OUTPUT_TOKENS_ENV",
    "OUTPUT_TOKEN_CEILING",
    "RANKING_BATCH_SIZE_ENV",
    "REASONING_EFFORT_ENV",
    "REQUEST_TIMEOUT_ENV",
    "SUPPORTED_PROVIDERS",
    "SUPPORTED_REASONING_EFFORTS",
    "SYNTHESIS_MAX_OUTPUT_TOKENS",
    "default_chat_model",
    "default_model_for",
    "resolve_embedding_model",
    "resolve_llm_model",
    "resolve_llm_provider",
    "resolve_max_output_tokens",
    "resolve_ranking_batch_size",
    "resolve_reasoning_effort",
    "resolve_request_timeout",
]
