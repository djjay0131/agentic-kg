# Sprint 02: Data Acquisition Layer

**Sprint Goal:** Implement the Data Acquisition Layer for ingesting papers from academic sources

**Start Date:** 2026-01-25
**End Date:** 2026-01-25
**Status:** Complete

**Prerequisites:** Sprint 01 complete (Knowledge Graph Foundation merged)

**Related Components:** C5 (API Clients), C6 (Rate Limiting), C7 (Caching), C8 (Paper Normalization)

**Requirements Document:** [data-acquisition-requirements.md](../requirements/data-acquisition-requirements.md)

---

## Tasks

### Task 1: Data Acquisition Module Structure ✅
- [x] Create `agentic_kg/data_acquisition/` package directory
- [x] Create `__init__.py` with public API exports
- [x] Create `config.py` for data acquisition settings
- [x] Add API key configurations (Semantic Scholar, OpenAlex email)
- [x] Add rate limit configurations per source
- [x] Add cache TTL configurations

**Acceptance Criteria:**
- ✅ Clean package structure following existing conventions
- ✅ Configuration works with environment variables
- ✅ Settings documented in `.env.example`

---

### Task 2: Base API Client Infrastructure ✅

- [x] Create `agentic_kg/data_acquisition/base.py`
- [x] Implement `BaseAPIClient` abstract class
- [x] Add httpx async client setup with timeout configuration
- [x] Implement request/response logging
- [x] Add common error handling patterns
- [x] Create `exceptions.py` with custom exceptions

**Acceptance Criteria:**
- ✅ All API clients inherit from BaseAPIClient
- ✅ Consistent error handling across clients
- ✅ Request/response logging at DEBUG level

---

### Task 3: Rate Limiting Infrastructure ✅

- [x] Create `agentic_kg/data_acquisition/rate_limiter.py`
- [x] Implement token bucket rate limiter
- [x] Support per-source rate limit configuration
- [x] Add async-compatible waiting/queueing
- [x] Implement backoff on 429 responses
- [x] Add rate limit metrics/logging

**Acceptance Criteria:**
- ✅ Rate limits enforced per source
- ✅ Requests queue when at capacity
- ✅ Automatic backoff on rate limit errors

---

### Task 4: Retry and Circuit Breaker ✅

- [x] Create `agentic_kg/data_acquisition/resilience.py`
- [x] Implement retry decorator with exponential backoff
- [x] Add jitter to prevent thundering herd
- [x] Implement circuit breaker pattern
- [x] Configure retry strategies per error type
- [x] Log all retry attempts

**Acceptance Criteria:**
- ✅ Retries occur on transient failures (429, 5xx, timeout)
- ✅ Circuit opens after consecutive failures
- ✅ Circuit half-opens after cooldown

---

### Task 5: Caching Layer ✅

- [x] Create `agentic_kg/data_acquisition/cache.py`
- [x] Implement in-memory cache with TTL (cachetools)
- [x] Add cache key generation from request parameters
- [x] Implement cache bypass option
- [x] Add cache statistics (hits/misses)
- [ ] Add optional Redis backend interface (deferred to future sprint)

**Acceptance Criteria:**
- ✅ Repeated requests return cached data
- ✅ TTL configurable per data type
- ✅ Cache stats available for monitoring

---

### Task 6: Semantic Scholar Client ✅

- [x] Create `agentic_kg/data_acquisition/semantic_scholar.py`
- [x] Implement `SemanticScholarClient` class
- [x] Add `get_paper(paper_id_or_doi)` method
- [x] Add `search_papers(query, limit, offset)` method
- [x] Add `get_author(author_id)` method
- [x] Add `get_paper_citations(paper_id)` method
- [x] Add `get_paper_references(paper_id)` method
- [x] Add `bulk_get_papers(paper_ids)` method
- [x] Handle API key authentication

**Acceptance Criteria:**
- ✅ Can retrieve paper by DOI or Semantic Scholar ID
- ✅ Can search papers with pagination
- ✅ Can retrieve author information
- ✅ Rate limits respected

---

### Task 7: arXiv Client ✅

- [x] Create `agentic_kg/data_acquisition/arxiv.py`
- [x] Implement `ArxivClient` class
- [x] Add `get_paper(arxiv_id)` method
- [x] Add `search_papers(query, max_results, start)` method
- [x] Implement Atom feed parsing with feedparser
- [x] Construct PDF URLs from arXiv IDs
- [x] Handle category filtering

**Acceptance Criteria:**
- ✅ Can retrieve paper by arXiv ID
- ✅ Can search papers with pagination
- ✅ PDF URLs correctly constructed
- ✅ Rate limits respected (3 second minimum)

---

### Task 8: OpenAlex Client ✅

- [x] Create `agentic_kg/data_acquisition/openalex.py`
- [x] Implement `OpenAlexClient` class
- [x] Add `get_work(openalex_id_or_doi)` method
- [x] Add `search_works(query, filter, per_page, page)` method
- [x] Add `get_author(author_id)` method
- [x] Implement abstract reconstruction from inverted index
- [x] Add polite pool User-Agent header

**Acceptance Criteria:**
- ✅ Can retrieve work by DOI or OpenAlex ID
- ✅ Abstracts correctly reconstructed
- ✅ Can search with filters
- ✅ Polite pool identification included

---

### Task 9: Paper Metadata Normalization ✅

- [x] Create `agentic_kg/data_acquisition/normalizer.py`
- [x] Implement `PaperNormalizer` class
- [x] Add `normalize_semantic_scholar(data)` method
- [x] Add `normalize_arxiv(data)` method
- [x] Add `normalize_openalex(data)` method
- [x] Map all sources to unified schema
- [x] Handle missing fields gracefully
- [x] Implement author normalization

