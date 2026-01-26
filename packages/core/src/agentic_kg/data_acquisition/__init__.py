"""
Data Acquisition module for Agentic KG.

Provides:
- API clients for Semantic Scholar, arXiv, and OpenAlex
- Rate limiting and retry infrastructure
- Response caching
- Paper metadata normalization
- Multi-source aggregation and deduplication
"""

from agentic_kg.data_acquisition.config import (
    ArxivConfig,
    CacheConfig,
    CircuitBreakerConfig,
    DataAcquisitionConfig,
    OpenAlexConfig,
    RateLimitConfig,
    SemanticScholarConfig,
    get_data_acquisition_config,
    reset_data_acquisition_config,
)
from agentic_kg.data_acquisition.exceptions import (
    APIError,
    CacheError,
    CircuitOpenError,
    DataAcquisitionError,
    NormalizationError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)

__all__ = [
    # Config
    "ArxivConfig",
    "CacheConfig",
    "CircuitBreakerConfig",
    "DataAcquisitionConfig",
    "OpenAlexConfig",
    "RateLimitConfig",
    "SemanticScholarConfig",
    "get_data_acquisition_config",
    "reset_data_acquisition_config",
    # Exceptions
    "APIError",
    "CacheError",
    "CircuitOpenError",
    "DataAcquisitionError",
    "NormalizationError",
    "NotFoundError",
    "RateLimitError",
    "ValidationError",
]
