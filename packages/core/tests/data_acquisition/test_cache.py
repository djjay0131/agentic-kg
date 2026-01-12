"""Tests for PDF and metadata caching."""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from agentic_kg.data_acquisition.cache import (
    PaperCache,
    get_paper_cache,
    reset_paper_cache,
)
from agentic_kg.data_acquisition.metadata_cache import (
    TTLCache,
    CacheEntry,
    MetadataCache,
    get_metadata_cache,
    reset_metadata_cache,
)
from agentic_kg.data_acquisition.models import (
    PaperMetadata,
    SourceType,
)


class TestCacheEntry:
    """Tests for CacheEntry dataclass."""

    def test_create(self):
        entry = CacheEntry(value="test", ttl_seconds=60.0)
        assert entry.value == "test"
        assert entry.ttl_seconds == 60.0

    def test_is_expired_false(self):
        entry = CacheEntry(value="test", ttl_seconds=3600.0)
        assert entry.is_expired() is False

    def test_is_expired_true(self):
        import time

        entry = CacheEntry(value="test", ttl_seconds=0.01)
        time.sleep(0.02)
        assert entry.is_expired() is True

    def test_touch(self):
        import time

        entry = CacheEntry(value="test")
        original_accessed = entry.accessed_at
        time.sleep(0.01)
        entry.touch()
        assert entry.accessed_at > original_accessed


class TestTTLCache:
    """Tests for TTLCache."""

    def test_create(self):
        cache = TTLCache[str](max_size=100, ttl_seconds=3600.0)
        assert len(cache) == 0

    def test_set_and_get(self):
        cache = TTLCache[str]()
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing(self):
        cache = TTLCache[str]()
        assert cache.get("missing") is None

    def test_get_expired(self):
        import time

        cache = TTLCache[str](ttl_seconds=0.01)
        cache.set("key1", "value1")
        time.sleep(0.02)
        assert cache.get("key1") is None

    def test_get_bypass(self):
        cache = TTLCache[str]()
        cache.set("key1", "value1")
        assert cache.get("key1", bypass=True) is None

    def test_invalidate(self):
        cache = TTLCache[str]()
        cache.set("key1", "value1")
        assert cache.invalidate("key1") is True
        assert cache.get("key1") is None

    def test_invalidate_missing(self):
        cache = TTLCache[str]()
        assert cache.invalidate("missing") is False

    def test_invalidate_pattern(self):
        cache = TTLCache[str]()
        cache.set("paper:123", "value1")
        cache.set("paper:456", "value2")
        cache.set("author:789", "value3")

        count = cache.invalidate_pattern("paper:")
        assert count == 2
        assert cache.get("paper:123") is None
        assert cache.get("author:789") == "value3"

    def test_clear(self):
        cache = TTLCache[str]()
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert len(cache) == 0

    def test_lru_eviction(self):
        cache = TTLCache[str](max_size=2)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")  # Should evict key1 (LRU)

        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"
        assert cache.get("key3") == "value3"

    def test_cleanup_expired(self):
        import time

        cache = TTLCache[str](ttl_seconds=0.01)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        time.sleep(0.02)
        cache.set("key3", "value3", ttl_seconds=3600.0)

        count = cache.cleanup_expired()
        assert count == 2
        assert len(cache) == 1

    def test_get_stats(self):
        cache = TTLCache[str](max_size=100, ttl_seconds=3600.0)
        cache.set("key1", "value1")
        cache.get("key1")  # Hit
        cache.get("missing")  # Miss

        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1
        assert stats["max_size"] == 100
        assert stats["hit_rate"] == 0.5

    def test_contains(self):
        cache = TTLCache[str]()
        cache.set("key1", "value1")
        assert "key1" in cache
        assert "missing" not in cache


class TestMetadataCache:
    """Tests for MetadataCache."""

    def test_create(self):
        cache = MetadataCache(max_size=100, ttl_days=7.0)
        assert cache is not None

    def test_set_and_get_paper(self):
        cache = MetadataCache()
        paper = PaperMetadata(
            paper_id="test123",
            title="Test Paper",
            doi="10.1038/nature12373",
        )
        cache.set_paper("test123", paper)
        retrieved = cache.get_paper("test123")
        assert retrieved is not None
        assert retrieved.title == "Test Paper"

    def test_get_paper_by_doi(self):
        cache = MetadataCache()
        paper = PaperMetadata(
            paper_id="test123",
            title="Test Paper",
            doi="10.1038/nature12373",
        )
        cache.set_paper("test123", paper)
        # Should also be cached by DOI
        retrieved = cache.get_paper("doi:10.1038/nature12373")
        assert retrieved is not None

    def test_get_paper_bypass(self):
        cache = MetadataCache()
        paper = PaperMetadata(paper_id="test123", title="Test Paper")
        cache.set_paper("test123", paper)
        assert cache.get_paper("test123", bypass=True) is None

    def test_invalidate(self):
        cache = MetadataCache()
        paper = PaperMetadata(paper_id="test123", title="Test Paper")
        cache.set_paper("test123", paper)
        assert cache.invalidate("test123") is True
        assert cache.get_paper("test123") is None

    def test_clear(self):
        cache = MetadataCache()
        paper = PaperMetadata(paper_id="test123", title="Test Paper")
        cache.set_paper("test123", paper)
        cache.clear()
        assert cache.get_paper("test123") is None

    def test_get_stats(self):
        cache = MetadataCache()
        stats = cache.get_stats()
        assert "hits" in stats
        assert "misses" in stats


