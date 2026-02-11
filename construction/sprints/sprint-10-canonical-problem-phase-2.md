# Sprint 10: Canonical Problem Architecture - Phase 2: Agent Workflows for MEDIUM/LOW Confidence

**Sprint Goal:** Implement agent-based review workflows for MEDIUM and LOW confidence matches that Phase 1's auto-linker cannot handle

**Start Date:** 2026-02-10
**End Date:** TBD
**Status:** In Progress

**Prerequisites:**
- Sprint 09 Phase 1 Complete (Merged): Data Model & Core Matching
- Design document complete: [canonical-problem-architecture-phase-2.md](../design/canonical-problem-architecture-phase-2.md)
- Parent design: [canonical-problem-architecture.md](../design/canonical-problem-architecture.md)

**Related ADRs:**
- ADR-003: Problems as First-Class Entities
- ADR-005: Hybrid Retrieval

---

## Overview

Phase 2 builds upon Phase 1's auto-linking by implementing agent-based workflows for matches that require intelligent review:

- **EvaluatorAgent**: Quick single-agent review for MEDIUM confidence (80-95%)
- **Maker/Hater/Arbiter Consensus**: Multi-agent debate for LOW confidence (50-80%)
- **Human Review Queue**: Database-backed queue for disputed matches
- **Concept Refinement**: Synthesize canonical statements at mention thresholds (5/10/25/50)

**Key Principle:** Near-zero false negatives. Missing a duplicate is worse than a false link.

**Success Criteria:**
- MEDIUM confidence matches processed in <5 seconds per mention
- LOW confidence matches processed in <30 seconds (3 rounds max)
- EvaluatorAgent achieves >90% agreement with human judgment on golden dataset
- Consensus workflow achieves >85% agreement with human judgment
- Human review queue maintains <7 day SLA for high priority items
- Concept refinement triggers at 5/10/25/50 mention thresholds
- False negative rate remains 0% (no missed duplicates)
- False positive rate <5% (acceptable incorrect links)

---

## Tasks

### Task 1: Agent Models and Schemas
**Owner:** Construction Agent
**Estimated Effort:** 2 hours
**Status:** COMPLETED (2026-02-11)

- [x] Create `packages/core/src/agentic_kg/agents/matching/schemas.py`
  - EvaluatorResult model with decision, confidence, reasoning, key_factors
  - MakerResult model with arguments, evidence, confidence, strongest_argument
  - HaterResult model with arguments, evidence, confidence, strongest_argument
  - ArbiterResult model with decision, confidence, reasoning, maker_weight, hater_weight, decisive_factor
  - MatchingWorkflowState TypedDict for LangGraph state management

- [x] Add new enums to `models/enums.py`
  - EscalationReason: evaluator_uncertain, consensus_failed, arbiter_low_confidence, max_rounds_exceeded
  - ReviewResolution: linked, created_new, blacklisted

- [x] Create PendingReview model in `models/entities.py`
  - Fields: id, trace_id, mention_id, mention_statement, paper_doi, domain
  - Suggested concepts list with concept_id, statement, score, reasoning
  - Agent context: escalation_reason, agent_results, rounds_attempted
  - Queue management: priority, status, assigned_to, assigned_at
  - SLA tracking: created_at, sla_deadline
  - Resolution: resolution, resolved_concept_id, resolved_by, resolved_at, resolution_notes

- [x] Unit tests for model validation and serialization

**Acceptance Criteria:**

- [x] All models serialize/deserialize correctly with `.model_dump()` and `.model_validate()`
- [x] Validators catch invalid data (empty arguments, invalid decisions)
- [x] 100% Python syntax validation (31 unit tests passing)
- [x] TypedDict properly typed for LangGraph compatibility

**Completed Files (1,260 lines total):**

- `packages/core/src/agentic_kg/agents/matching/__init__.py`
- `packages/core/src/agentic_kg/agents/matching/schemas.py` (306 lines)
- `packages/core/src/agentic_kg/agents/matching/state.py` (196 lines)
- `packages/core/tests/agents/matching/test_schemas.py` (31 tests)
- `packages/core/src/agentic_kg/knowledge_graph/models/enums.py` (updated)
- `packages/core/src/agentic_kg/knowledge_graph/models/entities.py` (updated)
- `packages/core/src/agentic_kg/knowledge_graph/models/__init__.py` (updated)

**Related Requirements:** FR-1, FR-2 from design

---

### Task 2: EvaluatorAgent Implementation
**Owner:** Construction Agent
**Estimated Effort:** 3 hours
**Status:** COMPLETED (2026-02-11)

