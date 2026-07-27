"""
LLM Client Wrapper for Structured Extraction.

Provides an abstraction layer for LLM providers (OpenAI, Anthropic) with
structured output support via the instructor library, retry logic, and
token usage tracking.
"""

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agentic_kg.data_acquisition.config import RateLimitConfig
from agentic_kg.data_acquisition.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

# --- OpenAI TPM throttle (SM-6) ---------------------------------------------
# Default OpenAI tokens-per-minute ceiling (matches gpt-4-turbo's 30k tier).
_DEFAULT_TPM = 30000


def _read_tpm_budget() -> int:
    """
    Read the OpenAI TPM budget from ``OPENAI_TPM`` (default 30000).

    Raises:
        ValueError: If ``OPENAI_TPM`` is set to a non-integer or non-positive
            value. Fail loud rather than silently fall back to a default that
            would hide the misconfiguration.
    """
    raw = os.getenv("OPENAI_TPM", str(_DEFAULT_TPM))
    try:
        tpm = int(raw)
    except ValueError as e:
        raise ValueError(
            f"OPENAI_TPM must be a positive integer (tokens per minute); got {raw!r}"
        ) from e
    if tpm <= 0:
        raise ValueError(
            f"OPENAI_TPM must be a positive integer (tokens per minute); got {tpm}"
        )
    return tpm


def estimate_tokens(prompt: str, system_prompt: Optional[str], max_tokens: int) -> int:
    """
    Heuristic token estimate for a chat completion (SM-6, no tokenizer dep).

    Uses ~4 characters per token for the input and reserves the full completion
    budget. Over-reserving throttles slightly harder — the safe direction for a
    rate limit.
    """
    return (len(prompt) + len(system_prompt or "")) // 4 + max_tokens


def _parse_retry_after(exc: Exception) -> Optional[float]:
    """
    Extract the server's requested wait (seconds) from a 429, or ``None``.

    Prefers the SDK error's typed ``Retry-After`` response header; falls back to
    a regex on the message ("try again in 1.8s" / "try again in 20ms"). Returns
    ``None`` when nothing parseable is found — the caller logs that loudly so a
    format drift is visible instead of silently reverting to blind backoff.
    """
    # 1) Typed header on the SDK error's response, when present.
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        raw = headers.get("retry-after")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                # Header may be an HTTP-date rather than delta-seconds; fall through.
                pass

    # 2) Regex on the message: "try again in 1.8s" / "try again in 20ms".
    match = re.search(r"try again in ([\d.]+)\s*(ms|s)\b", str(exc), re.IGNORECASE)
    if match:
        amount = float(match.group(1))
        return amount / 1000.0 if match.group(2).lower() == "ms" else amount

    return None


def _rate_limit_wait(retry_state) -> float:
    """
    tenacity wait strategy: honor a server ``Retry-After`` when present.

    Returns the exception's ``retry_after`` when the raised ``LLMRateLimitError``
    carries one; otherwise falls back to exponential backoff.
    """
    exc = retry_state.outcome.exception()
    if isinstance(exc, LLMRateLimitError) and exc.retry_after:
        return exc.retry_after
    return wait_exponential(multiplier=1, min=1, max=60)(retry_state)

# Generic type for structured output
T = TypeVar("T", bound=BaseModel)


class LLMProvider(str, Enum):
    """Supported LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class LLMError(Exception):
    """Base exception for LLM errors."""

    pass


class LLMRateLimitError(LLMError):
    """Raised when rate limited by the LLM provider."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        self.retry_after = retry_after
        super().__init__(message)


class LLMAPIError(LLMError):
    """Raised when the LLM API returns an error."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.status_code = status_code
        super().__init__(message)


@dataclass
class TokenUsage:
    """Tracks token usage for a request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        """Add token usage from multiple requests."""
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass
class LLMResponse(Generic[T]):
    """Response from an LLM request with structured output."""

    content: T  # Parsed structured response
    raw_response: Optional[Any] = None  # Raw API response
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    finish_reason: Optional[str] = None