**Acceptance Criteria:**
- ✅ All sources produce identical output schema
- ✅ Missing fields handled with defaults/None
- ✅ Authors normalized with external IDs

---

### Task 10: Multi-Source Aggregator ✅

- [x] Create `agentic_kg/data_acquisition/aggregator.py`
- [x] Implement `PaperAggregator` class
- [x] Add `get_paper(identifier)` - tries all sources
- [x] Add `search_papers(query, sources)` - aggregates results
- [x] Implement deduplication by DOI
- [x] Merge metadata from multiple sources
- [x] Track canonical source per paper

**Acceptance Criteria:**
- ✅ Single identifier lookup tries appropriate sources
- ✅ Search results deduplicated across sources
- ✅ Best metadata merged from multiple sources

---

### Task 11: Knowledge Graph Integration ✅

- [x] Create `agentic_kg/data_acquisition/importer.py`
- [x] Implement `PaperImporter` class
- [x] Add `import_paper(identifier)` method
- [x] Add `batch_import(identifiers)` method
- [x] Add `import_author_papers(author_id)` method
- [x] Check for existing papers before creating
- [x] Update metadata if newer data available
- [x] Create AUTHORED_BY relations

**Acceptance Criteria:**
- ✅ Papers created in Knowledge Graph
- ✅ Authors linked to papers
- ✅ Duplicates handled correctly
- ✅ Batch import reports progress

---

### Task 12: CLI/Script Interface ✅

- [x] Create `scripts/import_papers.py`
- [x] Add CLI for single paper import
- [x] Add CLI for batch import from file
- [x] Add CLI for author bibliography import
- [x] Add progress reporting
- [x] Add JSON output option

**Acceptance Criteria:**
- ✅ Can import paper from command line
- ✅ Can import from CSV/JSON file of identifiers
- ✅ Progress displayed for batch operations

---

### Task 13: Unit Tests ✅

- [x] Create `tests/data_acquisition/` directory
- [x] Write tests for rate limiter
- [x] Write tests for retry/circuit breaker logic
- [x] Write tests for cache operations
- [x] Write tests for normalizer (each source)
- [x] Write tests for aggregator deduplication
- [x] Mock API responses for client tests

**Acceptance Criteria:**
- ✅ Comprehensive test coverage for module
- ✅ All clients tested with mocked responses
- ✅ Edge cases covered (errors, missing data)

---

### Task 14: Integration Tests (Deferred)

- [ ] Write integration tests for real API calls
- [ ] Test Semantic Scholar paper retrieval
- [ ] Test arXiv paper retrieval
- [ ] Test OpenAlex paper retrieval
- [ ] Test full import pipeline to Neo4j
- [ ] Mark as slow/skip without network

**Note:** Integration tests deferred - requires test environment with API credentials and Neo4j.

**Acceptance Criteria:**
- Integration tests pass with live APIs
- Tests skip gracefully in CI without credentials
- Full pipeline tested end-to-end

---

## Architecture Decisions

- **ADR-012**: httpx for async HTTP (to be created)
- **ADR-003**: Problems as First-Class Entities (existing - papers support problems)
- **ADR-010**: Neo4j for Graph Database (existing - paper storage)

---

## Dependencies

- Sprint 01 complete (Knowledge Graph repository for paper storage)
- httpx >= 0.24.0 for async HTTP
- tenacity >= 8.0.0 for retry logic
- cachetools >= 5.0.0 for in-memory caching
- feedparser >= 6.0.0 for arXiv Atom parsing

---

## File Structure

```
agentic-kg/
├── packages/core/src/agentic_kg/
│   └── data_acquisition/
│       ├── __init__.py           # Public API exports
│       ├── config.py             # Data acquisition settings
│       ├── base.py               # BaseAPIClient abstract class
│       ├── exceptions.py         # Custom exceptions
│       ├── rate_limiter.py       # Token bucket rate limiter
│       ├── resilience.py         # Retry and circuit breaker
│       ├── cache.py              # Caching layer
│       ├── semantic_scholar.py   # Semantic Scholar client
│       ├── arxiv.py              # arXiv client
│       ├── openalex.py           # OpenAlex client
│       ├── normalizer.py         # Metadata normalization
│       ├── aggregator.py         # Multi-source aggregation
│       ├── importer.py           # Knowledge Graph integration
│       └── README.md             # Module documentation
├── packages/core/tests/
│   └── data_acquisition/
│       ├── __init__.py
│       ├── conftest.py           # Fixtures, mocked responses
│       ├── test_rate_limiter.py
│       ├── test_resilience.py
│       ├── test_cache.py
│       ├── test_semantic_scholar.py
│       ├── test_arxiv.py
│       ├── test_openalex.py
│       ├── test_normalizer.py
│       ├── test_aggregator.py
│       └── test_importer.py
├── scripts/
│   └── import_papers.py          # CLI for paper import
└── pyproject.toml                # Updated dependencies
```

---

## Risks

| Risk | Mitigation | Status |
|------|------------|--------|
| API rate limits exceeded | Token bucket + backoff | Planned |
| API unavailability | Circuit breaker + fallback sources | Planned |
| Data schema differences | Thorough normalizer testing | Open |
| OpenAlex abstract reconstruction | Validate against original | Open |
| Large batch memory usage | Streaming/chunked processing | Open |

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Test coverage | >80% |
| Single paper import latency | <2 seconds |
| Batch import throughput | >25 papers/minute |
| API error rate | <5% |
| Cache hit ratio (repeated) | >90% |

---

## Notes

- Design document: To be created if complex decisions arise
- Requirements document: [data-acquisition-requirements.md](../requirements/data-acquisition-requirements.md)
- Reference paper: [files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf](../../files/)
- Sprint 01 deliverables: Knowledge Graph Foundation (models, repository, search)
