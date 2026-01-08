"""
TTL-based metadata caching for paper acquisition.

Provides in-memory caching with configurable TTL for paper metadata,
reducing API calls for frequently accessed papers.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Generic, Optional, TypeVar

from agentic_kg.config import get_config
from agentic_kg.data_acquisition.models import PaperMetadata

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """A cached item with timestamp and TTL tracking."""

    value: T
    created_at: float = field(default_factory=time.time)
    accessed_at: float = field(default_factory=time.time)
    ttl_seconds: float = 604800.0  # 7 days default

    def is_expired(self) -> bool:
        """Check if entry has expired."""
        return time.time() - self.created_at > self.ttl_seconds

    def touch(self) -> None:
        """Update last access time."""
        self.accessed_at = time.time()


class TTLCache(Generic[T]):
    """
    Thread-safe TTL-based in-memory cache.

    Features:
    - Configurable TTL (time-to-live)
    - LRU eviction when max size exceeded
    - Thread-safe operations
    - Cache statistics tracking
    - Explicit invalidation and bypass

    Example:
        cache = TTLCache[PaperMetadata](max_size=1000, ttl_seconds=86400)

        # Store
        cache.set("10.1038/nature12373", paper_metadata)

        # Retrieve
        paper = cache.get("10.1038/nature12373")

        # Bypass cache
        paper = cache.get("10.1038/nature12373", bypass=True)  # Always None
    """

    def __init__(
        self,
        max_size: int = 1000,
        ttl_seconds: float = 604800.0,  # 7 days
    ):
        """
        Initialize TTL cache.

        Args:
            max_size: Maximum number of items to cache
            ttl_seconds: Time-to-live in seconds (default 7 days)
        """
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, CacheEntry[T]] = {}
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def get(
        self,
        key: str,
        bypass: bool = False,
    ) -> Optional[T]:
        """
        Get item from cache.

        Args:
            key: Cache key
            bypass: If True, always return None (bypass cache)

        Returns:
            Cached value or None if not found/expired
        """
        if bypass:
            with self._lock:
                self._misses += 1
            return None

        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._misses += 1
                return None

            if entry.is_expired():
                del self._cache[key]
                self._misses += 1
                return None

            entry.touch()
            self._hits += 1
            return entry.value

    def set(
        self,
        key: str,
        value: T,
        ttl_seconds: Optional[float] = None,
    ) -> None:
        """
        Store item in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl_seconds: Custom TTL (uses default if not specified)
        """
        with self._lock:
            # Evict if at capacity
            if len(self._cache) >= self._max_size and key not in self._cache:
                self._evict_lru()

            self._cache[key] = CacheEntry(
                value=value,
                ttl_seconds=ttl_seconds or self._ttl_seconds,
            )

    def invalidate(self, key: str) -> bool:
        """
        Remove item from cache.

        Args:
            key: Cache key

        Returns:
            True if item was removed, False if not found
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def invalidate_pattern(self, pattern: str) -> int:
        """
        Remove items matching a pattern.

        Args:
            pattern: Pattern to match (simple substring match)

        Returns:
            Number of items removed
        """
        with self._lock:
            keys_to_delete = [
                key for key in self._cache if pattern in key
            ]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)

    def clear(self) -> None:
        """Clear all cached items."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def _evict_lru(self) -> None:
        """Evict least recently used item."""
        if not self._cache:
            return

        # Find LRU entry
        lru_key = min(
            self._cache.keys(),
            key=lambda k: self._cache[k].accessed_at,
        )
        del self._cache[lru_key]

    def cleanup_expired(self) -> int:
        """
        Remove all expired entries.

        Returns:
            Number of entries removed
        """
        with self._lock:
            expired = [
                key for key, entry in self._cache.items()
                if entry.is_expired()
            ]
            for key in expired:
                del self._cache[key]
            return len(expired)

    def get_stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dict with hits, misses, hit_rate, size, max_size
        """
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / total if total > 0 else 0.0,
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl_seconds,
            }

    def __len__(self) -> int:
        """Get number of items in cache."""
        with self._lock:
            return len(self._cache)

    def __contains__(self, key: str) -> bool:
        """Check if key is in cache and not expired."""
        return self.get(key) is not None


class MetadataCache:
    """
    Metadata cache manager for paper acquisition.

    Provides caching for paper metadata from different sources,
    with source-specific TTL configuration.

    Example:
        cache = MetadataCache()

        # Cache metadata
        cache.set_paper("10.1038/nature12373", paper_metadata)

        # Retrieve (returns None if expired)
        paper = cache.get_paper("10.1038/nature12373")

        # Force fresh fetch by bypassing cache
        paper = cache.get_paper("10.1038/nature12373", bypass=True)
    """

    def __init__(
        self,
        max_size: int = 1000,
        ttl_days: float = 7.0,
    ):
        """
        Initialize metadata cache.

        Args:
            max_size: Maximum number of items to cache
            ttl_days: Time-to-live in days
        """
        config = get_config()
        cache_config = config.data_acquisition.cache

        self._ttl_seconds = ttl_days * 24 * 60 * 60
        self._paper_cache: TTLCache[PaperMetadata] = TTLCache(
            max_size=max_size,
            ttl_seconds=self._ttl_seconds,
        )

    def get_paper(
        self,
        identifier: str,
        bypass: bool = False,
    ) -> Optional[PaperMetadata]:
        """
        Get cached paper metadata.

        Args:
            identifier: Paper identifier
            bypass: If True, always return None (fetch fresh)

        Returns:
            Cached metadata or None
        """
        return self._paper_cache.get(identifier, bypass=bypass)

    def set_paper(
        self,
        identifier: str,
        metadata: PaperMetadata,
        ttl_seconds: Optional[float] = None,
    ) -> None:
        """
        Cache paper metadata.

        Args:
            identifier: Paper identifier
            metadata: Paper metadata to cache
            ttl_seconds: Custom TTL (uses default if not specified)
        """
        self._paper_cache.set(identifier, metadata, ttl_seconds)

        # Also cache by alternate identifiers
        if metadata.doi and metadata.doi != identifier:
            self._paper_cache.set(f"doi:{metadata.doi}", metadata, ttl_seconds)
        if metadata.arxiv_id and metadata.arxiv_id != identifier:
            self._paper_cache.set(f"arxiv:{metadata.arxiv_id}", metadata, ttl_seconds)
        if metadata.s2_id and metadata.s2_id != identifier:
            self._paper_cache.set(f"s2:{metadata.s2_id}", metadata, ttl_seconds)

    def invalidate(self, identifier: str) -> bool:
        """
        Remove paper from cache.

        Args:
            identifier: Paper identifier

        Returns:
            True if removed
        """
        return self._paper_cache.invalidate(identifier)

    def clear(self) -> None:
        """Clear all cached metadata."""
        self._paper_cache.clear()

    def cleanup_expired(self) -> int:
        """Remove expired entries."""
        return self._paper_cache.cleanup_expired()

    def get_stats(self) -> dict:
        """Get cache statistics."""
        return self._paper_cache.get_stats()


# Singleton cache
_metadata_cache: Optional[MetadataCache] = None


def get_metadata_cache() -> MetadataCache:
    """Get the metadata cache singleton."""
    global _metadata_cache
    if _metadata_cache is None:
        _metadata_cache = MetadataCache()
    return _metadata_cache


def reset_metadata_cache() -> None:
    """Reset the metadata cache singleton."""
    global _metadata_cache
    if _metadata_cache is not None:
        _metadata_cache.clear()
    _metadata_cache = None
