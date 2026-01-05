"""
Configuration management for Agentic Knowledge Graph.

Supports environment-based configuration for local development and production.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


# Insecure default passwords that should never be used in production
_INSECURE_PASSWORDS = {"password", "changeme", "secret", "admin", ""}


@dataclass
class Neo4jConfig:
    """Neo4j database connection configuration."""

    uri: str = field(default_factory=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    username: str = field(default_factory=lambda: os.getenv("NEO4J_USERNAME", "neo4j"))
    password: str = field(default_factory=lambda: os.getenv("NEO4J_PASSWORD", ""))
    database: str = field(default_factory=lambda: os.getenv("NEO4J_DATABASE", "neo4j"))

    # Connection pool settings
    max_connection_lifetime: int = 3600  # seconds
    max_connection_pool_size: int = 50
    connection_acquisition_timeout: int = 60  # seconds

    # Retry settings
    max_retries: int = 3
    retry_delay: float = 1.0  # seconds, with exponential backoff

    @property
    def is_secure(self) -> bool:
        """Check if password is secure (not a known insecure default)."""
        return self.password not in _INSECURE_PASSWORDS


@dataclass
class EmbeddingConfig:
    """OpenAI embedding configuration."""

    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"))
    dimensions: int = 1536

    # Batch settings
    batch_size: int = 100
    max_retries: int = 3
    retry_delay: float = 1.0

    @property
    def is_configured(self) -> bool:
        """Check if API key is properly configured."""
        return bool(self.api_key and not self.api_key.startswith("sk-your-"))


@dataclass
class SearchConfig:
    """Search and retrieval configuration."""

    # Semantic search
    default_top_k: int = 10
    similarity_threshold: float = 0.5

    # Deduplication
    deduplication_threshold: float = 0.95

    # Hybrid search weights
    semantic_weight: float = 0.7
    structured_weight: float = 0.3


class ConfigurationError(Exception):
    """Raised when configuration is invalid."""

    pass


@dataclass
class Config:
    """Main application configuration."""

    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    search: SearchConfig = field(default_factory=SearchConfig)

    # Environment
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.is_production:
            self.validate_production()

    def validate_production(self) -> None:
        """Validate that production configuration is secure."""
        errors = []

        if not self.neo4j.is_secure:
            errors.append("NEO4J_PASSWORD must be set to a secure value in production")

        if not self.embedding.is_configured:
            errors.append("OPENAI_API_KEY must be set in production")

        if errors:
            raise ConfigurationError(
                "Production configuration errors:\n" + "\n".join(f"  - {e}" for e in errors)
            )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


# Singleton instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the application configuration singleton."""
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config() -> None:
    """Reset the configuration singleton (useful for testing)."""
    global _config
    _config = None


# Convenience export
__all__ = ["Config", "ConfigurationError", "get_config", "reset_config"]
