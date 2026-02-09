# Canonical Problem Architecture - Requirements

**Created:** 2026-02-09
**Status:** Complete
**Related Design:** [canonical-problem-architecture.md](../design/canonical-problem-architecture.md)
**Related ADRs:** ADR-003, ADR-005

---

## Executive Summary

Transform the knowledge graph from storing duplicate problem nodes to a canonical problem architecture where:
- Each paper creates `ProblemMention` nodes preserving original context
- Mentions are matched to canonical `ProblemConcept` nodes
- System enables aggregated research tracking, consensus tracking, and logical graph traversal

---

## Business Goals

1. **Eliminate duplicate problems**: Single canonical concept with multiple mentions instead of 50 duplicate nodes
2. **Preserve provenance**: Show how different papers framed the same problem
3. **Enable consensus tracking**: "15 papers agree this problem is important"
4. **Support research discovery**: Flow logically from problem to problem across literature

---

## User Stories

### US-1: As a researcher, I want to see all papers working on the same problem
**Acceptance Criteria:**
- Query returns canonical concept with list of all mentions
- Each mention shows original paper statement and source
- Displays paper count and mention count
- Response time <500ms

### US-2: As a researcher, I want to trust the system's problem matching
**Acceptance Criteria:**
- False positive rate <5% (incorrectly linked problems)
- False negative rate 0% (no missed duplicates)
- Every match shows confidence score and reasoning
- Can reject wrong matches with one click

### US-3: As a researcher, I want to understand consensus around problems
**Acceptance Criteria:**
- Shows how many papers mention each concept
- Displays temporal progression (first/last mention year)
- Highlights conflicting information (different baseline values)
- Shows which papers agree/disagree

### US-4: As a curator, I want to review uncertain matches before they're approved
**Acceptance Criteria:**
- Low-confidence matches queue for human review
- Review queue sorted by priority (lowest confidence first)
- Side-by-side comparison of mention and candidate concept
- One-click approve/reject/blacklist actions

### US-5: As a curator, I want to correct mistakes in the knowledge graph
**Acceptance Criteria:**
- Can reject incorrect matches with reason
- Can request re-matching with different thresholds
- Rejected pairs added to blacklist (never suggested again)
- Full audit trail of all decisions

### US-6: As a system administrator, I want to debug production issues quickly
**Acceptance Criteria:**
- End-to-end trace IDs for every request
- Debug Docker instance can be spun up with Neo4j access
- Comprehensive logging at each pipeline stage
- Can rollback changes if needed

---

## Functional Requirements

### FR-1: Dual Entity Model

**Requirement:** System must support separate ProblemMention and ProblemConcept entities

**Details:**
- **ProblemMention**: Paper-specific, preserves original statement and context
- **ProblemConcept**: Canonical representation, AI-synthesized from all mentions
- Relationship: `(ProblemMention)-[:INSTANCE_OF]->(ProblemConcept)`

**Acceptance Criteria:**
- Paper import creates ProblemMention nodes (not Problem nodes)
- First mention auto-creates new ProblemConcept
- Subsequent mentions link to existing concepts or create new ones
- Both entity types queryable via API

### FR-2: Embedding-Based Matching

**Requirement:** System must find similar problems using vector embeddings

**Details:**
- Generate 1536-dimensional embeddings for all mentions and concepts
- Use cosine similarity for matching
- Support vector similarity search in Neo4j
- Thresholds: >95% = high, 80-95% = medium, 50-80% = low, <50% = no match

**Acceptance Criteria:**
- Embedding generation <1s per mention
- Similarity search returns top-10 candidates in <100ms
- Similarity scores are symmetric (A→B = B→A)
- Citation relationships boost similarity by 10%

### FR-3: Multi-Threshold Workflow

**Requirement:** System must route matches to appropriate review based on confidence

**Workflow:**
```
High confidence (>95%) → Auto-link
Medium confidence (80-95%) → Single agent review
Low confidence (50-80%) → Multi-agent consensus
No match (<50%) → Create new concept
```

**Acceptance Criteria:**
- High-confidence matches auto-link without human review
- Medium-confidence reviewed by evaluator agent (<10s)
- Low-confidence goes through maker/hater debate (<30s)
- Review queue shows pending items with priority scoring

### FR-4: Agent Consensus System

**Requirement:** Low-confidence matches must use maker/hater model for consensus

