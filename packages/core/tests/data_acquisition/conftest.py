"""
Pytest fixtures for data acquisition tests.
"""

import pytest

from agentic_kg.data_acquisition.config import (
    ArxivConfig,
    CacheConfig,
    CircuitBreakerConfig,
    DataAcquisitionConfig,
    OpenAlexConfig,
    RateLimitConfig,
    SemanticScholarConfig,
    reset_data_acquisition_config,
)
from agentic_kg.data_acquisition.cache import ResponseCache, reset_response_cache
from agentic_kg.data_acquisition.rate_limiter import (
    TokenBucketRateLimiter,
    reset_rate_limiter_registry,
)
from agentic_kg.data_acquisition.resilience import (
    CircuitBreaker,
    reset_circuit_breaker_registry,
)


@pytest.fixture
def rate_limit_config():
    """Create a test rate limit config."""
    return RateLimitConfig(
        burst_multiplier=1.5,
        initial_backoff=0.1,  # Fast for tests
        max_backoff=1.0,
        backoff_multiplier=2.0,
        jitter=0.0,  # No jitter for deterministic tests
    )


@pytest.fixture
def circuit_breaker_config():
    """Create a test circuit breaker config."""
    return CircuitBreakerConfig(
        failure_threshold=3,
        cooldown_period=1.0,  # Fast for tests
        success_threshold=1,
    )


@pytest.fixture
def cache_config():
    """Create a test cache config."""
    return CacheConfig(
        enabled=True,
        paper_ttl=60,  # Short TTL for tests
        search_ttl=30,
        author_ttl=60,
        max_size=100,
    )


@pytest.fixture
def rate_limiter(rate_limit_config):
    """Create a test rate limiter."""
    return TokenBucketRateLimiter(
        rate=10.0,  # Fast for tests
        config=rate_limit_config,
        source="test",
    )


@pytest.fixture
def circuit_breaker(circuit_breaker_config):
    """Create a test circuit breaker."""
    return CircuitBreaker(
        config=circuit_breaker_config,
        source="test",
    )


@pytest.fixture
def cache(cache_config):
    """Create a test cache."""
    return ResponseCache(config=cache_config)


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset all singletons before each test."""
    reset_data_acquisition_config()
    reset_response_cache()
    reset_rate_limiter_registry()
    reset_circuit_breaker_registry()
    yield
    reset_data_acquisition_config()
    reset_response_cache()
    reset_rate_limiter_registry()
    reset_circuit_breaker_registry()


# Sample data fixtures

@pytest.fixture
def sample_semantic_scholar_paper():
    """Sample Semantic Scholar paper response."""
    return {
        "paperId": "649def34f8be52c8b66281af98ae884c09aef38b",
        "externalIds": {
            "DOI": "10.18653/v1/N18-1202",
            "ArXiv": "1802.05365",
            "MAG": "2889531643",
        },
        "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        "abstract": "We introduce BERT, a new language representation model...",
        "year": 2019,
        "venue": "NAACL",
        "authors": [
            {"authorId": "1234", "name": "Jacob Devlin"},
            {"authorId": "5678", "name": "Ming-Wei Chang"},
        ],
        "citationCount": 50000,
        "referenceCount": 50,
        "fieldsOfStudy": ["Computer Science"],
        "publicationTypes": ["JournalArticle"],
        "isOpenAccess": True,
        "openAccessPdf": {"url": "https://arxiv.org/pdf/1810.04805.pdf"},
    }


@pytest.fixture
def sample_arxiv_paper():
    """Sample arXiv paper response (after parsing)."""
    return {
        "id": "1810.04805",
        "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        "summary": "We introduce BERT, a new language representation model...",
        "authors": [
            {"name": "Jacob Devlin"},
            {"name": "Ming-Wei Chang"},
        ],
        "published": "2018-10-11T00:00:00Z",
        "updated": "2019-05-24T00:00:00Z",
        "categories": ["cs.CL"],
        "primary_category": "cs.CL",
        "doi": "10.18653/v1/N18-1202",
        "pdf_url": "https://arxiv.org/pdf/1810.04805.pdf",
        "abs_url": "https://arxiv.org/abs/1810.04805",
    }


@pytest.fixture
def sample_openalex_work():
    """Sample OpenAlex work response."""
    return {
        "id": "https://openalex.org/W2963403868",
        "doi": "https://doi.org/10.18653/v1/n19-1423",
        "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        "abstract_inverted_index": {
            "We": [0],
            "introduce": [1],
            "BERT": [2],
        },
        "publication_year": 2019,
        "publication_date": "2019-06-01",
        "authorships": [
            {
                "author": {
                    "id": "https://openalex.org/A1234",
                    "display_name": "Jacob Devlin",
                    "orcid": "https://orcid.org/0000-0001-1234-5678",
                },
                "author_position": "first",
                "institutions": [{"display_name": "Google"}],
            },
        ],
        "cited_by_count": 55000,
        "referenced_works_count": 45,
        "concepts": [
            {"display_name": "Natural language processing"},
            {"display_name": "Deep learning"},
        ],
        "primary_location": {
            "source": {"display_name": "NAACL-HLT"},
        },
        "open_access": {
            "is_oa": True,
            "oa_url": "https://arxiv.org/pdf/1810.04805.pdf",
        },
        "type": "journal-article",
    }
