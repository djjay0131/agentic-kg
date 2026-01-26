"""
Resilience infrastructure for Data Acquisition.

Implements retry logic with exponential backoff and circuit breaker pattern.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, TypeVar

from agentic_kg.data_acquisition.config import CircuitBreakerConfig, RateLimitConfig
from agentic_kg.data_acquisition.exceptions import (
    APIError,
    CircuitOpenError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerState:
    """State for circuit breaker."""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float | None = None
    last_state_change: float = field(default_factory=time.monotonic)


class CircuitBreaker:
    """
    Circuit breaker for API resilience.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Service failing, requests fail fast
    - HALF_OPEN: Testing recovery, limited requests allowed
    """

    def __init__(
        self,
        config: CircuitBreakerConfig | None = None,
        source: str = "default",
    ):
        """
        Initialize circuit breaker.

        Args:
            config: Circuit breaker configuration
            source: Source identifier for logging
        """
        self.config = config or CircuitBreakerConfig()
        self.source = source
        self._state = CircuitBreakerState()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state.state

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self._state.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (failing)."""
        return self._state.state == CircuitState.OPEN

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self._state.last_failure_time is None:
            return True

        elapsed = time.monotonic() - self._state.last_failure_time
        return elapsed >= self.config.cooldown_period

    def _cooldown_remaining(self) -> float:
        """Get remaining cooldown time."""
        if self._state.last_failure_time is None:
            return 0.0

        elapsed = time.monotonic() - self._state.last_failure_time
        remaining = self.config.cooldown_period - elapsed
        return max(0.0, remaining)

    async def check(self) -> None:
        """
        Check if request should proceed.

        Raises:
            CircuitOpenError: If circuit is open
        """
        async with self._lock:
            if self._state.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    # Transition to half-open
                    logger.info(
                        "[%s] Circuit breaker: OPEN -> HALF_OPEN",
                        self.source,
                    )
                    self._state.state = CircuitState.HALF_OPEN
                    self._state.success_count = 0
                    self._state.last_state_change = time.monotonic()
                else:
                    raise CircuitOpenError(
                        source=self.source,
                        cooldown_remaining=self._cooldown_remaining(),
                    )

    async def record_success(self) -> None:
        """Record a successful request."""
        async with self._lock:
            if self._state.state == CircuitState.HALF_OPEN:
                self._state.success_count += 1
                if self._state.success_count >= self.config.success_threshold:
                    # Transition to closed
                    logger.info(
                        "[%s] Circuit breaker: HALF_OPEN -> CLOSED",
                        self.source,
                    )
                    self._state.state = CircuitState.CLOSED
                    self._state.failure_count = 0
                    self._state.last_state_change = time.monotonic()

            elif self._state.state == CircuitState.CLOSED:
                # Reset failure count on success
                self._state.failure_count = 0

    async def record_failure(self) -> None:
        """Record a failed request."""
        async with self._lock:
            self._state.failure_count += 1
            self._state.last_failure_time = time.monotonic()

            if self._state.state == CircuitState.HALF_OPEN:
                # Any failure in half-open goes back to open
                logger.warning(
                    "[%s] Circuit breaker: HALF_OPEN -> OPEN (failure in half-open)",
                    self.source,
                )
                self._state.state = CircuitState.OPEN
                self._state.last_state_change = time.monotonic()

            elif self._state.state == CircuitState.CLOSED:
                if self._state.failure_count >= self.config.failure_threshold:
                    # Transition to open
                    logger.warning(
                        "[%s] Circuit breaker: CLOSED -> OPEN (failure_count=%d)",
                        self.source,
                        self._state.failure_count,
                    )
                    self._state.state = CircuitState.OPEN
                    self._state.last_state_change = time.monotonic()

    @property
    def stats(self) -> dict:
        """Get circuit breaker statistics."""
        return {
            "source": self.source,
            "state": self._state.state.value,
            "failure_count": self._state.failure_count,
            "success_count": self._state.success_count,
            "cooldown_remaining": self._cooldown_remaining(),
        }

    def reset(self) -> None:
        """Reset circuit breaker to closed state."""
        self._state = CircuitBreakerState()


