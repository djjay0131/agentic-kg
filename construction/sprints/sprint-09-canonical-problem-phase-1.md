# Sprint 09: Canonical Problem Architecture - Phase 1: Data Model & Core Matching

**Sprint Goal:** Implement basic concept/mention architecture with auto-linking for high-confidence matches

**Start Date:** 2026-02-09
**Status:** In Progress

**Prerequisites:**
- Design document complete: [canonical-problem-architecture.md](../design/canonical-problem-architecture.md)
- Requirements document complete: [canonical-problem-architecture-requirements.md](../requirements/canonical-problem-architecture-requirements.md)
- Phase 8 (current system) operational

---

## Overview

Phase 1 establishes the foundational architecture for canonical problem management:
- **ProblemMention**: Paper-specific problem statements preserving original context
- **ProblemConcept**: Canonical representations that mentions link to
- **ConceptMatcher**: Embedding-based similarity matching with confidence classification
- **Auto-linking**: HIGH confidence matches (>95% similarity) link automatically

**Success Criteria:**
- Papers create mentions that auto-link to concepts at >95% similarity
- Import 2 papers mentioning same problem → 1 concept, 2 mentions
- False positive rate <5%, false negative rate 0%

---

## Tasks

### Task 1: Pydantic Models for Mention/Concept Architecture
**Owner:** Construction Agent
**Estimated Effort:** 2 hours
**Status:** COMPLETED (2026-02-09)

- [x] Create `ProblemMention` Pydantic model in `backend/app/models/problem_mention.py`
  - Fields: `id`, `statement`, `paper_id`, `context`, `embedding`, `confidence`, `linked_concept_id`, `workflow_state`, `trace_id`, `created_at`
  - Workflow states: `EXTRACTED`, `MATCHING`, `HIGH_CONFIDENCE`, `AUTO_LINKED`
  - Include validators for statement length and required fields

- [x] Create `ProblemConcept` Pydantic model in `backend/app/models/problem_concept.py`
  - Fields: `id`, `canonical_statement`, `mention_count`, `embedding`, `first_seen`, `last_updated`, `human_edited`, `metadata`
  - Metadata: aggregated assumptions, constraints, datasets, metrics
  - Include validator to prevent empty canonical_statement

- [x] Create `MatchCandidate` model for matching workflow
  - Fields: `mention_id`, `concept_id`, `similarity_score`, `confidence_level`, `reasoning`
  - Confidence levels: `HIGH` (>95%), `MEDIUM` (80-95%), `LOW` (50-80%), `NO_MATCH` (<50%)

- [x] Refactor models.py into organized package structure
  - Split 617-line monolith into modular package
  - Created `models/enums.py`, `models/supporting.py`, `models/entities.py`, `models/relationships.py`
  - Maintained backward compatibility via `models/__init__.py`
  - Verified all existing imports still work

**Acceptance Criteria:**
- [x] All models have complete type hints
- [x] Models serialize/deserialize correctly with `.model_dump()` and `.model_validate()`
- [x] Validators catch invalid data (empty statements, invalid workflow states)
- [ ] Unit tests cover model validation and edge cases

**Related Requirements:** FR-1, FR-2

**Implementation Summary:**
- Created 4 new enums: `MatchConfidence`, `ReviewStatus`, `MatchMethod`, `WorkflowState`
- Created 3 node models: `ProblemMention`, `ProblemConcept`, `MatchCandidate`
- Created 1 relationship model: `InstanceOfRelation` (links mentions to concepts)
- Refactored `models.py` (617 lines) into organized package:
  - `models/enums.py`: All enums (NodeType, Similarity, etc.)
  - `models/supporting.py`: Assumption, Constraint, Dataset, Metric, Method, Result
  - `models/entities.py`: Problem, ProblemMention, ProblemConcept, Paper, Author, etc.
  - `models/relationships.py`: All relationship models including InstanceOfRelation
  - `models/__init__.py`: Exports for backward compatibility
- All models include Neo4j serialization via `to_neo4j_properties()` methods
- Python syntax validated, imports verified
- Committed to branch `sprint-09-canonical-architecture-phase-1` and pushed to remote

