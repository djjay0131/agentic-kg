"""
Tests for agentic_kg.config module.

Tests configuration loading, validation, and security checks.
"""

import pytest

from agentic_kg.config import (
    Config,
    ConfigurationError,
    EmbeddingConfig,
    Neo4jConfig,
    SearchConfig,
    get_config,
    reset_config,
)


# =============================================================================
# Neo4jConfig Tests
# =============================================================================


class TestNeo4jConfig:
    """Tests for Neo4jConfig dataclass."""

    # Happy path tests
    def test_create_with_defaults(self, clean_env):
        """Neo4jConfig can be created with default values."""
        config = Neo4jConfig()
        assert config.uri == "bolt://localhost:7687"
        assert config.username == "neo4j"
        assert config.password == ""
        assert config.database == "neo4j"

    def test_create_with_custom_values(self):
        """Neo4jConfig can be created with custom values."""
        config = Neo4jConfig(
            uri="bolt://custom:7687",
            username="custom_user",
            password="custom_password",
            database="custom_db",
        )
        assert config.uri == "bolt://custom:7687"
        assert config.username == "custom_user"
        assert config.password == "custom_password"
        assert config.database == "custom_db"

    def test_loads_from_environment(self, monkeypatch):
        """Neo4jConfig loads values from environment variables."""
        monkeypatch.setenv("NEO4J_URI", "bolt://env-host:7687")
        monkeypatch.setenv("NEO4J_USERNAME", "env_user")
        monkeypatch.setenv("NEO4J_PASSWORD", "env_password")
        monkeypatch.setenv("NEO4J_DATABASE", "env_db")

        config = Neo4jConfig()
        assert config.uri == "bolt://env-host:7687"
        assert config.username == "env_user"
        assert config.password == "env_password"
        assert config.database == "env_db"

    def test_connection_pool_defaults(self):
        """Neo4jConfig has correct connection pool defaults."""
        config = Neo4jConfig()
        assert config.max_connection_lifetime == 3600
        assert config.max_connection_pool_size == 50
        assert config.connection_acquisition_timeout == 60

    def test_retry_defaults(self):
        """Neo4jConfig has correct retry defaults."""
        config = Neo4jConfig()
        assert config.max_retries == 3
        assert config.retry_delay == 1.0

    # Security tests
    def test_is_secure_with_strong_password(self):
        """is_secure returns True for strong password."""
        config = Neo4jConfig(password="strong_password_12345")
        assert config.is_secure is True

    @pytest.mark.parametrize(
        "insecure_password",
        [
            "",
            "password",
            "changeme",
            "secret",
            "admin",
        ],
    )
    def test_is_secure_false_for_insecure_passwords(self, insecure_password):
        """is_secure returns False for known insecure passwords."""
        config = Neo4jConfig(password=insecure_password)
        assert config.is_secure is False


# =============================================================================
# EmbeddingConfig Tests
# =============================================================================


