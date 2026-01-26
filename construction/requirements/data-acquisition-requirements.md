# Data Acquisition Layer - Requirements Specification

**Version:** 1.0
**Date:** 2026-01-25
**Sprint:** 02
**Status:** Draft

**Related Documents:**
- [System Architecture](../design/system-architecture.md)
- [Knowledge Graph Requirements](knowledge-graph-requirements.md)
- [Project Brief](../../memory-bank/projectbrief.md)
- [System Patterns](../../memory-bank/systemPatterns.md)

---

## 1. Overview

This document specifies the requirements for the Data Acquisition Layer, which provides the ability to ingest research papers from external academic sources and populate the Knowledge Graph with paper metadata.

### 1.1 Purpose

Enable automated acquisition of research papers from multiple academic sources with:
- Unified API client interfaces for Semantic Scholar, arXiv, and OpenAlex
- Rate limiting and retry logic for API reliability
- Caching layer to minimize redundant API calls
- Paper metadata normalization across sources
- Full-text retrieval where available
- Integration with the Knowledge Graph for paper storage

### 1.2 Scope

**In Scope:**
- API client implementations for Semantic Scholar, arXiv, and OpenAlex
- Rate limiting infrastructure with configurable limits per source
- Caching layer for API responses
- Paper metadata normalization to unified schema
- Full-text/abstract retrieval
- Batch import capabilities
- Error handling and retry logic

**Out of Scope:**
- PDF parsing/text extraction (Phase 3: Extraction Pipeline)
- Problem extraction from papers (Phase 3)
- Agent-driven paper discovery (Phase 4)
- Citation graph construction (future sprint)

---

## 2. Functional Requirements

### 2.1 API Client Implementations

#### FR-2.1.1: Semantic Scholar Client
**Priority:** High

The system shall provide a client for the Semantic Scholar Academic Graph API.

| Capability | Description |
|------------|-------------|
| Paper Search | Search papers by keyword, title, or author |
| Paper Details | Retrieve full paper metadata by Paper ID or DOI |
| Author Details | Retrieve author information and publications |
| Citations | Get papers that cite a given paper |
| References | Get papers referenced by a given paper |
| Bulk Retrieval | Retrieve multiple papers by ID list |

**API Details:**
- Base URL: `https://api.semanticscholar.org/graph/v1`
- Authentication: API key (optional but recommended for higher limits)
- Rate Limits: 100 requests/5 min (unauthenticated), 1 request/sec (authenticated)

**Required Fields to Extract:**
- `paperId`, `externalIds` (DOI, arXiv, MAG, etc.)
- `title`, `abstract`, `year`, `venue`
- `authors` (name, authorId)
- `citationCount`, `referenceCount`
- `fieldsOfStudy`, `publicationTypes`
- `isOpenAccess`, `openAccessPdf`

#### FR-2.1.2: arXiv Client
**Priority:** High

The system shall provide a client for the arXiv API.

| Capability | Description |
|------------|-------------|
| Search | Query papers by title, author, abstract, category |
| Metadata | Retrieve paper metadata by arXiv ID |
| PDF URL | Construct PDF download URLs |
| Categories | Filter by arXiv subject categories |

**API Details:**
- Base URL: `http://export.arxiv.org/api/query`
- Authentication: None required
- Rate Limits: 1 request/3 seconds (per arXiv guidelines)

**Required Fields to Extract:**
- `id` (arXiv identifier), `doi` (if available)
- `title`, `summary` (abstract), `published`, `updated`
- `authors` (name, affiliation if available)
- `categories` (primary and secondary)
- `pdf_url`, `abs_url`
- `comment` (often contains page count, figures)

#### FR-2.1.3: OpenAlex Client
**Priority:** High

The system shall provide a client for the OpenAlex API.

| Capability | Description |
|------------|-------------|
| Works Search | Search works by title, author, concept |
| Work Details | Retrieve work details by OpenAlex ID or DOI |
| Author Details | Retrieve author information |
| Venue/Source | Get publication venue information |
| Concepts | Retrieve concept/topic hierarchies |
| Institutions | Get institution information |

**API Details:**
- Base URL: `https://api.openalex.org`
- Authentication: Polite pool (email in User-Agent) recommended
- Rate Limits: 10 requests/second (polite pool), 100K/day

**Required Fields to Extract:**
- `id` (OpenAlex ID), `doi`, `ids` (other identifiers)
- `title`, `abstract_inverted_index` (requires reconstruction)
- `publication_year`, `publication_date`
- `authorships` (author + institution + position)
- `cited_by_count`, `referenced_works_count`
- `concepts` (with scores), `topics`
- `primary_location`, `open_access`

