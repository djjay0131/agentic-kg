# Knowledge Graph Foundation - Requirements Specification

**Version:** 1.0
**Date:** 2026-01-05
**Sprint:** 01
**Status:** Draft

**Related Documents:**
- [Phase 1 Design](../design/phase-1-knowledge-graph.md)
- [System Architecture](../design/system-architecture.md)
- [ADR-010: Neo4j Selection](../../memory-bank/architecturalDecisions.md)
- [ADR-003: Problems as First-Class Entities](../../memory-bank/architecturalDecisions.md)

---

## 1. Overview

This document specifies the requirements for the Knowledge Graph Foundation layer, which provides the core data storage and retrieval capabilities for research problems, papers, and their relationships.

### 1.1 Purpose

Enable storage, retrieval, and querying of research problems as first-class entities with:
- Structured attributes (assumptions, constraints, datasets, metrics)
- Semantic similarity search via embeddings
- Graph traversal for relationships between problems
- Provenance tracking to source papers

### 1.2 Scope

**In Scope:**
- Neo4j graph database setup and configuration
- Pydantic data models for all entities
- CRUD operations for Problem, Paper, and Author entities
- Embedding generation and vector search
- Hybrid search combining graph queries and vector similarity
- Relation management between problems

**Out of Scope:**
- Paper acquisition/download (Phase 1.5)
- LLM-based extraction from papers (Phase 2)
- Agent orchestration (Phase 3)
- User interface (Phase 4)

---

## 2. Functional Requirements

### 2.1 Entity Management

#### FR-2.1.1: Problem Entity CRUD
**Priority:** High

The system shall support Create, Read, Update, and Delete operations for Problem entities.

| Operation | Description |
|-----------|-------------|
| Create | Insert a new Problem with required fields (statement, evidence) |
| Read | Retrieve a Problem by ID, with all nested objects |
| Update | Modify Problem fields, auto-increment version number |
| Deprecate | Set status to deprecated (Problems are never hard-deleted) |
| List | Query Problems with filtering (domain, status, date range) |

**Validation Rules:**
- `statement` is required, minimum 20 characters
- `evidence.source_doi` must be a valid DOI format
- `status` must be one of: open, in_progress, resolved, deprecated
- `confidence_score` must be between 0.0 and 1.0

#### FR-2.1.2: Paper Entity CRUD
**Priority:** High

The system shall support CRUD operations for Paper entities.

| Operation | Description |
|-----------|-------------|
| Create | Insert a new Paper with DOI as primary key |
| Read | Retrieve a Paper by DOI |
| Update | Modify Paper fields (title, venue, etc.) |
| Delete | Remove a Paper only if no Problems reference it |
| List | Query Papers by year, venue, author |

**Validation Rules:**
- `doi` is required, must be unique
- `title` is required, minimum 10 characters
- `year` must be between 1900 and current year

#### FR-2.1.3: Author Entity CRUD
**Priority:** Medium

The system shall support CRUD operations for Author entities.

| Operation | Description |
|-----------|-------------|
| Create | Insert a new Author with generated ID |
| Read | Retrieve an Author by ID or ORCID |
| Update | Modify Author fields (name, affiliations) |
| List | Query Authors by name prefix or affiliation |

---

### 2.2 Relationship Management

#### FR-2.2.1: Problem-to-Problem Relations
**Priority:** High

The system shall support creating and querying relationships between Problems.

| Relation Type | Description | Required Properties |
|---------------|-------------|---------------------|
| EXTENDS | Problem B builds on Problem A | confidence, evidence_doi |
| CONTRADICTS | Problem B conflicts with Problem A | confidence, contradiction_type |
| DEPENDS_ON | Problem B requires solution to A | confidence, dependency_type |
| REFRAMES | Problem B redefines Problem A's scope | confidence |

**Requirements:**
- Relations are directional (from → to)
- Each relation must have a confidence score (0.0-1.0)
- Relations can include optional evidence_doi for source

#### FR-2.2.2: Problem-to-Paper Relations
**Priority:** High

The system shall link Problems to their source Papers.

| Relation Type | Description | Required Properties |
|---------------|-------------|---------------------|
| EXTRACTED_FROM | Problem was extracted from Paper | section, extraction_date |
| CITES | Problem references another Paper | citation_context |

#### FR-2.2.3: Paper-to-Author Relations
**Priority:** Medium

The system shall link Papers to their Authors.

| Relation Type | Description | Required Properties |
|---------------|-------------|---------------------|
| AUTHORED_BY | Paper was written by Author | author_position |

---

### 2.3 Search and Retrieval

#### FR-2.3.1: Semantic Search
**Priority:** High

The system shall support finding Problems by semantic similarity to a query.

**Requirements:**
- Accept a natural language query string
- Generate embedding for query using same model as stored embeddings
- Return top-K most similar Problems ranked by cosine similarity
- Return similarity scores with results

**Example Query:**
```
Input: "challenges in few-shot learning for NLP"
Output: [
  {Problem: {...}, score: 0.89},
  {Problem: {...}, score: 0.82},
  ...
]
```

