# Data Acquisition Layer - Requirements Specification

**Version:** 1.0
**Date:** 2026-01-07
**Sprint:** 02
**Status:** Draft

**Related Documents:**
- [System Architecture](../design/system-architecture.md)
- [Phase 1 Knowledge Graph](../design/phase-1-knowledge-graph.md)
- [Sprint 01 - Knowledge Graph Foundation](../sprints/sprint-01-knowledge-graph.md)
- [ADR-011: Microservice Architecture for Paper Acquisition](../../memory-bank/architecturalDecisions.md)

---

## 1. Overview

This document specifies the requirements for the Data Acquisition Layer, which provides unified access to research papers from multiple sources including open access repositories and paywalled publishers.

### 1.1 Purpose

Enable acquisition of research papers from any source through a unified interface:
- Semantic Scholar API for paper metadata, citations, and SPECTER2 embeddings
- Open access retrieval from arXiv and OpenAlex
- Paywalled paper access via research-ai-paper microservice integration
- Consistent paper representation regardless of source

### 1.2 Scope

**In Scope:**
- Semantic Scholar API client for metadata and citations
- Paper acquisition from arXiv (open access)
- Paper acquisition from OpenAlex/PubMed Central
- Integration with research-ai-paper microservice for paywalled papers
- Unified interface for paper retrieval
- Caching to avoid redundant downloads
- Rate limiting and retry logic

**Out of Scope:**
- PDF text extraction (Phase 2 - Extraction Pipeline)
- LLM-based problem extraction (Phase 2)
- Paper content analysis (Phase 2)
- User interface for paper search (Phase 4)

---

## 2. Functional Requirements

### 2.1 Semantic Scholar Client

#### FR-2.1.1: Paper Search
**Priority:** High

The system shall support searching for papers via Semantic Scholar API.

| Operation | Description |
|-----------|-------------|
| Search by query | Full-text search with keyword string |
| Search by DOI | Look up paper by DOI identifier |
| Search by arXiv ID | Look up paper by arXiv identifier |
| Search by title | Find papers matching title string |

**Response Data:**
- Paper ID (Semantic Scholar corpus ID)
- Title, abstract, year
- Authors with affiliations
- Venue/journal
- DOI, arXiv ID, PubMed ID (if available)
- Citation count
- Open access PDF URL (if available)

#### FR-2.1.2: Citation Retrieval
**Priority:** High

The system shall retrieve citation information for papers.

| Operation | Description |
|-----------|-------------|
| Get references | Papers cited by the target paper |
| Get citations | Papers that cite the target paper |
| Get citation context | Snippet showing where citation appears |

**Requirements:**
- Support pagination for papers with many citations
- Return citation count even without fetching all citations
- Include relationship context where available

#### FR-2.1.3: SPECTER2 Embeddings
**Priority:** Medium

The system shall retrieve SPECTER2 embeddings from Semantic Scholar.

**Requirements:**
- Fetch 768-dimensional SPECTER2 embedding for papers
- Support batch retrieval for multiple papers
- Handle papers without embeddings gracefully

#### FR-2.1.4: Author Information
**Priority:** Low

The system shall retrieve author information.

| Operation | Description |
|-----------|-------------|
| Get author | Retrieve author by Semantic Scholar ID |
| Get author papers | List papers by an author |
| Get affiliations | Current and historical affiliations |

---

### 2.2 Paper Acquisition Layer

#### FR-2.2.1: Unified Paper Retrieval
**Priority:** High

The system shall provide a single interface for retrieving papers regardless of source.

```python
class PaperAcquisitionLayer:
    def get_pdf(identifier: str) -> bytes
    def get_pdf_path(identifier: str) -> Path
    def is_available(identifier: str) -> bool
    def get_source_type(identifier: str) -> SourceType
```

**Identifier Support:**
- DOI (e.g., `10.1234/example.2023.001`)
- arXiv ID (e.g., `2301.12345`, `cs.AI/0501001`)
- URL (e.g., `https://arxiv.org/abs/2301.12345`)
- Semantic Scholar ID

