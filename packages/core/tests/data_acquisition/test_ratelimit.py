"""Tests for rate limiting infrastructure."""

import pytest
import time
import threading
from unittest.mock import patch

from agentic_kg.data_acquisition.ratelimit import (
    TokenBucketRateLimiter,
    CompositeRateLimiter,
    RateLimiterRegistry,
    get_rate_limiter_registry,
    reset_rate_limiter_registry,
)


class TestTokenBucketRateLimiter:
    """Tests for TokenBucketRateLimiter."""

    def test_create_default(self):
        limiter = TokenBucketRateLimiter()
        assert limiter.rate == 1.0
        assert limiter._burst_size == 1

    def test_create_custom(self):
        limiter = TokenBucketRateLimiter(
            requests_per_second=10.0,
            burst_size=5,
            name="test",
        )
        assert limiter.rate == 10.0
        assert limiter._burst_size == 5
        assert limiter._name == "test"

    def test_try_acquire_success(self):
        limiter = TokenBucketRateLimiter(requests_per_second=10.0, burst_size=5)
        assert limiter.try_acquire() is True

    def test_try_acquire_exhausted(self):
        limiter = TokenBucketRateLimiter(requests_per_second=1.0, burst_size=1)
        assert limiter.try_acquire() is True
        assert limiter.try_acquire() is False  # Exhausted

    def test_acquire_blocking(self):
        limiter = TokenBucketRateLimiter(requests_per_second=100.0, burst_size=1)
        limiter.try_acquire()  # Exhaust

        start = time.time()
        result = limiter.acquire(timeout=1.0)
        elapsed = time.time() - start

        assert result is True
        assert elapsed >= 0.009  # Should wait ~10ms

    def test_acquire_timeout(self):
        limiter = TokenBucketRateLimiter(requests_per_second=0.5, burst_size=1)
        limiter.try_acquire()  # Exhaust

        result = limiter.acquire(timeout=0.1)
        assert result is False

    def test_get_wait_time(self):
        limiter = TokenBucketRateLimiter(requests_per_second=10.0, burst_size=1)
        assert limiter.get_wait_time() == 0.0  # Token available

        limiter.try_acquire()
        wait = limiter.get_wait_time()
        assert wait > 0.0  # Need to wait

    def test_get_stats(self):
        limiter = TokenBucketRateLimiter(
            requests_per_second=10.0, burst_size=5, name="test"
        )
        limiter.try_acquire()
        limiter.try_acquire()

        stats = limiter.get_stats()
        assert stats["name"] == "test"
        assert stats["rate"] == 10.0
        assert stats["burst_size"] == 5
        assert stats["total_acquired"] == 2

    def test_reset(self):
        limiter = TokenBucketRateLimiter(requests_per_second=1.0, burst_size=5)
        for _ in range(5):
            limiter.try_acquire()
        assert limiter.try_acquire() is False

        limiter.reset()
        assert limiter.try_acquire() is True

    def test_rate_setter(self):
        limiter = TokenBucketRateLimiter(requests_per_second=1.0)
        assert limiter.rate == 1.0

        limiter.rate = 10.0
        assert limiter.rate == 10.0

    def test_thread_safety(self):
        limiter = TokenBucketRateLimiter(requests_per_second=100.0, burst_size=50)
        acquired = []

        def acquire_tokens():
            for _ in range(10):
                if limiter.try_acquire():
                    acquired.append(1)

        threads = [threading.Thread(target=acquire_tokens) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have acquired exactly 50 tokens (burst size)
        assert len(acquired) == 50


class TestCompositeRateLimiter:
    """Tests for CompositeRateLimiter."""

    def test_create(self):
        limiters = [
            TokenBucketRateLimiter(10.0, name="per_second"),
            TokenBucketRateLimiter(100.0 / 60, burst_size=100, name="per_minute"),
        ]
        composite = CompositeRateLimiter(limiters)
        assert len(composite._limiters) == 2

    def test_try_acquire_all_available(self):
        limiters = [
            TokenBucketRateLimiter(10.0, burst_size=5),
            TokenBucketRateLimiter(10.0, burst_size=5),
        ]
        composite = CompositeRateLimiter(limiters)
        assert composite.try_acquire() is True

    def test_try_acquire_one_exhausted(self):
        limiter1 = TokenBucketRateLimiter(10.0, burst_size=1)
        limiter2 = TokenBucketRateLimiter(10.0, burst_size=5)
        limiter1.try_acquire()  # Exhaust first

        composite = CompositeRateLimiter([limiter1, limiter2])
        assert composite.try_acquire() is False

    def test_get_stats(self):
        limiters = [
            TokenBucketRateLimiter(10.0, name="a"),
            TokenBucketRateLimiter(5.0, name="b"),
        ]
        composite = CompositeRateLimiter(limiters)
        stats = composite.get_stats()

        assert len(stats) == 2
        assert stats[0]["name"] == "a"
        assert stats[1]["name"] == "b"


class TestRateLimiterRegistry:
    """Tests for RateLimiterRegistry."""

    def test_register_and_get(self):
        registry = RateLimiterRegistry()
        limiter = TokenBucketRateLimiter(10.0, name="test")
        registry.register("test", limiter)

        retrieved = registry.get("test")
        assert retrieved is limiter

    def test_get_nonexistent(self):
        registry = RateLimiterRegistry()
        assert registry.get("nonexistent") is None

    def test_get_or_create_new(self):
        registry = RateLimiterRegistry()
        limiter = registry.get_or_create("new", requests_per_second=5.0)

        assert limiter is not None
        assert limiter.rate == 5.0

    def test_get_or_create_existing(self):
        registry = RateLimiterRegistry()
        limiter1 = registry.get_or_create("test", requests_per_second=5.0)
        limiter2 = registry.get_or_create("test", requests_per_second=10.0)

        assert limiter1 is limiter2
        assert limiter2.rate == 5.0  # Original rate preserved

    def test_update_rate(self):
        registry = RateLimiterRegistry()
        registry.register("test", TokenBucketRateLimiter(5.0, name="test"))

        result = registry.update_rate("test", 10.0)
        assert result is True
        assert registry.get("test").rate == 10.0

    def test_update_rate_nonexistent(self):
        registry = RateLimiterRegistry()
        result = registry.update_rate("nonexistent", 10.0)
        assert result is False

    def test_get_all_stats(self):
        registry = RateLimiterRegistry()
        registry.register("a", TokenBucketRateLimiter(5.0, name="a"))
        registry.register("b", TokenBucketRateLimiter(10.0, name="b"))

        stats = registry.get_all_stats()
        assert "a" in stats
        assert "b" in stats
        assert stats["a"]["rate"] == 5.0
        assert stats["b"]["rate"] == 10.0

    def test_reset_all(self):
        registry = RateLimiterRegistry()
        limiter = TokenBucketRateLimiter(1.0, burst_size=2, name="test")
        limiter.try_acquire()
        limiter.try_acquire()
        registry.register("test", limiter)

        assert limiter.try_acquire() is False
        registry.reset_all()
        assert limiter.try_acquire() is True


class TestGlobalRegistry:
    """Tests for global registry functions."""

    def setup_method(self):
        reset_rate_limiter_registry()

    def teardown_method(self):
        reset_rate_limiter_registry()

    def test_get_rate_limiter_registry(self):
        registry = get_rate_limiter_registry()
        assert registry is not None

    def test_singleton(self):
        registry1 = get_rate_limiter_registry()
        registry2 = get_rate_limiter_registry()
        assert registry1 is registry2

    def test_default_limiters(self):
        registry = get_rate_limiter_registry()

        # Check default limiters are registered
        assert registry.get("semantic_scholar") is not None
        assert registry.get("arxiv") is not None
        assert registry.get("openalex") is not None

    def test_reset(self):
        registry1 = get_rate_limiter_registry()
        reset_rate_limiter_registry()
        registry2 = get_rate_limiter_registry()
        assert registry1 is not registry2