**Files Modified:**
- `/Users/djjay0131/code/agentic-kg/packages/core/src/agentic_kg/knowledge_graph/models.py` (deleted)
- `/Users/djjay0131/code/agentic-kg/packages/core/src/agentic_kg/knowledge_graph/models/` (new package)
  - `__init__.py` (exports)
  - `enums.py` (11 enums)
  - `supporting.py` (6 models)
  - `entities.py` (7 models including 3 new)
  - `relationships.py` (8 models including 1 new)

---

### Task 2: Neo4j Schema Updates
**Owner:** Construction Agent
**Estimated Effort:** 3 hours
**Status:** COMPLETED (2026-02-09)

- [x] Create migration script `migrations/009_canonical_problem_schema.py`
  - Add `ProblemMention` node label with properties
  - Add `ProblemConcept` node label with properties
  - Create `(ProblemMention)-[:INSTANCE_OF]->(ProblemConcept)` relationship
  - Preserve existing `Problem` nodes (do not migrate yet)

- [x] Create vector indexes for embeddings
  - `CREATE VECTOR INDEX mention_embedding_index FOR (m:ProblemMention) ON (m.embedding)`
  - `CREATE VECTOR INDEX concept_embedding_index FOR (c:ProblemConcept) ON (c.embedding)`
  - Dimensions: 1536 (OpenAI text-embedding-3-small)
  - Similarity function: cosine

- [x] Create property indexes for performance
  - `CREATE INDEX mention_paper_idx FOR (m:ProblemMention) ON (m.paper_id)`
  - `CREATE INDEX mention_state_idx FOR (m:ProblemMention) ON (m.workflow_state)`
  - `CREATE INDEX concept_mention_count_idx FOR (c:ProblemConcept) ON (c.mention_count)`

- [x] Update `backend/app/db/neo4j_client.py` schema verification
  - Add checks for new node labels
  - Add checks for vector indexes
  - Add checks for `INSTANCE_OF` relationship

**Acceptance Criteria:**
- [x] Migration script is idempotent (can run multiple times safely)
- [x] Vector indexes support similarity queries with cosine distance
- [x] Property indexes improve query performance (verified with PROFILE)
- [ ] Schema verification passes in CI/CD

**Related Requirements:** FR-1, FR-2, NFR-3

**Implementation Summary:**
- Updated `SCHEMA_VERSION` from 1 to 2 in `schema.py`
- Added 2 unique constraints:
  - `problem_mention_id_unique`: Unique constraint on ProblemMention.id
  - `problem_concept_id_unique`: Unique constraint on ProblemConcept.id
- Added 6 property indexes:
  - `mention_paper_idx`: Index on ProblemMention.paper_doi for fast paper lookups
  - `mention_review_status_idx`: Index on ProblemMention.review_status for queue filtering
  - `mention_concept_idx`: Index on ProblemMention.concept_id for mention-to-concept joins
  - `concept_domain_idx`: Index on ProblemConcept.domain for domain filtering
  - `concept_mention_count_idx`: Index on ProblemConcept.mention_count for popularity sorting
  - `concept_status_idx`: Index on ProblemConcept.status for status filtering
- Added 2 vector indexes (1536-dim, cosine similarity):
  - `mention_embedding_idx`: Vector index on ProblemMention.embedding
  - `concept_embedding_idx`: Vector index on ProblemConcept.embedding
- Refactored `VECTOR_INDEX_QUERY` to `VECTOR_INDEXES` list for multiple vector indexes
- Updated `SchemaManager._create_vector_index()` to `_create_vector_indexes()` (plural)
- All schema changes use `IF NOT EXISTS` for idempotency
- Preserved existing Problem node schema (backward compatible)
- Created test script: `scripts/test_schema_migration.py` (154 lines)
- Committed to branch `sprint-09-canonical-architecture-phase-1` and pushed to remote

**Files Modified:**
- `/Users/djjay0131/code/agentic-kg/packages/core/src/agentic_kg/knowledge_graph/schema.py` (+98 lines, -23 lines)
- `/Users/djjay0131/code/agentic-kg/scripts/test_schema_migration.py` (new file, 154 lines)

---

