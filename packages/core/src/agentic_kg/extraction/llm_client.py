"""
LLM Client Wrapper for Structured Extraction.

Provides an abstraction layer for LLM providers (OpenAI, Anthropic) with
structured output support via the instructor library, retry logic, and
token usage tracking.
"""

import logging
import os
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

logger = logging.getLogger(__name__)

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
            except ImportError as e:
                raise LLMError(
                    "instructor package not installed. Install with: pip install instructor"
                ) from e

            client = self._get_client()
            self._instructor_client = instructor.from_openai(client)

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
                raise LLMRateLimitError(f"OpenAI rate limited: {e}") from e

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
            except ImportError as e:
                raise LLMError(
                    "instructor package not installed. Install with: pip install instructor"
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
                finish_reason=completion.stop_reason if hasattr(completion, "stop_reason") else None,
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
    """
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


def get_openai_client(
    model: str = "gpt-4-turbo",
    temperature: float = 0.1,
) -> OpenAIClient:
    """
    Get or create singleton OpenAI client.

    Args:
        model: OpenAI model name.
        temperature: Temperature for generation.

    Returns:
        OpenAI client instance.
    """
    global _openai_client

    if _openai_client is None:
        config = LLMConfig(
            provider=LLMProvider.OPENAI,
            model=model,
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
    """Reset all singleton LLM clients (for testing)."""
    global _openai_client, _anthropic_client
    _openai_client = None
    _anthropic_client = None