#### FR-2.2.2: arXiv Integration
**Priority:** High

The system shall download papers from arXiv.

**Requirements:**
- Parse arXiv IDs from various formats
- Download PDF from arXiv CDN
- Handle arXiv API rate limits (3 requests/second)
- Extract metadata from arXiv API
- Support both old-style (`cs.AI/0501001`) and new-style (`2301.12345`) IDs

#### FR-2.2.3: OpenAlex Integration
**Priority:** Medium

The system shall access papers via OpenAlex.

**Requirements:**
- Query papers by DOI, title, or OpenAlex ID
- Retrieve open access PDF links where available
- Extract author and institution metadata
- Handle API rate limits

#### FR-2.2.4: Paywall Integration
**Priority:** Medium

The system shall access paywalled papers via research-ai-paper microservice.

**Requirements:**
- Communicate with research-ai-paper REST API
- Submit download requests to task queue
- Poll for download completion
- Retrieve downloaded PDFs
- Handle authentication for paywalled sources

**Supported Publishers (via microservice):**
- IEEE Xplore
- ACM Digital Library
- Springer
- Elsevier (ScienceDirect)
- Wiley

#### FR-2.2.5: Source Resolution
**Priority:** High

The system shall determine the best source for a given paper.

**Resolution Order:**
1. Check local cache first
2. Try arXiv (free, fast)
3. Try OpenAlex/PubMed Central (free)
4. Fall back to paywalled source via microservice

**Requirements:**
- Return source type with retrieved paper
- Track provenance for each paper
- Log retrieval source and method

---

### 2.3 Caching

#### FR-2.3.1: Local PDF Cache
**Priority:** High

The system shall cache downloaded PDFs locally.

**Requirements:**
- Cache PDFs by content hash (SHA-256)
- Support configurable cache directory
- Implement cache size limits with LRU eviction
- Track cache metadata (source, download date, size)

#### FR-2.3.2: Metadata Cache
**Priority:** Medium

The system shall cache paper metadata from APIs.

**Requirements:**
- Cache Semantic Scholar responses
- TTL-based expiration (default: 7 days for metadata)
- Invalidation on explicit refresh request
- Memory or disk-based cache (configurable)

---

### 2.4 Rate Limiting

#### FR-2.4.1: API Rate Limit Handling
**Priority:** High

The system shall respect rate limits for all external APIs.

| API | Rate Limit | Strategy |
|-----|------------|----------|
| Semantic Scholar | 1 req/sec (unauthenticated) | Token bucket |
| Semantic Scholar | 10 req/sec (authenticated) | Token bucket |
| arXiv | 3 req/sec | Fixed delay |
| OpenAlex | 100 req/sec | Token bucket |
| research-ai-paper | Per-server limits | Queue-based |

**Requirements:**
- Implement per-API rate limiters
- Automatic retry with backoff on 429 responses
- Support for API keys to increase limits
- Queue requests during rate limit windows

---

### 2.5 Data Models

#### FR-2.5.1: Paper Metadata Model
**Priority:** High

The system shall define a unified paper metadata model.

```python
class PaperMetadata(BaseModel):
    semantic_scholar_id: Optional[str]
    doi: Optional[str]
    arxiv_id: Optional[str]
    title: str
    abstract: Optional[str]
    year: Optional[int]
    venue: Optional[str]
    authors: List[AuthorRef]
    citation_count: Optional[int]
    reference_count: Optional[int]
    open_access_url: Optional[str]
    pdf_available: bool
    source: SourceType
```

#### FR-2.5.2: Author Reference Model
**Priority:** Medium

```python
class AuthorRef(BaseModel):
    name: str
    semantic_scholar_id: Optional[str]
    orcid: Optional[str]
    affiliations: List[str]
```

#### FR-2.5.3: Citation Model
**Priority:** Medium

