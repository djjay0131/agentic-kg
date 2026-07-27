"""
Unit tests for LLM client wrapper.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agentic_kg.extraction.llm_client import (
    AnthropicClient,
    LLMAPIError,
    LLMConfig,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMResponse,
    OpenAIClient,
    TokenUsage,
    _parse_retry_after,
    _rate_limit_wait,
    _read_tpm_budget,
    create_llm_client,
    estimate_tokens,
    get_anthropic_client,
    get_openai_client,
    get_tpm_limiter,
    reset_llm_clients,
)
from pydantic import BaseModel
from tenacity import RetryError


# Sample Pydantic model for testing
class SampleExtraction(BaseModel):
    """Sample extraction result for testing."""

    title: str
    summary: str
    confidence: float


class TestTokenUsage:
    """Tests for TokenUsage dataclass."""

    def test_create_usage(self):
        """Test creating token usage."""
        usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)

        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_tokens == 150

    def test_default_values(self):
        """Test default values are zero."""
        usage = TokenUsage()

        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_add_usage(self):
        """Test adding two usage objects."""
        usage1 = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        usage2 = TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300)

        result = usage1 + usage2

        assert result.prompt_tokens == 300
        assert result.completion_tokens == 150
        assert result.total_tokens == 450


class TestLLMResponse:
    """Tests for LLMResponse dataclass."""

    def test_create_response(self):
        """Test creating a response."""
        content = SampleExtraction(
            title="Test",
            summary="Test summary",
            confidence=0.95,
        )
        response = LLMResponse(
            content=content,
            usage=TokenUsage(total_tokens=100),
            model="gpt-4",
            finish_reason="stop",
        )

        assert response.content.title == "Test"
        assert response.usage.total_tokens == 100
        assert response.model == "gpt-4"
        assert response.finish_reason == "stop"


class TestLLMConfig:
    """Tests for LLMConfig dataclass."""

    def test_default_config(self):
        """Test default configuration."""
        config = LLMConfig()

        assert config.provider == LLMProvider.OPENAI
        assert config.model == "gpt-4-turbo"
        assert config.temperature == 0.1
        assert config.max_tokens == 4096
        assert config.max_retries == 3

    def test_custom_config(self):
        """Test custom configuration."""
        config = LLMConfig(
            provider=LLMProvider.ANTHROPIC,
            model="claude-3-opus",
            temperature=0.5,
            api_key="test-key",
        )

        assert config.provider == LLMProvider.ANTHROPIC
        assert config.model == "claude-3-opus"
        assert config.temperature == 0.5
        assert config.api_key == "test-key"

    def test_loads_api_key_from_env(self):
        """Test that API key is loaded from environment."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "env-key"}):
            config = LLMConfig(provider=LLMProvider.OPENAI)
            assert config.api_key == "env-key"

    def test_anthropic_loads_from_env(self):
        """Test Anthropic API key from environment."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "anthropic-key"}):
            config = LLMConfig(provider=LLMProvider.ANTHROPIC)
            assert config.api_key == "anthropic-key"


class TestLLMExceptions:
    """Tests for LLM exceptions."""

    def test_rate_limit_error(self):
        """Test rate limit error with retry_after."""
        error = LLMRateLimitError("Rate limited", retry_after=30.0)

        assert "Rate limited" in str(error)
        assert error.retry_after == 30.0

    def test_api_error(self):
        """Test API error with status code."""
        error = LLMAPIError("Server error", status_code=500)

        assert "Server error" in str(error)
        assert error.status_code == 500

    def test_generic_error(self):
        """Test generic LLM error."""
        error = LLMError("Something went wrong")

        assert "Something went wrong" in str(error)


class TestOpenAIClient:
    """Tests for OpenAI client."""

    @pytest.fixture
    def client(self):
        """Create OpenAI client with mocked dependencies."""
        config = LLMConfig(
            provider=LLMProvider.OPENAI,
            api_key="test-key",
        )
        return OpenAIClient(config)

    def test_initialization(self, client):
        """Test client initialization."""
        assert client.config.provider == LLMProvider.OPENAI
        assert client.config.api_key == "test-key"
        assert client._client is None  # Lazy initialization

    def test_total_usage_starts_at_zero(self, client):
        """Test that usage starts at zero."""
        assert client.total_usage.total_tokens == 0

    def test_reset_usage(self, client):
        """Test resetting usage counter."""
        client._total_usage = TokenUsage(total_tokens=100)
        client.reset_usage()

        assert client.total_usage.total_tokens == 0

    def test_get_client_missing_openai(self, client):
        """Test error when openai not installed."""
        with patch.dict("sys.modules", {"openai": None}):
            with patch(
                "builtins.__import__",
                side_effect=ImportError("No module named 'openai'"),
            ):
                with pytest.raises(LLMError) as exc_info:
                    client._get_client()

                assert "openai" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_extract_success(self, client):
        """Test successful extraction."""
        # Mock the instructor client
        mock_completion = MagicMock()
        mock_completion.usage = MagicMock(
            prompt_tokens=50, completion_tokens=30, total_tokens=80
        )
        mock_completion.choices = [MagicMock(finish_reason="stop")]

        mock_response = SampleExtraction(
            title="Test Title",
            summary="Test summary",
            confidence=0.9,
        )

        mock_instructor = MagicMock()
        mock_instructor.chat.completions.create_with_completion = AsyncMock(
            return_value=(mock_response, mock_completion)
        )

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            result = await client.extract(
                prompt="Extract info from this text",
                response_model=SampleExtraction,
                system_prompt="You are an extractor.",
            )

        assert result.content.title == "Test Title"
        assert result.usage.total_tokens == 80
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_extract_rate_limit_error(self, client):
        """Test handling of rate limit errors."""
        mock_instructor = MagicMock()
        mock_instructor.chat.completions.create_with_completion = AsyncMock(
            side_effect=Exception("Rate limit exceeded")
        )

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            # Rate limit errors are retried by tenacity, so we get RetryError after 3 attempts
            with pytest.raises(RetryError):
                await client.extract(
                    prompt="Extract this",
                    response_model=SampleExtraction,
                )

    @pytest.mark.asyncio
    async def test_extract_generic_error(self, client):
        """Test handling of generic errors."""
        mock_instructor = MagicMock()
        mock_instructor.chat.completions.create_with_completion = AsyncMock(
            side_effect=Exception("Unknown error")
        )

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            with pytest.raises(LLMError):
                await client.extract(
                    prompt="Extract this",
                    response_model=SampleExtraction,
                )

    @pytest.mark.asyncio
    async def test_extract_api_error_with_status_code(self, client):
        """A non-rate-limit error carrying status_code surfaces as LLMAPIError."""
        api_error = Exception("Bad request")
        api_error.status_code = 400

        mock_instructor = MagicMock()
        mock_instructor.chat.completions.create_with_completion = AsyncMock(
            side_effect=api_error
        )

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            with pytest.raises(LLMAPIError) as exc_info:
                await client.extract(
                    prompt="Extract this",
                    response_model=SampleExtraction,
                )

        assert exc_info.value.status_code == 400


class TestAnthropicClient:
    """Tests for Anthropic client."""

    @pytest.fixture
    def client(self):
        """Create Anthropic client with mocked dependencies."""
        config = LLMConfig(
            provider=LLMProvider.ANTHROPIC,
            model="claude-3-5-sonnet-20241022",
            api_key="test-key",
        )
        return AnthropicClient(config)

    def test_initialization(self, client):
        """Test client initialization."""
        assert client.config.provider == LLMProvider.ANTHROPIC
        assert "claude" in client.config.model
        assert client._client is None

    @pytest.mark.asyncio
    async def test_extract_success(self, client):
        """Test successful extraction."""
        mock_completion = MagicMock()
        mock_completion.usage = MagicMock(input_tokens=50, output_tokens=30)
        mock_completion.stop_reason = "end_turn"

        mock_response = SampleExtraction(
            title="Claude Title",
            summary="Claude summary",
            confidence=0.85,
        )

        mock_instructor = MagicMock()
        mock_instructor.messages.create_with_completion = AsyncMock(
            return_value=(mock_response, mock_completion)
        )

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            result = await client.extract(
                prompt="Extract info",
                response_model=SampleExtraction,
            )

        assert result.content.title == "Claude Title"
        assert result.usage.total_tokens == 80  # 50 + 30


class TestCreateLLMClient:
    """Tests for factory function."""

    def test_create_openai_client(self):
        """Test creating OpenAI client."""
        client = create_llm_client(
            provider=LLMProvider.OPENAI,
            model="gpt-4",
            api_key="test-key",
        )

        assert isinstance(client, OpenAIClient)
        assert client.config.model == "gpt-4"

    def test_create_anthropic_client(self):
        """Test creating Anthropic client."""
        client = create_llm_client(
            provider=LLMProvider.ANTHROPIC,
            model="claude-3-opus",
            api_key="test-key",
        )

        assert isinstance(client, AnthropicClient)
        assert client.config.model == "claude-3-opus"

    def test_invalid_provider(self):
        """Test error with invalid provider."""
        with pytest.raises(ValueError):
            create_llm_client(provider="invalid")


class TestSingletonClients:
    """Tests for singleton client access."""

    def setup_method(self):
        """Reset singletons before each test."""
        reset_llm_clients()

    def teardown_method(self):
        """Reset singletons after each test."""
        reset_llm_clients()

    def test_get_openai_client_returns_same_instance(self):
        """Test OpenAI singleton returns same instance."""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
            client1 = get_openai_client()
            client2 = get_openai_client()

            assert client1 is client2

    def test_get_anthropic_client_returns_same_instance(self):
        """Test Anthropic singleton returns same instance."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            client1 = get_anthropic_client()
            client2 = get_anthropic_client()

            assert client1 is client2

    def test_reset_clears_singletons(self):
        """Test reset clears all singletons."""
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "test-key", "ANTHROPIC_API_KEY": "test-key"},
        ):
            client1 = get_openai_client()
            reset_llm_clients()
            client2 = get_openai_client()

            assert client1 is not client2


