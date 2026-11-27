"""The one configuration surface for LLM and embedding model selection.

Every model id used at runtime resolves here. Operators set four environment
variables:

* ``LLM_PROVIDER`` -- ``openai`` (default) or ``anthropic``;
* ``LLM_MODEL`` -- the chat/extraction model for that provider;
* ``LLM_REASONING_EFFORT`` -- the reasoning effort for models that expose one
  (default ``medium``, the GPT-6.2 default; OpenAI's Chat Completions accepts it
  as ``reasoning_effort``). Set this together with ``LLM_MODEL``: a value the
  model rejects is a provider error, and GPT-6.2 Sol/Astra reject ``none``;
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
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_LLM_PROVIDER",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_REASONING_EFFORT",
    "EMBEDDING_MODEL_ENV",
    "LEGACY_OPENAI_MODEL_ENV",
    "LLM_MODEL_ENV",
    "LLM_PROVIDER_ENV",
    "REASONING_EFFORT_ENV",
    "SUPPORTED_PROVIDERS",
    "SUPPORTED_REASONING_EFFORTS",
    "default_chat_model",
    "default_model_for",
    "resolve_embedding_model",
    "resolve_llm_model",
    "resolve_llm_provider",
    "resolve_reasoning_effort",
]