```python
class Citation(BaseModel):
    citing_paper: PaperMetadata
    cited_paper: PaperMetadata
    context: Optional[str]  # Citation snippet
    intent: Optional[str]   # background, methodology, result
```

---

## 3. Non-Functional Requirements

### 3.1 Performance

#### NFR-3.1.1: Retrieval Latency
**Priority:** High

| Operation | Target Latency (p95) |
|-----------|----------------------|
| Cache hit (PDF) | < 10ms |
| Cache hit (metadata) | < 5ms |
| Semantic Scholar query | < 2s |
| arXiv download | < 10s |
| Paywall download | < 60s (queued) |

#### NFR-3.1.2: Throughput
**Priority:** Medium

| Operation | Target Throughput |
|-----------|-------------------|
| Metadata queries | 10 req/sec (authenticated) |
| PDF downloads | 3 concurrent downloads |
| Batch metadata | 100 papers/minute |

---

### 3.2 Reliability

#### NFR-3.2.1: Retry Logic
**Priority:** High

- Retry failed requests up to 3 times
- Exponential backoff: 1s, 2s, 4s
- Circuit breaker after 5 consecutive failures
- Graceful fallback to alternative sources

#### NFR-3.2.2: Offline Operation
**Priority:** Low

- Serve cached content when APIs unavailable
- Queue requests for later retry
- Report cache staleness to callers

---

### 3.3 Security

#### NFR-3.3.1: Credential Management
**Priority:** High

- API keys stored in environment variables
- Support for Secret Manager integration
- No credentials in code or logs
- Secure storage of publisher credentials

#### NFR-3.3.2: Input Validation
**Priority:** High

- Validate DOI format before API calls
- Sanitize file paths for cache storage
- Validate URLs before download attempts

---

### 3.4 Observability

#### NFR-3.4.1: Logging
**Priority:** Medium

- Log all API calls with timing
- Log cache hits/misses
- Log rate limit events
- Log download failures with reasons

#### NFR-3.4.2: Metrics
**Priority:** Low

- Track API call counts by source
- Track cache hit rates
- Track download success/failure rates
- Track average retrieval times

---

## 4. User Stories

### US-01: Fetch Paper by DOI
**As a** system ingesting papers for extraction
**I want to** retrieve a paper by its DOI
**So that** I can extract research problems from it

**Acceptance Criteria:**
1. Given a valid DOI, When I call get_paper(), Then I get paper metadata
2. Given a DOI for an arXiv paper, When I call get_pdf(), Then I get the PDF
3. Given a DOI for a paywalled paper, When configured, Then download is queued
4. Given an invalid DOI, When I call get_paper(), Then I get a clear error

---

### US-02: Search Papers by Topic
**As a** researcher exploring a domain
**I want to** search for papers by keyword
**So that** I can find relevant research

**Acceptance Criteria:**
1. Given a query string, When I search, Then I get ranked paper results
2. Given search results, When I inspect them, Then each has title, abstract, year
3. Given a search, When I paginate, Then I can access more results
4. Given no matches, When I search, Then I get an empty list (not error)

---

### US-03: Get Paper Citations
**As a** system building citation graphs
**I want to** retrieve papers that cite a given paper
**So that** I can understand research progression

**Acceptance Criteria:**
1. Given a paper ID, When I get citations, Then I get citing paper metadata
2. Given a highly-cited paper, When I paginate, Then I can get all citations
3. Given citation data, When available, Then I see citation context snippets
4. Given a paper with no citations, When I query, Then I get empty list

---

### US-04: Download from arXiv
**As a** system acquiring open access papers
**I want to** download PDFs from arXiv
**So that** I can extract text for analysis

**Acceptance Criteria:**
1. Given an arXiv ID, When I call get_pdf(), Then PDF bytes are returned
2. Given a cached paper, When I request again, Then cache is used
3. Given rate limiting, When I make many requests, Then delays are applied
4. Given an invalid arXiv ID, When I request, Then clear error is returned

---

### US-05: Access Paywalled Paper
**As a** system with institutional access
**I want to** retrieve paywalled papers
**So that** I can include all relevant research

