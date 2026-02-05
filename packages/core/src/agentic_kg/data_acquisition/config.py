"""
Configuration for Data Acquisition module.

Provides settings for API clients, rate limiting, caching, and resilience.
"""
from __future__ import annotations


import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SemanticScholarConfig:
    """Semantic Scholar API configuration."""

    api_key: str = field(
        default_factory=lambda: os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    )
    base_url: str = "https://api.semanticscholar.org/graph/v1"

    # Rate limits (requests per second)
    # Unauthenticated: 100 requests per 5 minutes = 0.33 req/sec
    # Authenticated: 1 request per second
    rate_limit: float = field(
        default_factory=lambda: float(os.getenv("SEMANTIC_SCHOLAR_RATE_LIMIT", "1.0"))
    )

    # Request settings
    timeout: float = 30.0
    max_retries: int = 3

    @property
    def is_authenticated(self) -> bool:
        """Check if API key is configured."""
        return bool(self.api_key)

    @property
    def headers(self) -> dict[str, str]:
        """Get request headers including API key if configured."""
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers


@dataclass
class ArxivConfig:
    """arXiv API configuration."""

    base_url: str = "http://export.arxiv.org/api/query"

    # Rate limit: arXiv requests 3 second delay between requests
    rate_limit: float = field(
        default_factory=lambda: float(os.getenv("ARXIV_RATE_LIMIT", "0.33"))
    )

    # Request settings
    timeout: float = 30.0
    max_retries: int = 3

    # PDF base URL
    pdf_base_url: str = "https://arxiv.org/pdf"
    abs_base_url: str = "https://arxiv.org/abs"


@dataclass
class OpenAlexConfig:
    """OpenAlex API configuration."""

    base_url: str = "https://api.openalex.org"

    # Polite pool email (recommended for higher rate limits)
    email: str = field(default_factory=lambda: os.getenv("OPENALEX_EMAIL", ""))

    # Rate limits
    # Polite pool (with email): 10 requests per second
    # Without email: lower limits
    rate_limit: float = field(
        default_factory=lambda: float(os.getenv("OPENALEX_RATE_LIMIT", "10.0"))
    )

    # Request settings
    timeout: float = 30.0
    max_retries: int = 3

    @property
    def is_polite(self) -> bool:
        """Check if using polite pool (email configured)."""
        return bool(self.email)

    @property
    def user_agent(self) -> str:
        """Get User-Agent string for polite pool identification."""
        base = "agentic-kg/0.1.0 (https://github.com/djjay0131/agentic-kg)"
        if self.email:
            return f"{base}; mailto:{self.email}"
        return base


@dataclass
class RateLimitConfig:
    """Rate limiting configuration."""

    # Token bucket settings
    burst_multiplier: float = 1.5  # Allow burst up to 1.5x rate

    # Backoff settings
    initial_backoff: float = 1.0  # seconds
    max_backoff: float = 60.0  # seconds
    backoff_multiplier: float = 2.0
    jitter: float = 0.1  # 10% jitter


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""

    # Failure threshold to open circuit
    failure_threshold: int = field(
        default_factory=lambda: int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5"))
    )

    # Cooldown period before half-open state (seconds)
    cooldown_period: float = field(
        default_factory=lambda: float(os.getenv("CIRCUIT_COOLDOWN_PERIOD", "60.0"))
    )

    # Success threshold to close circuit from half-open
    success_threshold: int = 1


@dataclass
class CacheConfig:
    """Caching configuration."""

    # Cache enabled
    enabled: bool = field(
        default_factory=lambda: os.getenv("CACHE_ENABLED", "true").lower() == "true"
    )

    # TTL settings (seconds)
    paper_ttl: int = field(
        default_factory=lambda: int(os.getenv("CACHE_PAPER_TTL", str(7 * 24 * 3600)))
    )  # 7 days
    search_ttl: int = field(
        default_factory=lambda: int(os.getenv("CACHE_SEARCH_TTL", str(1 * 3600)))
    )  # 1 hour
    author_ttl: int = field(
        default_factory=lambda: int(os.getenv("CACHE_AUTHOR_TTL", str(7 * 24 * 3600)))
    )  # 7 days

    # Cache size limits
    max_size: int = field(
        default_factory=lambda: int(os.getenv("CACHE_MAX_SIZE", "10000"))
    )

    # Redis settings (optional)
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", ""))

    @property
    def use_redis(self) -> bool:
        """Check if Redis should be used for caching."""
        return bool(self.redis_url)


@dataclass
class DataAcquisitionConfig:
    """Main configuration for Data Acquisition module."""

    semantic_scholar: SemanticScholarConfig = field(
        default_factory=SemanticScholarConfig
    )
    arxiv: ArxivConfig = field(default_factory=ArxivConfig)
    openalex: OpenAlexConfig = field(default_factory=OpenAlexConfig)

    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)

    # General settings
    default_batch_size: int = 50
    max_concurrent_requests: int = 5


# Singleton instance
_config: Optional[DataAcquisitionConfig] = None


def get_data_acquisition_config() -> DataAcquisitionConfig:
    """Get the data acquisition configuration singleton."""
    global _config
    if _config is None:
        _config = DataAcquisitionConfig()
    return _config


def reset_data_acquisition_config() -> None:
    """Reset the configuration singleton (useful for testing)."""
    global _config
    _config = None


__all__ = [
    "SemanticScholarConfig",
    "ArxivConfig",
    "OpenAlexConfig",
    "RateLimitConfig",
    "CircuitBreakerConfig",
    "CacheConfig",
    "DataAcquisitionConfig",
    "get_data_acquisition_config",
    "reset_data_acquisition_config",
]