class TestTokenTracking:
    """Tests for cumulative token tracking."""

    @pytest.fixture
    def client(self):
        """Create OpenAI client."""
        config = LLMConfig(provider=LLMProvider.OPENAI, api_key="test-key")
        return OpenAIClient(config)

    @pytest.mark.asyncio
    async def test_cumulative_usage_tracking(self, client):
        """Test that token usage accumulates across requests."""
        mock_completion = MagicMock()
        mock_completion.usage = MagicMock(
            prompt_tokens=50, completion_tokens=30, total_tokens=80
        )
        mock_completion.choices = [MagicMock(finish_reason="stop")]

        mock_response = SampleExtraction(
            title="Test",
            summary="Test",
            confidence=0.9,
        )

        mock_instructor = MagicMock()
        mock_instructor.chat.completions.create_with_completion = AsyncMock(
            return_value=(mock_response, mock_completion)
        )

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            # First request
            await client.extract(prompt="First", response_model=SampleExtraction)
            assert client.total_usage.total_tokens == 80

            # Second request
            await client.extract(prompt="Second", response_model=SampleExtraction)
            assert client.total_usage.total_tokens == 160

            # Reset and verify
            client.reset_usage()
            assert client.total_usage.total_tokens == 0


# ---------------------------------------------------------------------------
# SM-6: Extraction rate-limit resilience
# ---------------------------------------------------------------------------


