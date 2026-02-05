"""
Data Acquisition module for Agentic KG.

Provides:
- API clients for Semantic Scholar, arXiv, and OpenAlex
- Rate limiting and retry infrastructure
- Response caching
- Paper metadata normalization
- Multi-source aggregation and deduplication
- Knowledge Graph import
"""
from __future__ import annotations


# Config
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

# Exceptions
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

# Rate Limiting
from agentic_kg.data_acquisition.rate_limiter import (
    RateLimiterRegistry,
    TokenBucketRateLimiter,
    get_rate_limiter_registry,
    reset_rate_limiter_registry,
)

# Resilience
from agentic_kg.data_acquisition.resilience import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitState,
    get_circuit_breaker_registry,
    reset_circuit_breaker_registry,
    retry_with_backoff,
    with_retry,
)

# Caching
from agentic_kg.data_acquisition.cache import (
    CachedResponse,
    CacheStats,
    CacheType,
    ResponseCache,
    generate_cache_key,
    get_response_cache,
    reset_response_cache,
)

# API Clients
from agentic_kg.data_acquisition.semantic_scholar import (
    SemanticScholarClient,
    get_semantic_scholar_client,
    reset_semantic_scholar_client,
)
from agentic_kg.data_acquisition.arxiv import (
    ArxivClient,
    construct_abs_url,
    construct_pdf_url,
    get_arxiv_client,
    normalize_arxiv_id,
    reset_arxiv_client,
)
from agentic_kg.data_acquisition.openalex import (
    OpenAlexClient,
    get_openalex_client,
    normalize_openalex_id,
    reconstruct_abstract,
    reset_openalex_client,
)

# Normalization
from agentic_kg.data_acquisition.normalizer import (
    NormalizedAuthor,
    NormalizedPaper,
    PaperNormalizer,
    get_paper_normalizer,
    merge_normalized_papers,
)

# Aggregation
from agentic_kg.data_acquisition.aggregator import (
    AggregatedResult,
    PaperAggregator,
    SearchResult,
    detect_identifier_type,
    get_paper_aggregator,
    reset_paper_aggregator,
)

# Import
from agentic_kg.data_acquisition.importer import (
    BatchImportResult,
    ImportResult,
    PaperImporter,
    get_paper_importer,
    normalized_to_kg_author,
    normalized_to_kg_paper,
    reset_paper_importer,
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
    # Rate Limiting
    "RateLimiterRegistry",
    "TokenBucketRateLimiter",
    "get_rate_limiter_registry",
    "reset_rate_limiter_registry",
    # Resilience
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitState",
    "get_circuit_breaker_registry",
    "reset_circuit_breaker_registry",
    "retry_with_backoff",
    "with_retry",
    # Caching
    "CachedResponse",
    "CacheStats",
    "CacheType",
    "ResponseCache",
    "generate_cache_key",
    "get_response_cache",
    "reset_response_cache",
    # Semantic Scholar
    "SemanticScholarClient",
    "get_semantic_scholar_client",
    "reset_semantic_scholar_client",
    # arXiv
    "ArxivClient",
    "construct_abs_url",
    "construct_pdf_url",
    "get_arxiv_client",
    "normalize_arxiv_id",
    "reset_arxiv_client",
    # OpenAlex
    "OpenAlexClient",
    "get_openalex_client",
    "normalize_openalex_id",
    "reconstruct_abstract",
    "reset_openalex_client",
    # Normalization
    "NormalizedAuthor",
    "NormalizedPaper",
    "PaperNormalizer",
    "get_paper_normalizer",
    "merge_normalized_papers",
    # Aggregation
    "AggregatedResult",
    "PaperAggregator",
    "SearchResult",
    "detect_identifier_type",
    "get_paper_aggregator",
    "reset_paper_aggregator",
    # Import
    "BatchImportResult",
    "ImportResult",
    "PaperImporter",
    "get_paper_importer",
    "normalized_to_kg_author",
    "normalized_to_kg_paper",
    "reset_paper_importer",
]
