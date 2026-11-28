"""
LLM Client Wrapper for Structured Extraction.

Provides an abstraction layer for LLM providers (OpenAI, Anthropic) with
structured output support via the instructor library, retry logic, and
token usage tracking.
"""

import asyncio
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
from agentic_kg.llm_config import (
    OUTPUT_TOKEN_CEILING,
    resolve_llm_model,
    resolve_llm_provider,
    resolve_max_output_tokens,
    resolve_reasoning_effort,
    resolve_request_timeout,
)

logger = logging.getLogger(__name__)

# --- OpenAI TPM throttle (SM-6) ---------------------------------------------
# Default OpenAI tokens-per-minute ceiling. Set OPENAI_TPM to match the
# configured model's tier; the conservative default keeps a burst under a
# low-tier account rather than 429-ing.
_DEFAULT_TPM = 30000

# Pre-call reservation for a completion whose max budget is larger than a
# typical response. Reserving the full completion budget (now reasoning-aware,
# 16k+) makes the throttle wait ~34s per call at a 30k TPM tier even when the
# call actually uses ~2k. Reserve the historical output-sized 4096 as the
# expected completion, then reconcile the provider-reported total after the
# call. Operators can tune the bound with OPENAI_TPM_RESERVE_TOKENS.
_DEFAULT_TPM_RESERVE = 4096


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


def _read_tpm_reserve() -> int:
    """Read the pre-call completion reservation (``OPENAI_TPM_RESERVE_TOKENS``)."""
    raw = os.getenv("OPENAI_TPM_RESERVE_TOKENS", str(_DEFAULT_TPM_RESERVE))
    try:
        reserve = int(raw)
    except ValueError as e:
        raise ValueError(
            "OPENAI_TPM_RESERVE_TOKENS must be a positive integer; "
            f"got {raw!r}"
        ) from e
    if reserve <= 0:
        raise ValueError(
            "OPENAI_TPM_RESERVE_TOKENS must be a positive integer; "
            f"got {reserve}"
        )
    return reserve


def tpm_reserve(max_tokens: int) -> int:
    """The completion tokens to reserve before a call.

    The completion budget exists so a response is not truncated; it is not a
    forecast of how many tokens a call will use. Reserving the whole budget
    would pace every call at the maximum, so the proactive throttle reserves at
    most ``OPENAI_TPM_RESERVE_TOKENS`` (default 8192) and reconciles actual
    provider-reported usage after the call.
    """
    return min(max_tokens, _read_tpm_reserve())


