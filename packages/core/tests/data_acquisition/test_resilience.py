"""
Unit tests for resilience infrastructure (retry and circuit breaker).
"""

import asyncio

import pytest

from agentic_kg.data_acquisition.resilience import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitState,
    calculate_backoff,
    is_retryable_error,
    retry_with_backoff,
)
from agentic_kg.data_acquisition.exceptions import APIError, RateLimitError


class TestCalculateBackoff:
    """Tests for backoff calculation."""

    def test_initial_backoff(self, rate_limit_config):
        """Test initial backoff value."""
        backoff = calculate_backoff(0, rate_limit_config)

        # With 0 jitter, should be exactly initial_backoff
        assert backoff == rate_limit_config.initial_backoff

    def test_exponential_increase(self, rate_limit_config):
        """Test that backoff increases exponentially."""
        b0 = calculate_backoff(0, rate_limit_config)
        b1 = calculate_backoff(1, rate_limit_config)
        b2 = calculate_backoff(2, rate_limit_config)

        assert b1 == b0 * rate_limit_config.backoff_multiplier
        assert b2 == b1 * rate_limit_config.backoff_multiplier

    def test_max_backoff(self, rate_limit_config):
        """Test that backoff doesn't exceed max."""
        # Very high attempt number
        backoff = calculate_backoff(100, rate_limit_config)

        assert backoff <= rate_limit_config.max_backoff

    def test_retry_after_takes_precedence(self, rate_limit_config):
        """Test that retry_after overrides calculated backoff."""
        backoff = calculate_backoff(0, rate_limit_config, retry_after=30.0)

        assert backoff == 30.0


class TestIsRetryableError:
    """Tests for retryable error detection."""

    def test_rate_limit_error_is_retryable(self):
        """Test that RateLimitError is retryable."""
        error = RateLimitError(source="test")
        assert is_retryable_error(error) is True

    def test_server_error_is_retryable(self):
        """Test that 5xx errors are retryable."""
        error = APIError(message="Server error", source="test", status_code=500)
        assert is_retryable_error(error) is True

        error = APIError(message="Bad gateway", source="test", status_code=502)
        assert is_retryable_error(error) is True

    def test_429_is_retryable(self):
        """Test that 429 (rate limit) is retryable."""
        error = APIError(message="Too many requests", source="test", status_code=429)
        assert is_retryable_error(error) is True

    def test_client_error_not_retryable(self):
        """Test that 4xx errors (except 429) are not retryable."""
        error = APIError(message="Not found", source="test", status_code=404)
        assert is_retryable_error(error) is False

        error = APIError(message="Bad request", source="test", status_code=400)
        assert is_retryable_error(error) is False

    def test_connection_error_is_retryable(self):
        """Test that connection errors are retryable."""
        assert is_retryable_error(ConnectionError("Connection refused")) is True

    def test_timeout_error_is_retryable(self):
        """Test that timeout errors are retryable."""
        assert is_retryable_error(TimeoutError("Timed out")) is True


class TestRetryWithBackoff:
    """Tests for retry_with_backoff function."""

    @pytest.mark.asyncio
    async def test_success_no_retry(self, rate_limit_config):
        """Test that successful call doesn't retry."""
        call_count = 0

        async def success():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await retry_with_backoff(
            success,
            max_retries=3,
            config=rate_limit_config,
        )

        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_retryable_error(self, rate_limit_config):
        """Test that retryable errors are retried."""
        call_count = 0

        async def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise APIError("Server error", source="test", status_code=500)
            return "success"

        result = await retry_with_backoff(
            fail_then_succeed,
            max_retries=3,
            config=rate_limit_config,
        )

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_error(self, rate_limit_config):
        """Test that non-retryable errors are not retried."""
        call_count = 0

        async def fail_permanently():
            nonlocal call_count
            call_count += 1
            raise APIError("Not found", source="test", status_code=404)

        with pytest.raises(APIError):
            await retry_with_backoff(
                fail_permanently,
                max_retries=3,
                config=rate_limit_config,
            )

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, rate_limit_config):
        """Test that error is raised after max retries."""
        call_count = 0

        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise APIError("Server error", source="test", status_code=500)

        with pytest.raises(APIError):
            await retry_with_backoff(
                always_fail,
                max_retries=2,
                config=rate_limit_config,
            )

        # Initial call + 2 retries = 3 calls
        assert call_count == 3