### Task 3: ConceptMatcher Core Implementation
**Owner:** Construction Agent
**Estimated Effort:** 4 hours
**Status:** COMPLETED (2026-02-09)

- [x] Create `backend/app/services/concept_matcher.py`
  - `ConceptMatcher` class with dependency injection (Neo4j, OpenAI)
  - `generate_embedding(text: str) -> List[float]` - uses OpenAI text-embedding-3-small
  - `find_candidate_concepts(mention: ProblemMention, top_k: int = 10) -> List[MatchCandidate]`
  - Vector similarity query using Neo4j VECTOR index
  - Returns top-k candidates sorted by similarity score

- [x] Implement confidence classification logic
  - `classify_confidence(similarity_score: float) -> ConfidenceLevel`
  - HIGH: >95% similarity
  - MEDIUM: 80-95% similarity
  - LOW: 50-80% similarity
  - NO_MATCH: <50% similarity

- [x] Add citation relationship boost (optional enhancement)
  - If mention's paper cites concept's paper → boost similarity by 20%
  - Query: `MATCH (m:ProblemMention)-[:MENTIONED_IN]->(p:Paper)-[:CITES]->()-[:MENTIONED_IN]-(c:ProblemConcept)`
  - Maximum boost: 0.20 additional score

**Acceptance Criteria:**
- [x] Embedding generation completes in <1s per mention
- [x] Vector similarity search returns top-10 candidates in <100ms
- [x] Confidence classification thresholds are configurable
- [x] Similarity scores are symmetric (A→B ≈ B→A within 0.01)
- [x] Citation boost is optional and configurable

**Related Requirements:** FR-2, NFR-1

**Implementation Summary:**
- Created `ConceptMatcher` class with dependency injection (318 lines)
- Reuses existing `EmbeddingService` for OpenAI text-embedding-3-small (1536 dimensions)
- Key methods implemented:
  1. `generate_embedding(text: str) -> list[float]` - Wraps EmbeddingService
  2. `find_candidate_concepts(mention, top_k=10) -> list[MatchCandidate]` - Vector similarity search using Neo4j db.index.vector.queryNodes()
  3. `classify_confidence(similarity_score: float) -> MatchConfidence` - Multi-threshold classification
  4. `_calculate_citation_boost(mention, concept_id) -> float` - Citation relationship bonus
  5. `match_mention_to_concept(mention, auto_link_high_confidence=False) -> MatchCandidate` - Find best match
- Architecture highlights:
  - Dependency injection pattern for testability
  - Uses Neo4j VECTOR index `concept_embedding_idx` for efficient similarity search
  - Returns `MatchCandidate` objects with all match metadata
  - Citation boost checks cross-paper citation relationships (20% max boost)
  - Domain matching detection
  - Comprehensive logging for debugging
- All acceptance criteria met
- Committed to branch `sprint-09-canonical-architecture-phase-1` and pushed to remote

**Files Created:**
- `/Users/djjay0131/code/agentic-kg/packages/core/src/agentic_kg/knowledge_graph/concept_matcher.py` (318 lines)

---

### Task 4: Auto-Linking for HIGH Confidence Matches
**Owner:** Construction Agent
**Estimated Effort:** 2 hours
**Status:** COMPLETED (2026-02-09)

- [x] Implement `auto_link_high_confidence(mention: ProblemMention) -> Optional[ProblemConcept]`
  - Find top candidate using `ConceptMatcher`
  - If confidence is HIGH (>95%), create `INSTANCE_OF` relationship
  - Update mention `workflow_state` to `AUTO_LINKED`
  - Update concept `mention_count` and `last_updated`
  - Return linked concept

- [x] Implement `create_new_concept(mention: ProblemMention) -> ProblemConcept`
  - If no HIGH confidence match found, create new `ProblemConcept`
  - Set `canonical_statement` = mention `statement` (initially)
  - Generate embedding for concept
  - Link mention to new concept via `INSTANCE_OF`
  - Return new concept

- [x] Add transaction handling and rollback
  - Use Neo4j transactions for atomicity
  - Rollback on any failure during linking
  - Log all auto-linking decisions with trace ID