class TestReadTpmBudget:
    """AC-5: OPENAI_TPM parsing — env-configurable, fail loud on bad input."""

    def test_default_is_30000_when_unset(self, monkeypatch):
        monkeypatch.delenv("OPENAI_TPM", raising=False)
        assert _read_tpm_budget() == 30000

    def test_reads_env_value(self, monkeypatch):
        monkeypatch.setenv("OPENAI_TPM", "450000")
        assert _read_tpm_budget() == 450000

    def test_non_integer_raises_value_error_naming_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_TPM", "banana")
        with pytest.raises(ValueError) as exc_info:
            _read_tpm_budget()
        assert "OPENAI_TPM" in str(exc_info.value)

    def test_non_positive_raises_value_error_naming_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_TPM", "0")
        with pytest.raises(ValueError) as exc_info:
            _read_tpm_budget()
        assert "OPENAI_TPM" in str(exc_info.value)


class TestGetTpmLimiter:
    """AC-5: the shared TPM limiter is sized rate = OPENAI_TPM / 60."""

    def setup_method(self):
        reset_llm_clients()

    def teardown_method(self):
        reset_llm_clients()

    def test_default_rate_is_500_per_second(self, monkeypatch):
        monkeypatch.delenv("OPENAI_TPM", raising=False)
        limiter = get_tpm_limiter()
        assert limiter.rate == pytest.approx(500.0)  # 30000 / 60

    def test_capacity_is_one_minute_budget(self, monkeypatch):
        monkeypatch.delenv("OPENAI_TPM", raising=False)
        limiter = get_tpm_limiter()
        # rate 500 * burst 60 == 30000 (a full minute's budget)
        assert limiter.capacity == pytest.approx(30000.0)

    def test_env_value_sets_rate(self, monkeypatch):
        monkeypatch.setenv("OPENAI_TPM", "450000")
        limiter = get_tpm_limiter()
        assert limiter.rate == pytest.approx(7500.0)  # 450000 / 60

    def test_returns_shared_singleton(self, monkeypatch):
        monkeypatch.delenv("OPENAI_TPM", raising=False)
        assert get_tpm_limiter() is get_tpm_limiter()

    def test_reset_clears_the_limiter(self, monkeypatch):
        monkeypatch.delenv("OPENAI_TPM", raising=False)
        first = get_tpm_limiter()
        reset_llm_clients()
        assert get_tpm_limiter() is not first

    def test_malformed_env_raises_on_construction(self, monkeypatch):
        monkeypatch.setenv("OPENAI_TPM", "not-a-number")
        with pytest.raises(ValueError):
            get_tpm_limiter()