**Acceptance Criteria:**
1. Given a paywalled DOI, When configured, Then download is submitted
2. Given a queued download, When I poll, Then I get status updates
3. Given a completed download, When I retrieve, Then I get PDF bytes
4. Given no credentials, When I request paywalled paper, Then I get clear error

---

## 5. Acceptance Criteria Matrix

| Requirement | Acceptance Test | Priority |
|-------------|-----------------|----------|
| FR-2.1.1 | Paper search returns valid metadata | High |
| FR-2.1.2 | Can retrieve references and citations | High |
| FR-2.2.1 | Unified interface works for all sources | High |
| FR-2.2.2 | arXiv PDF download succeeds | High |
| FR-2.2.3 | OpenAlex metadata retrieval works | Medium |
| FR-2.2.4 | Paywall integration queues downloads | Medium |
| FR-2.3.1 | PDF cache prevents re-downloads | High |
| FR-2.4.1 | Rate limits respected for all APIs | High |
| NFR-3.1.1 | Cache hits meet latency targets | Medium |
| NFR-3.2.1 | Failed requests retry appropriately | High |

---

## 6. Dependencies

### 6.1 External Services
| Service | Purpose | Required |
|---------|---------|----------|
| Semantic Scholar API | Paper metadata, citations, embeddings | Yes |
| arXiv API/CDN | Open access paper download | Yes |
| OpenAlex API | Additional metadata source | Optional |
| research-ai-paper | Paywalled paper access | Optional |

### 6.2 Python Packages
| Package | Version | Purpose |
|---------|---------|---------|
| httpx | >=0.24.0 | Async HTTP client |
| aiofiles | >=23.0.0 | Async file operations |
| pydantic | >=2.0.0 | Data models |
| cachetools | >=5.0.0 | In-memory caching |
| ratelimit | >=2.2.0 | Rate limiting |
| tenacity | >=8.0.0 | Retry logic |

### 6.3 Internal Dependencies
| Component | Purpose |
|-----------|---------|
| Knowledge Graph (Sprint 01) | Store paper metadata in Neo4j |
| Config module | Environment-based settings |

---

## 7. Constraints

1. **API Key Requirement**: Semantic Scholar authenticated access requires API key. Unauthenticated access limited to 1 req/sec.

2. **Paywall Access**: Requires research-ai-paper microservice running with appropriate credentials. Not all publishers supported.

3. **arXiv Lag**: arXiv has ~24-48 hour delay between submission and availability.

4. **Rate Limits**: External API rate limits constrain throughput for bulk operations.

---

## 8. Design Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| HTTP Client | httpx (async) | Better performance for concurrent requests |
| Cache Storage | Disk-based with SQLite metadata | Persist across restarts, track metadata |
| Paywall Integration | REST API to microservice | Decoupled deployment, existing codebase |
| Source Priority | arXiv > OpenAlex > Paywall | Prefer free, fast sources |

---

## 9. Integration with Knowledge Graph

The Data Acquisition Layer integrates with Sprint 01's Knowledge Graph:

```
┌────────────────────────────────────────────────────┐
│            Sprint 02: Data Acquisition              │
│  ┌──────────────────────────────────────────────┐  │
│  │       Semantic Scholar Client (C5)            │  │
│  │  - Paper metadata                             │  │
│  │  - Citations                                  │  │
│  │  - SPECTER2 embeddings                        │  │
│  └──────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────┐  │
│  │      Paper Acquisition Layer (C6)             │  │
│  │  - arXiv integration                          │  │
│  │  - OpenAlex integration                       │  │
│  │  - Paywall via microservice                   │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────┐
│            Sprint 01: Knowledge Graph               │
│  ┌──────────────────────────────────────────────┐  │
│  │         Paper Repository                      │  │
│  │  - Create/update Paper entities               │  │
│  │  - Link to Authors                            │  │
│  │  - Store metadata from acquisition            │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────┘
```

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-07 | Claude | Initial requirements specification |