- [x] Create `packages/core/src/agentic_kg/agents/matching/evaluator.py`
  - EvaluatorAgent class with LLM dependency injection
  - Structured JSON output parsing via Pydantic
  - evaluate() returns (state, EvaluatorResult) tuple
  - run() method for LangGraph node compatibility

- [x] Implement evaluation prompt from design
  - Full context: mention statement, domain, paper DOI
  - Candidate info: statement, domain, mention count, similarity score
  - Three decisions: APPROVE, REJECT, ESCALATE
  - System prompt emphasizes "err on side of APPROVE"

- [x] Implement `run(state: MatchingWorkflowState) -> EvaluatorResult`
  - JSON parsing via instructor library
  - Trace ID logging for every decision
  - Returns structured EvaluatorResult

- [x] Handle edge cases
  - Empty mention/candidate statement detection (raises EvaluatorError)
  - LLM timeout handling (configurable, default 10s)
  - JSON parsing errors caught and logged
  - Unknown decisions default to ESCALATE (conservative)

- [x] Unit tests with mocked LLM responses (22 tests)

**Acceptance Criteria:**

- [x] Returns EvaluatorResult in <5 seconds (measured)
- [x] Handles edge cases gracefully (no crashes)
- [x] JSON parsing errors caught and logged
- [x] Decisions logged with trace IDs for audit trail

**Completed Files:**

- `packages/core/src/agentic_kg/agents/matching/evaluator.py` (282 lines)
- `packages/core/tests/agents/matching/test_evaluator.py` (340 lines, 22 tests)
- `packages/core/src/agentic_kg/agents/matching/__init__.py` (updated exports)

**Related Requirements:** Section 3.1 of design, FR-4

---

### Task 3: Maker/Hater/Arbiter Agents
**Owner:** Construction Agent
**Estimated Effort:** 4 hours
**Status:** COMPLETED (2026-02-11)

- [x] Create `packages/core/src/agentic_kg/agents/matching/maker.py`
  - MakerAgent class with LLM dependency injection
  - Prompt argues FOR linking with 5 evidence dimensions
  - Returns MakerResult with 3-5 arguments, claims, evidence, strength
  - Acknowledges weak points honestly

- [x] Create `packages/core/src/agentic_kg/agents/matching/hater.py`
  - HaterAgent class with LLM dependency injection
  - Prompt argues AGAINST linking with 5 evidence dimensions
  - Returns HaterResult with 3-5 arguments, claims, evidence, strength
  - Fair criticism - acknowledges strong matches honestly

- [x] Create `packages/core/src/agentic_kg/agents/matching/arbiter.py`
  - ArbiterAgent class with LLM dependency injection
  - format_arguments() utility for prompt building
  - Three decisions: LINK, CREATE_NEW, RETRY
  - Confidence threshold of 0.7 (configurable constant)
  - Forces RETRY when confidence < 0.7 (non-final round)
  - Final round: defaults to LINK (conservative, avoids false negatives)

- [x] Implement shared utilities
  - format_arguments() for Arbiter prompt formatting
  - Argument Pydantic model (claim, evidence, strength)
  - Round number tracking in state (current_round, max_rounds)

- [x] Unit tests for each agent with mocked LLM (25 tests)

**Acceptance Criteria:**

- [x] Maker/Hater produce 3-5 relevant arguments each
- [x] Arbiter returns "retry" when confidence < 0.7
- [x] All agents handle LLM errors gracefully
- [x] Round number properly tracked through state
- [x] Prompts follow design specifications exactly

**Completed Files:**

- `packages/core/src/agentic_kg/agents/matching/maker.py` (270 lines)
- `packages/core/src/agentic_kg/agents/matching/hater.py` (268 lines)
- `packages/core/src/agentic_kg/agents/matching/arbiter.py` (360 lines)
- `packages/core/tests/agents/matching/test_consensus_agents.py` (350 lines, 25 tests)
- `packages/core/src/agentic_kg/agents/matching/__init__.py` (updated exports)

**Related Requirements:** Sections 3.2, 3.3, 3.4 of design, FR-5

---

### Task 4: LangGraph Workflow
**Owner:** Construction Agent
**Estimated Effort:** 4 hours
**Status:** COMPLETED (2026-02-11)

- [x] Create `packages/core/src/agentic_kg/agents/matching/workflow.py`
  - `build_matching_workflow()` function returning compiled StateGraph
  - MemorySaver checkpointing with trace_id as thread_id
  - Singleton pattern with `get_matching_workflow()`