def estimate_tokens(prompt: str, system_prompt: Optional[str], max_tokens: int) -> int:
    """
    Heuristic token estimate for a chat completion (SM-6, no tokenizer dep).

    Uses ~4 characters per token for the input and adds the supplied completion
    reservation (see :func:`tpm_reserve`; callers should pass the bounded
    reserve, not the full truncation budget).
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


def _strict_schema_supported(response_model: type[BaseModel]) -> bool:
    """Whether OpenAI's strict structured-output subset can express the model.

    Structured outputs accept a JSON schema either strictly (OpenAI's restricted
    subset, which guarantees adherence) or as a best-effort schema. A schema
    with an arbitrary mapping (``dict[str, ...]``) cannot be made strict — the
    synthesis report and the batch extraction result both have one — so forcing
    ``strict=True`` would break those call sites at request-build time.

    ``instructor`` owns the conversion; ask it rather than guessing. If the
    introspection API is unavailable in a resolved instructor version, decline
    strict mode (still a JSON-schema request, validated by pydantic, with
    instructor's re-ask on validation failure) instead of risking a crash.
    """
    try:
        from instructor.v2.core.schema import make_strict_schema
    except Exception:  # noqa: BLE001 - optional introspection, not a hard dep
        return False
    try:
        make_strict_schema(response_model.model_json_schema())
    except Exception:  # noqa: BLE001 - unsupported schema, not a failure
        return False
    return True


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


class LLMTruncationError(LLMError):
    """Raised when a completion hit its budget even after one retry.

    The message names the call site, the budget that was used and the reasoning
    effort, because those are the three levers an operator can change. A budget
    cannot silently keep growing: the retry is bounded by
    :data:`agentic_kg.llm_config.OUTPUT_TOKEN_CEILING`.
    """

    def __init__(
        self,
        *,
        call_site: str,
        budget: int,
        reasoning_effort: Optional[str],
        retried_budget: Optional[int] = None,
    ):
        self.call_site = call_site
        self.budget = budget
        self.reasoning_effort = reasoning_effort
        self.retried_budget = retried_budget
        retry_text = (
            f"; retried once at max_completion_tokens={retried_budget}"
            if retried_budget is not None
            else "; no larger budget was available within the ceiling"
        )
        super().__init__(
            f"LLM call {call_site!r} was truncated: the response hit "
            f"max_completion_tokens={budget}{retry_text} with "
            f"reasoning_effort={reasoning_effort!r}. Reasoning tokens count "
            "against the completion budget, so raise LLM_MAX_OUTPUT_TOKENS or "
            "reduce the input batch."
        )


class LLMTimeoutError(LLMError):
    """Raised when a completion timed out even after one retry.

    The message names the call site, the timeout and the completion budget,
    because those are the three levers an operator can change. Retrying once
    is deliberate: a reasoning call that loses a race with a cold connection
    should get one more chance, but a call that cannot finish inside a
    generous timeout should fail loudly rather than hang a workflow.
    """

    def __init__(
        self,
        *,
        call_site: str,
        timeout: float,
        budget: int,
        reasoning_effort: Optional[str],
        retried: bool = True,
    ):
        self.call_site = call_site
        self.timeout = timeout
        self.budget = budget
        self.reasoning_effort = reasoning_effort
        self.retried = retried
        retry_text = (
            " after one retry" if retried else " (no retry attempted)"
        )
        super().__init__(
            f"LLM call {call_site!r} timed out after {timeout:g}s{retry_text} "
            f"(max_completion_tokens={budget}, "
            f"reasoning_effort={reasoning_effort!r}). Set "
            "LLM_REQUEST_TIMEOUT_SECONDS or pass a per-call timeout."
        )


#: Seconds to wait before the single timeout retry. Overridable in tests.
TIMEOUT_RETRY_BACKOFF_SECONDS = 2.0

#: Exception class names / message markers that mean a request timed out.
_TIMEOUT_CLASS_MARKERS = (
    "timeout",
    "timedout",
    "readtimeout",
    "connecttimeout",
    "apitimeout",
)
_TIMEOUT_MESSAGE_MARKERS = (
    "timed out",
    "timeout",
    "read timeout",
    "request timed out",
)


def _is_timeout_error(exc: Exception) -> bool:
    """Whether ``exc`` reports a request timeout (SDK-version tolerant)."""
    if isinstance(exc, LLMTimeoutError):
        return False
    name = type(exc).__name__.lower()
    if any(marker in name for marker in _TIMEOUT_CLASS_MARKERS):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _TIMEOUT_MESSAGE_MARKERS)


# Error text/markers that mean "the model ran out of completion budget".
# Instructor raises ``IncompleteOutputException`` with the provider's message;
# some versions surface ``finish_reason`` in the message instead. Match both
# without importing a version-specific instructor class.
_TRUNCATION_MARKERS = (
    "incomplete due to a max_tokens",
    "max_tokens length limit",
    "finish_reason 'length'",
    'finish_reason="length"',
    "finish_reason=length",
    "stop_reason 'max_tokens'",
    'stop_reason="max_tokens"',
)


def _is_truncation_error(exc: Exception) -> bool:
    """Whether ``exc`` reports a completion that hit its length limit."""
    name = type(exc).__name__.lower()
    if "incompleteoutput" in name or "lengthfinishreason" in name:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _TRUNCATION_MARKERS)


@dataclass
class TokenUsage:
    """Tracks token usage for a request.

    Reasoning models report the hidden reasoning tokens separately from the
    visible completion. Keep both so budgets can be tuned from evidence instead
    of guesswork; ``completion_tokens`` is the provider's total completion
    count (reasoning + visible) where the provider reports it that way.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        """Add token usage from multiple requests."""
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


def _openai_usage(completion: Any) -> TokenUsage:
    """Extract prompt/completion/reasoning tokens from an OpenAI completion."""
    usage = getattr(completion, "usage", None)
    if usage is None:
        return TokenUsage()
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = int(getattr(details, "reasoning_tokens", 0) or 0) if details else 0
    return TokenUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
        reasoning_tokens=reasoning,
    )


