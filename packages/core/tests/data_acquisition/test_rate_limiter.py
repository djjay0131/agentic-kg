"""
Unit tests for rate limiting infrastructure.
"""

import asyncio
import time

import pytest

from agentic_kg.data_acquisition.rate_limiter import (
    RateLimiterRegistry,
    TokenBucketRateLimiter,
)


class TestTokenBucketRateLimiter:
    """Tests for TokenBucketRateLimiter."""

    @pytest.mark.asyncio
    async def test_acquire_immediate_when_tokens_available(self, rate_limiter):
        """Test that acquire returns immediately when tokens are available."""
        wait_time = await rate_limiter.acquire()
        assert wait_time == 0.0

    @pytest.mark.asyncio
    async def test_acquire_waits_when_no_tokens(self, rate_limit_config):
        """Test that acquire waits when no tokens are available."""
        # Create limiter with very low rate
        limiter = TokenBucketRateLimiter(
            rate=1.0,  # 1 token per second
            config=rate_limit_config,
            source="test",
        )

        # Consume all tokens (burst allows 1.5 tokens)
        await limiter.acquire(1.5)

        # Next acquire should wait
        start = time.monotonic()
        await limiter.acquire(0.5)
        elapsed = time.monotonic() - start

        # Should have waited approximately 0.5 seconds
        assert elapsed >= 0.4

    @pytest.mark.asyncio
    async def test_try_acquire_returns_false_when_no_tokens(self, rate_limit_config):
        """Test that try_acquire returns False when no tokens available."""
        limiter = TokenBucketRateLimiter(
            rate=1.0,
            config=rate_limit_config,
            source="test",
        )

        # Consume all tokens
        await limiter.acquire(1.5)

        # try_acquire should return False
        result = await limiter.try_acquire()
        assert result is False

    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self, rate_limit_config):
        """Test that tokens refill over time."""
        limiter = TokenBucketRateLimiter(
            rate=10.0,  # 10 tokens per second
            config=rate_limit_config,
            source="test",
        )

        # Consume all tokens
        initial_capacity = limiter.capacity
        await limiter.acquire(initial_capacity)

        # Wait for refill
        await asyncio.sleep(0.2)  # Should get ~2 tokens back

        # Should be able to acquire some tokens
        result = await limiter.try_acquire(1.0)
        assert result is True

    def test_stats_tracking(self, rate_limiter):
        """Test that stats are tracked correctly."""
        stats = rate_limiter.stats

        assert stats["source"] == "test"
        assert stats["rate"] == 10.0
        assert stats["requests_made"] == 0
        assert stats["requests_throttled"] == 0

    @pytest.mark.asyncio
    async def test_stats_increment_on_acquire(self, rate_limiter):
        """Test that stats increment on acquire."""
        await rate_limiter.acquire()
        await rate_limiter.acquire()

        stats = rate_limiter.stats
        assert stats["requests_made"] == 2

    def test_reset(self, rate_limiter):
        """Test that reset restores full capacity."""
        # Drain some tokens
        asyncio.run(rate_limiter.acquire(5))

        # Reset
        rate_limiter.reset()

        # Should have full capacity again
        assert rate_limiter.available_tokens == rate_limiter.capacity


class TestRateLimiterRegistry:
    """Tests for RateLimiterRegistry."""

    def test_get_creates_limiter(self, rate_limit_config):
        """Test that get creates a new limiter."""
        registry = RateLimiterRegistry(config=rate_limit_config)

        limiter = registry.get("test_source", 5.0)

        assert limiter is not None
        assert limiter.rate == 5.0
        assert limiter.source == "test_source"

    def test_get_returns_same_limiter(self, rate_limit_config):
        """Test that get returns the same limiter for same source."""
        registry = RateLimiterRegistry(config=rate_limit_config)

        limiter1 = registry.get("test_source", 5.0)
        limiter2 = registry.get("test_source", 5.0)

        assert limiter1 is limiter2

    def test_different_sources_different_limiters(self, rate_limit_config):
        """Test that different sources get different limiters."""
        registry = RateLimiterRegistry(config=rate_limit_config)

        limiter1 = registry.get("source1", 5.0)
        limiter2 = registry.get("source2", 10.0)

        assert limiter1 is not limiter2
        assert limiter1.rate == 5.0
        assert limiter2.rate == 10.0

    def test_get_all_stats(self, rate_limit_config):
        """Test that get_all_stats returns stats for all limiters."""
        registry = RateLimiterRegistry(config=rate_limit_config)

        registry.get("source1", 5.0)
        registry.get("source2", 10.0)

        stats = registry.get_all_stats()

        assert "source1" in stats
        assert "source2" in stats
        assert stats["source1"]["rate"] == 5.0
        assert stats["source2"]["rate"] == 10.0

    def test_reset_all(self, rate_limit_config):
        """Test that reset_all resets all limiters."""
        registry = RateLimiterRegistry(config=rate_limit_config)

        limiter1 = registry.get("source1", 5.0)
        limiter2 = registry.get("source2", 10.0)

        # Drain some tokens
        asyncio.run(limiter1.acquire(5))
        asyncio.run(limiter2.acquire(5))

        # Reset all
        registry.reset_all()

        # Both should have full capacity
        assert limiter1.available_tokens == limiter1.capacity
        assert limiter2.available_tokens == limiter2.capacity