- [x] Implement node functions (7 total)
  - `create_evaluator_node()` - runs EvaluatorAgent
  - `create_maker_node()` - runs MakerAgent, increments round
  - `create_hater_node()` - runs HaterAgent
  - `create_arbiter_node()` - runs ArbiterAgent, tracks consensus
  - `create_link_node()` - marks decision as LINKED
  - `create_new_node()` - marks decision as CREATED_NEW
  - `create_human_review_node()` - escalates with reason

- [x] Implement routing functions (3 total)
  - `route_by_confidence()` - MEDIUM→evaluator, LOW→maker
  - `route_evaluator_decision()` - approve/reject/escalate routing
  - `route_arbiter_decision()` - link/create_new/retry/human routing

- [x] Implement retry logic
  - MAX_CONSENSUS_ROUNDS = 3 (configurable constant)
  - After 3 rounds → human_review escalation
  - Round count tracked via maker_node increment

- [x] State module (created in Task 1)
  - MatchingWorkflowState TypedDict with all fields
  - Helper functions: create_matching_state, add_matching_message, etc.

- [x] Unit tests for workflow (20 tests)
  - Routing function tests
  - Node function tests
  - Singleton tests

**Acceptance Criteria:**

- [x] MEDIUM confidence routes to Evaluator first
- [x] LOW confidence routes directly to Maker/Hater/Arbiter
- [x] Retry works up to 3 rounds
- [x] 3 failed rounds escalate to human queue
- [x] Checkpoints enable resume after failure
- [x] All paths tested with unit tests

**Completed Files:**

- `packages/core/src/agentic_kg/agents/matching/workflow.py` (380 lines)
- `packages/core/tests/agents/matching/test_workflow.py` (280 lines, 20 tests)
- `packages/core/src/agentic_kg/agents/matching/__init__.py` (updated exports)

**Related Requirements:** Section 4 of design, FR-6

---

### Task 5: Human Review Queue Service
**Owner:** Construction Agent
**Estimated Effort:** 4 hours
**Status:** COMPLETED (2026-02-11)

- [x] Create `packages/core/src/agentic_kg/knowledge_graph/review_queue.py`
  - ReviewQueueService class with Neo4jRepository dependency injection
  - Singleton pattern with get_review_queue_service()

- [x] Implement Neo4j storage for PendingReview nodes
  - Creates PendingReview nodes with all properties
  - Creates REVIEWS relationship to ProblemMention
  - Stores agent context and suggested concepts as JSON

- [x] Implement queue operations
  - `enqueue()` - Add to queue with auto priority/SLA
  - `get_pending()` - Query with filters, sorted by priority ASC
  - `get_by_id()` / `get_by_mention_id()` - Get specific review
  - `count_pending()` - Count with filters
  - `assign()` / `unassign()` - Assignment management
  - `resolve()` - Resolve with LINKED/CREATED_NEW/BLACKLISTED

- [x] Implement priority calculation
  - Base: 5, Confidence: +(1-score)*5, Domain: -1 for NLP/ML/CV
  - Clamped to [1, 10]

- [x] Implement SLA deadline calculation
  - Priority 1-3: 24h, 4-6: 168h, 7-10: 720h

- [x] Unit tests with mocked Neo4j repository (20 tests)

**Acceptance Criteria:**

- [x] Reviews stored and queryable in Neo4j
- [x] Priority ordering works correctly (1=highest)
- [x] SLA deadlines calculated per priority tier
- [x] Agent context (all agent results) preserved
- [x] Query supports filters (priority, domain)

**Completed Files:**

- `packages/core/src/agentic_kg/knowledge_graph/review_queue.py` (480 lines)
- `packages/core/tests/knowledge_graph/test_review_queue.py` (280 lines, 20 tests)

**Related Requirements:** Section 5 of design, FR-7

---

### Task 6: Review Queue API Endpoints
**Owner:** Construction Agent
**Estimated Effort:** 3 hours
**Status:** Not Started

- [ ] Create `packages/api/src/agentic_kg_api/routers/reviews.py`
  - FastAPI router for review queue endpoints

- [ ] Implement GET /reviews/pending
  - Query parameters: limit, priority, domain
  - Returns list of PendingReviewResponse
  - Sorted by priority ASC, sla_deadline ASC

- [ ] Implement GET /reviews/{review_id}
  - Returns PendingReviewDetailResponse
  - Includes full agent debate context
  - Includes suggested concepts with reasoning