def _anthropic_usage(completion: Any) -> TokenUsage:
    """Extract usage from an Anthropic completion.

    Anthropic reports ``output_tokens`` (which includes any extended thinking
    the client requested); it does not split reasoning out in the response, so
    ``reasoning_tokens`` stays 0 rather than being guessed.
    """
    usage = getattr(completion, "usage", None)
    if usage is None:
        return TokenUsage()
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    return TokenUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def _log_usage(
    *,
    provider: str,
    call_site: str,
    model: str,
    budget: int,
    reasoning_effort: Optional[str],
    usage: TokenUsage,
    finish_reason: Optional[str],
) -> None:
    """Record per-call usage so budgets can be tuned from evidence."""
    logger.info(
        "LLM usage provider=%s call_site=%s model=%s budget=%d "
        "reasoning_effort=%s input=%d reasoning=%d output=%d total=%d "
        "finish_reason=%s",
        provider,
        call_site,
        model,
        budget,
        reasoning_effort,
        usage.prompt_tokens,
        usage.reasoning_tokens,
        usage.completion_tokens,
        usage.total_tokens,
        finish_reason,
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

    provider: Optional[LLMProvider] = None
    model: Optional[str] = None
    temperature: float = 0.1
    # Reasoning + visible output share this budget. The default reads
    # ``LLM_MAX_OUTPUT_TOKENS``; per-call overrides are passed to ``extract``.
    max_tokens: int = field(default_factory=resolve_max_output_tokens)
    timeout: float = field(default_factory=resolve_request_timeout)
    max_retries: int = 3
    api_key: Optional[str] = None
    reasoning_effort: Optional[str] = None

    def __post_init__(self):
        """Resolve provider/model/effort from the one config surface, then the key."""
        if self.provider is None:
            self.provider = LLMProvider(resolve_llm_provider())
        elif not isinstance(self.provider, LLMProvider):
            self.provider = LLMProvider(resolve_llm_provider(str(self.provider)))
        self.model = resolve_llm_model(self.provider.value, explicit=self.model)
        self.max_tokens = resolve_max_output_tokens(self.max_tokens)
        if self.reasoning_effort is None:
            self.reasoning_effort = resolve_reasoning_effort()

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
        *,
        max_tokens: Optional[int] = None,
        call_site: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> LLMResponse[T]:
        """
        Extract structured data from text using LLM.

        Args:
            prompt: The user prompt containing text to extract from.
            response_model: Pydantic model defining the expected output structure.
            system_prompt: Optional system prompt for context.
            max_tokens: Per-call completion budget (reasoning + output). Defaults
                to the client's ``LLM_MAX_OUTPUT_TOKENS`` budget.
            call_site: Short label used in usage logs and timeout/truncation errors.
            reasoning_effort: Per-call reasoning effort; defaults to the client's
                configured effort.
            timeout: Per-call HTTP timeout in seconds; defaults to the call
                site's timeout surface (``LLM_REQUEST_TIMEOUT_SECONDS``).

        Returns:
            LLMResponse containing the parsed structured output.

        Raises:
            LLMError: If extraction fails.
            LLMTruncationError: If the completion hit its budget after one retry.
        """
        pass

    async def structured_extract(
        self,
        response_model: type[T],
        system_prompt: Optional[str] = None,
        user_prompt: Optional[str] = None,
        *,
        prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        call_site: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> LLMResponse[T]:
        """Structured extraction using the research agents' call shape.

        The agents were written against a ``structured_extract(response_model=,
        system_prompt=, user_prompt=)`` interface while the concrete clients
        implement ``extract(prompt=, response_model=, system_prompt=)``. Keep
        both: this adapter delegates to :meth:`extract` so every real provider
        client works, and returns the same :class:`LLMResponse` the agents read
        via ``response.content``.

        ``prompt`` is accepted as an alias for ``user_prompt`` for callers that
        use the ``extract`` argument name.
        """
        text = user_prompt if user_prompt is not None else prompt
        if text is None:
            raise ValueError("structured_extract requires user_prompt or prompt")
        return await self.extract(
            prompt=text,
            response_model=response_model,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            call_site=call_site,
            reasoning_effort=reasoning_effort,
            timeout=timeout,
        )


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

            # Retries are explicit here: tenacity owns rate limits, and
            # ``extract`` owns the single timeout retry. The SDK's hidden
            # retries would multiply a timeout wait and make the job budget
            # unpredictable.
            self._client = AsyncOpenAI(
                api_key=self.config.api_key,
                timeout=self.config.timeout,
                max_retries=0,
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

            # Schema-constrained structured outputs via ``response_format:
            # {type: json_schema}``, not function tools. Current reasoning
            # model families reject function tools on /v1/chat/completions when
            # reasoning_effort is not ``none`` (and some reject ``none`` too), so
            # JSON_SCHEMA keeps one request shape working for every model we
            # configure. Instructor still validates the parsed result against
            # the pydantic model, including re-asks on validation failure.
            client = self._get_client()
            self._instructor_client = instructor.from_openai(
                client, mode=instructor.Mode.JSON_SCHEMA
            )

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
        *,
        max_tokens: Optional[int] = None,
        call_site: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> LLMResponse[T]:
        """
        Extract structured data using OpenAI with instructor.

        Args:
            prompt: The user prompt containing text to extract from.
            response_model: Pydantic model defining the expected output structure.
            system_prompt: Optional system prompt for context.
            max_tokens: Per-call completion budget (reasoning + output). The
                default is ``LLM_MAX_OUTPUT_TOKENS``.
            call_site: Short label used in usage logs and timeout/truncation errors.
            reasoning_effort: Per-call reasoning effort (defaults to the client).
            timeout: Per-call HTTP timeout in seconds (defaults to the call
                site's timeout surface).

        Returns:
            LLMResponse containing the parsed structured output.

        Raises:
            LLMError: If extraction fails.
            LLMTruncationError: If the completion hit its budget after one retry.
        """
        client = self._get_instructor_client()
        site = call_site or "openai.extract"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        budget = (
            self.config.max_tokens if max_tokens is None else resolve_max_output_tokens(max_tokens)
        )
        # One bounded retry: reasoning tokens count against the same budget, so
        # a call that reasoned longer than expected gets a single larger chance.
        retry_budget = min(budget * 2, OUTPUT_TOKEN_CEILING)
        attempts: list[tuple[int, Optional[int]]] = [(budget, None)]
        if retry_budget > budget:
            attempts.append((retry_budget, budget))

        # Reasoning effort is explicit so the request shape never depends on a
        # model family's implicit default. Reasoning models reject sampling
        # parameters (``temperature``) when they are actually reasoning, so only
        # send it when the effort is ``none`` — where a non-reasoning request
        # accepts it. The current GPT-6.2 family rejects ``none`` itself; the
        # default is ``medium`` (see ``agentic_kg.llm_config``). A call site may
        # lower it (code generation uses ``low``).
        effort = (
            resolve_reasoning_effort(reasoning_effort)
            if reasoning_effort is not None
            else self.config.reasoning_effort
        )
        request_timeout = resolve_request_timeout(timeout, call_site=site)
        for attempt_budget, previous_budget in attempts:
            # SM-6: reserve the estimated token cost from the shared TPM budget
            # BEFORE firing, so concurrent extractors serialize under the ceiling
            # instead of all 429-ing. A long predicted wait is logged up front so
            # a throttle stall reads as a legible message, not a silent hang.
            limiter = get_tpm_limiter()
            estimate = estimate_tokens(
                prompt, system_prompt, tpm_reserve(attempt_budget)
            )
            eta = limiter.wait_estimate(estimate)
            if eta > 5.0:
                logger.info(
                    "TPM throttle: ~%.0fs wait for ~%d tokens (budget %d TPM). "
                    "Raise throughput with LLM_MODEL=<higher-tier model> "
                    "and a matching OPENAI_TPM.",
                    eta,
                    estimate,
                    int(limiter.capacity),
                )
            waited = await limiter.acquire(estimate)
            if waited:
                logger.info("TPM throttle: waited %.1fs for ~%d tokens", waited, estimate)

            request_kwargs: dict[str, Any] = {
                "model": self.config.model,
                "messages": messages,
                "response_model": response_model,
                # Strict schema adherence where OpenAI's subset can express the
                # pydantic model; best-effort JSON schema (still validated by
                # instructor/pydantic) where it cannot.
                "strict": _strict_schema_supported(response_model),
                # Current OpenAI models reject the legacy ``max_tokens``
                # parameter ("use max_completion_tokens instead"); this is the
                # successor name for the same completion budget.
                "max_completion_tokens": attempt_budget,
            }
            if effort:
                request_kwargs["reasoning_effort"] = effort
            if effort in (None, "", "none"):
                request_kwargs["temperature"] = self.config.temperature

            request_kwargs["timeout"] = request_timeout
            retry_truncation = False
            for timeout_attempt in range(2):
                try:
                    response, completion = await client.chat.completions.create_with_completion(
                        **request_kwargs
                    )
                    break
                except LLMRateLimitError:
                    raise
                except Exception as e:  # noqa: BLE001 - classified below
                    if _is_timeout_error(e):
                        if timeout_attempt == 0:
                            logger.warning(
                                "LLM call %s timed out after %gs "
                                "(max_completion_tokens=%d, reasoning_effort=%s); "
                                "retrying once",
                                site,
                                request_timeout,
                                attempt_budget,
                                effort,
                            )
                            await asyncio.sleep(TIMEOUT_RETRY_BACKOFF_SECONDS)
                            continue
                        raise LLMTimeoutError(
                            call_site=site,
                            timeout=request_timeout,
                            budget=attempt_budget,
                            reasoning_effort=effort,
                        ) from e

                    if _is_truncation_error(e):
                        if previous_budget is not None:
                            raise LLMTruncationError(
                                call_site=site,
                                budget=attempt_budget,
                                reasoning_effort=effort,
                                retried_budget=attempt_budget,
                            ) from e
                        if retry_budget > attempt_budget:
                            logger.warning(
                                "LLM call %s hit max_completion_tokens=%d; "
                                "retrying once at %d",
                                site,
                                attempt_budget,
                                retry_budget,
                            )
                            retry_truncation = True
                            break
                        raise LLMTruncationError(
                            call_site=site,
                            budget=attempt_budget,
                            reasoning_effort=effort,
                        ) from e

                    error_str = str(e).lower()
                    if "rate" in error_str and "limit" in error_str:
                        # SM-6: honor the server's Retry-After hint. A parse miss
                        # is logged loudly so a stale parser is visible instead
                        # of silently reverting to blind exponential backoff.
                        retry_after = _parse_retry_after(e)
                        if retry_after is None:
                            logger.warning(
                                "OpenAI 429 with unparseable Retry-After; falling "
                                "back to exponential backoff — the parser may be "
                                "stale. Error: %s",
                                e,
                            )
                        raise LLMRateLimitError(
                            f"OpenAI rate limited: {e}", retry_after=retry_after
                        ) from e
                    if hasattr(e, "status_code"):
                        raise LLMAPIError(
                            str(e), getattr(e, "status_code", None)
                        ) from e
                    raise LLMError(f"OpenAI extraction failed: {e}") from e
            if retry_truncation:
                continue

            finish_reason = (
                completion.choices[0].finish_reason if completion.choices else None
            )
            if finish_reason == "length":
                if previous_budget is not None:
                    raise LLMTruncationError(
                        call_site=site,
                        budget=attempt_budget,
                        reasoning_effort=effort,
                        retried_budget=attempt_budget,
                    )
                if retry_budget > attempt_budget:
                    logger.warning(
                        "LLM call %s hit finish_reason=length at "
                        "max_completion_tokens=%d; retrying once at %d",
                        site,
                        attempt_budget,
                        retry_budget,
                    )
                    continue
                raise LLMTruncationError(
                    call_site=site,
                    budget=attempt_budget,
                    reasoning_effort=effort,
                )

            usage = _openai_usage(completion)
            self._total_usage = self._total_usage + usage
            _log_usage(
                provider="openai",
                call_site=site,
                model=self.config.model,
                budget=attempt_budget,
                reasoning_effort=effort,
                usage=usage,
                finish_reason=finish_reason,
            )

            # Reconcile real provider-reported usage with the bounded pre-call
            # reservation. Over-reservation is not refunded (the safe
            # direction); an unusually large response is charged before the next
            # call so the bucket tracks what was actually spent.
            actual = usage.total_tokens or (
                usage.prompt_tokens + usage.completion_tokens
            )
            deficit = actual - estimate
            if deficit > 0:
                reconciled = await limiter.acquire(deficit)
                logger.info(
                    "TPM throttle: reconciled %d actual tokens over the "
                    "pre-call estimate (waited %.1fs)",
                    deficit,
                    reconciled,
                )

            return LLMResponse(
                content=response,
                raw_response=completion,
                usage=usage,
                model=self.config.model,
                finish_reason=finish_reason,
            )

        # The loop either returns or raises; this is defensive.
        raise LLMTruncationError(
            call_site=site,
            budget=budget,
            reasoning_effort=effort,
            retried_budget=retry_budget if retry_budget > budget else None,
        )


class AnthropicClient(BaseLLMClient[T]):
    """Anthropic (Claude) LLM client with structured output support."""

    def __init__(self, config: Optional[LLMConfig] = None):
        """Initialize Anthropic client."""
        if config is None:
            config = LLMConfig(provider=LLMProvider.ANTHROPIC)
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
                max_retries=0,
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

            # Anthropic has no analogue of OpenAI's Chat-Completions
            # "function tools with reasoning effort" restriction: tool use is
            # supported alongside extended thinking, and this client does not
            # request a thinking budget. Keep instructor's native tool mode —
            # it is Anthropic's reliable structured-output path.
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
        *,
        max_tokens: Optional[int] = None,
        call_site: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> LLMResponse[T]:
        """
        Extract structured data using Anthropic with instructor.

        Args:
            prompt: The user prompt containing text to extract from.
            response_model: Pydantic model defining the expected output structure.
            system_prompt: Optional system prompt for context.
            max_tokens: Per-call completion budget. The default is
                ``LLM_MAX_OUTPUT_TOKENS``.
            call_site: Short label used in usage logs and timeout/truncation errors.
            reasoning_effort: Recorded for error context (Anthropic has no
                per-call effort parameter here).
            timeout: Per-call HTTP timeout in seconds.

        Returns:
            LLMResponse containing the parsed structured output.

        Raises:
            LLMError: If extraction fails.
            LLMTruncationError: If the completion hit its budget after one retry.
        """
        client = self._get_instructor_client()
        site = call_site or "anthropic.extract"
        budget = (
            self.config.max_tokens if max_tokens is None else resolve_max_output_tokens(max_tokens)
        )
        retry_budget = min(budget * 2, OUTPUT_TOKEN_CEILING)
        attempts: list[tuple[int, Optional[int]]] = [(budget, None)]
        if retry_budget > budget:
            attempts.append((retry_budget, budget))

        effort = (
            resolve_reasoning_effort(reasoning_effort)
            if reasoning_effort is not None
            else self.config.reasoning_effort
        )
        request_timeout = resolve_request_timeout(timeout, call_site=site)
        for attempt_budget, previous_budget in attempts:
            retry_truncation = False
            for timeout_attempt in range(2):
                try:
                    response, completion = await client.messages.create_with_completion(
                        model=self.config.model,
                        max_tokens=attempt_budget,
                        system=system_prompt or "",
                        messages=[{"role": "user", "content": prompt}],
                        response_model=response_model,
                        timeout=request_timeout,
                    )
                    break
                except LLMRateLimitError:
                    raise
                except Exception as e:  # noqa: BLE001 - classified below
                    if _is_timeout_error(e):
                        if timeout_attempt == 0:
                            logger.warning(
                                "LLM call %s timed out after %gs "
                                "(max_tokens=%d, reasoning_effort=%s); retrying once",
                                site,
                                request_timeout,
                                attempt_budget,
                                effort,
                            )
                            await asyncio.sleep(TIMEOUT_RETRY_BACKOFF_SECONDS)
                            continue
                        raise LLMTimeoutError(
                            call_site=site,
                            timeout=request_timeout,
                            budget=attempt_budget,
                            reasoning_effort=effort,
                        ) from e

                    if _is_truncation_error(e):
                        if previous_budget is not None:
                            raise LLMTruncationError(
                                call_site=site,
                                budget=attempt_budget,
                                reasoning_effort=effort,
                                retried_budget=attempt_budget,
                            ) from e
                        if retry_budget > attempt_budget:
                            logger.warning(
                                "LLM call %s hit max_tokens=%d; retrying once at %d",
                                site,
                                attempt_budget,
                                retry_budget,
                            )
                            retry_truncation = True
                            break
                        raise LLMTruncationError(
                            call_site=site,
                            budget=attempt_budget,
                            reasoning_effort=effort,
                        ) from e

                    error_str = str(e).lower()
                    if "rate" in error_str and "limit" in error_str:
                        raise LLMRateLimitError(
                            f"Anthropic rate limited: {e}"
                        ) from e
                    if hasattr(e, "status_code"):
                        raise LLMAPIError(
                            str(e), getattr(e, "status_code", None)
                        ) from e
                    raise LLMError(f"Anthropic extraction failed: {e}") from e
            if retry_truncation:
                continue

            finish_reason = (
                completion.stop_reason if hasattr(completion, "stop_reason") else None
            )
            if finish_reason == "max_tokens":
                if previous_budget is not None:
                    raise LLMTruncationError(
                        call_site=site,
                        budget=attempt_budget,
                        reasoning_effort=self.config.reasoning_effort,
                        retried_budget=attempt_budget,
                    )
                if retry_budget > attempt_budget:
                    logger.warning(
                        "LLM call %s hit stop_reason=max_tokens at max_tokens=%d; "
                        "retrying once at %d",
                        site,
                        attempt_budget,
                        retry_budget,
                    )
                    continue
                raise LLMTruncationError(
                    call_site=site,
                    budget=attempt_budget,
                    reasoning_effort=self.config.reasoning_effort,
                )

            usage = _anthropic_usage(completion)
            self._total_usage = self._total_usage + usage
            _log_usage(
                provider="anthropic",
                call_site=site,
                model=self.config.model,
                budget=attempt_budget,
                reasoning_effort=self.config.reasoning_effort,
                usage=usage,
                finish_reason=finish_reason,
            )
            return LLMResponse(
                content=response,
                raw_response=completion,
                usage=usage,
                model=self.config.model,
                finish_reason=finish_reason,
            )

        raise LLMTruncationError(
            call_site=site,
            budget=budget,
            reasoning_effort=self.config.reasoning_effort,
            retried_budget=retry_budget if retry_budget > budget else None,
        )


def create_llm_client(
    provider: Optional[LLMProvider | str] = None,
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
    # ``LLM_PROVIDER`` is the operator lever; an explicit argument still wins
    # for callers that construct a provider deliberately.
    resolved = LLMProvider(resolve_llm_provider(
        provider.value if isinstance(provider, LLMProvider) else provider
    ))
    config = LLMConfig(
        provider=resolved,
        model=model,
        temperature=temperature,
        api_key=api_key,
    )

    if resolved == LLMProvider.OPENAI:
        return OpenAIClient(config)
    return AnthropicClient(config)


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

    The extraction model resolves through :func:`resolve_llm_model`, so
    ``LLM_MODEL`` (or the legacy ``OPENAI_EXTRACTION_MODEL``) wins over the
    passed ``model``; otherwise the passed ``model``, otherwise the current
    OpenAI default.

    Args:
        model: OpenAI model name; overridden by ``LLM_MODEL`` (or the legacy
            ``OPENAI_EXTRACTION_MODEL``).
        temperature: Temperature for generation.

    Returns:
        OpenAI client instance.
    """
    global _openai_client

    if _openai_client is None:
        resolved_model = resolve_llm_model("openai", explicit=model)
        config = LLMConfig(
            provider=LLMProvider.OPENAI,
            model=resolved_model,
            temperature=temperature,
        )
        _openai_client = OpenAIClient(config)

    return _openai_client


def get_anthropic_client(
    model: Optional[str] = None,
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
            model=resolve_llm_model("anthropic", explicit=model),
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
