"""
Rate limiting infrastructure for API clients.

Provides token bucket rate limiters with:
- Per-client rate limit configuration
- Thread-safe operations
- Metrics and logging
- Optional persistence
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Configuration for a rate limiter."""

    requests_per_second: float = 1.0
    burst_size: int = 1
    name: str = "default"


class TokenBucketRateLimiter:
    """
    Token bucket rate limiter.

    Implements the token bucket algorithm for rate limiting:
    - Tokens are added at a fixed rate
    - Each request consumes one token
    - Requests wait if no tokens available
    - Burst capacity allows short bursts above rate

    Example:
        limiter = TokenBucketRateLimiter(requests_per_second=10.0)

        # Wait for permission to make request
        limiter.acquire()
        make_api_call()

        # Or check without blocking
        if limiter.try_acquire():
            make_api_call()
    """

    def __init__(
        self,
        requests_per_second: float = 1.0,
        burst_size: Optional[int] = None,
        name: str = "default",
    ):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Maximum sustained request rate
            burst_size: Maximum burst capacity (default: 1)
            name: Name for logging and metrics
        """
        self._rate = requests_per_second
        self._burst_size = burst_size or max(1, int(requests_per_second))
        self._name = name

        self._tokens = float(self._burst_size)
        self._last_update = time.time()
        self._lock = threading.Lock()

        # Metrics
        self._total_acquired = 0
        self._total_waited_ms = 0.0
        self._total_throttled = 0

    @property
    def rate(self) -> float:
        """Get current rate limit."""
        return self._rate

    @rate.setter
    def rate(self, value: float) -> None:
        """Set new rate limit."""
        with self._lock:
            self._rate = value
            self._burst_size = max(1, int(value))

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_update
        self._tokens = min(
            self._burst_size,
            self._tokens + elapsed * self._rate,
        )
        self._last_update = now

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """
        Acquire a token, blocking if necessary.

        Args:
            timeout: Maximum time to wait (None = wait forever)

        Returns:
            True if acquired, False if timeout
        """
        start_time = time.time()
        deadline = start_time + timeout if timeout else None

        with self._lock:
            self._refill()

            while self._tokens < 1.0:
                # Calculate wait time
                wait_time = (1.0 - self._tokens) / self._rate

                if deadline:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        self._total_throttled += 1
                        return False
                    wait_time = min(wait_time, remaining)

                # Release lock while waiting
                self._lock.release()
                try:
                    time.sleep(wait_time)
                finally:
                    self._lock.acquire()

                self._refill()

            # Consume token
            self._tokens -= 1.0
            self._total_acquired += 1
            self._total_waited_ms += (time.time() - start_time) * 1000

            return True

    def try_acquire(self) -> bool:
        """
        Try to acquire a token without blocking.

        Returns:
            True if acquired, False if no tokens available
        """
        with self._lock:
            self._refill()

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                self._total_acquired += 1
                return True

            self._total_throttled += 1
            return False

    def get_wait_time(self) -> float:
        """
        Get estimated wait time for next token.

        Returns:
            Seconds until next token available (0 if available now)
        """
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                return 0.0
            return (1.0 - self._tokens) / self._rate

    def get_stats(self) -> dict:
        """
        Get rate limiter statistics.

        Returns:
            Dict with total_acquired, total_waited_ms, total_throttled,
            avg_wait_ms, current_tokens
        """
        with self._lock:
            self._refill()
            avg_wait = (
                self._total_waited_ms / self._total_acquired
                if self._total_acquired > 0
                else 0.0
            )
            return {
                "name": self._name,
                "rate": self._rate,
                "burst_size": self._burst_size,
                "current_tokens": self._tokens,
                "total_acquired": self._total_acquired,
                "total_waited_ms": self._total_waited_ms,
                "total_throttled": self._total_throttled,
                "avg_wait_ms": avg_wait,
            }

    def reset(self) -> None:
        """Reset limiter to full capacity."""
        with self._lock:
            self._tokens = float(self._burst_size)
            self._last_update = time.time()


class CompositeRateLimiter:
    """
    Composite rate limiter that enforces multiple limits.

    Useful for APIs with both per-second and per-minute limits.

    Example:
        limiter = CompositeRateLimiter([
            TokenBucketRateLimiter(10.0, name="per_second"),
            TokenBucketRateLimiter(100.0 / 60, burst_size=100, name="per_minute"),
        ])

        limiter.acquire()  # Waits for all limits
    """

    def __init__(self, limiters: list[TokenBucketRateLimiter]):
        """
        Initialize composite limiter.

        Args:
            limiters: List of rate limiters to enforce
        """
        self._limiters = limiters

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """Acquire from all limiters."""
        start = time.time()
        for limiter in self._limiters:
            remaining = None
            if timeout:
                remaining = timeout - (time.time() - start)
                if remaining <= 0:
                    return False
            if not limiter.acquire(remaining):
                return False
        return True

    def try_acquire(self) -> bool:
        """Try to acquire from all limiters."""
        # Check all first
        for limiter in self._limiters:
            if limiter.get_wait_time() > 0:
                return False

        # Then acquire all
        for limiter in self._limiters:
            if not limiter.try_acquire():
                return False
        return True

    def get_stats(self) -> list[dict]:
        """Get stats from all limiters."""
        return [limiter.get_stats() for limiter in self._limiters]


class RateLimiterRegistry:
    """
    Registry for managing rate limiters across clients.

    Provides centralized management of rate limiters with:
    - Named limiter lookup
    - Global statistics
    - Configuration updates

    Example:
        registry = RateLimiterRegistry()

        # Register limiters
        registry.register("semantic_scholar", TokenBucketRateLimiter(10.0))
        registry.register("arxiv", TokenBucketRateLimiter(3.0))

        # Get limiter
        limiter = registry.get("semantic_scholar")
        limiter.acquire()
    """

    def __init__(self):
        """Initialize registry."""
        self._limiters: dict[str, TokenBucketRateLimiter] = {}
        self._lock = threading.Lock()

    def register(
        self,
        name: str,
        limiter: TokenBucketRateLimiter,
    ) -> None:
        """
        Register a rate limiter.

        Args:
            name: Limiter name
            limiter: Rate limiter instance
        """
        with self._lock:
            self._limiters[name] = limiter

    def get(self, name: str) -> Optional[TokenBucketRateLimiter]:
        """
        Get a rate limiter by name.

        Args:
            name: Limiter name

        Returns:
            Rate limiter or None if not found
        """
        with self._lock:
            return self._limiters.get(name)

    def get_or_create(
        self,
        name: str,
        requests_per_second: float = 1.0,
        burst_size: Optional[int] = None,
    ) -> TokenBucketRateLimiter:
        """
        Get existing limiter or create new one.

        Args:
            name: Limiter name
            requests_per_second: Rate for new limiter
            burst_size: Burst size for new limiter

        Returns:
            Rate limiter
        """
        with self._lock:
            if name not in self._limiters:
                self._limiters[name] = TokenBucketRateLimiter(
                    requests_per_second=requests_per_second,
                    burst_size=burst_size,
                    name=name,
                )
            return self._limiters[name]

    def update_rate(self, name: str, requests_per_second: float) -> bool:
        """
        Update rate limit for a limiter.

        Args:
            name: Limiter name
            requests_per_second: New rate

        Returns:
            True if updated, False if not found
        """
        limiter = self.get(name)
        if limiter:
            limiter.rate = requests_per_second
            return True
        return False

    def get_all_stats(self) -> dict[str, dict]:
        """Get stats from all registered limiters."""
        with self._lock:
            return {
                name: limiter.get_stats()
                for name, limiter in self._limiters.items()
            }

    def reset_all(self) -> None:
        """Reset all limiters to full capacity."""
        with self._lock:
            for limiter in self._limiters.values():
                limiter.reset()


# Global registry
_registry: Optional[RateLimiterRegistry] = None


def get_rate_limiter_registry() -> RateLimiterRegistry:
    """Get the global rate limiter registry."""
    global _registry
    if _registry is None:
        _registry = RateLimiterRegistry()
        # Register default limiters
        _registry.register(
            "semantic_scholar",
            TokenBucketRateLimiter(1.0, name="semantic_scholar"),
        )
        _registry.register(
            "semantic_scholar_authenticated",
            TokenBucketRateLimiter(10.0, name="semantic_scholar_authenticated"),
        )
        _registry.register(
            "arxiv",
            TokenBucketRateLimiter(3.0, name="arxiv"),
        )
        _registry.register(
            "openalex",
            TokenBucketRateLimiter(10.0, name="openalex"),
        )
    return _registry


def reset_rate_limiter_registry() -> None:
    """Reset the global rate limiter registry."""
    global _registry
    _registry = None
