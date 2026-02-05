"""
Rate limiting infrastructure for Data Acquisition.

Implements token bucket algorithm for per-source rate limiting.
"""
from __future__ import annotations


import asyncio
import logging
import time
from dataclasses import dataclass, field

from agentic_kg.data_acquisition.config import RateLimitConfig

logger = logging.getLogger(__name__)


@dataclass
class RateLimiterState:
    """State for a single rate limiter."""

    tokens: float
    last_update: float
    requests_made: int = 0
    requests_throttled: int = 0


class TokenBucketRateLimiter:
    """
    Token bucket rate limiter for API requests.

    Features:
    - Configurable rate per source
    - Burst allowance
    - Async-compatible
    - Per-source statistics
    """

    def __init__(
        self,
        rate: float,
        config: RateLimitConfig | None = None,
        source: str = "default",
    ):
        """
        Initialize rate limiter.

        Args:
            rate: Requests per second
            config: Rate limit configuration
            source: Source identifier for logging
        """
        self.rate = rate
        self.config = config or RateLimitConfig()
        self.source = source

        # Calculate bucket capacity (allow burst)
        self.capacity = rate * self.config.burst_multiplier

        # Initialize state
        self._state = RateLimiterState(
            tokens=self.capacity,
            last_update=time.monotonic(),
        )

        # Lock for thread safety (created lazily to avoid event loop issues)
        self._lock: asyncio.Lock | None = None

    @property
    def lock(self) -> asyncio.Lock:
        """Get or create the async lock."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._state.last_update

        # Add tokens based on elapsed time
        new_tokens = elapsed * self.rate
        self._state.tokens = min(self.capacity, self._state.tokens + new_tokens)
        self._state.last_update = now

    async def acquire(self, tokens: float = 1.0) -> float:
        """
        Acquire tokens, waiting if necessary.

        Args:
            tokens: Number of tokens to acquire (default: 1)

        Returns:
            Wait time in seconds (0 if no wait was needed)
        """
        async with self.lock:
            self._refill()

            if self._state.tokens >= tokens:
                # Tokens available, consume and proceed
                self._state.tokens -= tokens
                self._state.requests_made += 1
                return 0.0

            # Calculate wait time
            tokens_needed = tokens - self._state.tokens
            wait_time = tokens_needed / self.rate

            logger.debug(
                "[%s] Rate limit: waiting %.2fs for %.1f tokens",
                self.source,
                wait_time,
                tokens_needed,
            )

            self._state.requests_throttled += 1

        # Wait outside the lock
        await asyncio.sleep(wait_time)

        # Acquire after waiting
        async with self.lock:
            self._refill()
            self._state.tokens -= tokens
            self._state.requests_made += 1
            return wait_time

    async def try_acquire(self, tokens: float = 1.0) -> bool:
        """
        Try to acquire tokens without waiting.

        Args:
            tokens: Number of tokens to acquire

        Returns:
            True if tokens were acquired, False otherwise
        """
        async with self.lock:
            self._refill()

            if self._state.tokens >= tokens:
                self._state.tokens -= tokens
                self._state.requests_made += 1
                return True

            return False

    @property
    def available_tokens(self) -> float:
        """Get current number of available tokens."""
        # Note: This is approximate, as tokens refill continuously
        return self._state.tokens

    @property
    def stats(self) -> dict:
        """Get rate limiter statistics."""
        return {
            "source": self.source,
            "rate": self.rate,
            "capacity": self.capacity,
            "available_tokens": self._state.tokens,
            "requests_made": self._state.requests_made,
            "requests_throttled": self._state.requests_throttled,
        }

    def reset(self) -> None:
        """Reset rate limiter to full capacity."""
        self._state = RateLimiterState(
            tokens=self.capacity,
            last_update=time.monotonic(),
        )


@dataclass
class RateLimiterRegistry:
    """
    Registry for managing multiple rate limiters.

    One rate limiter per API source.
    """

    config: RateLimitConfig = field(default_factory=RateLimitConfig)
    _limiters: dict[str, TokenBucketRateLimiter] = field(default_factory=dict)

    def get(self, source: str, rate: float) -> TokenBucketRateLimiter:
        """
        Get or create a rate limiter for a source.

        Args:
            source: Source identifier
            rate: Requests per second for this source

        Returns:
            Rate limiter for the source
        """
        if source not in self._limiters:
            self._limiters[source] = TokenBucketRateLimiter(
                rate=rate,
                config=self.config,
                source=source,
            )
        return self._limiters[source]

    def get_all_stats(self) -> dict[str, dict]:
        """Get statistics for all rate limiters."""
        return {source: limiter.stats for source, limiter in self._limiters.items()}

    def reset_all(self) -> None:
        """Reset all rate limiters."""
        for limiter in self._limiters.values():
            limiter.reset()


# Singleton registry
_registry: RateLimiterRegistry | None = None


def get_rate_limiter_registry() -> RateLimiterRegistry:
    """Get the rate limiter registry singleton."""
    global _registry
    if _registry is None:
        _registry = RateLimiterRegistry()
    return _registry


def reset_rate_limiter_registry() -> None:
    """Reset the rate limiter registry (useful for testing)."""
    global _registry
    _registry = None


__all__ = [
    "TokenBucketRateLimiter",
    "RateLimiterRegistry",
    "get_rate_limiter_registry",
    "reset_rate_limiter_registry",
]