- [ ] Implement POST /reviews/{review_id}/resolve
  - Request body: ReviewResolutionRequest (decision, concept_id, notes)
  - Requires authentication
  - Returns updated PendingReviewResponse

- [ ] Implement POST /reviews/{review_id}/assign
  - Assigns review to current user
  - Updates assigned_to and assigned_at
  - Returns updated PendingReviewResponse

- [ ] Add authentication/authorization
  - Require authenticated user for resolve/assign
  - Log user ID for audit trail

- [ ] API tests for all endpoints

**Acceptance Criteria:**
- All endpoints return correct data shapes
- Authorization required for resolve/assign operations
- Validation errors return 400 with details
- Audit logging for all mutations (who/when/what)
- OpenAPI documentation generated

**Related Requirements:** Section 5.4 of design, FR-8

---

### Task 7: Concept Refinement Service
**Owner:** Construction Agent
**Estimated Effort:** 3 hours
**Status:** Not Started

- [ ] Create `packages/core/src/agentic_kg/knowledge_graph/concept_refinement.py`
  - ConceptRefinementService class with dependency injection

- [ ] Implement threshold checking
  - Refinement thresholds: 5, 10, 25, 50 mentions
  - Track `last_refined_at_count` in concept metadata
  - Skip if already refined at current threshold

- [ ] Implement human-edited protection
  - Check `human_edited` flag on concept
  - Never auto-refine human-edited concepts
  - Log skipped refinements

- [ ] Implement synthesis prompt
  - Gather all mentions for concept
  - LLM synthesizes best canonical statement
  - 1-2 sentences, general but specific
  - Avoids paper-specific details

- [ ] Implement refinement execution
  - Update canonical_statement
  - Set synthesis_method = "synthesized"
  - Set synthesized_at = current timestamp
  - Set synthesized_by = "refinement_agent"
  - Increment version

- [ ] Integration with linking flow
  - `check_and_refine(concept_id, trace_id)` callable after linking
  - Returns refined concept or None if skipped

- [ ] Unit tests with mocked LLM

**Acceptance Criteria:**
- Refinement triggers at correct thresholds (5, 10, 25, 50)
- Human-edited concepts never auto-refined
- Version incremented on each refinement
- synthesis_method updated to "synthesized"
- All refinements logged with trace IDs

**Related Requirements:** Section 6 of design, FR-9

---

### Task 8: Integration with KGIntegratorV2
**Owner:** Construction Agent
**Estimated Effort:** 3 hours
**Status:** Not Started

- [ ] Update `kg_integration_v2.py` to call agent workflow
  - Import matching workflow module
  - Add `_process_agent_workflow()` method

- [ ] Implement routing logic
  - HIGH confidence: existing auto-linker (Phase 1)
  - MEDIUM confidence: EvaluatorAgent workflow
  - LOW confidence: Maker/Hater/Arbiter workflow
  - NO_MATCH: create new concept

- [ ] Add workflow invocation
  - Create initial MatchingWorkflowState
  - Invoke workflow with trace ID as thread_id
  - Handle workflow results (link/create_new/human_review)

- [ ] Ensure proper error handling
  - Workflow failures logged with trace IDs
  - Failed workflows don't block pipeline
  - Errors captured in MentionIntegrationResult

- [ ] Integration tests with full pipeline
  - Test MEDIUM confidence routes correctly
  - Test LOW confidence routes correctly
  - Test human queue escalation works

**Acceptance Criteria:**
- MEDIUM/LOW confidence routes to agent workflow
- HIGH confidence still uses Phase 1 auto-linker
- Errors logged with trace IDs
- End-to-end integration test passes
- No regression on Phase 1 functionality

**Related Requirements:** Section 8 of design, FR-10

---

### Task 9: Unit Tests for All Agents
**Owner:** Construction Agent
**Estimated Effort:** 4 hours
**Status:** Not Started

- [ ] Create `tests/agents/matching/test_evaluator.py`
  - Test approve for high similarity cases
  - Test reject for different scope cases
  - Test escalate for uncertain cases
  - Test error handling for LLM failures

- [ ] Create `tests/agents/matching/test_maker.py`
  - Test argument generation
  - Test evidence extraction
  - Test confidence scoring
  - Test edge cases (minimal context)

- [ ] Create `tests/agents/matching/test_hater.py`
  - Test counter-argument generation
  - Test evidence for differences
  - Test honesty when match is strong
  - Test edge cases

