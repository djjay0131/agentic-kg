"""
Unit tests for LLM client wrapper.
"""

import pytest
from tenacity import RetryError
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel

from agentic_kg.extraction.llm_client import (
    AnthropicClient,
    BaseLLMClient,
    LLMAPIError,
    LLMConfig,
    LLMError,
    LLMProvider,
    LLMRateLimitError,
    LLMResponse,
    OpenAIClient,
    TokenUsage,
    create_llm_client,
    get_anthropic_client,
    get_openai_client,
    reset_llm_clients,
)


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