#### FR-2.3.2: Structured Search
**Priority:** High

The system shall support filtering Problems by structured attributes.

**Supported Filters:**
| Filter | Type | Description |
|--------|------|-------------|
| domain | string | Research domain (exact or partial match) |
| status | enum | Problem status (open, resolved, etc.) |
| dataset_available | boolean | Has at least one available dataset |
| year_min | integer | Paper publication year >= value |
| year_max | integer | Paper publication year <= value |
| constraint_type | enum | Has constraint of specified type |

#### FR-2.3.3: Hybrid Search
**Priority:** High

The system shall combine semantic and structured search.

**Requirements:**
- Apply structured filters first to reduce candidate set
- Perform semantic search on filtered candidates
- Support configurable weighting between semantic and structured scores
- Return unified ranking with combined scores

#### FR-2.3.4: Graph Traversal
**Priority:** Medium

The system shall support traversing relationships from a Problem.

**Requirements:**
- Get all Problems that extend a given Problem
- Get all Problems that contradict a given Problem
- Get all Problems extracted from the same Paper
- Support multi-hop traversal (e.g., problems that extend problems that extend P)
- Return traversal paths with relation metadata

---

### 2.4 Embedding Management

#### FR-2.4.1: Automatic Embedding Generation
**Priority:** High

The system shall generate embeddings for new Problems automatically.

**Requirements:**
- Generate embedding from Problem statement on create
- Use OpenAI text-embedding-3-small model (1536 dimensions)
- Store embedding in Neo4j vector index
- Regenerate embedding if statement is updated

#### FR-2.4.2: Batch Embedding
**Priority:** Medium

The system shall support bulk embedding generation.

**Requirements:**
- Accept list of Problem IDs without embeddings
- Generate embeddings in batches (max 100 per API call)
- Update Problems with embeddings
- Report progress and failures

---

### 2.5 Data Integrity

#### FR-2.5.1: Unique Constraints
**Priority:** High

The system shall enforce uniqueness for key identifiers.

| Entity | Unique Field |
|--------|--------------|
| Problem | id (UUID) |
| Paper | doi |
| Author | id, orcid (if present) |

#### FR-2.5.2: Referential Integrity
**Priority:** High

The system shall maintain consistency between related entities.

**Requirements:**
- Cannot delete a Paper if Problems reference it via EXTRACTED_FROM
- Deleting a Problem should remove all its relations
- Author references should be validated on Paper creation

#### FR-2.5.3: Version Tracking
**Priority:** Medium

The system shall track versions of Problem entities.

**Requirements:**
- Increment version number on each update
- Store updated_at timestamp on each modification
- Optionally store previous state for audit (configurable)

---

## 3. Non-Functional Requirements

### 3.1 Performance

#### NFR-3.1.1: Query Latency
**Priority:** High

| Operation | Target Latency (p95) |
|-----------|----------------------|
| Single entity read | < 50ms |
| CRUD operations | < 100ms |
| Semantic search (top-10) | < 500ms |
| Hybrid search | < 1000ms |
| Graph traversal (2 hops) | < 200ms |

#### NFR-3.1.2: Throughput
**Priority:** Medium

| Operation | Target Throughput |
|-----------|-------------------|
| Concurrent reads | 100 requests/second |
| Concurrent writes | 20 requests/second |
| Batch import | 1000 entities/minute |

#### NFR-3.1.3: Scalability
**Priority:** Low (for Sprint 01)

The system shall support:
- Up to 100,000 Problem entities
- Up to 1,000,000 Paper entities
- Up to 10,000,000 relations

---

### 3.2 Reliability

#### NFR-3.2.1: Data Durability
**Priority:** High

- All data persisted to disk
- Transaction support for multi-step operations
- Automatic rollback on failure

#### NFR-3.2.2: Connection Resilience
**Priority:** Medium

- Automatic retry on transient failures (up to 3 retries)
- Exponential backoff (1s, 2s, 4s)
- Connection pool management

#### NFR-3.2.3: Graceful Degradation
**Priority:** Medium

- If embedding service fails, Problem created without embedding
- Flag Problems missing embeddings for later processing
- Log failures for monitoring

---

### 3.3 Security

#### NFR-3.3.1: Authentication
**Priority:** Medium

- Neo4j connection uses authenticated credentials
- Credentials stored in environment variables, not code
- Support for Neo4j Aura token-based auth

#### NFR-3.3.2: Input Validation
**Priority:** High

- All inputs validated via Pydantic models
- Cypher queries use parameterized statements (no string interpolation)
- DOI and URL fields validated against patterns

---

### 3.4 Maintainability

#### NFR-3.4.1: Code Quality
**Priority:** High

- Type hints on all public functions
- Docstrings on all public classes and methods
- Test coverage > 80%
- Linting with ruff, formatting with black

#### NFR-3.4.2: Schema Evolution
**Priority:** Medium

- Schema migrations are idempotent (can run multiple times)
- Schema version tracked in database
- Backward-compatible changes preferred