**Details:**
- **Maker agent**: Argues FOR the match
- **Hater agent**: Argues AGAINST the match
- **Consensus agent**: Weighs both perspectives and decides
- System must achieve <5% false positive, 0% false negative

**Acceptance Criteria:**
- Maker and hater agents provide opposing arguments
- Consensus agent provides final decision with confidence score
- Decision reasoning is transparent and logged
- Failed consensus escalates to human review

### FR-5: Canonical Statement Synthesis

**Requirement:** System must generate canonical statements from multiple mentions

**Details:**
- AI-synthesized statement captures essence of all mentions
- Aggregates metadata (assumptions, constraints, datasets, metrics)
- Validates baselines before marking as "verified"
- Human override capability for canonical statements

**Acceptance Criteria:**
- Synthesis completes in <5s per concept
- Canonical statement is 1-2 sentences, clear and concise
- Aggregated metadata includes provenance (which paper contributed what)
- Human edits set `human_edited` flag to prevent auto re-synthesis

### FR-6: Review Queue Management

**Requirement:** System must provide efficient queue for human review of uncertain matches

**Details:**
- Hybrid Neo4j + Redis architecture
- Priority scoring based on confidence, citation count, domain importance
- SLA tracking with escalation (7 days → high priority)
- Batch operations for high-confidence items

**Acceptance Criteria:**
- Queue query returns results in <50ms
- Priority sorted (lowest confidence first)
- Shows side-by-side comparison of mention and candidate concept
- Bulk approve/reject operations supported
- SLA breaches trigger automatic escalation

### FR-7: Workflow State Management

**Requirement:** System must track work items through complete matching workflow

**States:**
- EXTRACTED → MATCHING → HIGH/MEDIUM/LOW/NO_MATCH
- HIGH_CONFIDENCE → AUTO_LINKED
- MEDIUM_CONFIDENCE → AGENT_REVIEW → APPROVED/NEEDS_CONSENSUS
- LOW_CONFIDENCE → PENDING_REVIEW
- PENDING_REVIEW → APPROVED/REJECTED/BLACKLISTED

**Acceptance Criteria:**
- Every work item has trace ID for end-to-end tracking
- State transitions are validated (no invalid transitions)
- Checkpoints saved at each major stage
- Failed transitions rollback to previous checkpoint
- Stuck items (>1 hour) automatically retry or escalate

### FR-8: Blacklist System

**Requirement:** System must permanently block incorrect mention-concept pairs

**Details:**
- When human rejects a match, option to add to blacklist
- Blacklisted pairs never suggested again by matching algorithm
- Blacklist includes reason and who blacklisted it
- Blacklist is permanent (not time-limited)

**Acceptance Criteria:**
- Blacklist entries stored in Neo4j
- Matching algorithm filters blacklisted candidates
- Blacklist UI shows all entries with search/filter
- Can view but not delete blacklist entries (append-only)

### FR-9: Trace IDs and Debugging

**Requirement:** System must support end-to-end request tracing for debugging

**Details:**
- Trace ID format: `{timestamp}-{mention_id}-{operation}`
- Propagated through all agents, API calls, database operations
- Logged in all operations
- Queryable for full request history

**Acceptance Criteria:**
- Every request generates or receives trace ID
- Trace ID in HTTP headers (X-Trace-ID)
- All log entries include trace ID
- Can query all operations for a specific trace ID
- Debug runbook documents how to use trace IDs

### FR-10: Draft Saves and Rollback

**Requirement:** System must support draft states and rollback capabilities

**Details:**
- Checkpoint at each workflow stage (extraction, matching, review, commit)
- Draft saves stored with work item
- Final commit only after approval
- Rollback restores to last checkpoint

**Acceptance Criteria:**
- Checkpoints include full work item state
- Can rollback single mention or batch of mentions
- Rollback preserves audit trail (logs rollback action)
- Draft state not visible in production queries

---

## Non-Functional Requirements

### NFR-1: Performance

| Operation | Target | Max Acceptable |
|-----------|--------|----------------|
| Concept matching | <100ms per mention | <200ms |
| Review queue query | <50ms | <100ms |
| Full extraction pipeline | <30s per paper | <60s |
| Agent consensus | <10s per decision | <30s |
| Synthesis | <5s per concept | <15s |
| API response time (p95) | <500ms | <1s |

### NFR-2: Accuracy

- **False positive rate**: <5% (incorrectly linked problems)
- **False negative rate**: 0% (missed correct links - MUST be zero)
- **Overall accuracy**: >95%
- **Consensus agreement**: 4/5 runs must agree (80% threshold)