- [ ] Create `tests/agents/matching/test_arbiter.py`
  - Test decision with high confidence
  - Test retry when confidence < 0.7
  - Test weight calculation
  - Test decisive factor identification

- [ ] Create `tests/agents/matching/test_workflow.py`
  - Test MEDIUM routes to Evaluator
  - Test LOW routes to consensus
  - Test retry logic (up to 3 rounds)
  - Test human queue escalation

- [ ] Create `tests/knowledge_graph/test_review_queue.py`
  - Test enqueue with priority calculation
  - Test get_pending with filters
  - Test resolve with state updates
  - Test SLA deadline calculation

- [ ] Create `tests/knowledge_graph/test_concept_refinement.py`
  - Test threshold detection
  - Test human-edited protection
  - Test synthesis prompt
  - Test version increment

**Acceptance Criteria:**
- Unit test coverage >90% for new code
- All tests pass with mocked dependencies
- Tests use pytest fixtures for consistency
- Edge cases covered: empty inputs, timeouts, failures
- All agents tested in isolation

**Related Requirements:** Section 9.1 of design, NFR-2

---

### Task 10: Integration Tests with Live Neo4j
**Owner:** Construction Agent
**Estimated Effort:** 4 hours
**Status:** Not Started

- [ ] Create `tests/integration/test_phase2_workflow.py`
  - Test MEDIUM confidence end-to-end (85% match -> Evaluator -> linked)
  - Test LOW confidence consensus (65% match -> debate -> decision)
  - Test human queue creation (disputed match -> queue)
  - Test concept refinement at 5th mention

- [ ] Create golden dataset for validation
  - MEDIUM confidence cases (10+ examples)
  - LOW confidence cases (10+ examples)
  - Expected decisions for each case

- [ ] Implement accuracy measurement
  - Run Evaluator on golden dataset
  - Calculate agreement with expected decisions
  - Assert >90% accuracy for Evaluator
  - Assert >85% accuracy for consensus

- [ ] Performance tests
  - Evaluator completes in <5 seconds
  - Single consensus round completes in <15 seconds
  - Review queue query completes in <100ms

- [ ] Document tuning decisions
  - Record any prompt adjustments
  - Track accuracy improvements over iterations

**Acceptance Criteria:**
- Evaluator achieves >90% accuracy on golden dataset
- Consensus achieves >85% accuracy on golden dataset
- False negative rate 0% (verified on golden dataset)
- False positive rate <5% (verified on golden dataset)
- Performance benchmarks met
- Tests clean up after each run

**Related Requirements:** Sections 9.2, 9.3, 9.4 of design, NFR-1, NFR-3

---

## Architecture Decisions

### Referenced ADRs
- **ADR-003: Problems as First-Class Entities** - Establishes problems as central units
- **ADR-005: Hybrid Retrieval** - Embedding-based similarity matching foundation

### New Decisions from Phase 2
- EvaluatorAgent uses gpt-4o for speed (switch to claude-3-5-sonnet if accuracy issues)
- Best-of-3 consensus protocol with Maker/Hater/Arbiter
- Arbiter confidence threshold of 0.7 for final decision
- Simple Neo4j-based queue (no external ticketing system)
- Threshold-based refinement at 5/10/25/50 mentions
- Human-edited concepts never auto-refined
- Priority-based SLA: 24h/7d/30d for high/medium/low priority

---

## Dependencies

### External Services
- Neo4j 5.x with vector index support (from Phase 1)
- OpenAI API for embeddings (from Phase 1)
- LLM API for agents (gpt-4o or claude-3-5-sonnet)
- LangGraph for workflow orchestration

### Internal Dependencies
- Sprint 09 Phase 1 (Complete) - ConceptMatcher, AutoLinker
- KGIntegratorV2 for pipeline integration
- Existing test infrastructure

### Configuration Required
- LLM API key in GCP Secret Manager (may be same as embedding key)
- Confidence thresholds (configurable, defaults from design)
- SLA hour configuration (configurable, defaults: 24/168/720)

---

## Testing Strategy

### Unit Tests (tests/agents/matching/, tests/knowledge_graph/)
- All 4 agents: evaluator, maker, hater, arbiter
- LangGraph workflow paths
- Review queue operations
- Concept refinement logic
- Mocked LLM responses (no live API calls)

### Integration Tests (tests/integration/)
- End-to-end MEDIUM confidence flow
- End-to-end LOW confidence flow
- Human queue escalation
- Concept refinement triggers
- Live Neo4j required