class TestEstimateTokens:
    """AC-1: heuristic token estimate = len(prompt+system)//4 + max_tokens."""

    def test_prompt_and_system_and_completion_budget(self):
        prompt = "x" * 400
        system = "y" * 400
        # (400 + 400) // 4 + 100 == 300
        assert estimate_tokens(prompt, system, 100) == 300

    def test_system_prompt_none_treated_as_empty(self):
        prompt = "x" * 400
        # 400 // 4 + 50 == 150
        assert estimate_tokens(prompt, None, 50) == 150

    def test_empty_prompt_reserves_completion_only(self):
        assert estimate_tokens("", None, 4096) == 4096


class TestParseRetryAfter:
    """AC-3: parse the server's Retry-After hint, or return None."""

    def _exc_with_header(self, value):
        exc = Exception("rate limit")
        exc.response = MagicMock()
        exc.response.headers = {"retry-after": value}
        return exc

    def test_typed_header_seconds(self):
        assert _parse_retry_after(self._exc_with_header("3")) == pytest.approx(3.0)

    def test_typed_header_non_numeric_falls_through_to_none(self):
        # An HTTP-date header value is not delta-seconds → unparseable → None.
        exc = self._exc_with_header("Wed, 21 Oct 2026 07:28:00 GMT")
        assert _parse_retry_after(exc) is None

    def test_header_present_but_no_retry_after_key(self):
        exc = Exception("rate limit")
        exc.response = MagicMock()
        exc.response.headers = {}
        assert _parse_retry_after(exc) is None

    def test_message_seconds(self):
        exc = Exception("Rate limit reached. Please try again in 1.8s. Contact us")
        assert _parse_retry_after(exc) == pytest.approx(1.8)

    def test_message_milliseconds(self):
        exc = Exception("Rate limit reached. Please try again in 20ms.")
        assert _parse_retry_after(exc) == pytest.approx(0.02)

    def test_no_header_no_pattern_returns_none(self):
        assert _parse_retry_after(Exception("Rate limit exceeded")) is None


class TestRateLimitWait:
    """AC-3: the tenacity wait honors retry_after, else falls back to backoff."""

    def _retry_state(self, exc):
        state = MagicMock()
        state.outcome.exception.return_value = exc
        return state

    def test_honors_retry_after_when_present(self):
        exc = LLMRateLimitError("rate limited", retry_after=2.5)
        assert _rate_limit_wait(self._retry_state(exc)) == pytest.approx(2.5)

    def test_falls_back_to_exponential_when_retry_after_none(self):
        exc = LLMRateLimitError("rate limited", retry_after=None)
        state = self._retry_state(exc)
        state.attempt_number = 1
        wait = _rate_limit_wait(state)
        # wait_exponential(multiplier=1, min=1, max=60) → >= 1s
        assert wait >= 1.0

    def test_falls_back_for_non_rate_limit_exception(self):
        state = self._retry_state(ValueError("something else"))
        state.attempt_number = 1
        assert _rate_limit_wait(state) >= 1.0


class TestGetOpenAIClientModel:
    """AC-4: extraction model is env-first, default gpt-4-turbo."""

    def setup_method(self):
        reset_llm_clients()

    def teardown_method(self):
        reset_llm_clients()

    def test_default_model_when_env_and_arg_unset(self, monkeypatch):
        monkeypatch.delenv("OPENAI_EXTRACTION_MODEL", raising=False)
        client = get_openai_client()
        assert client.config.model == "gpt-4-turbo"

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("OPENAI_EXTRACTION_MODEL", "gpt-4o")
        client = get_openai_client()
        assert client.config.model == "gpt-4o"

    def test_env_wins_over_explicit_arg(self, monkeypatch):
        """The env lever must reach even callers that pass an explicit model."""
        monkeypatch.setenv("OPENAI_EXTRACTION_MODEL", "gpt-4o")
        client = get_openai_client(model="gpt-4-turbo")
        assert client.config.model == "gpt-4o"

    def test_arg_used_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("OPENAI_EXTRACTION_MODEL", raising=False)
        client = get_openai_client(model="gpt-4o-mini")
        assert client.config.model == "gpt-4o-mini"


