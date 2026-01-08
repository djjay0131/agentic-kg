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

- [ ] Create `agentic_kg/data_acquisition/arxiv.py`
- [ ] Implement `ArxivClient` class
- [ ] Implement arXiv ID parsing (old-style: `cs.AI/0501001`, new-style: `2301.12345`)
- [ ] Implement `get_metadata(arxiv_id)` using arXiv API
- [ ] Implement `download_pdf(arxiv_id)` from arXiv CDN
- [ ] Implement `get_pdf_url(arxiv_id)` for direct URL generation
- [ ] Add rate limiting (3 requests/second)
- [ ] Add retry logic for failed downloads
- [ ] Handle version specifiers (e.g., `2301.12345v2`)

**Acceptance Criteria:**
- Can parse all arXiv ID formats
- PDF download succeeds for valid papers
- Metadata extraction returns title, authors, abstract
- Rate limits prevent API throttling

---

### Task 5: OpenAlex Integration
**Estimate:** 1 day

- [ ] Create `agentic_kg/data_acquisition/openalex.py`
- [ ] Implement `OpenAlexClient` class
- [ ] Implement `get_work(doi)` method for paper lookup
- [ ] Implement `search_works(query, filters)` method
- [ ] Implement `get_open_access_url(work)` to find PDF links
- [ ] Extract author and institution metadata
- [ ] Add polite pool email header for rate limits
- [ ] Add retry logic

**Acceptance Criteria:**
- Paper lookup by DOI works
- Open access PDF URLs extracted when available
- Author metadata includes affiliations
- Polite headers configured for higher rate limits

---

### Task 6: Paper Acquisition Layer (Unified Interface)
**Estimate:** 1.5 days

- [ ] Create `agentic_kg/data_acquisition/acquisition.py`
- [ ] Implement `PaperAcquisitionLayer` class
- [ ] Implement `get_paper_metadata(identifier)` with source resolution
- [ ] Implement `get_pdf(identifier)` returning PDF bytes
- [ ] Implement `get_pdf_path(identifier)` with caching
- [ ] Implement `is_available(identifier)` to check availability
- [ ] Implement `get_source_type(identifier)` to identify source
- [ ] Implement identifier type detection (DOI, arXiv, URL, S2 ID)
- [ ] Add source priority resolution (cache > arXiv > OpenAlex > paywall)
- [ ] Add provenance tracking for retrieved papers

**Acceptance Criteria:**
- Single interface works for all paper sources
- Identifier type auto-detected
- Best available source selected automatically
- Provenance tracked with each retrieval

---

### Task 7: PDF Caching
**Estimate:** 1 day

- [ ] Create `agentic_kg/data_acquisition/cache.py`
- [ ] Implement `PaperCache` class with disk-based storage
- [ ] Implement content-addressable storage (SHA-256 hash)
- [ ] Implement `store_pdf(identifier, content)` method
- [ ] Implement `get_pdf(identifier)` method
- [ ] Implement `has_pdf(identifier)` method
- [ ] Add SQLite metadata database for cache tracking
- [ ] Track source, download date, file size, content hash
- [ ] Implement LRU eviction when cache exceeds size limit
- [ ] Add cache statistics (hits, misses, size)

**Acceptance Criteria:**
- PDFs cached and retrievable by identifier
- Duplicate content detected via hash
- Cache respects size limits
- Statistics available for monitoring

---

### Task 8: Metadata Caching
**Estimate:** 0.5 days

- [ ] Add TTL-based metadata caching to clients
- [ ] Implement in-memory cache with cachetools
- [ ] Configure TTL (default: 7 days for metadata)
- [ ] Add cache invalidation on explicit refresh
- [ ] Add cache bypass option for fresh data

**Acceptance Criteria:**
- Repeated metadata queries use cache
- Cache expires after TTL
- Can force refresh when needed

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

- [ ] Create `agentic_kg/data_acquisition/ratelimit.py`
- [ ] Implement token bucket rate limiter
- [ ] Implement per-client rate limit configuration
- [ ] Add rate limit state persistence (optional)
- [ ] Add rate limit metrics/logging

**Acceptance Criteria:**
- Rate limiters prevent API throttling
- Each client has independent limits
- Rate limit events logged for debugging

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

- [ ] Create `agentic_kg/data_acquisition/kg_sync.py`
- [ ] Implement `sync_paper_to_kg(metadata, repository)` function
- [ ] Map `PaperMetadata` to Knowledge Graph `Paper` entity
- [ ] Map `AuthorRef` to Knowledge Graph `Author` entity
- [ ] Create AUTHORED_BY relations for paper authors
- [ ] Handle duplicate detection via DOI
- [ ] Update existing papers with new metadata

**Acceptance Criteria:**
- Acquired papers automatically synced to Neo4j
- Authors created and linked correctly
- Duplicates detected and handled
- Existing papers updated (not duplicated)

---

### Task 13: CLI Tools
**Estimate:** 0.5 days

- [ ] Create `scripts/fetch_paper.py` CLI tool
- [ ] Accept DOI, arXiv ID, or URL as input
- [ ] Display paper metadata
- [ ] Download PDF to specified location
- [ ] Show source used for retrieval

**Acceptance Criteria:**
- CLI works for all identifier types
- Output is human-readable
- PDF downloads to correct location

---

### Task 14: Documentation
**Estimate:** 0.5 days

- [ ] Create `agentic_kg/data_acquisition/README.md`
- [ ] Document all public APIs with examples
- [ ] Document rate limiting behavior
- [ ] Document cache configuration
- [ ] Document paywall integration setup
- [ ] Add API docstrings to all public methods
- [ ] Update memory-bank/techContext.md with data acquisition details

**Acceptance Criteria:**
- New developers can understand and use the module
- All configuration options documented
- Examples for common use cases

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