### Acceptance Tests (Golden Dataset)
- Evaluator accuracy >90%
- Consensus accuracy >85%
- False negative rate 0%
- False positive rate <5%

### Performance Tests
- Evaluator <5 seconds
- Consensus round <15 seconds
- Review queue query <100ms

---

## Risks

| Risk | Likelihood | Impact | Mitigation | Status |
|------|------------|--------|------------|--------|
| LLM accuracy insufficient for agents | Medium | High | Start with gpt-4o, tune prompts, switch to claude if needed | Open |
| Consensus debate produces inconsistent results | Medium | Medium | 3-round retry, human escalation as fallback | Open |
| Review queue grows faster than human review capacity | Low | Medium | Priority-based SLA, batch processing tools | Open |
| Concept refinement changes meaning unexpectedly | Low | High | Human-edited protection, version history | Open |
| LangGraph workflow complexity leads to bugs | Medium | Medium | Comprehensive testing, checkpointing for debug | Open |

---

## Success Metrics

### Functional
- [ ] MEDIUM confidence routes to EvaluatorAgent
- [ ] LOW confidence routes to Maker/Hater/Arbiter consensus
- [ ] Failed consensus escalates to human queue after 3 rounds
- [ ] Human queue accessible via API endpoints
- [ ] Concept refinement triggers at 5/10/25/50 mentions
- [ ] Human-edited concepts protected from auto-refinement

### Performance
- [ ] Evaluator decision: <5 seconds
- [ ] Consensus (3 rounds max): <30 seconds
- [ ] Queue query: <100ms

### Quality
- [ ] Evaluator accuracy: >90% on golden dataset
- [ ] Consensus accuracy: >85% on golden dataset
- [ ] False negative rate: 0%
- [ ] False positive rate: <5%
- [ ] Unit test coverage: >90%
- [ ] Integration tests: 100% pass rate

---

## Rollout Plan

### Development
1. Implement agent schemas and models (Task 1)
2. Implement EvaluatorAgent with unit tests (Task 2)
3. Implement Maker/Hater/Arbiter agents (Task 3)
4. Build LangGraph workflow (Task 4)
5. Build review queue service (Task 5)
6. Build API endpoints (Task 6)
7. Build concept refinement (Task 7)
8. Integrate with pipeline (Task 8)
9. Complete unit tests (Task 9)
10. Run integration and golden dataset tests (Task 10)

### Testing
1. Run unit tests in CI/CD (all agents, queue, refinement)
2. Run integration tests against staging Neo4j
3. Run golden dataset validation (Evaluator >90%, Consensus >85%)
4. Manual acceptance: import papers with 60-95% similarity, verify routing

### Deployment
1. Deploy to staging with new agent modules
2. Test MEDIUM/LOW confidence routing works
3. Verify review queue populates correctly
4. Monitor LLM costs and latency
5. Deploy to production (if staging successful)

### Rollback Plan
- Phase 2 is additive (Phase 1 auto-linker still works)
- Disable agent workflow by routing all MEDIUM/LOW to create new concept
- Review queue can be purged if needed
- No data loss on rollback

---

## Definition of Done

- [ ] All 10 tasks complete with acceptance criteria met
- [ ] Unit test coverage >90% for new code
- [ ] Integration tests pass 100%
- [ ] Golden dataset validation: Evaluator >90%, Consensus >85%
- [ ] Performance: Evaluator <5s, Consensus <30s, Queue query <100ms
- [ ] Review queue accessible via API
- [ ] Concept refinement triggers at thresholds
- [ ] Code reviewed and merged to main
- [ ] Documentation updated (API docs, workflow docs)

---

## Open Questions

**Q1: LLM Model Selection**
- Should Evaluator use gpt-4o (faster) or claude-3-5-sonnet (better reasoning)?
- **Recommendation:** Start with gpt-4o for speed, switch if accuracy issues

**Q2: Batch Processing**
- Should we batch multiple mentions through workflow for efficiency?
- **Recommendation:** Single mention initially, optimize later if needed

**Q3: Agent Prompt Versioning**
- How to track prompt versions for reproducibility?
- **Recommendation:** Store prompt version in agent_results metadata

---

## Notes

- **Builds on Phase 1:** This sprint extends the canonical architecture with intelligent agents
- **Near-Zero False Negatives:** The guiding principle - missing duplicates is worse than over-linking
- **Human in the Loop:** All disputed matches go to human review, not automatic rejection
- **Conservative Escalation:** When in doubt, escalate rather than reject
- **Trace IDs:** Every operation tagged for end-to-end debugging