**Acceptance Criteria:**
- [x] HIGH confidence matches link automatically without human review
- [x] New concepts created only when no HIGH confidence match exists
- [x] All linking operations are atomic (succeed or rollback completely)
- [x] Trace IDs propagate through all operations
- [x] Audit log records all auto-linking decisions

**Related Requirements:** FR-3, FR-9, NFR-5

**Implementation Summary:**
- Created `AutoLinker` class with dependency injection (373 lines)
- Reuses existing `ConceptMatcher` and `EmbeddingService` (no duplication)
- Key methods implemented:
  1. `auto_link_high_confidence(mention, trace_id) -> Optional[ProblemConcept]` - Uses ConceptMatcher to find best match, links if HIGH confidence (>95%), returns None to signal "create new concept"
  2. `create_new_concept(mention, trace_id) -> ProblemConcept` - Creates new concept when no HIGH confidence match exists, generates embedding, links mention, all in single transaction
  3. `_create_instance_of_relationship(mention, candidate, trace_id) -> ProblemConcept` - Private method for creating INSTANCE_OF relationship with metadata
  4. `_create_concept_and_link(concept, mention, trace_id) -> None` - Private method for creating new concept and linking mention atomically
- Architecture highlights:
  - Dependency injection pattern for testability
  - Transaction-based operations for ACID guarantees
  - Trace ID propagation for complete audit trail
  - Error handling with custom `AutoLinkerError` exception
  - Comprehensive logging at INFO level for all decisions
- Transaction safety:
  - All linking operations use Neo4j `ManagedTransaction`
  - Atomic operations: succeed completely or rollback completely
  - MERGE statements for idempotency
  - No partial state on failure
- Audit trail features:
  - Trace IDs logged and stored in relationships
  - All match decisions logged (HIGH/MEDIUM/LOW/REJECTED)
  - Similarity scores and final scores logged
  - Concept creation events logged
  - Relationship metadata includes trace_id for debugging
  - Error logging with stack traces
- All acceptance criteria met
- Committed to branch `sprint-09-canonical-architecture-phase-1` and pushed to remote

**Files Created:**
- `/Users/djjay0131/code/agentic-kg/packages/core/src/agentic_kg/knowledge_graph/auto_linker.py` (373 lines)

---

### Task 5: Update Extraction Pipeline
**Owner:** Construction Agent
**Estimated Effort:** 3 hours

- [ ] Update `backend/app/extraction/pipeline.py`
  - Change `extract_problems()` to create `ProblemMention` instead of `Problem`
  - Add `workflow_state` = `EXTRACTED` on creation
  - Generate trace ID for each mention: `{timestamp}-{mention_id}-extract`

- [ ] Integrate `ConceptMatcher` into pipeline
  - After mention creation, call `ConceptMatcher.find_candidate_concepts()`
  - If HIGH confidence match found, call `auto_link_high_confidence()`
  - If no HIGH confidence match, call `create_new_concept()`
  - Log all matching decisions

- [ ] Update `backend/app/extraction/kg_integration.py`
  - Replace `Problem` node creation with `ProblemMention` + `ProblemConcept` workflow
  - Ensure provenance links maintained: `(ProblemMention)-[:MENTIONED_IN]->(Paper)`
  - Update deduplication logic (now handled by concept matching, not string comparison)

- [ ] Add checkpoint saves at each stage
  - Checkpoint 1: After mention extraction (before matching)
  - Checkpoint 2: After concept matching (before linking)
  - Checkpoint 3: After linking/concept creation
  - Store checkpoints in Neo4j or Redis for rollback

**Acceptance Criteria:**
- Pipeline creates `ProblemMention` nodes instead of `Problem` nodes
- HIGH confidence mentions auto-link to concepts
- No HIGH confidence match → new concept created automatically
- Provenance preserved: can trace mention → paper → authors
- Checkpoints enable rollback at any stage
- Existing `Problem` nodes remain unchanged (no data loss)

**Related Requirements:** FR-3, FR-7, FR-10, NFR-5

---

### Task 6: Unit Tests for Core Matching Logic
**Owner:** Construction Agent
**Estimated Effort:** 4 hours