---

### 2.2 Rate Limiting Infrastructure

#### FR-2.2.1: Per-Source Rate Limiting
**Priority:** High

The system shall enforce configurable rate limits per API source.

| Source | Default Limit | Configurable |
|--------|---------------|--------------|
| Semantic Scholar | 1 req/sec | Yes |
| arXiv | 1 req/3sec | Yes |
| OpenAlex | 10 req/sec | Yes |

**Requirements:**
- Token bucket algorithm for smooth rate limiting
- Per-source limit configuration via environment or config file
- Automatic backoff when rate limit exceeded
- Queue requests when at capacity

#### FR-2.2.2: Retry Logic
**Priority:** High

The system shall automatically retry failed requests.

| Error Type | Retry Strategy |
|------------|----------------|
| 429 Too Many Requests | Exponential backoff (1s, 2s, 4s, 8s) |
| 5xx Server Error | Exponential backoff, max 3 retries |
| Timeout | Immediate retry, then backoff |
| Connection Error | Exponential backoff, max 3 retries |

**Requirements:**
- Configurable max retry count (default: 3)
- Configurable initial backoff (default: 1 second)
- Jitter added to prevent thundering herd
- Log all retries with reason

#### FR-2.2.3: Circuit Breaker
**Priority:** Medium

The system shall implement circuit breaker pattern for API health.

**Requirements:**
- Open circuit after N consecutive failures (default: 5)
- Half-open state after cooldown period (default: 60 seconds)
- Close circuit after successful request in half-open state
- Expose circuit state for monitoring

---

### 2.3 Caching Layer

#### FR-2.3.1: Response Caching
**Priority:** High

The system shall cache API responses to minimize redundant calls.

| Cache Type | TTL | Purpose |
|------------|-----|---------|
| Paper Metadata | 7 days | Metadata rarely changes |
| Search Results | 1 hour | Results may update |
| Author Details | 7 days | Author info stable |
| Rate Limit State | N/A | Track request counts |

**Requirements:**
- Configurable cache backend (in-memory default, Redis optional)
- TTL-based expiration
- Cache key includes API source and query parameters
- Cache bypass option for fresh data
- Cache statistics (hits, misses, size)

#### FR-2.3.2: Deduplication
**Priority:** Medium

The system shall deduplicate papers across sources using identifiers.

**Requirements:**
- Match by DOI (primary)
- Match by arXiv ID
- Match by title + year (fuzzy, when no DOI)
- Mark canonical source when multiple exist
- Store all external IDs on merged record

---

### 2.4 Paper Metadata Normalization

#### FR-2.4.1: Unified Paper Schema
**Priority:** High

The system shall normalize paper metadata to a unified schema compatible with the Knowledge Graph Paper model.

**Normalized Fields:**
| Field | Type | Source Mapping |
|-------|------|----------------|
| doi | string | All sources |
| title | string | All sources |
| abstract | string | SS: abstract, arXiv: summary, OA: reconstructed |
| year | int | publication_year / published |
| venue | string | venue / journal_ref / primary_location |
| authors | list | Normalized author objects |
| external_ids | dict | {semantic_scholar, arxiv, openalex, mag, ...} |
| citation_count | int | cited_by_count / citationCount |
| fields_of_study | list | fieldsOfStudy / concepts / categories |
| is_open_access | bool | All sources |
| pdf_url | string | openAccessPdf / pdf_url / primary_location |

#### FR-2.4.2: Author Normalization
**Priority:** Medium

The system shall normalize author information across sources.

**Normalized Author Fields:**
| Field | Type | Description |
|-------|------|-------------|
| name | string | Display name |
| external_ids | dict | {semantic_scholar_id, orcid, openalex_id, ...} |
| affiliations | list | Current affiliations (if available) |

**Requirements:**
- Attempt author matching across sources by ORCID or name similarity
- Preserve source-specific IDs for future disambiguation
- Handle name variations (initials, full names)

---

### 2.5 Full-Text Retrieval

#### FR-2.5.1: Abstract Retrieval
**Priority:** High

The system shall retrieve paper abstracts from all sources.

**Requirements:**
- All three APIs provide abstracts
- OpenAlex requires inverted index reconstruction
- Store as plain text in Paper model
- Handle missing abstracts gracefully

#### FR-2.5.2: PDF URL Retrieval
**Priority:** Medium

The system shall retrieve PDF URLs where available.