def calculate_backoff(
    attempt: int,
    config: RateLimitConfig,
    retry_after: float | None = None,
) -> float:
    """
    Calculate backoff time with jitter.

    Args:
        attempt: Current attempt number (0-indexed)
        config: Rate limit configuration
        retry_after: Server-specified retry time (takes precedence)

    Returns:
        Backoff time in seconds
    """
    if retry_after is not None:
        return retry_after

    # Exponential backoff
    backoff = config.initial_backoff * (config.backoff_multiplier**attempt)
    backoff = min(backoff, config.max_backoff)

    # Add jitter
    jitter = backoff * config.jitter * random.random()
    backoff += jitter

    return backoff


def is_retryable_error(error: Exception) -> bool:
    """
    Check if an error is retryable.

    Args:
        error: The exception to check

    Returns:
        True if the error is retryable
    """
    if isinstance(error, RateLimitError):
        return True

    if isinstance(error, APIError):
        # Retry on server errors (5xx)
        if error.status_code and error.status_code >= 500:
            return True
        # Retry on rate limits (429)
        if error.status_code == 429:
            return True

    # Retry on connection/timeout errors
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True

    return False


async def retry_with_backoff(
    func: Callable[..., Any],
    max_retries: int = 3,
    config: RateLimitConfig | None = None,
    source: str = "default",
) -> Any:
    """
    Execute function with retry and exponential backoff.

    Args:
        func: Async function to execute
        max_retries: Maximum number of retries
        config: Rate limit configuration
        source: Source identifier for logging

    Returns:
        Result from successful function call

    Raises:
        Last exception if all retries fail
    """
    config = config or RateLimitConfig()
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await func()

        except Exception as e:
            last_exception = e

            if not is_retryable_error(e):
                raise

            if attempt >= max_retries:
                logger.warning(
                    "[%s] Max retries (%d) exceeded",
                    source,
                    max_retries,
                )
                raise

            # Get retry_after from rate limit error if available
            retry_after = None
            if isinstance(e, RateLimitError):
                retry_after = e.retry_after

            backoff = calculate_backoff(attempt, config, retry_after)

            logger.info(
                "[%s] Retry %d/%d after %.2fs: %s",
                source,
                attempt + 1,
                max_retries,
                backoff,
                str(e),
            )

            await asyncio.sleep(backoff)

    # Should not reach here, but satisfy type checker
    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected state in retry_with_backoff")


def with_retry(
    max_retries: int = 3,
    config: RateLimitConfig | None = None,
):
    """
    Decorator for adding retry logic to async functions.

    Args:
        max_retries: Maximum number of retries
        config: Rate limit configuration
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Try to get source from self if available
            source = "default"
            if args and hasattr(args[0], "SOURCE"):
                source = args[0].SOURCE

            async def call() -> Any:
                return await func(*args, **kwargs)

            return await retry_with_backoff(
                call,
                max_retries=max_retries,
                config=config,
                source=source,
            )

        return wrapper

    return decorator


@dataclass
class CircuitBreakerRegistry:
    """Registry for managing multiple circuit breakers."""

    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    _breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    def get(self, source: str) -> CircuitBreaker:
        """Get or create a circuit breaker for a source."""
        if source not in self._breakers:
            self._breakers[source] = CircuitBreaker(
                config=self.config,
                source=source,
            )
        return self._breakers[source]

    def get_all_stats(self) -> dict[str, dict]:
        """Get statistics for all circuit breakers."""
        return {source: breaker.stats for source, breaker in self._breakers.items()}

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        for breaker in self._breakers.values():
            breaker.reset()


# Singleton registry
_circuit_registry: CircuitBreakerRegistry | None = None


def get_circuit_breaker_registry() -> CircuitBreakerRegistry:
    """Get the circuit breaker registry singleton."""
    global _circuit_registry
    if _circuit_registry is None:
        _circuit_registry = CircuitBreakerRegistry()
    return _circuit_registry


def reset_circuit_breaker_registry() -> None:
    """Reset the circuit breaker registry (useful for testing)."""
    global _circuit_registry
    _circuit_registry = None


__all__ = [
    "CircuitState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "calculate_backoff",
    "is_retryable_error",
    "retry_with_backoff",
    "with_retry",
    "get_circuit_breaker_registry",
    "reset_circuit_breaker_registry",
]
