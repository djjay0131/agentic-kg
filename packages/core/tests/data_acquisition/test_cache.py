"""
Unit tests for caching layer.
"""

import pytest

from agentic_kg.data_acquisition.cache import (
    CacheType,
    CachedResponse,
    ResponseCache,
    generate_cache_key,
)


class TestGenerateCacheKey:
    """Tests for cache key generation."""

    def test_basic_key_generation(self):
        """Test basic cache key generation."""
        key = generate_cache_key("semantic_scholar", "get_paper", doi="10.1234/test")

        assert key == "semantic_scholar:get_paper:doi=10.1234/test"

    def test_multiple_params(self):
        """Test key generation with multiple params."""
        key = generate_cache_key(
            "openalex",
            "search",
            query="test",
            limit=10,
            offset=0,
        )

        # Params should be sorted alphabetically
        assert "limit=10" in key
        assert "offset=0" in key
        assert "query=test" in key

    def test_none_params_excluded(self):
        """Test that None params are excluded."""
        key = generate_cache_key(
            "arxiv",
            "get_paper",
            arxiv_id="1234.5678",
            other=None,
        )

        assert "arxiv_id=1234.5678" in key
        assert "other" not in key

    def test_long_keys_hashed(self):
        """Test that long keys are hashed."""
        # Create a very long parameter
        long_value = "a" * 300
        key = generate_cache_key("test", "operation", param=long_value)

        # Key should be shortened
        assert len(key) < 250


class TestResponseCache:
    """Tests for ResponseCache."""

    def test_get_returns_none_for_missing_key(self, cache):
        """Test that get returns None for missing keys."""
        result = cache.get("nonexistent_key")
        assert result is None

    def test_set_and_get(self, cache):
        """Test set and get operations."""
        cache.set("test_key", {"data": "value"})
        result = cache.get("test_key")

        assert result == {"data": "value"}

    def test_cache_hit_stats(self, cache):
        """Test that cache hits are tracked."""
        cache.set("test_key", "value")
        cache.get("test_key")  # Hit

        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 0

    def test_cache_miss_stats(self, cache):
        """Test that cache misses are tracked."""
        cache.get("nonexistent")  # Miss

        stats = cache.stats
        assert stats["hits"] == 0
        assert stats["misses"] == 1

    def test_cache_set_stats(self, cache):
        """Test that cache sets are tracked."""
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        stats = cache.stats
        assert stats["sets"] == 2

    def test_delete(self, cache):
        """Test delete operation."""
        cache.set("test_key", "value")
        deleted = cache.delete("test_key")

        assert deleted is True
        assert cache.get("test_key") is None

    def test_delete_nonexistent(self, cache):
        """Test delete of nonexistent key."""
        deleted = cache.delete("nonexistent")
        assert deleted is False

    def test_clear(self, cache):
        """Test clear operation."""
        cache.set("key1", "value1", CacheType.PAPER)
        cache.set("key2", "value2", CacheType.SEARCH)

        cache.clear()

        assert cache.get("key1", CacheType.PAPER) is None
        assert cache.get("key2", CacheType.SEARCH) is None

    def test_clear_specific_type(self, cache):
        """Test clearing specific cache type."""
        cache.set("paper_key", "paper_value", CacheType.PAPER)
        cache.set("search_key", "search_value", CacheType.SEARCH)

        cache.clear(CacheType.PAPER)

        assert cache.get("paper_key", CacheType.PAPER) is None
        assert cache.get("search_key", CacheType.SEARCH) == "search_value"

    def test_contains(self, cache):
        """Test contains operation."""
        cache.set("test_key", "value")

        assert cache.contains("test_key") is True
        assert cache.contains("nonexistent") is False

    def test_different_cache_types(self, cache):
        """Test that different cache types are separate."""
        cache.set("key", "paper_value", CacheType.PAPER)
        cache.set("key", "search_value", CacheType.SEARCH)

        assert cache.get("key", CacheType.PAPER) == "paper_value"
        assert cache.get("key", CacheType.SEARCH) == "search_value"

    def test_disabled_cache(self, cache_config):
        """Test that disabled cache doesn't store."""
        cache_config.enabled = False
        cache = ResponseCache(config=cache_config)

        cache.set("key", "value")
        result = cache.get("key")

        assert result is None

    def test_hit_ratio(self, cache):
        """Test hit ratio calculation."""
        cache.set("key", "value")
        cache.get("key")  # Hit
        cache.get("key")  # Hit
        cache.get("nonexistent")  # Miss

        stats = cache.stats
        assert stats["hit_ratio"] == pytest.approx(2/3, rel=0.01)


class TestCachedResponse:
    """Tests for CachedResponse wrapper."""

    def test_to_dict(self):
        """Test serialization to dict."""
        response = CachedResponse(
            data={"test": "data"},
            source="semantic_scholar",
            ttl=3600,
        )

        d = response.to_dict()

        assert d["data"] == {"test": "data"}
        assert d["source"] == "semantic_scholar"
        assert d["ttl"] == 3600
        assert "cached_at" in d
        assert "age" in d

    def test_from_dict(self):
        """Test deserialization from dict."""
        d = {
            "data": {"test": "data"},
            "source": "semantic_scholar",
            "cached_at": 1000.0,
            "ttl": 3600,
        }

        response = CachedResponse.from_dict(d)

        assert response.data == {"test": "data"}
        assert response.source == "semantic_scholar"
        assert response.cached_at == 1000.0
        assert response.ttl == 3600

    def test_age(self):
        """Test age property."""
        response = CachedResponse(
            data="test",
            source="test",
        )

        # Age should be very small (just created)
        assert response.age < 1.0

    def test_is_stale(self):
        """Test is_stale property."""
        # Response with no TTL is never stale
        response1 = CachedResponse(data="test", source="test", ttl=0)
        assert response1.is_stale is False

        # Response with TTL in future is not stale
        response2 = CachedResponse(data="test", source="test", ttl=3600)
        assert response2.is_stale is False
