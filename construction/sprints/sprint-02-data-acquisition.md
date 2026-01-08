# Sprint 02: Data Acquisition Layer

**Sprint Goal:** Implement unified paper acquisition from multiple sources (Semantic Scholar, arXiv, OpenAlex, paywall)

**Start Date:** 2026-01-08
**Status:** In Progress

**Prerequisites:** Sprint 01 complete (Knowledge Graph Foundation)

**Related Components:** C5 (Semantic Scholar Client), C6 (Paper Acquisition Layer)

**Requirements Document:** [sprint-02-requirements.md](../requirements/sprint-02-requirements.md)

---

## Tasks

### Task 1: Project Structure & Configuration
**Estimate:** 0.5 days

- [x] Create `agentic_kg/data_acquisition/` package directory
- [x] Create `agentic_kg/data_acquisition/__init__.py` with public exports
- [x] Add Semantic Scholar API key to configuration
- [x] Add cache directory configuration
- [x] Add rate limit settings to config
- [x] Update `.env.example` with new environment variables

**Acceptance Criteria:**
- Clean package structure following existing conventions
- Configuration supports all required API keys and settings
- Environment variables documented in `.env.example`

**Implementation Notes:**
- Added `SemanticScholarConfig`, `ArxivConfig`, `OpenAlexConfig`, `CacheConfig` to config.py
- All wrapped in `DataAcquisitionConfig` accessible via `config.data_acquisition`

---

### Task 2: Data Models
**Estimate:** 1 day

- [x] Create `agentic_kg/data_acquisition/models.py`
- [x] Implement `PaperMetadata` model with all fields
- [x] Implement `AuthorRef` model for author references
- [x] Implement `Citation` model for citation data
- [x] Implement `SourceType` enum (ARXIV, OPENALEX, SEMANTIC_SCHOLAR, PAYWALL, CACHE)
- [x] Implement `DownloadStatus` enum for async downloads
- [x] Add JSON serialization for caching
- [x] Add model validators for DOI and arXiv ID formats

**Acceptance Criteria:**
- All models defined with proper validation
- DOI regex validation (10.XXXX/...)
- arXiv ID validation (both old and new formats)
- Models compatible with Knowledge Graph Paper entity