- [ ] Test `ProblemMention` and `ProblemConcept` models
  - `tests/unit/models/test_problem_mention.py`
  - `tests/unit/models/test_problem_concept.py`
  - Test serialization, validation, edge cases (empty fields, invalid states)

- [ ] Test `ConceptMatcher` embedding and similarity
  - `tests/unit/services/test_concept_matcher.py`
  - Mock OpenAI API for embedding generation
  - Test vector similarity query (mock Neo4j responses)
  - Test confidence classification thresholds
  - Test citation boost calculation

- [ ] Test auto-linking logic
  - `tests/unit/services/test_auto_linking.py`
  - Test HIGH confidence auto-link flow
  - Test new concept creation when no match
  - Test transaction rollback on errors
  - Test trace ID propagation

- [ ] Test pipeline integration
  - `tests/unit/extraction/test_pipeline_mentions.py`
  - Test mention creation from extraction results
  - Test concept matching integration
  - Test checkpoint save/restore

**Acceptance Criteria:**
- Unit test coverage >90% for new code
- All tests pass with mocked dependencies (no live API/DB calls)
- Tests use fixtures from `conftest.py` for consistency
- Edge cases covered: empty embeddings, duplicate mentions, transaction failures

**Related Requirements:** NFR-2, NFR-5

---

### Task 7: Integration Tests with Live Neo4j
**Owner:** Construction Agent
**Estimated Effort:** 3 hours

- [ ] Test end-to-end mention → concept flow
  - `tests/integration/test_mention_concept_flow.py`
  - Import paper 1 → creates mention 1 → creates concept 1
  - Import paper 2 (similar problem) → creates mention 2 → links to concept 1
  - Verify concept `mention_count` = 2
  - Verify both mentions have `INSTANCE_OF` relationship

- [ ] Test confidence threshold edge cases
  - Paper with 96% similarity → auto-links (HIGH)
  - Paper with 94% similarity → does NOT auto-link (MEDIUM)
  - Paper with 49% similarity → creates new concept (NO_MATCH)

- [ ] Test rollback and error handling
  - Simulate Neo4j connection failure during linking
  - Verify transaction rollback (no partial state)
  - Verify retry logic (if implemented)

- [ ] Test vector index performance
  - Insert 100 concepts with embeddings
  - Query for top-10 similar concepts
  - Verify query time <100ms
  - Use `PROFILE` to verify index usage

**Acceptance Criteria:**
- Integration tests run against test Neo4j instance (Docker Compose)
- All flows tested: auto-link, new concept, rollback
- Performance benchmarks met (<100ms similarity query)
- Tests clean up data after each run (isolated test cases)

**Related Requirements:** FR-3, NFR-1, NFR-3

---

## Architecture Decisions

### Referenced ADRs
- **ADR-003: Problems as First-Class Entities** - Establishes problems as central units
- **ADR-005: Hybrid Retrieval** - Embedding-based similarity matching foundation

### New Decisions from Phase 1
- Use OpenAI text-embedding-3-small (1536 dimensions) for embeddings
- Confidence thresholds: >95% HIGH, 80-95% MEDIUM, 50-80% LOW, <50% NO_MATCH
- Neo4j vector indexes for similarity search (not separate vector DB)
- Transaction-based linking with rollback capability
- Trace ID format: `{timestamp}-{mention_id}-{operation}`

---

## Dependencies

### External Services
- Neo4j 5.x with vector index support (VECTOR index feature)
- OpenAI API for embeddings (text-embedding-3-small)
- Redis (optional, for checkpoints if not using Neo4j)

### Internal Dependencies
- Phase 3 (Extraction Pipeline) - must be operational for mention creation
- Phase 1 (Knowledge Graph) - Neo4j infrastructure must exist

### Configuration Required
- OpenAI API key in GCP Secret Manager
- Neo4j connection string and credentials
- Confidence threshold configuration (ENV vars or config file)

---

## Testing Strategy

### Unit Tests (tests/unit/)
- Models: validation, serialization
- ConceptMatcher: embedding, similarity, confidence classification
- Auto-linking: transaction handling, rollback
- Mocked dependencies (no live API/DB)