**Requirements:**
- Prioritize open access PDFs
- Construct arXiv PDF URLs from ID
- Use Semantic Scholar openAccessPdf
- Use OpenAlex primary_location or best_oa_location
- Mark papers without PDF access

#### FR-2.5.3: Full-Text Download (Optional)
**Priority:** Low

The system may optionally download PDF content for later processing.

**Requirements:**
- Only download open access PDFs
- Store in configurable location (local or cloud storage)
- Track download status per paper
- Respect source terms of service

---

### 2.6 Batch Operations

#### FR-2.6.1: Batch Paper Import
**Priority:** High

The system shall support importing multiple papers in a single operation.

**Requirements:**
- Accept list of DOIs, arXiv IDs, or OpenAlex IDs
- Process in parallel with rate limit compliance
- Report progress and failures
- Create/update Paper entities in Knowledge Graph
- Support configurable batch size (default: 50)

#### FR-2.6.2: Search and Import
**Priority:** Medium

The system shall support searching and importing papers by query.

**Requirements:**
- Accept search query and source preference
- Return paginated results with metadata
- Allow selective import of search results
- Support max results limit

#### FR-2.6.3: Author Bibliography Import
**Priority:** Medium

The system shall support importing all papers by an author.

**Requirements:**
- Accept author ID (from any source)
- Retrieve author's publication list
- Import papers with proper authorship links
- Handle pagination for prolific authors

---

### 2.7 Knowledge Graph Integration

#### FR-2.7.1: Paper Creation
**Priority:** High

The system shall create Paper entities in the Knowledge Graph from acquired data.

**Requirements:**
- Map normalized metadata to Paper Pydantic model
- Check for existing paper by DOI before creating
- Update existing paper if newer data available
- Return created/updated Paper entity

#### FR-2.7.2: Author Linking
**Priority:** Medium

The system shall create Author entities and link to Papers.

**Requirements:**
- Create Author if not exists (match by ID or ORCID)
- Create AUTHORED_BY relation with position
- Update author metadata if newer data available

---

## 3. Non-Functional Requirements

### 3.1 Performance

#### NFR-3.1.1: Throughput
**Priority:** High

| Operation | Target Throughput |
|-----------|-------------------|
| Single paper lookup | < 2 seconds |
| Batch import (50 papers) | < 2 minutes |
| Search query | < 5 seconds |
| Full bibliography import | < 10 minutes |

#### NFR-3.1.2: Concurrency
**Priority:** Medium

| Metric | Target |
|--------|--------|
| Concurrent API requests | Per-source limits (1-10) |
| Concurrent batch imports | 3 simultaneous |
| Background job queue | 100 pending jobs |

### 3.2 Reliability

#### NFR-3.2.1: API Resilience
**Priority:** High

- Handle API outages gracefully
- Queue failed requests for retry
- Log all API errors with context
- Provide fallback to alternative sources when possible

#### NFR-3.2.2: Data Consistency
**Priority:** High

- Atomic paper creation (all or nothing)
- Idempotent import operations
- Preserve existing data on partial update failures

### 3.3 Observability

#### NFR-3.3.1: Logging
**Priority:** Medium

- Log all API requests (source, endpoint, status)
- Log rate limit events
- Log cache hits/misses
- Log import progress and failures

#### NFR-3.3.2: Metrics
**Priority:** Low

- Track API latency by source
- Track error rates by source
- Track cache hit ratio
- Track papers imported per hour

### 3.4 Configuration

#### NFR-3.4.1: Environment Configuration
**Priority:** High

| Variable | Purpose | Default |
|----------|---------|---------|
| SEMANTIC_SCHOLAR_API_KEY | API authentication | None |
| OPENALEX_EMAIL | Polite pool identification | None |
| CACHE_TTL_SECONDS | Default cache TTL | 604800 (7 days) |
| MAX_RETRIES | Retry limit | 3 |
| RATE_LIMIT_* | Per-source rate limits | Source defaults |

---

## 4. User Stories

### US-01: Import Paper by DOI
**As a** researcher
**I want to** import a paper by its DOI
**So that** it's available in the knowledge graph for problem extraction

**Acceptance Criteria:**
1. Given a valid DOI, When I call import_paper(doi), Then the paper is fetched and stored
2. Given a DOI that exists in the graph, When I import, Then metadata is updated if newer
3. Given an invalid DOI, When I import, Then an appropriate error is returned
4. Given a DOI, When I import, Then authors are created/linked automatically

---

### US-02: Search Papers by Topic
**As a** researcher
**I want to** search for papers on a topic across multiple sources
**So that** I can find relevant literature

