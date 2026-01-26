"""
Caching layer for Data Acquisition.

Provides in-memory caching with TTL support for API responses.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from cachetools import TTLCache

from agentic_kg.data_acquisition.config import CacheConfig

logger = logging.getLogger(__name__)


class CacheType(Enum):
    """Types of cached data with different TTLs."""

    PAPER = "paper"
    SEARCH = "search"
    AUTHOR = "author"


@dataclass
class CacheStats:
    """Statistics for cache operations."""

    hits: int = 0
    misses: int = 0
    sets: int = 0
    deletes: int = 0

    @property
    def hit_ratio(self) -> float:
        """Calculate cache hit ratio."""
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "deletes": self.deletes,
            "hit_ratio": round(self.hit_ratio, 3),
        }


def generate_cache_key(
    source: str,
    operation: str,
    **params: Any,
) -> str:
    """
    Generate a consistent cache key from parameters.

    Args:
        source: API source (e.g., "semantic_scholar")
        operation: Operation type (e.g., "get_paper", "search")
        **params: Parameters that uniquely identify the request

    Returns:
        Cache key string
    """
    # Sort params for consistent ordering
    sorted_params = sorted(params.items())

    # Create a string representation
    key_parts = [source, operation]
    for k, v in sorted_params:
        if v is not None:
            key_parts.append(f"{k}={v}")

    key_str = ":".join(key_parts)

    # Hash long keys
    if len(key_str) > 200:
        key_hash = hashlib.md5(key_str.encode()).hexdigest()[:16]
        return f"{source}:{operation}:{key_hash}"

    return key_str


class ResponseCache:
    """
    Response cache with TTL support.

    Uses cachetools TTLCache for in-memory caching with automatic expiration.
    """

    def __init__(self, config: CacheConfig | None = None):
        """
        Initialize the cache.

        Args:
            config: Cache configuration
        """
        self.config = config or CacheConfig()
        self._stats = CacheStats()

        # Create separate caches for each type with different TTLs
        self._caches: dict[CacheType, TTLCache[str, Any]] = {}

        if self.config.enabled:
            self._caches[CacheType.PAPER] = TTLCache(
                maxsize=self.config.max_size,
                ttl=self.config.paper_ttl,
            )
            self._caches[CacheType.SEARCH] = TTLCache(
                maxsize=self.config.max_size // 2,  # Smaller for search results
                ttl=self.config.search_ttl,
            )
            self._caches[CacheType.AUTHOR] = TTLCache(
                maxsize=self.config.max_size // 2,
                ttl=self.config.author_ttl,
            )

    def _get_cache(self, cache_type: CacheType) -> TTLCache[str, Any] | None:
        """Get the cache for a specific type."""
        if not self.config.enabled:
            return None
        return self._caches.get(cache_type)

    def get(
        self,
        key: str,
        cache_type: CacheType = CacheType.PAPER,
    ) -> Any | None:
        """
        Get a value from cache.

        Args:
            key: Cache key
            cache_type: Type of cache to use

        Returns:
            Cached value or None if not found/expired
        """
        cache = self._get_cache(cache_type)
        if cache is None:
            self._stats.misses += 1
            return None

        value = cache.get(key)
        if value is not None:
            self._stats.hits += 1
            logger.debug("Cache hit: %s", key)
        else:
            self._stats.misses += 1
            logger.debug("Cache miss: %s", key)

        return value

    def set(
        self,
        key: str,
        value: Any,
        cache_type: CacheType = CacheType.PAPER,
    ) -> None:
        """
        Set a value in cache.

        Args:
            key: Cache key
            value: Value to cache
            cache_type: Type of cache to use
        """
        cache = self._get_cache(cache_type)
        if cache is None:
            return

        cache[key] = value
        self._stats.sets += 1
        logger.debug("Cache set: %s", key)

    def delete(self, key: str, cache_type: CacheType = CacheType.PAPER) -> bool:
        """
        Delete a value from cache.

        Args:
            key: Cache key
            cache_type: Type of cache to use

        Returns:
            True if value was deleted, False if not found
        """
        cache = self._get_cache(cache_type)
        if cache is None:
            return False

        if key in cache:
            del cache[key]
            self._stats.deletes += 1
            logger.debug("Cache delete: %s", key)
            return True
        return False

    def clear(self, cache_type: CacheType | None = None) -> None:
        """
        Clear cache(s).

        Args:
            cache_type: Specific cache to clear, or None for all
        """
        if cache_type is not None:
            cache = self._get_cache(cache_type)
            if cache is not None:
                cache.clear()
        else:
            for cache in self._caches.values():
                cache.clear()

        logger.info("Cache cleared")

    @property
    def stats(self) -> dict:
        """Get cache statistics."""
        result = self._stats.to_dict()
        result["enabled"] = self.config.enabled

        # Add per-cache sizes
        if self.config.enabled:
            result["sizes"] = {
                cache_type.value: len(cache)
                for cache_type, cache in self._caches.items()
            }

        return result

    def contains(self, key: str, cache_type: CacheType = CacheType.PAPER) -> bool:
        """
        Check if key exists in cache (without counting as hit/miss).

        Args:
            key: Cache key
            cache_type: Type of cache to check

        Returns:
            True if key exists and is not expired
        """
        cache = self._get_cache(cache_type)
        if cache is None:
            return False
        return key in cache


@dataclass
class CachedResponse:
    """Wrapper for cached API responses with metadata."""

    data: Any
    source: str
    cached_at: float = field(default_factory=time.time)
    ttl: int = 0

    @property
    def age(self) -> float:
        """Get age of cached response in seconds."""
        return time.time() - self.cached_at

    @property
    def is_stale(self) -> bool:
        """Check if response is past its TTL."""
        if self.ttl <= 0:
            return False
        return self.age > self.ttl

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "data": self.data,
            "source": self.source,
            "cached_at": self.cached_at,
            "ttl": self.ttl,
            "age": self.age,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CachedResponse":
        """Deserialize from dictionary."""
        return cls(
            data=d["data"],
            source=d["source"],
            cached_at=d.get("cached_at", time.time()),
            ttl=d.get("ttl", 0),
        )


# Singleton instance
_cache: ResponseCache | None = None


def get_response_cache() -> ResponseCache:
    """Get the response cache singleton."""
    global _cache
    if _cache is None:
        _cache = ResponseCache()
    return _cache


def reset_response_cache() -> None:
    """Reset the response cache (useful for testing)."""
    global _cache
    _cache = None


__all__ = [
    "CacheType",
    "CacheStats",
    "generate_cache_key",
    "ResponseCache",
    "CachedResponse",
    "get_response_cache",
    "reset_response_cache",
]