@dataclass
class LLMConfig:
    """Configuration for LLM clients."""

    provider: LLMProvider = LLMProvider.OPENAI
    model: str = "gpt-4-turbo"
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout: float = 60.0
    max_retries: int = 3
    api_key: Optional[str] = None

    def __post_init__(self):
        """Load API key from environment if not provided."""
        if self.api_key is None:
            if self.provider == LLMProvider.OPENAI:
                self.api_key = os.getenv("OPENAI_API_KEY")
            elif self.provider == LLMProvider.ANTHROPIC:
                self.api_key = os.getenv("ANTHROPIC_API_KEY")

        if not self.api_key:
            logger.warning(
                f"No API key found for {self.provider.value}. "
                f"Set {self.provider.value.upper()}_API_KEY environment variable."
            )


class BaseLLMClient(ABC, Generic[T]):
    """Abstract base class for LLM clients."""

    def __init__(self, config: Optional[LLMConfig] = None):
        """
        Initialize the LLM client.

        Args:
            config: LLM configuration. Uses defaults if not provided.
        """
        self.config = config or LLMConfig()
        self._total_usage = TokenUsage()

    @property
    def total_usage(self) -> TokenUsage:
        """Get cumulative token usage across all requests."""
        return self._total_usage

    def reset_usage(self) -> None:
        """Reset token usage counter."""
        self._total_usage = TokenUsage()

    @abstractmethod
    async def extract(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse[T]:
        """
        Extract structured data from text using LLM.

        Args:
            prompt: The user prompt containing text to extract from.
            response_model: Pydantic model defining the expected output structure.
            system_prompt: Optional system prompt for context.

        Returns:
            LLMResponse containing the parsed structured output.

        Raises:
            LLMError: If extraction fails.
        """
        pass


class OpenAIClient(BaseLLMClient[T]):
    """OpenAI LLM client with structured output support via instructor."""

    def __init__(self, config: Optional[LLMConfig] = None):
        """Initialize OpenAI client."""
        if config is None:
            config = LLMConfig(provider=LLMProvider.OPENAI)
        super().__init__(config)

        self._client = None
        self._instructor_client = None

    def _get_client(self):
        """Lazily initialize the OpenAI client."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as e:
                raise LLMError(
                    "openai package not installed. Install with: pip install openai"
                ) from e

            self._client = AsyncOpenAI(
                api_key=self.config.api_key,
                timeout=self.config.timeout,
            )

        return self._client

    def _get_instructor_client(self):
        """Get instructor-patched client for structured output."""
        if self._instructor_client is None:
            try:
                import instructor
            except ModuleNotFoundError as e:
                # instructor truly isn't installed — the original message is correct.
                raise LLMError(
                    "instructor package not installed. Install with: pip install instructor"
                ) from e
            except ImportError as e:
                # instructor IS installed but its import chain failed — almost
                # always a resolved-but-untested version conflict (SM-4). Surface
                # the real error instead of the misleading "not installed".
                raise LLMError(
                    f"instructor is installed but failed to import — likely a "
                    f"dependency version conflict: {e}"
                ) from e

            client = self._get_client()
            self._instructor_client = instructor.from_openai(client)

        return self._instructor_client

    @retry(
        retry=retry_if_exception_type(LLMRateLimitError),
        stop=stop_after_attempt(3),
        wait=_rate_limit_wait,
    )
    async def extract(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse[T]:
        """
        Extract structured data using OpenAI with instructor.

        Args:
            prompt: The user prompt containing text to extract from.
            response_model: Pydantic model defining the expected output structure.
            system_prompt: Optional system prompt for context.

        Returns:
            LLMResponse containing the parsed structured output.

        Raises:
            LLMError: If extraction fails.
        """
        client = self._get_instructor_client()

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # SM-6: reserve the estimated token cost from the shared TPM budget
        # BEFORE firing, so concurrent extractors serialize under the ceiling
        # instead of all 429-ing. A long predicted wait is logged up front so a
        # throttle stall reads as a legible message, not a silent hang.
        limiter = get_tpm_limiter()
        estimate = estimate_tokens(prompt, system_prompt, self.config.max_tokens)
        eta = limiter.wait_estimate(estimate)
        if eta > 5.0:
            logger.info(
                "TPM throttle: ~%.0fs wait for ~%d tokens (budget %d TPM). "
                "Raise throughput with OPENAI_EXTRACTION_MODEL=gpt-4o "
                "and a matching OPENAI_TPM.",
                eta,
                estimate,
                int(limiter.capacity),
            )
        waited = await limiter.acquire(estimate)
        if waited:
            logger.info("TPM throttle: waited %.1fs for ~%d tokens", waited, estimate)

        try:
            response, completion = await client.chat.completions.create_with_completion(
                model=self.config.model,
                messages=messages,
                response_model=response_model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

            # Track token usage
            usage = TokenUsage(
                prompt_tokens=completion.usage.prompt_tokens if completion.usage else 0,
                completion_tokens=completion.usage.completion_tokens if completion.usage else 0,
                total_tokens=completion.usage.total_tokens if completion.usage else 0,
            )
            self._total_usage = self._total_usage + usage

            return LLMResponse(
                content=response,
                raw_response=completion,
                usage=usage,
                model=self.config.model,
                finish_reason=completion.choices[0].finish_reason if completion.choices else None,
            )

        except Exception as e:
            error_str = str(e).lower()

            # Check for rate limit errors
            if "rate" in error_str and "limit" in error_str:
                # SM-6: honor the server's Retry-After hint. A parse miss is
                # logged loudly so a stale parser (OpenAI changed the error
                # surface) is visible instead of silently reverting to blind
                # exponential backoff.
                retry_after = _parse_retry_after(e)
                if retry_after is None:
                    logger.warning(
                        "OpenAI 429 with unparseable Retry-After; falling back to "
                        "exponential backoff — the parser may be stale. Error: %s",
                        e,
                    )
                raise LLMRateLimitError(
                    f"OpenAI rate limited: {e}", retry_after=retry_after
                ) from e

            # Check for API errors
            if hasattr(e, "status_code"):
                raise LLMAPIError(str(e), getattr(e, "status_code", None)) from e

            raise LLMError(f"OpenAI extraction failed: {e}") from e


class AnthropicClient(BaseLLMClient[T]):
    """Anthropic (Claude) LLM client with structured output support."""

    def __init__(self, config: Optional[LLMConfig] = None):
        """Initialize Anthropic client."""
        if config is None:
            config = LLMConfig(
                provider=LLMProvider.ANTHROPIC,
                model="claude-3-5-sonnet-20241022",
            )
        super().__init__(config)

        self._client = None
        self._instructor_client = None

    def _get_client(self):
        """Lazily initialize the Anthropic client."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as e:
                raise LLMError(
                    "anthropic package not installed. Install with: pip install anthropic"
                ) from e

            self._client = AsyncAnthropic(
                api_key=self.config.api_key,
                timeout=self.config.timeout,
            )

        return self._client

    def _get_instructor_client(self):
        """Get instructor-patched client for structured output."""
        if self._instructor_client is None:
            try:
                import instructor
            except ModuleNotFoundError as e:
                # instructor truly isn't installed — the original message is correct.
                raise LLMError(
                    "instructor package not installed. Install with: pip install instructor"
                ) from e
            except ImportError as e:
                # instructor IS installed but its import chain failed — almost
                # always a resolved-but-untested version conflict (SM-4). Surface
                # the real error instead of the misleading "not installed".
                raise LLMError(
                    f"instructor is installed but failed to import — likely a "
                    f"dependency version conflict: {e}"
                ) from e

            client = self._get_client()
            self._instructor_client = instructor.from_anthropic(client)

        return self._instructor_client

    @retry(
        retry=retry_if_exception_type(LLMRateLimitError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=60),
    )
    async def extract(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse[T]:
        """
        Extract structured data using Anthropic with instructor.

        Args:
            prompt: The user prompt containing text to extract from.
            response_model: Pydantic model defining the expected output structure.
            system_prompt: Optional system prompt for context.

        Returns:
            LLMResponse containing the parsed structured output.

        Raises:
            LLMError: If extraction fails.
        """
        client = self._get_instructor_client()

        try:
            response, completion = await client.messages.create_with_completion(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=system_prompt or "",
                messages=[{"role": "user", "content": prompt}],
                response_model=response_model,
            )

            # Track token usage
            usage = TokenUsage(
                prompt_tokens=completion.usage.input_tokens if completion.usage else 0,
                completion_tokens=completion.usage.output_tokens if completion.usage else 0,
                total_tokens=(
                    (completion.usage.input_tokens + completion.usage.output_tokens)
                    if completion.usage
                    else 0
                ),
            )
            self._total_usage = self._total_usage + usage

            return LLMResponse(
                content=response,
                raw_response=completion,
                usage=usage,
                model=self.config.model,
                finish_reason=(
                    completion.stop_reason
                    if hasattr(completion, "stop_reason")
                    else None
                ),
            )

        except Exception as e:
            error_str = str(e).lower()

            # Check for rate limit errors
            if "rate" in error_str and "limit" in error_str:
                raise LLMRateLimitError(f"Anthropic rate limited: {e}") from e

            # Check for API errors
            if hasattr(e, "status_code"):
                raise LLMAPIError(str(e), getattr(e, "status_code", None)) from e

            raise LLMError(f"Anthropic extraction failed: {e}") from e


def create_llm_client(
    provider: LLMProvider = LLMProvider.OPENAI,
    model: Optional[str] = None,
    temperature: float = 0.1,
    api_key: Optional[str] = None,
) -> BaseLLMClient:
    """
    Factory function to create an LLM client.

    Args:
        provider: The LLM provider to use.
        model: Model name (uses provider default if not specified).
        temperature: Temperature for generation.
        api_key: API key (uses environment variable if not specified).

    Returns:
        Configured LLM client.

    Raises:
        ValueError: If provider is not a valid LLMProvider.
    """
    # Validate provider before creating config
    if not isinstance(provider, LLMProvider):
        raise ValueError(f"Unsupported provider: {provider}")

    config = LLMConfig(
        provider=provider,
        temperature=temperature,
        api_key=api_key,
    )

    if model:
        config.model = model

    if provider == LLMProvider.OPENAI:
        return OpenAIClient(config)
    elif provider == LLMProvider.ANTHROPIC:
        return AnthropicClient(config)
    else:
        raise ValueError(f"Unsupported provider: {provider}")


# Singleton clients
_openai_client: Optional[OpenAIClient] = None
_anthropic_client: Optional[AnthropicClient] = None
_tpm_limiter: Optional[TokenBucketRateLimiter] = None


def get_tpm_limiter() -> TokenBucketRateLimiter:
    """
    Get or create the shared OpenAI TPM token bucket (SM-6).

    One process-global bucket paces every OpenAI extraction call across all
    extractors and papers so a concurrent burst stays under the account's
    tokens-per-minute ceiling. Sized from ``OPENAI_TPM`` (default 30000):
    ``rate = TPM / 60`` tokens/sec, capacity = one minute's budget.

    Returns:
        The shared TPM rate limiter.

    Raises:
        ValueError: If ``OPENAI_TPM`` is malformed (via ``_read_tpm_budget``).
    """
    global _tpm_limiter

    if _tpm_limiter is None:
        tpm = _read_tpm_budget()
        _tpm_limiter = TokenBucketRateLimiter(
            rate=tpm / 60.0,
            config=RateLimitConfig(burst_multiplier=60.0),  # capacity == TPM
            source="openai-tpm",
        )

    return _tpm_limiter


def get_openai_client(
    model: Optional[str] = None,
    temperature: float = 0.1,
) -> OpenAIClient:
    """
    Get or create singleton OpenAI client.

    The extraction model resolves env-first: ``OPENAI_EXTRACTION_MODEL`` wins
    when set (the operator's throughput lever — see SM-6), so a single env flip
    reaches every extractor including callers that pass an explicit model;
    otherwise the passed ``model``, otherwise ``gpt-4-turbo``.

    Args:
        model: OpenAI model name; overridden by ``OPENAI_EXTRACTION_MODEL``.
        temperature: Temperature for generation.

    Returns:
        OpenAI client instance.
    """
    global _openai_client

    if _openai_client is None:
        resolved_model = os.getenv("OPENAI_EXTRACTION_MODEL") or model or "gpt-4-turbo"
        config = LLMConfig(
            provider=LLMProvider.OPENAI,
            model=resolved_model,
            temperature=temperature,
        )
        _openai_client = OpenAIClient(config)

    return _openai_client


def get_anthropic_client(
    model: str = "claude-3-5-sonnet-20241022",
    temperature: float = 0.1,
) -> AnthropicClient:
    """
    Get or create singleton Anthropic client.

    Args:
        model: Anthropic model name.
        temperature: Temperature for generation.

    Returns:
        Anthropic client instance.
    """
    global _anthropic_client

    if _anthropic_client is None:
        config = LLMConfig(
            provider=LLMProvider.ANTHROPIC,
            model=model,
            temperature=temperature,
        )
        _anthropic_client = AnthropicClient(config)

    return _anthropic_client


def reset_llm_clients() -> None:
    """Reset all singleton LLM clients + the shared TPM limiter (for testing)."""
    global _openai_client, _anthropic_client, _tpm_limiter
    _openai_client = None
    _anthropic_client = None
    _tpm_limiter = None