**Acceptance Criteria:**
1. Given a search query, When I call search_papers(query), Then I get results from all sources
2. Given results, When displayed, Then duplicates across sources are merged
3. Given results, When I specify source="arxiv", Then only arXiv results return
4. Given results, When paginating, Then I can retrieve additional pages

---

### US-03: Import Author's Bibliography
**As a** researcher
**I want to** import all papers by a specific author
**So that** I can analyze their research trajectory

**Acceptance Criteria:**
1. Given an author ID, When I call import_author_papers(id), Then all their papers are imported
2. Given a prolific author, When importing, Then pagination is handled automatically
3. Given import, When complete, Then author entity is linked to all papers
4. Given import, When rate limited, Then the operation completes without error

---

### US-04: Batch Import from List
**As a** system administrator
**I want to** import papers from a list of identifiers
**So that** I can bootstrap the knowledge graph

**Acceptance Criteria:**
1. Given a list of DOIs, When I call batch_import(dois), Then all papers are imported
2. Given mixed identifiers (DOI, arXiv), When importing, Then each is resolved correctly
3. Given failures in batch, When import completes, Then a report shows successes/failures
4. Given large batch, When importing, Then progress is reported periodically

---

### US-05: Handle API Unavailability
**As a** system operator
**I want** the system to handle API outages gracefully
**So that** partial data is preserved and operations can resume

**Acceptance Criteria:**
1. Given Semantic Scholar is down, When I search, Then arXiv and OpenAlex still return results
2. Given repeated failures, When circuit opens, Then subsequent requests fail fast
3. Given API recovery, When circuit half-opens, Then requests resume automatically
4. Given failure during batch, When operation stops, Then completed imports are preserved

---

## 5. Acceptance Criteria Matrix

| Requirement | Acceptance Test | Priority |
|-------------|-----------------|----------|
| FR-2.1.1 | Can retrieve paper from Semantic Scholar by DOI | High |
| FR-2.1.2 | Can retrieve paper from arXiv by ID | High |
| FR-2.1.3 | Can retrieve paper from OpenAlex by DOI | High |
| FR-2.2.1 | Rate limits enforced per source | High |
| FR-2.2.2 | Retries occur on transient failures | High |
| FR-2.3.1 | Repeated requests return cached data | High |
| FR-2.4.1 | Metadata normalized to unified schema | High |
| FR-2.6.1 | Batch import creates Paper entities | High |
| FR-2.7.1 | Papers stored in Knowledge Graph | High |
| NFR-3.1.1 | Single paper lookup < 2 seconds | Medium |
| NFR-3.2.1 | System handles API outages gracefully | High |

---

## 6. Dependencies

### 6.1 External Services
| Service | Purpose | Required |
|---------|---------|----------|
| Semantic Scholar API | Paper metadata, citations | Yes |
| arXiv API | Preprint metadata, PDFs | Yes |
| OpenAlex API | Paper metadata, concepts | Yes |
| Redis (optional) | Distributed cache | No |

### 6.2 Internal Dependencies
| Component | Purpose |
|-----------|---------|
| Knowledge Graph Repository | Store Paper entities |
| Configuration Module | API keys, rate limits |

### 6.3 Python Packages
| Package | Version | Purpose |
|---------|---------|---------|
| httpx | >=0.24.0 | Async HTTP client |
| tenacity | >=8.0.0 | Retry logic |
| cachetools | >=5.0.0 | In-memory caching |
| redis (optional) | >=4.0.0 | Distributed cache |
| feedparser | >=6.0.0 | arXiv Atom parsing |

---

## 7. Constraints

1. **API Rate Limits**: Must respect each source's rate limits to avoid bans.

2. **API Key Management**: Semantic Scholar API key recommended but optional. OpenAlex requires email in User-Agent.

3. **Data Freshness**: Cached data may be up to 7 days stale for metadata.

4. **Coverage Gaps**: Not all papers exist in all sources. DOI coverage varies.

5. **Abstract Reconstruction**: OpenAlex inverted index requires reconstruction which may have minor formatting differences.

---

## 8. Design Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| HTTP Client | httpx (async) | Native async support, good performance |
| Primary Cache | In-memory (cachetools) | Simple, sufficient for single instance |
| Identifier Priority | DOI > arXiv > OpenAlex | DOI is most universal |
| Duplicate Resolution | Keep first, merge IDs | Avoid data conflicts |
| PDF Download | Optional, not default | Storage costs, TOS concerns |

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-25 | Claude | Initial requirements specification |
