"""
Unit tests for rate limiting infrastructure.
"""

import asyncio
import time

import pytest
from agentic_kg.data_acquisition.config import RateLimitConfig
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


class TestWaitEstimate:
    """Tests for the pure-read ``wait_estimate`` peek (SM-6 throttle ETA).

    The clock is frozen (``time.monotonic`` monkeypatched) so the projection is
    exact — no real sleeping, no wall-clock tolerance.
    """

    def _frozen_limiter(self, monkeypatch, rate, tokens):
        """A limiter with a frozen clock and a hand-set token count.

        Uses the TPM-style config (``burst_multiplier=60`` → capacity = rate*60,
        i.e. a one-minute burst) that SM-6 constructs, so hand-set token counts
        below a full minute's budget are within capacity.
        """
        monkeypatch.setattr(
            "agentic_kg.data_acquisition.rate_limiter.time.monotonic",
            lambda: 1000.0,
        )
        limiter = TokenBucketRateLimiter(
            rate=rate,
            config=RateLimitConfig(burst_multiplier=60.0),
            source="frozen",
        )
        limiter._state.tokens = tokens
        limiter._state.last_update = 1000.0  # elapsed == 0 → no drift
        return limiter

    def test_returns_zero_when_tokens_available(self, monkeypatch):
        """Enough tokens on hand → no wait predicted."""
        limiter = self._frozen_limiter(monkeypatch, rate=500.0, tokens=1000.0)

        assert limiter.wait_estimate(800.0) == 0.0

    def test_returns_deficit_over_rate_when_depleted(self, monkeypatch):
        """Depleted bucket → wait == (requested - available) / rate."""
        limiter = self._frozen_limiter(monkeypatch, rate=500.0, tokens=0.0)

        # 5000 tokens needed, 0 available, 500/s → 10.0s
        assert limiter.wait_estimate(5000.0) == pytest.approx(10.0)

    def test_partial_tokens_reduce_the_estimate(self, monkeypatch):
        """Available tokens are netted off the deficit."""
        limiter = self._frozen_limiter(monkeypatch, rate=500.0, tokens=1000.0)

        # (5000 - 1000) / 500 = 8.0s
        assert limiter.wait_estimate(5000.0) == pytest.approx(8.0)

    def test_request_larger_than_capacity_is_finite(self, monkeypatch):
        """A request exceeding capacity still returns a finite, non-zero wait.

        Guards the 'estimate exceeds bucket capacity' edge case: a single call
        larger than a full minute's budget must not deadlock or report 0.
        """
        # rate 500, burst 60 → capacity 30000, full; ask for 60000 (> capacity)
        limiter = self._frozen_limiter(monkeypatch, rate=500.0, tokens=30000.0)

        eta = limiter.wait_estimate(60000.0)

        assert eta > 0.0
        # (60000 - 30000) / 500 = 60.0s — finite despite exceeding capacity
        assert eta == pytest.approx(60.0)

    def test_does_not_mutate_state(self, monkeypatch):
        """wait_estimate is a pure read — tokens are untouched."""
        limiter = self._frozen_limiter(monkeypatch, rate=500.0, tokens=200.0)

        limiter.wait_estimate(5000.0)

        assert limiter._state.tokens == 200.0
        assert limiter._state.requests_made == 0
        assert limiter._state.requests_throttled == 0


class TestConcurrentAcquire:
    """SM-6 edge case: five extractors acquire at once (asyncio.gather).

    Proves the throttle serializes concurrent acquires without deadlocking and
    that the waits are driven only by the token math — a mocked sleep captures
    the requested durations so no real time passes.
    """

    @pytest.mark.asyncio
    async def test_five_concurrent_acquires_all_complete(self, monkeypatch):
        monkeypatch.setattr(
            "agentic_kg.data_acquisition.rate_limiter.time.monotonic",
            lambda: 2000.0,
        )
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(
            "agentic_kg.data_acquisition.rate_limiter.asyncio.sleep", fake_sleep
        )

        # rate 500, burst 60 → capacity 30000. Five 8000-token calls sum to
        # 40000 > capacity, so some must wait.
        limiter = TokenBucketRateLimiter(
            rate=500.0,
            config=RateLimitConfig(burst_multiplier=60.0),
            source="frozen",
        )
        limiter._state.last_update = 2000.0  # frozen: no refill during waits

        results = await asyncio.gather(
            *(limiter.acquire(8000.0) for _ in range(5))
        )

        # All five completed (no deadlock) and were counted.
        assert len(results) == 5
        assert limiter.stats["requests_made"] == 5
        # At least one had to wait; every wait came from the mocked sleep.
        assert any(w > 0 for w in results)
        assert len(sleeps) == sum(1 for w in results if w > 0)


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