class TestMetadataCacheGlobal:
    """Tests for global metadata cache functions."""

    def setup_method(self):
        reset_metadata_cache()

    def teardown_method(self):
        reset_metadata_cache()

    def test_get_metadata_cache(self):
        cache = get_metadata_cache()
        assert cache is not None

    def test_singleton(self):
        cache1 = get_metadata_cache()
        cache2 = get_metadata_cache()
        assert cache1 is cache2

    def test_reset(self):
        cache1 = get_metadata_cache()
        reset_metadata_cache()
        cache2 = get_metadata_cache()
        assert cache1 is not cache2


class TestPaperCache:
    """Tests for PaperCache (PDF caching)."""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create temporary cache directory."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def cache(self, temp_cache_dir):
        """Create cache with temp directory."""
        from agentic_kg.config import CacheConfig

        config = CacheConfig(
            cache_dir=str(temp_cache_dir),
            max_size_bytes=10 * 1024 * 1024,  # 10 MB
        )
        return PaperCache(config)

    def test_store_and_retrieve(self, cache):
        content = b"PDF content here"
        path = cache.store_pdf("10.1038/nature12373", content, SourceType.ARXIV)

        assert path.exists()
        assert cache.has_pdf("10.1038/nature12373")

        retrieved = cache.get_pdf("10.1038/nature12373")
        assert retrieved == content

    def test_get_pdf_path(self, cache):
        content = b"PDF content here"
        cache.store_pdf("test123", content, SourceType.ARXIV)

        path = cache.get_pdf_path("test123")
        assert path is not None
        assert path.exists()

    def test_has_pdf_false(self, cache):
        assert cache.has_pdf("nonexistent") is False

    def test_get_pdf_missing(self, cache):
        assert cache.get_pdf("nonexistent") is None

    def test_delete(self, cache):
        content = b"PDF content here"
        cache.store_pdf("test123", content, SourceType.ARXIV)
        assert cache.has_pdf("test123")

        result = cache.delete("test123")
        assert result is True
        assert cache.has_pdf("test123") is False

    def test_delete_missing(self, cache):
        result = cache.delete("nonexistent")
        assert result is False

    def test_content_addressable(self, cache):
        """Same content should be stored once."""
        content = b"Same PDF content"
        path1 = cache.store_pdf("paper1", content, SourceType.ARXIV)
        path2 = cache.store_pdf("paper2", content, SourceType.OPENALEX)

        # Both should point to same file (same hash)
        assert path1 == path2

        # But both identifiers should work
        assert cache.has_pdf("paper1")
        assert cache.has_pdf("paper2")

    def test_get_metadata(self, cache):
        content = b"PDF content here"
        cache.store_pdf("test123", content, SourceType.ARXIV)

        metadata = cache.get_metadata("test123")
        assert metadata is not None
        assert metadata["identifier"] == "test123"
        assert metadata["source"] == "arxiv"
        assert metadata["file_size"] == len(content)

    def test_clear(self, cache):
        cache.store_pdf("test1", b"content1", SourceType.ARXIV)
        cache.store_pdf("test2", b"content2", SourceType.ARXIV)
        cache.clear()

        assert cache.has_pdf("test1") is False
        assert cache.has_pdf("test2") is False

    def test_get_stats(self, cache):
        cache.store_pdf("test1", b"content1", SourceType.ARXIV)
        cache.get_pdf("test1")  # Hit
        cache.get_pdf("missing")  # Miss

        stats = cache.get_stats()
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1
        assert stats["item_count"] == 1

    def test_deduplication_on_delete(self, cache):
        """Deleting one reference shouldn't delete shared content."""
        content = b"Shared content"
        cache.store_pdf("paper1", content, SourceType.ARXIV)
        cache.store_pdf("paper2", content, SourceType.OPENALEX)

        cache.delete("paper1")
        assert cache.has_pdf("paper1") is False
        assert cache.has_pdf("paper2") is True  # Content still exists


class TestPaperCacheGlobal:
    """Tests for global paper cache functions."""

    def setup_method(self):
        reset_paper_cache()

    def teardown_method(self):
        reset_paper_cache()

    def test_get_paper_cache(self):
        with patch("agentic_kg.data_acquisition.cache.get_config") as mock_config:
            mock_config.return_value.data_acquisition.cache.cache_dir = tempfile.mkdtemp()
            mock_config.return_value.data_acquisition.cache.max_size_mb = 10

            cache = get_paper_cache()
            assert cache is not None

    def test_singleton(self):
        with patch("agentic_kg.data_acquisition.cache.get_config") as mock_config:
            temp_dir = tempfile.mkdtemp()
            mock_config.return_value.data_acquisition.cache.cache_dir = temp_dir
            mock_config.return_value.data_acquisition.cache.max_size_mb = 10

            cache1 = get_paper_cache()
            cache2 = get_paper_cache()
            assert cache1 is cache2

            shutil.rmtree(temp_dir)
