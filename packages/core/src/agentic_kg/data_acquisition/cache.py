"""
PDF and metadata caching for paper acquisition.

Provides disk-based caching with:
- Content-addressable storage (SHA-256 hash)
- SQLite metadata database
- LRU eviction when cache exceeds size limit
- Cache statistics for monitoring
"""

import hashlib
import logging
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from agentic_kg.config import CacheConfig, get_config
from agentic_kg.data_acquisition.models import SourceType

logger = logging.getLogger(__name__)


class PaperCache:
    """
    Disk-based cache for PDF files with metadata tracking.

    Uses content-addressable storage where files are stored by their
    SHA-256 hash, enabling deduplication of identical content.

    Features:
    - SQLite metadata database for fast lookups
    - LRU eviction when cache exceeds size limit
    - Statistics tracking (hits, misses, size)
    - Thread-safe operations

    Example:
        cache = PaperCache()

        # Store a PDF
        path = cache.store_pdf("10.1038/nature12373", pdf_content, SourceType.OPENALEX)

        # Retrieve
        if cache.has_pdf("10.1038/nature12373"):
            content = cache.get_pdf("10.1038/nature12373")

        # Statistics
        stats = cache.get_stats()
        print(f"Cache hits: {stats['hits']}, misses: {stats['misses']}")
    """

    def __init__(self, config: Optional[CacheConfig] = None):
        """
        Initialize the paper cache.

        Args:
            config: Cache configuration. Uses global config if not provided.
        """
        self._config = config or get_config().data_acquisition.cache
        self._cache_dir = Path(self._config.cache_dir)
        self._pdf_dir = self._cache_dir / "pdfs"
        self._db_path = self._cache_dir / "cache.db"

        # Thread-local storage for database connections
        self._local = threading.local()

        # Statistics
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

        # Initialize
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._pdf_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_db(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        """Initialize the SQLite database schema."""
        conn = self._get_db()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pdf_cache (
                identifier TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                source TEXT NOT NULL,
                downloaded_at TEXT NOT NULL,
                last_accessed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_content_hash ON pdf_cache(content_hash);
            CREATE INDEX IF NOT EXISTS idx_last_accessed ON pdf_cache(last_accessed_at);

            CREATE TABLE IF NOT EXISTS cache_stats (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );

            INSERT OR IGNORE INTO cache_stats (key, value) VALUES ('total_size', 0);
            INSERT OR IGNORE INTO cache_stats (key, value) VALUES ('hits', 0);
            INSERT OR IGNORE INTO cache_stats (key, value) VALUES ('misses', 0);
        """
        )
        conn.commit()

    def _compute_hash(self, content: bytes) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content).hexdigest()

    def _get_hash_path(self, content_hash: str) -> Path:
        """Get file path for a content hash (2-level directory structure)."""
        return self._pdf_dir / content_hash[:2] / content_hash[2:4] / f"{content_hash}.pdf"

    def store_pdf(
        self,
        identifier: str,
        content: bytes,
        source: SourceType,
    ) -> Path:
        """
        Store a PDF in the cache.

        Args:
            identifier: Paper identifier
            content: PDF content as bytes
            source: Source from which PDF was downloaded

        Returns:
            Path to stored file
        """
        content_hash = self._compute_hash(content)
        file_path = self._get_hash_path(content_hash)
        file_size = len(content)

        # Check if content already exists (deduplication)
        if not file_path.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(content)

            # Update total size
            self._update_total_size(file_size)

        # Update or insert metadata
        conn = self._get_db()
        now = datetime.utcnow().isoformat()

        # Check if this identifier already has a different file
        cursor = conn.execute(
            "SELECT content_hash, file_size FROM pdf_cache WHERE identifier = ?",
            (identifier,),
        )
        existing = cursor.fetchone()
        if existing and existing["content_hash"] != content_hash:
            # Different file - update size delta
            self._update_total_size(-existing["file_size"])

        conn.execute(
            """
            INSERT OR REPLACE INTO pdf_cache
            (identifier, content_hash, file_path, file_size, source, downloaded_at, last_accessed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identifier,
                content_hash,
                str(file_path),
                file_size,
                source.value,
                now,
                now,
            ),
        )
        conn.commit()

        # Check if eviction needed
        self._maybe_evict()

        return file_path

    def get_pdf(self, identifier: str) -> Optional[bytes]:
        """
        Get PDF content from cache.

        Args:
            identifier: Paper identifier

        Returns:
            PDF content as bytes, or None if not cached
        """
        conn = self._get_db()
        cursor = conn.execute(
            "SELECT file_path FROM pdf_cache WHERE identifier = ?",
            (identifier,),
        )
        row = cursor.fetchone()

        if row is None:
            self._record_miss()
            return None

        file_path = Path(row["file_path"])
        if not file_path.exists():
            # File was deleted - remove from database
            conn.execute("DELETE FROM pdf_cache WHERE identifier = ?", (identifier,))
            conn.commit()
            self._record_miss()
            return None

        # Update last accessed time
        conn.execute(
            "UPDATE pdf_cache SET last_accessed_at = ? WHERE identifier = ?",
            (datetime.utcnow().isoformat(), identifier),
        )
        conn.commit()

        self._record_hit()
        return file_path.read_bytes()

    def get_pdf_path(self, identifier: str) -> Optional[Path]:
        """
        Get path to cached PDF file.

        Args:
            identifier: Paper identifier

        Returns:
            Path to PDF file, or None if not cached
        """
        conn = self._get_db()
        cursor = conn.execute(
            "SELECT file_path FROM pdf_cache WHERE identifier = ?",
            (identifier,),
        )
        row = cursor.fetchone()

        if row is None:
            self._record_miss()
            return None

        file_path = Path(row["file_path"])
        if not file_path.exists():
            conn.execute("DELETE FROM pdf_cache WHERE identifier = ?", (identifier,))
            conn.commit()
            self._record_miss()
            return None

        # Update last accessed time
        conn.execute(
            "UPDATE pdf_cache SET last_accessed_at = ? WHERE identifier = ?",
            (datetime.utcnow().isoformat(), identifier),
        )
        conn.commit()

        self._record_hit()
        return file_path

    def has_pdf(self, identifier: str) -> bool:
        """
        Check if PDF is cached.

        Args:
            identifier: Paper identifier

        Returns:
            True if PDF is cached and file exists
        """
        conn = self._get_db()
        cursor = conn.execute(
            "SELECT file_path FROM pdf_cache WHERE identifier = ?",
            (identifier,),
        )
        row = cursor.fetchone()

        if row is None:
            return False

        return Path(row["file_path"]).exists()

    def get_metadata(self, identifier: str) -> Optional[dict]:
        """
        Get cache metadata for an identifier.

        Args:
            identifier: Paper identifier

        Returns:
            Metadata dict or None if not cached
        """
        conn = self._get_db()
        cursor = conn.execute(
            """
            SELECT identifier, content_hash, file_path, file_size,
                   source, downloaded_at, last_accessed_at
            FROM pdf_cache WHERE identifier = ?
            """,
            (identifier,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def delete(self, identifier: str) -> bool:
        """
        Delete a PDF from cache.

        Args:
            identifier: Paper identifier

        Returns:
            True if deleted, False if not found
        """
        conn = self._get_db()
        cursor = conn.execute(
            "SELECT content_hash, file_path, file_size FROM pdf_cache WHERE identifier = ?",
            (identifier,),
        )
        row = cursor.fetchone()

        if row is None:
            return False

        # Check if other identifiers use the same file
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM pdf_cache WHERE content_hash = ?",
            (row["content_hash"],),
        )
        count = cursor.fetchone()["cnt"]

        # Delete from database
        conn.execute("DELETE FROM pdf_cache WHERE identifier = ?", (identifier,))
        conn.commit()

        # Only delete file if no other identifiers reference it
        if count <= 1:
            file_path = Path(row["file_path"])
            if file_path.exists():
                file_path.unlink()
            self._update_total_size(-row["file_size"])

        return True

    def clear(self) -> None:
        """Clear all cached PDFs."""
        conn = self._get_db()
        conn.execute("DELETE FROM pdf_cache")
        conn.execute("UPDATE cache_stats SET value = 0 WHERE key = 'total_size'")
        conn.commit()

        # Remove all PDF files
        if self._pdf_dir.exists():
            shutil.rmtree(self._pdf_dir)
            self._pdf_dir.mkdir(parents=True, exist_ok=True)

    def _update_total_size(self, delta: int) -> None:
        """Update total cache size."""
        conn = self._get_db()
        conn.execute(
            "UPDATE cache_stats SET value = MAX(0, value + ?) WHERE key = 'total_size'",
            (delta,),
        )
        conn.commit()

    def _get_total_size(self) -> int:
        """Get total cache size in bytes."""
        conn = self._get_db()
        cursor = conn.execute(
            "SELECT value FROM cache_stats WHERE key = 'total_size'"
        )
        row = cursor.fetchone()
        return row["value"] if row else 0

    def _maybe_evict(self) -> None:
        """Evict oldest items if cache exceeds size limit."""
        max_size = self._config.max_size_bytes
        current_size = self._get_total_size()

        if current_size <= max_size:
            return

        conn = self._get_db()
        target_size = int(max_size * 0.8)  # Evict to 80% of max

        # Get items ordered by last access time (LRU)
        cursor = conn.execute(
            """
            SELECT identifier, content_hash, file_path, file_size
            FROM pdf_cache
            ORDER BY last_accessed_at ASC
            """
        )

        evicted_size = 0
        to_delete = []

        for row in cursor:
            if current_size - evicted_size <= target_size:
                break

            to_delete.append(row["identifier"])
            evicted_size += row["file_size"]

        # Delete evicted items
        for identifier in to_delete:
            self.delete(identifier)
            logger.debug(f"Evicted from cache: {identifier}")

        if to_delete:
            logger.info(f"Evicted {len(to_delete)} items from cache ({evicted_size} bytes)")

    def _record_hit(self) -> None:
        """Record a cache hit."""
        with self._lock:
            self._hits += 1
        conn = self._get_db()
        conn.execute(
            "UPDATE cache_stats SET value = value + 1 WHERE key = 'hits'"
        )
        conn.commit()

    def _record_miss(self) -> None:
        """Record a cache miss."""
        with self._lock:
            self._misses += 1
        conn = self._get_db()
        conn.execute(
            "UPDATE cache_stats SET value = value + 1 WHERE key = 'misses'"
        )
        conn.commit()

    def get_stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dict with hits, misses, size, item_count
        """
        conn = self._get_db()

        # Get persistent stats
        cursor = conn.execute("SELECT key, value FROM cache_stats")
        stats = {row["key"]: row["value"] for row in cursor}

        # Get item count
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM pdf_cache")
        stats["item_count"] = cursor.fetchone()["cnt"]

        # Calculate hit rate
        total_requests = stats.get("hits", 0) + stats.get("misses", 0)
        stats["hit_rate"] = (
            stats.get("hits", 0) / total_requests if total_requests > 0 else 0.0
        )

        # Format size
        total_size = stats.get("total_size", 0)
        stats["total_size_mb"] = total_size / (1024 * 1024)

        return stats

    def close(self) -> None:
        """Close database connections."""
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn


# Singleton cache
_cache: Optional[PaperCache] = None


def get_paper_cache() -> PaperCache:
    """Get the paper cache singleton."""
    global _cache
    if _cache is None:
        _cache = PaperCache()
    return _cache


def reset_paper_cache() -> None:
    """Reset the paper cache singleton."""
    global _cache
    if _cache is not None:
        _cache.close()
        _cache = None