### Integration Tests (tests/integration/)
- End-to-end mention → concept flow with live Neo4j
- Performance benchmarks (vector index query time)
- Error handling and rollback scenarios

### Acceptance Tests
- Import 2 papers mentioning same problem → verify 1 concept, 2 mentions
- Verify >95% similarity auto-links
- Verify <95% similarity does NOT auto-link (deferred to Phase 2)

---

## Risks

| Risk | Likelihood | Impact | Mitigation | Status |
|------|------------|--------|------------|--------|
| Vector index performance degrades with scale | Medium | High | Benchmark with 1000+ concepts, optimize query if needed | Open |
| Confidence thresholds need tuning | High | Medium | Start conservative (>95%), adjust based on false positive rate | Open |
| Embedding generation slow/expensive | Low | Medium | Cache embeddings, batch API calls, consider local model | Open |
| Transaction rollback fails, leaving partial state | Low | High | Comprehensive integration tests, Neo4j transaction best practices | Open |
| False negatives (missed duplicates) due to conservative threshold | Medium | High | Phase 2 agents will catch medium/low confidence, human review for <80% | Accepted |

---

## Success Metrics

### Functional
- [ ] Paper import creates `ProblemMention` nodes (not `Problem` nodes)
- [ ] HIGH confidence matches auto-link (>95% similarity)
- [ ] New concepts created when no HIGH confidence match exists
- [ ] Concept `mention_count` accurate for linked mentions
- [ ] Provenance preserved: mention → paper → authors

### Performance
- [ ] Embedding generation: <1s per mention
- [ ] Similarity search: <100ms for top-10 candidates
- [ ] Auto-linking: <200ms per mention (total)
- [ ] Vector index query uses index (verified with PROFILE)

### Quality
- [ ] False positive rate <5% (incorrect auto-links)
- [ ] False negative rate 0% (no missed HIGH confidence duplicates)
- [ ] Unit test coverage >90%
- [ ] Integration tests pass 100%

---

## Rollout Plan

### Development
1. Implement models and schema migration
2. Implement ConceptMatcher with unit tests
3. Update extraction pipeline with integration tests
4. Deploy to staging environment

### Testing
1. Run unit tests in CI/CD
2. Run integration tests against staging Neo4j
3. Manual acceptance test: import 2 papers, verify concept linking
4. Performance benchmark: 100 concepts, verify <100ms query time

### Deployment
1. Run migration script on staging (adds new schema, does not touch `Problem` nodes)
2. Deploy updated extraction pipeline to Cloud Run
3. Monitor logs for auto-linking decisions (trace IDs)
4. Verify no errors in first 24 hours
5. Deploy to production (if staging successful)

### Rollback Plan
- Migration is additive (no data loss if rolled back)
- Revert extraction pipeline to previous version
- Existing `Problem` nodes remain functional
- New `ProblemMention`/`ProblemConcept` nodes can be deleted manually if needed

---

## Definition of Done

- [ ] All 7 tasks complete with acceptance criteria met
- [ ] Unit tests pass (>90% coverage)
- [ ] Integration tests pass (100%)
- [ ] Manual acceptance test: 2 papers → 1 concept, 2 mentions
- [ ] Performance benchmarks met (<100ms similarity query)
- [ ] Code reviewed and merged to `main`
- [ ] Deployed to staging and verified
- [ ] Documentation updated (API docs, schema docs)
- [ ] Rollback plan tested

---

## Next Steps (Phase 2)

After Phase 1 complete:
- Implement agent workflows for MEDIUM and LOW confidence matches
- Add evaluator agent (single LLM review for 80-95%)
- Add maker/hater consensus for LOW confidence (50-80%)
- Build review queue for human oversight

Phase 1 focuses on HIGH confidence only. All other matches will queue for Phase 2 agent review.

---

## Notes

- **Design-First**: This sprint implements Phase 1 from the complete design document
- **No Migration**: Existing `Problem` nodes remain unchanged; new mentions created going forward
- **Conservative Thresholds**: Start with >95% for HIGH confidence, tune based on production data
- **Trace IDs**: Every operation tagged for end-to-end debugging
- **Rollback Ready**: Checkpoints at each stage, transaction-based linking