def _mock_instructor_ok():
    """An instructor client whose completion returns a valid SampleExtraction."""
    mock_completion = MagicMock()
    mock_completion.usage = MagicMock(
        prompt_tokens=50, completion_tokens=30, total_tokens=80
    )
    mock_completion.choices = [MagicMock(finish_reason="stop")]
    mock_response = SampleExtraction(title="T", summary="S", confidence=0.9)
    mock_instructor = MagicMock()
    mock_instructor.chat.completions.create_with_completion = AsyncMock(
        return_value=(mock_response, mock_completion)
    )
    return mock_instructor, mock_completion


class TestExtractThrottle:
    """AC-1/AC-2/AC-7: the proactive TPM throttle around OpenAI extract."""

    def setup_method(self):
        reset_llm_clients()

    def teardown_method(self):
        reset_llm_clients()

    @pytest.fixture
    def client(self):
        return OpenAIClient(LLMConfig(provider=LLMProvider.OPENAI, api_key="k"))

    @pytest.mark.asyncio
    async def test_reserves_estimate_before_calling_the_api(self, client):
        """AC-1: acquire(estimate) is awaited BEFORE create_with_completion."""
        order = []
        mock_instructor, _ = _mock_instructor_ok()

        async def record_api(**kwargs):
            order.append("api")
            resp = SampleExtraction(title="T", summary="S", confidence=0.9)
            completion = MagicMock()
            completion.usage = MagicMock(
                prompt_tokens=1, completion_tokens=1, total_tokens=2
            )
            completion.choices = [MagicMock(finish_reason="stop")]
            return resp, completion

        mock_instructor.chat.completions.create_with_completion = AsyncMock(
            side_effect=record_api
        )

        limiter = MagicMock()
        limiter.wait_estimate.return_value = 0.0

        async def record_acquire(est):
            order.append(("acquire", est))
            return 0.0

        limiter.acquire = AsyncMock(side_effect=record_acquire)

        prompt = "p" * 40
        system = "s" * 40
        expected = estimate_tokens(prompt, system, client.config.max_tokens)

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            with patch(
                "agentic_kg.extraction.llm_client.get_tpm_limiter",
                return_value=limiter,
            ):
                await client.extract(
                    prompt=prompt,
                    response_model=SampleExtraction,
                    system_prompt=system,
                )

        assert order == [("acquire", expected), "api"]

    @pytest.mark.asyncio
    async def test_blocks_when_budget_exceeded(self, client, monkeypatch):
        """AC-2: acquire waits deficit/rate; asserted via a mocked sleep only."""
        monkeypatch.setenv("OPENAI_TPM", "6000")  # rate 100/s, capacity 6000
        monkeypatch.setattr(
            "agentic_kg.data_acquisition.rate_limiter.time.monotonic",
            lambda: 5000.0,
        )
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        monkeypatch.setattr(
            "agentic_kg.data_acquisition.rate_limiter.asyncio.sleep", fake_sleep
        )

        limiter = get_tpm_limiter()
        limiter._state.tokens = 0.0  # fully depleted
        limiter._state.last_update = 5000.0  # elapsed == 0 under frozen clock

        # 1000 tokens needed at 100/s → 10s deficit
        assert limiter.wait_estimate(1000.0) == pytest.approx(10.0)
        waited = await limiter.acquire(1000.0)

        assert waited == pytest.approx(10.0)
        assert sleeps == [pytest.approx(10.0)]  # slept the deficit, no real time

    @pytest.mark.asyncio
    async def test_logs_up_front_when_wait_exceeds_threshold(self, client, caplog):
        """AC-7: a >5s predicted wait emits an up-front INFO before blocking."""
        mock_instructor, _ = _mock_instructor_ok()
        limiter = MagicMock()
        limiter.wait_estimate.return_value = 6.0  # > 5s threshold
        limiter.capacity = 30000
        limiter.acquire = AsyncMock(return_value=0.0)

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            with patch(
                "agentic_kg.extraction.llm_client.get_tpm_limiter",
                return_value=limiter,
            ):
                with caplog.at_level(logging.INFO, logger="agentic_kg.extraction.llm_client"):
                    await client.extract(prompt="p", response_model=SampleExtraction)

        assert any(
            "TPM throttle: ~" in r.message and "OPENAI_EXTRACTION_MODEL" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_no_up_front_log_when_wait_below_threshold(self, client, caplog):
        """AC-7: sub-threshold predicted waits stay silent (no up-front log)."""
        mock_instructor, _ = _mock_instructor_ok()
        limiter = MagicMock()
        limiter.wait_estimate.return_value = 0.0
        limiter.capacity = 30000
        limiter.acquire = AsyncMock(return_value=0.0)

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            with patch(
                "agentic_kg.extraction.llm_client.get_tpm_limiter",
                return_value=limiter,
            ):
                with caplog.at_level(logging.INFO, logger="agentic_kg.extraction.llm_client"):
                    await client.extract(prompt="p", response_model=SampleExtraction)

        assert not any("TPM throttle: ~" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_no_up_front_log_at_exact_threshold(self, client, caplog):
        """AC-7 boundary: a predicted wait of exactly 5.0s stays silent (`>`,
        not `>=`)."""
        mock_instructor, _ = _mock_instructor_ok()
        limiter = MagicMock()
        limiter.wait_estimate.return_value = 5.0  # exactly at threshold
        limiter.capacity = 30000
        limiter.acquire = AsyncMock(return_value=0.0)

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            with patch(
                "agentic_kg.extraction.llm_client.get_tpm_limiter",
                return_value=limiter,
            ):
                with caplog.at_level(logging.INFO, logger="agentic_kg.extraction.llm_client"):
                    await client.extract(prompt="p", response_model=SampleExtraction)

        assert not any("TPM throttle: ~" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_logs_after_a_real_wait(self, client, caplog):
        """The post-hoc 'waited' INFO fires when acquire reports a wait."""
        mock_instructor, _ = _mock_instructor_ok()
        limiter = MagicMock()
        limiter.wait_estimate.return_value = 0.0
        limiter.capacity = 30000
        limiter.acquire = AsyncMock(return_value=3.5)  # acquire waited

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            with patch(
                "agentic_kg.extraction.llm_client.get_tpm_limiter",
                return_value=limiter,
            ):
                with caplog.at_level(logging.INFO, logger="agentic_kg.extraction.llm_client"):
                    await client.extract(prompt="p", response_model=SampleExtraction)

        assert any("TPM throttle: waited 3.5s" in r.message for r in caplog.records)


class TestExtractRetryAfterWarn:
    """AC-3: a 429 with unparseable Retry-After logs a WARN (loud parse-miss)."""

    def setup_method(self):
        reset_llm_clients()

    def teardown_method(self):
        reset_llm_clients()

    @pytest.fixture
    def client(self):
        return OpenAIClient(LLMConfig(provider=LLMProvider.OPENAI, api_key="k"))

    @pytest.mark.asyncio
    async def test_warns_when_retry_after_unparseable(self, client, caplog, monkeypatch):
        # Make tenacity's backoff instant so the test doesn't really sleep.
        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        mock_instructor = MagicMock()
        mock_instructor.chat.completions.create_with_completion = AsyncMock(
            side_effect=Exception("Rate limit exceeded")  # no parseable delay
        )

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            with caplog.at_level(logging.WARNING, logger="agentic_kg.extraction.llm_client"):
                with pytest.raises(RetryError):
                    await client.extract(prompt="p", response_model=SampleExtraction)

        assert any(
            "unparseable Retry-After" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_retry_after_attached_to_error_when_parseable(
        self, client, monkeypatch
    ):
        """A parseable delay is carried on the raised LLMRateLimitError."""
        monkeypatch.setattr("asyncio.sleep", AsyncMock())

        captured = {}
        original = LLMRateLimitError.__init__

        def spy_init(self, message, retry_after=None):
            captured["retry_after"] = retry_after
            original(self, message, retry_after=retry_after)

        monkeypatch.setattr(LLMRateLimitError, "__init__", spy_init)

        mock_instructor = MagicMock()
        mock_instructor.chat.completions.create_with_completion = AsyncMock(
            side_effect=Exception("Rate limit reached. Please try again in 2s.")
        )

        with patch.object(client, "_get_instructor_client", return_value=mock_instructor):
            with pytest.raises(RetryError):
                await client.extract(prompt="p", response_model=SampleExtraction)

        assert captured["retry_after"] == pytest.approx(2.0)