---

### 3.5 Observability

#### NFR-3.5.1: Logging
**Priority:** Medium

- Log all database operations at DEBUG level
- Log errors with full context
- Log slow queries (> 1s) at WARN level

#### NFR-3.5.2: Metrics
**Priority:** Low (for Sprint 01)

- Track query latencies
- Track error rates
- Track entity counts

---

## 4. User Stories

### US-01: Create a Research Problem
**As a** system importing extracted problems
**I want to** store a Problem with its full attributes
**So that** it can be retrieved and queried later

**Acceptance Criteria:**
1. Given valid Problem data, When I call create_problem(), Then the Problem is stored in Neo4j
2. Given a Problem without a statement, When I call create_problem(), Then validation fails
3. Given a Problem, When stored, Then an embedding is generated automatically
4. Given a duplicate ID, When I call create_problem(), Then an error is raised

---

### US-02: Find Similar Problems
**As a** researcher
**I want to** find problems similar to my description
**So that** I can discover related open research questions

**Acceptance Criteria:**
1. Given a query string, When I call semantic_search(), Then I get ranked Problems
2. Given a query, When results return, Then each has a similarity score
3. Given a query with no matches, When I search, Then I get an empty list
4. Given a query, When I specify limit=5, Then at most 5 results return

---

### US-03: Filter Problems by Criteria
**As a** researcher
**I want to** filter problems by domain, status, and dataset availability
**So that** I can find actionable problems in my field

**Acceptance Criteria:**
1. Given domain="NLP", When I call structured_search(), Then only NLP problems return
2. Given status="open", When I search, Then only open problems return
3. Given dataset_available=True, When I search, Then only problems with available datasets return
4. Given multiple filters, When I search, Then all filters are applied (AND logic)

---

### US-04: Find Problem Relations
**As a** system building a knowledge graph
**I want to** traverse relationships between problems
**So that** I can understand how problems relate

**Acceptance Criteria:**
1. Given a Problem ID, When I call get_related_problems(), Then I get all related problems
2. Given relation_type="EXTENDS", When I filter, Then only extending problems return
3. Given a Problem with no relations, When I query, Then I get an empty list
4. Given traversal depth=2, When I query, Then I get 2-hop relations

---

### US-05: Link Problem to Source
**As a** system importing extracted problems
**I want to** link a Problem to its source Paper
**So that** provenance is maintained

**Acceptance Criteria:**
1. Given a Problem and Paper DOI, When I call link_problem_to_paper(), Then EXTRACTED_FROM is created
2. Given a non-existent Paper DOI, When I link, Then the Paper is created first
3. Given a link, When I query the Problem, Then I can retrieve the source Paper
4. Given a Paper, When I query, Then I can get all Problems extracted from it

---

## 5. Acceptance Criteria Matrix

| Requirement | Acceptance Test | Priority |
|-------------|-----------------|----------|
| FR-2.1.1 | CRUD operations pass with valid data | High |
| FR-2.1.1 | Validation rejects invalid Problem data | High |
| FR-2.2.1 | Can create all 4 relation types | High |
| FR-2.2.1 | Relations include confidence scores | High |
| FR-2.3.1 | Semantic search returns ranked results | High |
| FR-2.3.2 | All filter types work independently | High |
| FR-2.3.3 | Hybrid search combines filters and semantics | High |
| FR-2.4.1 | Embedding generated on Problem create | High |
| FR-2.5.1 | Duplicate IDs rejected | High |
| NFR-3.1.1 | p95 latencies meet targets | Medium |
| NFR-3.2.1 | Data survives Neo4j restart | High |
| NFR-3.4.1 | Test coverage > 80% | High |

---

## 6. Dependencies

### 6.1 External Services
| Service | Purpose | Required |
|---------|---------|----------|
| Neo4j 5.x | Graph database | Yes |
| OpenAI API | Embedding generation | Yes |

### 6.2 Python Packages
| Package | Version | Purpose |
|---------|---------|---------|
| neo4j | >=5.0.0 | Neo4j driver |
| pydantic | >=2.0.0 | Data models |
| openai | >=1.0.0 | Embeddings |
| pytest | >=7.0.0 | Testing |
| testcontainers | >=3.0.0 | Neo4j test container |

---

## 7. Constraints

1. **Embedding Model Lock-in**: Using OpenAI embeddings requires API key and incurs cost. Future migration to local models should be considered.

2. **Neo4j Version**: Requires Neo4j 5.x+ for native vector index support. Older versions not supported.

3. **Embedding Dimension**: Using 1536 dimensions (text-embedding-3-small). Changing model requires re-embedding all Problems.

---

## 8. Design Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Deduplication Threshold | 0.95 cosine similarity | High threshold to avoid false positives |
| Delete Policy | Soft delete only (status change) | Problems should never be deleted, only deprecated |
| Embedding Update | Immediate regeneration | Keep embeddings in sync with statements |
| Multi-tenancy | Not for MVP | Simplifies initial implementation |

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-05 | Claude | Initial requirements specification |