**Implementation Notes:**
- Added `DownloadResult` model for tracking download status
- DOI/arXiv validators auto-clean common prefixes (https://doi.org/, arXiv:)
- 45 unit tests in test_models.py

---

### Task 3: Semantic Scholar Client
**Estimate:** 2 days

- [x] Create `agentic_kg/data_acquisition/semantic_scholar.py`
- [x] Implement `SemanticScholarClient` class with httpx
- [x] Implement `search_papers(query, limit, fields)` method
- [x] Implement `get_paper(paper_id)` method for single paper lookup
- [x] Implement `get_paper_by_doi(doi)` method
- [x] Implement `get_paper_by_arxiv_id(arxiv_id)` method
- [x] Implement `get_references(paper_id, limit)` method
- [x] Implement `get_citations(paper_id, limit)` method
- [x] Implement `get_embedding(paper_id)` for SPECTER2 embeddings
- [x] Add pagination support for large result sets
- [x] Add rate limiting (1 req/sec unauthenticated, 10 req/sec authenticated)
- [x] Add retry logic with exponential backoff
- [x] Add API key authentication support

**Acceptance Criteria:**
- All search and retrieval methods functional
- Rate limits respected (no 429 errors in normal use)
- Pagination works for large citation lists
- API key enables higher rate limits when configured

**Implementation Notes:**
- SemanticScholarClient with lazy-loaded httpx client
- Token bucket rate limiting based on authenticated status
- Exponential backoff with configurable retry count and delay
- Batch methods for bulk paper retrieval and embedding fetching
- Singleton pattern with `get_semantic_scholar_client()` factory

---

### Task 4: arXiv Integration
**Estimate:** 1.5 days

- [x] Create `agentic_kg/data_acquisition/arxiv.py`
- [x] Implement `ArxivClient` class
- [x] Implement arXiv ID parsing (old-style: `cs.AI/0501001`, new-style: `2301.12345`)
- [x] Implement `get_metadata(arxiv_id)` using arXiv API
- [x] Implement `download_pdf(arxiv_id)` from arXiv CDN
- [x] Implement `get_pdf_url(arxiv_id)` for direct URL generation
- [x] Add rate limiting (3 requests/second)
- [x] Add retry logic for failed downloads
- [x] Handle version specifiers (e.g., `2301.12345v2`)

**Acceptance Criteria:**
- Can parse all arXiv ID formats
- PDF download succeeds for valid papers
- Metadata extraction returns title, authors, abstract
- Rate limits prevent API throttling

**Implementation Notes:**
- ArxivClient parses Atom XML responses from arXiv API
- `parse_arxiv_id()` and `normalize_arxiv_id()` handle both old and new ID formats
- `search()` method with full arXiv query syntax support
- `download_pdf()` streams to file, `get_pdf_bytes()` returns content
- Version specifiers preserved through parse/normalize cycle
- Singleton pattern with `get_arxiv_client()` factory

---

### Task 5: OpenAlex Integration
**Estimate:** 1 day

- [x] Create `agentic_kg/data_acquisition/openalex.py`
- [x] Implement `OpenAlexClient` class
- [x] Implement `get_work(doi)` method for paper lookup
- [x] Implement `search_works(query, filters)` method
- [x] Implement `get_open_access_url(work)` to find PDF links
- [x] Extract author and institution metadata
- [x] Add polite pool email header for rate limits
- [x] Add retry logic

**Acceptance Criteria:**
- Paper lookup by DOI works
- Open access PDF URLs extracted when available
- Author metadata includes affiliations
- Polite headers configured for higher rate limits

**Implementation Notes:**
- OpenAlexClient with polite pool email for higher rate limits
- `get_work_by_doi()` and `get_work()` for paper lookup
- `search_works()` with filter and sort support
- `get_works_by_author()` for author-centric queries
- `_reconstruct_abstract()` handles OpenAlex inverted index format
- `_extract_best_oa_url()` finds PDFs from multiple locations
- `get_author()` and `get_institution()` for entity details
- Singleton pattern with `get_openalex_client()` factory

---

### Task 6: Paper Acquisition Layer (Unified Interface)
**Estimate:** 1.5 days

- [x] Create `agentic_kg/data_acquisition/acquisition.py`
- [x] Implement `PaperAcquisitionLayer` class
- [x] Implement `get_paper_metadata(identifier)` with source resolution
- [x] Implement `get_pdf(identifier)` returning PDF bytes
- [x] Implement `get_pdf_path(identifier)` with caching
- [x] Implement `is_available(identifier)` to check availability
- [x] Implement `get_source_type(identifier)` to identify source
- [x] Implement identifier type detection (DOI, arXiv, URL, S2 ID)
- [x] Add source priority resolution (cache > arXiv > OpenAlex > paywall)
- [x] Add provenance tracking for retrieved papers

**Acceptance Criteria:**
- Single interface works for all paper sources
- Identifier type auto-detected
- Best available source selected automatically
- Provenance tracked with each retrieval

**Implementation Notes:**
- `PaperAcquisitionLayer` unifies all three API clients
- `detect_identifier_type()` auto-detects DOI, arXiv, S2, OpenAlex, and URL formats
- `get_paper_metadata()` tries sources in priority order based on identifier type
- `get_pdf()` returns `DownloadResult` with status, path, and provenance
- `search()` queries all sources and deduplicates by DOI
- Source priority: arXiv > Semantic Scholar > OpenAlex for metadata
- PDF priority: arXiv > open access URL from metadata
- Singleton pattern with `get_acquisition_layer()` factory

---

### Task 7: PDF Caching
**Estimate:** 1 day

- [x] Create `agentic_kg/data_acquisition/cache.py`
- [x] Implement `PaperCache` class with disk-based storage
- [x] Implement content-addressable storage (SHA-256 hash)
- [x] Implement `store_pdf(identifier, content)` method
- [x] Implement `get_pdf(identifier)` method
- [x] Implement `has_pdf(identifier)` method
- [x] Add SQLite metadata database for cache tracking
- [x] Track source, download date, file size, content hash
- [x] Implement LRU eviction when cache exceeds size limit
- [x] Add cache statistics (hits, misses, size)

**Acceptance Criteria:**
- PDFs cached and retrievable by identifier
- Duplicate content detected via hash
- Cache respects size limits
- Statistics available for monitoring

**Implementation Notes:**
- `PaperCache` with SQLite metadata database for fast lookups
- Content-addressable storage using SHA-256 hash (2-level directory)
- Deduplication: identical content stored once even with different identifiers
- LRU eviction to 80% of max size when limit exceeded
- Thread-safe operations with thread-local database connections
- `get_stats()` returns hits, misses, hit_rate, total_size, item_count
- Singleton pattern with `get_paper_cache()` factory

---

### Task 8: Metadata Caching
**Estimate:** 0.5 days

- [x] Add TTL-based metadata caching to clients
- [x] Implement in-memory cache with cachetools
- [x] Configure TTL (default: 7 days for metadata)
- [x] Add cache invalidation on explicit refresh
- [x] Add cache bypass option for fresh data

**Acceptance Criteria:**
- Repeated metadata queries use cache
- Cache expires after TTL
- Can force refresh when needed

**Implementation Notes:**
- `TTLCache[T]` generic class with configurable TTL and max size
- `MetadataCache` specialized wrapper for `PaperMetadata`
- Auto-caching by alternate identifiers (DOI, arXiv, S2 ID)
- LRU eviction when max size exceeded
- `bypass=True` parameter to skip cache and fetch fresh
- `cleanup_expired()` for manual cache maintenance
- Thread-safe with `RLock` for concurrent access
- Singleton pattern with `get_metadata_cache()` factory

---

### Task 9: Paywall Integration (Optional)
**Estimate:** 1.5 days

- [ ] Create `agentic_kg/data_acquisition/paywall.py`
- [ ] Implement `PaywallClient` for research-ai-paper API
- [ ] Implement `submit_download(identifier)` to queue downloads
- [ ] Implement `get_download_status(task_id)` to check progress
- [ ] Implement `retrieve_pdf(task_id)` when complete
- [ ] Add async polling with configurable timeout
- [ ] Handle authentication errors gracefully
- [ ] Document microservice setup requirements

**Acceptance Criteria:**
- Can submit download requests to microservice
- Status polling works for queued downloads
- PDF retrieval works for completed downloads
- Clear error messages when microservice unavailable

---

### Task 10: Rate Limiting Infrastructure
**Estimate:** 0.5 days

- [x] Create `agentic_kg/data_acquisition/ratelimit.py`
- [x] Implement token bucket rate limiter
- [x] Implement per-client rate limit configuration
- [x] Add rate limit state persistence (optional)
- [x] Add rate limit metrics/logging

**Acceptance Criteria:**
- Rate limiters prevent API throttling
- Each client has independent limits
- Rate limit events logged for debugging

**Implementation Notes:**
- `TokenBucketRateLimiter` with configurable rate and burst size
- `CompositeRateLimiter` for multiple concurrent limits
- `RateLimiterRegistry` for centralized management
- `acquire()` blocks until token available, `try_acquire()` non-blocking
- Per-limiter metrics: total_acquired, total_waited_ms, total_throttled
- Default limiters: S2 (1/10 req/s), arXiv (3 req/s), OpenAlex (10 req/s)
- Thread-safe with proper locking
- Global registry via `get_rate_limiter_registry()`

---

### Task 11: Testing
**Estimate:** 2 days

- [ ] Create `tests/data_acquisition/` test directory
- [ ] Create test fixtures with mock API responses
- [ ] Write unit tests for all data models
- [ ] Write unit tests for Semantic Scholar client (mocked)
- [ ] Write unit tests for arXiv client (mocked)
- [ ] Write unit tests for OpenAlex client (mocked)
- [ ] Write unit tests for PDF cache
- [ ] Write integration tests with real APIs (marked slow)
- [ ] Add VCR/cassette recording for API tests
- [ ] Set up pytest markers for integration tests

**Acceptance Criteria:**
- >80% test coverage for data_acquisition module
- All tests pass in CI (integration tests skipped without API keys)
- Mock responses cover common scenarios
- VCR cassettes recorded for reproducibility

---

### Task 12: Knowledge Graph Integration
**Estimate:** 1 day

- [x] Create `agentic_kg/data_acquisition/kg_sync.py`
- [x] Implement `sync_paper_to_kg(metadata, repository)` function
- [x] Map `PaperMetadata` to Knowledge Graph `Paper` entity
- [x] Map `AuthorRef` to Knowledge Graph `Author` entity
- [x] Create AUTHORED_BY relations for paper authors
- [x] Handle duplicate detection via DOI
- [x] Update existing papers with new metadata

**Acceptance Criteria:**
- Acquired papers automatically synced to Neo4j
- Authors created and linked correctly
- Duplicates detected and handled
- Existing papers updated (not duplicated)

**Implementation Notes:**
- `sync_paper_to_kg()` syncs single paper with optional author sync
- `sync_papers_batch()` for bulk syncing
- `paper_metadata_to_kg_paper()` converts acquisition model to KG model
- `author_ref_to_kg_author()` with stable ID from S2 ID or ORCID
- `find_existing_author()` looks up by S2 ID or ORCID
- `KGSyncResult` tracks created/updated/skipped counts and errors
- DOI duplicate detection with update_existing option
- AUTHORED_BY relations with author position

---

### Task 13: CLI Tools
**Estimate:** 0.5 days

- [x] Create `scripts/fetch_paper.py` CLI tool
- [x] Accept DOI, arXiv ID, or URL as input
- [x] Display paper metadata
- [x] Download PDF to specified location
- [x] Show source used for retrieval

**Acceptance Criteria:**
- CLI works for all identifier types
- Output is human-readable
- PDF downloads to correct location

**Implementation Notes:**
- `fetch_paper.py` with argparse CLI
- Supports DOI, arXiv ID, URL, S2 ID identifiers
- `--search` flag for paper search
- `--download` flag to download PDF
- `--json` flag for JSON output
- `--embedding` flag to include SPECTER2 embedding
- `--sync` flag to sync to Knowledge Graph
- Human-readable output with word-wrapped abstract

---

### Task 14: Documentation
**Estimate:** 0.5 days

- [x] Create `agentic_kg/data_acquisition/README.md`
- [x] Document all public APIs with examples
- [x] Document rate limiting behavior
- [x] Document cache configuration
- [x] Document paywall integration setup
- [x] Add API docstrings to all public methods
- [ ] Update memory-bank/techContext.md with data acquisition details

**Acceptance Criteria:**
- New developers can understand and use the module
- All configuration options documented
- Examples for common use cases

**Implementation Notes:**
- Comprehensive README.md with code examples
- Quick start guide and API client documentation
- Configuration section with env vars and config object
- Rate limiting table and custom limiter usage
- Caching documentation for PDF and metadata
- KG integration and CLI tool usage
- Data models and error handling
- All modules have docstrings (techContext update deferred)

---

## Architecture Decisions

- **ADR-011**: Microservice Architecture for Paper Acquisition (use REST API to research-ai-paper)
- **ADR-010**: Neo4j for Graph Database (papers stored in same database as problems)

---

## Dependencies

### External Dependencies
- Semantic Scholar API (https://api.semanticscholar.org)
- arXiv API (https://export.arxiv.org/api)
- OpenAlex API (https://api.openalex.org)
- research-ai-paper microservice (optional, for paywall access)

### Python Packages (to add)
```toml
[project.dependencies]
httpx = ">=0.24.0"
aiofiles = ">=23.0.0"
cachetools = ">=5.0.0"
tenacity = ">=8.0.0"
```

### Internal Dependencies
- `agentic_kg.config` - Configuration module
- `agentic_kg.knowledge_graph.models` - Paper and Author models
- `agentic_kg.knowledge_graph.repository` - Neo4j repository

---

## File Structure

```
packages/core/src/agentic_kg/
├── data_acquisition/
│   ├── __init__.py              # Public exports
│   ├── models.py                # PaperMetadata, Citation, etc.
│   ├── semantic_scholar.py      # Semantic Scholar client
│   ├── arxiv.py                 # arXiv client
│   ├── openalex.py              # OpenAlex client
│   ├── paywall.py               # research-ai-paper integration
│   ├── acquisition.py           # Unified acquisition layer
│   ├── cache.py                 # PDF and metadata caching
│   ├── ratelimit.py             # Rate limiting utilities
│   ├── kg_sync.py               # Knowledge Graph integration
│   └── README.md                # Module documentation
├── scripts/
│   └── fetch_paper.py           # CLI tool
└── tests/
    └── data_acquisition/
        ├── __init__.py
        ├── conftest.py          # Test fixtures
        ├── test_models.py
        ├── test_semantic_scholar.py
        ├── test_arxiv.py
        ├── test_openalex.py
        ├── test_cache.py
        └── cassettes/           # VCR recorded responses
```

---

## Risks

| Risk | Mitigation | Status |
|------|------------|--------|
| Semantic Scholar API changes | Pin to specific API version, add version detection | Open |
| arXiv rate limiting | Conservative limits, exponential backoff | Open |
| Paywall microservice complexity | Make paywall integration optional | Open |
| Test isolation with real APIs | VCR cassettes, mock responses | Open |
| API key management | Use environment variables and Secret Manager | Open |

---

## Estimated Effort

| Category | Days |
|----------|------|
| Setup & Models | 1.5 |
| API Clients | 4.5 |
| Caching & Rate Limiting | 2.0 |
| Integration | 1.5 |
| Testing | 2.0 |
| Documentation | 0.5 |
| **Total** | **12 days** |

---

## Success Criteria

1. Can retrieve paper metadata from Semantic Scholar by DOI, arXiv ID, or search query
2. Can download PDFs from arXiv with proper rate limiting
3. Unified interface works for all supported sources
4. PDF caching prevents redundant downloads
5. Acquired papers sync to Knowledge Graph (Neo4j)
6. Test coverage >80% for data_acquisition module
7. Documentation complete for all public APIs

---

## Notes

- Paywall integration (Task 9) is optional and depends on research-ai-paper microservice availability
- Integration tests with real APIs should be run manually or in dedicated CI job with API keys
- Consider SPECTER2 embeddings for paper similarity instead of/in addition to OpenAI embeddings
- Design document: [system-architecture.md](../design/system-architecture.md) (Phase 1.5 section)