class TestEmbeddingConfig:
    """Tests for EmbeddingConfig dataclass."""

    # Happy path tests
    def test_create_with_defaults(self, clean_env):
        """EmbeddingConfig can be created with default values."""
        config = EmbeddingConfig()
        assert config.api_key == ""
        assert config.model == "text-embedding-3-small"
        assert config.dimensions == 1536

    def test_create_with_custom_values(self):
        """EmbeddingConfig can be created with custom values."""
        config = EmbeddingConfig(
            api_key="sk-test-key",
            model="text-embedding-3-large",
            dimensions=3072,
        )
        assert config.api_key == "sk-test-key"
        assert config.model == "text-embedding-3-large"
        assert config.dimensions == 3072

    def test_loads_from_environment(self, monkeypatch):
        """EmbeddingConfig loads values from environment variables."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
        monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-ada-002")

        config = EmbeddingConfig()
        assert config.api_key == "sk-env-key"
        assert config.model == "text-embedding-ada-002"

    def test_batch_settings_defaults(self):
        """EmbeddingConfig has correct batch settings defaults."""
        config = EmbeddingConfig()
        assert config.batch_size == 100
        assert config.max_retries == 3
        assert config.retry_delay == 1.0

    # Configuration check tests
    def test_is_configured_with_valid_key(self):
        """is_configured returns True for valid API key."""
        config = EmbeddingConfig(api_key="sk-real-api-key-12345")
        assert config.is_configured is True

    def test_is_configured_false_for_empty_key(self):
        """is_configured returns False for empty API key."""
        config = EmbeddingConfig(api_key="")
        assert config.is_configured is False

    def test_is_configured_false_for_placeholder_key(self):
        """is_configured returns False for placeholder API key."""
        config = EmbeddingConfig(api_key="sk-your-api-key-here")
        assert config.is_configured is False

    @pytest.mark.parametrize(
        "invalid_key",
        [
            "",
            "sk-your-api-key",
            "sk-your-key-here",
        ],
    )
    def test_is_configured_false_for_invalid_keys(self, invalid_key):
        """is_configured returns False for various invalid keys."""
        config = EmbeddingConfig(api_key=invalid_key)
        assert config.is_configured is False


# =============================================================================
# SearchConfig Tests
# =============================================================================


class TestSearchConfig:
    """Tests for SearchConfig dataclass."""

    def test_create_with_defaults(self):
        """SearchConfig can be created with default values."""
        config = SearchConfig()
        assert config.default_top_k == 10
        assert config.similarity_threshold == 0.5
        assert config.deduplication_threshold == 0.95
        assert config.semantic_weight == 0.7
        assert config.structured_weight == 0.3

    def test_create_with_custom_values(self):
        """SearchConfig can be created with custom values."""
        config = SearchConfig(
            default_top_k=20,
            similarity_threshold=0.7,
            deduplication_threshold=0.9,
            semantic_weight=0.6,
            structured_weight=0.4,
        )
        assert config.default_top_k == 20
        assert config.similarity_threshold == 0.7
        assert config.deduplication_threshold == 0.9
        assert config.semantic_weight == 0.6
        assert config.structured_weight == 0.4

    def test_weights_sum_to_one_by_default(self):
        """Default search weights sum to 1.0."""
        config = SearchConfig()
        assert config.semantic_weight + config.structured_weight == 1.0


# =============================================================================
# Config Tests
# =============================================================================


class TestConfig:
    """Tests for main Config class."""

    # Happy path tests
    def test_create_with_defaults(self, clean_env):
        """Config can be created with default values."""
        config = Config()
        assert isinstance(config.neo4j, Neo4jConfig)
        assert isinstance(config.embedding, EmbeddingConfig)
        assert isinstance(config.search, SearchConfig)
        assert config.environment == "development"
        assert config.debug is False

    def test_create_with_custom_subconfigs(self):
        """Config can be created with custom sub-configurations."""
        neo4j = Neo4jConfig(uri="bolt://custom:7687")
        embedding = EmbeddingConfig(api_key="sk-test")
        config = Config(neo4j=neo4j, embedding=embedding)
        assert config.neo4j.uri == "bolt://custom:7687"
        assert config.embedding.api_key == "sk-test"

    def test_loads_environment_from_env_var(self, monkeypatch):
        """Config loads environment setting from env var."""
        monkeypatch.setenv("ENVIRONMENT", "staging")
        config = Config()
        assert config.environment == "staging"

    def test_loads_debug_from_env_var(self, monkeypatch):
        """Config loads debug setting from env var."""
        monkeypatch.setenv("DEBUG", "true")
        config = Config()
        assert config.debug is True

    @pytest.mark.parametrize(
        "debug_value,expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("1", False),  # Only "true" (case-insensitive) is True
            ("", False),
        ],
    )
    def test_debug_parsing(self, monkeypatch, debug_value, expected):
        """Config correctly parses DEBUG env var."""
        monkeypatch.setenv("DEBUG", debug_value)
        monkeypatch.setenv("ENVIRONMENT", "development")  # Avoid production validation
        config = Config()
        assert config.debug is expected

    # Environment check tests
    def test_is_production_true(self, monkeypatch):
        """is_production returns True when environment is 'production'."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("NEO4J_PASSWORD", "secure_password")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")
        config = Config()
        assert config.is_production is True
        assert config.is_development is False

    def test_is_development_true(self, monkeypatch):
        """is_development returns True when environment is 'development'."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        config = Config()
        assert config.is_development is True
        assert config.is_production is False

    # Production validation tests
    def test_production_validation_passes_with_secure_config(self, production_env):
        """Production validation passes with secure configuration."""
        config = Config()
        assert config.is_production is True

    def test_production_validation_fails_without_neo4j_password(self, monkeypatch):
        """Production validation fails without secure Neo4j password."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("NEO4J_PASSWORD", "")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")

        with pytest.raises(ConfigurationError, match="NEO4J_PASSWORD"):
            Config()

    def test_production_validation_fails_without_api_key(self, monkeypatch):
        """Production validation fails without OpenAI API key."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("NEO4J_PASSWORD", "secure_password")
        monkeypatch.setenv("OPENAI_API_KEY", "")

        with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
            Config()

    def test_production_validation_fails_with_insecure_password(self, monkeypatch):
        """Production validation fails with insecure Neo4j password."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("NEO4J_PASSWORD", "password")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real-key")

        with pytest.raises(ConfigurationError, match="NEO4J_PASSWORD"):
            Config()

    def test_production_validation_fails_with_placeholder_api_key(self, monkeypatch):
        """Production validation fails with placeholder API key."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("NEO4J_PASSWORD", "secure_password")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-your-api-key")

        with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
            Config()

    def test_production_validation_reports_multiple_errors(self, monkeypatch):
        """Production validation reports all errors."""
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("NEO4J_PASSWORD", "")
        monkeypatch.setenv("OPENAI_API_KEY", "")

        with pytest.raises(ConfigurationError) as exc_info:
            Config()

        error_message = str(exc_info.value)
        assert "NEO4J_PASSWORD" in error_message
        assert "OPENAI_API_KEY" in error_message

    def test_development_allows_insecure_config(self, clean_env, development_env):
        """Development environment allows insecure configuration."""
        config = Config()
        assert config.is_development is True
        assert config.neo4j.password == ""  # Empty password allowed


# =============================================================================
# Singleton Tests
# =============================================================================


class TestConfigSingleton:
    """Tests for config singleton functions."""

    def test_get_config_returns_singleton(self, clean_env):
        """get_config returns the same instance on repeated calls."""
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_reset_config_clears_singleton(self, clean_env):
        """reset_config clears the singleton instance."""
        config1 = get_config()
        reset_config()
        config2 = get_config()
        assert config1 is not config2

    def test_get_config_after_reset_creates_new_instance(self, monkeypatch):
        """get_config creates new instance after reset with updated env."""
        monkeypatch.setenv("ENVIRONMENT", "development")
        config1 = get_config()
        assert config1.environment == "development"

        reset_config()
        monkeypatch.setenv("ENVIRONMENT", "staging")
        config2 = get_config()
        assert config2.environment == "staging"


# =============================================================================
# ConfigurationError Tests
# =============================================================================


class TestConfigurationError:
    """Tests for ConfigurationError exception."""

    def test_can_be_raised(self):
        """ConfigurationError can be raised and caught."""
        with pytest.raises(ConfigurationError):
            raise ConfigurationError("Test error")

    def test_contains_message(self):
        """ConfigurationError contains the error message."""
        try:
            raise ConfigurationError("Custom error message")
        except ConfigurationError as e:
            assert "Custom error message" in str(e)

    def test_is_exception_subclass(self):
        """ConfigurationError is a subclass of Exception."""
        assert issubclass(ConfigurationError, Exception)


# =============================================================================
# Edge Cases
# =============================================================================


class TestConfigEdgeCases:
    """Edge case tests for configuration."""

    def test_empty_environment_uses_default(self, monkeypatch):
        """Empty ENVIRONMENT env var is treated as empty string."""
        monkeypatch.setenv("ENVIRONMENT", "")
        config = Config()
        assert config.environment == ""

    def test_whitespace_environment(self, monkeypatch):
        """Whitespace-only ENVIRONMENT is preserved."""
        monkeypatch.setenv("ENVIRONMENT", "  ")
        config = Config()
        assert config.environment == "  "

    def test_unicode_password_is_secure(self):
        """Unicode characters in password are considered secure."""
        config = Neo4jConfig(password="secure_password_123")
        assert config.is_secure is True

    def test_very_long_api_key(self):
        """Very long API key is considered configured."""
        long_key = "sk-" + "a" * 1000
        config = EmbeddingConfig(api_key=long_key)
        assert config.is_configured is True
