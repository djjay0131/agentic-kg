"""
Configuration management for Agentic Knowledge Graph.

Supports environment-based configuration for local development and production.
"""

import os
from dataclasses import dataclass, field


@dataclass
class Neo4jConfig:
    """Neo4j database connection configuration."""

    uri: str = field(default_factory=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    username: str = field(default_factory=lambda: os.getenv("NEO4J_USERNAME", "neo4j"))
    password: str = field(default_factory=lambda: os.getenv("NEO4J_PASSWORD", "password"))
    database: str = field(default_factory=lambda: os.getenv("NEO4J_DATABASE", "neo4j"))

    # Connection pool settings
    max_connection_lifetime: int = 3600  # seconds
    max_connection_pool_size: int = 50
    connection_acquisition_timeout: int = 60  # seconds

    # Retry settings
    max_retries: int = 3
    retry_delay: float = 1.0  # seconds, with exponential backoff


@dataclass
class EmbeddingConfig:
    """OpenAI embedding configuration."""

    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    model: str = "text-embedding-3-small"
    dimensions: int = 1536

    # Batch settings
    batch_size: int = 100
    max_retries: int = 3
    retry_delay: float = 1.0


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


@dataclass
class Config:
    """Main application configuration."""

    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    search: SearchConfig = field(default_factory=SearchConfig)

    # Environment
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


def get_config() -> Config:
    """Get the application configuration singleton."""
    return Config()


# Default configuration instance
config = get_config()