### NFR-3: Scalability

- Support 10,000+ problems without performance degradation
- Handle 100 concurrent matching operations
- Review queue supports 1,000+ pending items
- Neo4j vector indexes for fast similarity search

### NFR-4: Security

- **Authentication**: API key for programmatic access, OAuth2 for web UI
- **Authorization**: RBAC with viewer/editor/admin roles
- **Audit logging**: All mutations logged with user ID and timestamp
- **Secrets management**: All credentials in GCP Secret Manager
- **Prompt injection protection**: Never concatenate user input into LLM prompts

### NFR-5: Reliability

- **Checkpointing**: Save state before each critical operation
- **Retry logic**: Automatic retry up to 3 times for transient failures
- **Rollback**: Can undo changes with audit trail preservation
- **SLA tracking**: Items in queue >7 days escalated automatically

### NFR-6: Observability

- **Logging**: Structured JSON logs with trace IDs
- **Metrics**: Track false positive/negative rates, throughput, latency
- **Alerting**: SLA breaches, stuck work items, high error rates
- **Debug access**: Admin can spin up debug Docker instance

---

## Test Domain

**Domain:** Computer Science - Knowledge Graph Retrieval

**Test Data:**
- 1 seed paper + 2 hops of citations
- Manually identified ground truth concepts
- Expected mention count and links

**Success Criteria:**
- Import papers and verify correct concept linking
- Measure false positive/negative rates
- Query performance meets targets
- Review queue operates correctly

---

## Out of Scope (Future Work)

1. **Migration from existing Problem nodes**: Start with clean slate, no migration tooling
2. **Disaster recovery plan**: Important but designed separately
3. **Multi-language support**: English only for MVP
4. **Real-time collaboration**: Single user review queue for MVP
5. **Metrics and monitoring dashboard**: Separate phase after MVP

---

## Dependencies

- Neo4j with vector index support
- OpenAI API for embeddings and LLM calls
- Redis for review queue
- GCP Secret Manager for credentials
- LangGraph for agent workflows

---

## Definition of Done

A feature is considered done when:

1. **Functionality Complete**
   - All acceptance criteria met
   - Both auto-linking and human review workflows work
   - API endpoints return correct data

2. **Quality Verified**
   - False positive rate <5%, false negative rate 0%
   - Performance benchmarks met
   - Golden dataset tests passing (4/5 consensus)

3. **Operational Ready**
   - Logging and trace IDs implemented
   - Debug runbook documented
   - Rollback capability tested

4. **User Validated**
   - Logical graph traversal verified
   - Can easily find important problems in test domain
   - Review queue is usable and efficient

5. **Documented**
   - API documentation updated
   - User guide for review queue
   - Admin guide for debugging

---

## Success Metrics

### MVP Success (Phase 1-3 Complete)

- [ ] Paper import creates ProblemMentions linked to concepts
- [ ] High-confidence matches auto-link (>95% similarity)
- [ ] Review queue shows pending medium/low-confidence matches
- [ ] False positive <5%, false negative 0%
- [ ] Queries complete in <500ms

### Full Success (All Phases Complete)

- [ ] All agents (evaluator, maker, hater, consensus, synthesis) operational
- [ ] Human review workflow complete with UI
- [ ] Golden dataset tests pass (20 test cases, 4/5 consensus)
- [ ] Performance benchmarks met
- [ ] Security requirements implemented
- [ ] Operational tooling (debug runbook, rollback, blacklist) working

### Long-Term Success

- [ ] Research validation: Can discover important problems via graph traversal
- [ ] User satisfaction: Researchers trust the matching quality
- [ ] System stability: <1% error rate in production
- [ ] Knowledge growth: Concept count grows linearly with papers (not exponentially)

---

## Constraints

1. **No tolerance for false negatives**: Missing duplicates defeats the purpose
2. **Baselines must be proven**: No unverified baseline claims
3. **Security first**: Prompt injection protection required
4. **Design before implementation**: This architecture replaces existing system
5. **Start fresh**: No migration tooling, clean slate approach

---

## References

- [Design Document](../design/canonical-problem-architecture.md)
- [ADR-003](../../memory-bank/architecturalDecisions.md): Problems as First-Class Entities
- [ADR-005](../../memory-bank/architecturalDecisions.md): Hybrid Retrieval
- [Sprint 09](../sprints/sprint-09-canonical-problem-architecture.md) (to be created)