class TestCircuitBreaker:
    """Tests for CircuitBreaker."""

    @pytest.mark.asyncio
    async def test_initial_state_closed(self, circuit_breaker):
        """Test that circuit starts in closed state."""
        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.is_closed is True

    @pytest.mark.asyncio
    async def test_check_passes_when_closed(self, circuit_breaker):
        """Test that check passes when circuit is closed."""
        await circuit_breaker.check()  # Should not raise

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self, circuit_breaker):
        """Test that circuit opens after failure threshold."""
        # Record failures up to threshold
        for _ in range(3):
            await circuit_breaker.record_failure()

        assert circuit_breaker.state == CircuitState.OPEN
        assert circuit_breaker.is_open is True

    @pytest.mark.asyncio
    async def test_check_raises_when_open(self, circuit_breaker):
        """Test that check raises when circuit is open."""
        # Open the circuit
        for _ in range(3):
            await circuit_breaker.record_failure()

        from agentic_kg.data_acquisition.exceptions import CircuitOpenError

        with pytest.raises(CircuitOpenError):
            await circuit_breaker.check()

    @pytest.mark.asyncio
    async def test_half_open_after_cooldown(self, circuit_breaker_config):
        """Test that circuit enters half-open after cooldown."""
        circuit_breaker_config.cooldown_period = 0.1  # Very short for test
        cb = CircuitBreaker(config=circuit_breaker_config, source="test")

        # Open the circuit
        for _ in range(3):
            await cb.record_failure()

        # Wait for cooldown
        await asyncio.sleep(0.15)

        # Check should not raise, circuit should be half-open
        await cb.check()
        assert cb.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_closes_on_success_in_half_open(self, circuit_breaker_config):
        """Test that circuit closes on success in half-open state."""
        circuit_breaker_config.cooldown_period = 0.1
        cb = CircuitBreaker(config=circuit_breaker_config, source="test")

        # Open the circuit
        for _ in range(3):
            await cb.record_failure()

        # Wait for cooldown
        await asyncio.sleep(0.15)

        # Enter half-open
        await cb.check()

        # Record success
        await cb.record_success()

        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_reopens_on_failure_in_half_open(self, circuit_breaker_config):
        """Test that circuit reopens on failure in half-open state."""
        circuit_breaker_config.cooldown_period = 0.1
        cb = CircuitBreaker(config=circuit_breaker_config, source="test")

        # Open the circuit
        for _ in range(3):
            await cb.record_failure()

        # Wait for cooldown
        await asyncio.sleep(0.15)

        # Enter half-open
        await cb.check()

        # Record failure
        await cb.record_failure()

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self, circuit_breaker):
        """Test that success resets failure count."""
        # Record some failures
        await circuit_breaker.record_failure()
        await circuit_breaker.record_failure()

        # Record success
        await circuit_breaker.record_success()

        # Circuit should still be closed and failure count reset
        assert circuit_breaker.state == CircuitState.CLOSED
        assert circuit_breaker.stats["failure_count"] == 0

    def test_stats(self, circuit_breaker):
        """Test circuit breaker stats."""
        stats = circuit_breaker.stats

        assert stats["source"] == "test"
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 0
        assert stats["success_count"] == 0

    def test_reset(self, circuit_breaker):
        """Test circuit breaker reset."""
        # Record failures
        asyncio.run(circuit_breaker.record_failure())
        asyncio.run(circuit_breaker.record_failure())
        asyncio.run(circuit_breaker.record_failure())

        assert circuit_breaker.is_open

        # Reset
        circuit_breaker.reset()

        assert circuit_breaker.is_closed
        assert circuit_breaker.stats["failure_count"] == 0


class TestCircuitBreakerRegistry:
    """Tests for CircuitBreakerRegistry."""

    def test_get_creates_breaker(self, circuit_breaker_config):
        """Test that get creates a new breaker."""
        registry = CircuitBreakerRegistry(config=circuit_breaker_config)

        breaker = registry.get("test_source")

        assert breaker is not None
        assert breaker.source == "test_source"

    def test_get_returns_same_breaker(self, circuit_breaker_config):
        """Test that get returns the same breaker for same source."""
        registry = CircuitBreakerRegistry(config=circuit_breaker_config)

        breaker1 = registry.get("test_source")
        breaker2 = registry.get("test_source")

        assert breaker1 is breaker2

    def test_get_all_stats(self, circuit_breaker_config):
        """Test that get_all_stats returns stats for all breakers."""
        registry = CircuitBreakerRegistry(config=circuit_breaker_config)

        registry.get("source1")
        registry.get("source2")

        stats = registry.get_all_stats()

        assert "source1" in stats
        assert "source2" in stats

    def test_reset_all(self, circuit_breaker_config):
        """Test that reset_all resets all breakers."""
        registry = CircuitBreakerRegistry(config=circuit_breaker_config)

        breaker = registry.get("source1")

        # Trigger failures
        asyncio.run(breaker.record_failure())
        asyncio.run(breaker.record_failure())
        asyncio.run(breaker.record_failure())

        assert breaker.is_open

        # Reset all
        registry.reset_all()

        assert breaker.is_closed
