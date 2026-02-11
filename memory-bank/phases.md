# Phase Lifecycle

**Last Updated:** 2026-02-10 (Sprint 10 created)

This file serves as the **coordination hub** between the memory-bank and construction folders. It tracks the lifecycle of each project phase and provides handoff signals between agents.

---

## Phase Registry

| Phase | Status | Design Doc | Sprint | ADRs | Implementation Ready |
|-------|--------|------------|--------|------|---------------------|
| 0: Infrastructure | Complete | N/A | [sprint-00](../construction/sprints/sprint-00-gcp-deployment.md) | ADR-004, ADR-008, ADR-009 | N/A |
| 1: Knowledge Graph | Complete (Merged) | [phase-1-knowledge-graph.md](../construction/design/phase-1-knowledge-graph.md) | [sprint-01](../construction/sprints/sprint-01-knowledge-graph.md) | ADR-010, ADR-011 | N/A |
| 2: Data Acquisition | Complete (Merged) | - | [sprint-02](../construction/sprints/sprint-02-data-acquisition.md) | ADR-012 | N/A |
| 3: Extraction Pipeline | Complete (Merged) | [extraction-pipeline-requirements.md](../construction/requirements/extraction-pipeline-requirements.md) | [sprint-03](../construction/sprints/sprint-03-extraction-pipeline.md) | ADR-013 | N/A |
| 4: API + Web UI | Complete (Merged) | - | [sprint-04](../construction/sprints/sprint-04-api-and-ui.md) | ADR-014 | N/A |
| 5: Agent Implementation | Complete (Merged) | - | [sprint-05](../construction/sprints/sprint-05-agent-implementation.md) | - | N/A |
| 6: Full-Stack Integration | Complete (Merged) | - | [sprint-06](../construction/sprints/sprint-06-integration.md) | ADR-014 | N/A |
| 7: End-to-End Testing | Complete (Merged) | - | [sprint-07](../construction/sprints/sprint-07-e2e-testing.md) | - | N/A |
| 8: Canonical Problem Architecture | Design Complete | [canonical-problem-architecture.md](../construction/design/canonical-problem-architecture.md) | [sprint-09](../construction/sprints/sprint-09-canonical-problem-phase-1.md) | ADR-003, ADR-005 | Yes |
| 9: Agent Workflows (Phase 2) | Not Started | [canonical-problem-architecture-phase-2.md](../construction/design/canonical-problem-architecture-phase-2.md) | [sprint-10](../construction/sprints/sprint-10-canonical-problem-phase-2.md) | ADR-003, ADR-005 | Yes |

### Administrative Agents (Cross-Cutting)

| Agent | Status | Design Doc | Implementation | Ready |
|-------|--------|------------|----------------|-------|
| memory-agent | Implementation Started | [memory-agent.md](../construction/design/memory-agent.md) | [.claude/agents/memory-agent.md](../.claude/agents/memory-agent.md) | Yes |
| construction-agent | Implementation Started | [construction-agent.md](../construction/design/construction-agent.md) | [.claude/agents/construction-agent.md](../.claude/agents/construction-agent.md) | Yes |

### Code Review System (Cross-Cutting)

| Agent | Type | Description | Ready |
|-------|------|-------------|-------|
| code-review-agent | Orchestrator | Dispatches to specialists, aggregates results | Yes |
| security-reviewer | Specialist | Injection, auth, crypto, data exposure | Yes |
| quality-reviewer | Specialist | Style, complexity, duplication, error handling | Yes |
| performance-reviewer | Specialist | Algorithms, N+1 queries, memory, caching | Yes |
| architecture-reviewer | Specialist | SOLID, patterns, coupling, layering | Yes |
| test-reviewer | Specialist | Coverage, test quality, flaky tests | Yes |
| docs-reviewer | Specialist | Docstrings, comments, README, API docs | Yes |
| review-reader | Action | Deep investigation of flagged issues | Yes |
| review-suggester | Action | Multi-pass fix generation | Yes |
| review-applier | Action | Apply approved fixes, verify tests | Yes |

**Design Doc:** [code-review-system.md](../construction/design/code-review-system.md)
**Location:** `.claude/agents/code-review/`

### Status Legend
- **Not Started**: Phase not yet begun
- **Design In Progress**: Construction-agent working on design docs
- **Design Complete**: Design docs ready, awaiting implementation
- **Implementation In Progress**: Active development
- **Complete**: Phase fully implemented and verified

---

## Phase Details

### Phase 0: Infrastructure Setup
- **Objective**: Deploy Denario to GCP Cloud Run
- **Key Deliverables**: CI/CD pipeline, Cloud Build triggers, Secret Manager config
- **Completion Date**: 2025-12-22
- **Notes**: Foundation for all subsequent phases

### Phase 1: Knowledge Graph Foundation
- **Objective**: Set up Neo4j with Problem entity schema and hybrid retrieval
- **Key Deliverables**: Neo4j Docker setup, Pydantic models, CRUD operations, vector indexing
- **Design Status**: Complete
- **Implementation Status**: Complete (Ready for Merge)
- **Branch**: `claude/problem-schema-design-SqUnQ`
- **Completed**: All 11 tasks, 5 user stories (US-01 through US-05), 221 tests
- **Deferred Items**: See `construction/backlog/sprint-01-deferred.md`
- **Blocking Issues**: None

### Phase 2: Data Acquisition Layer
- **Objective**: Ingest papers from academic sources (Semantic Scholar, arXiv, OpenAlex)
- **Key Deliverables**: API clients, rate limiting, caching, paper ingestion pipeline
- **Design Status**: Complete
- **Implementation Status**: Complete (Ready for Merge)
- **Branch**: `claude/sprint-02-data-acquisition`
- **Sprint**: [sprint-02-data-acquisition.md](../construction/sprints/sprint-02-data-acquisition.md)
- **Requirements**: [data-acquisition-requirements.md](../construction/requirements/data-acquisition-requirements.md)
- **Completed**: 13/14 tasks (integration tests deferred)
- **Components Built**:
  - Token bucket rate limiting with per-source registry
  - Circuit breaker and exponential backoff retry
  - TTL-based response caching with cachetools
  - Semantic Scholar, arXiv, and OpenAlex API clients
  - Paper metadata normalization and multi-source aggregation
  - Knowledge Graph import pipeline
  - CLI script for paper import
  - Comprehensive unit test suite (11 test files)
- **Deferred Items**: Integration tests (requires test environment)

### Phase 3: Extraction Pipeline
- **Objective**: LLM-based extraction of research problems from papers
- **Key Deliverables**: PDF text extraction, section segmentation, LLM-based structured extraction, provenance tracking, batch processing
- **Design Status**: Complete
- **Implementation Status**: Complete (Merged)
- **Sprint**: [sprint-03-extraction-pipeline.md](../construction/sprints/sprint-03-extraction-pipeline.md)
- **Requirements**: [extraction-pipeline-requirements.md](../construction/requirements/extraction-pipeline-requirements.md)
- **Tasks**: 12/13 complete (integration tests deferred)
- **Components Built**:
  - `pdf_extractor.py` - PyMuPDF text extraction with cleanup
  - `section_segmenter.py` - Heuristic pattern matching for sections
  - `llm_client.py` - OpenAI/Anthropic abstraction via instructor
  - `prompts/templates.py` - Versioned extraction prompts
  - `schemas.py` - Pydantic models for extraction output
  - `problem_extractor.py` - Main extraction logic with filtering
  - `relation_extractor.py` - Problem-to-problem relation detection
  - `pipeline.py` - End-to-end PDF → KG workflow
  - `kg_integration.py` - KG storage and deduplication
  - `batch.py` - SQLite job queue with parallel processing
  - `cli.py` - CLI with extract command
  - `conftest.py` - Shared test fixtures

### Phase 4: API + Web UI

- **Objective**: Build FastAPI backend and Next.js frontend
- **Key Deliverables**: REST API, production web UI, graph visualization
- **Design Status**: Complete
- **Implementation Status**: Complete (Merged)
- **Sprint**: [sprint-04-api-and-ui.md](../construction/sprints/sprint-04-api-and-ui.md)
- **Components Built**:
  - FastAPI application with CORS, error handling
  - Problem CRUD endpoints
  - Paper endpoints
  - Hybrid search endpoint
  - Extraction trigger endpoint
  - Graph data endpoint
  - Next.js 14 with App Router
  - Dashboard, Problems, Papers, Extract, Graph pages
  - Knowledge graph visualization with react-force-graph
  - Docker configuration updated for Next.js

### Phase 5: Agent Implementation
- **Objective**: Implement Ranking, Continuation, Evaluation, Synthesis agents
- **Key Deliverables**: LangGraph workflows, agent orchestration, human-in-the-loop, WebSocket, workflow UI
- **Design Status**: Complete
- **Implementation Status**: Complete (Merged)
- **Sprint**: [sprint-05-agent-implementation.md](../construction/sprints/sprint-05-agent-implementation.md)
- **Tasks**: 17/17 complete
- **Components Built**:
  - Agent schemas and Pydantic models
  - ResearchState TypedDict for LangGraph
  - BaseAgent ABC with dependency injection
  - Prompt templates for all four agents
  - AgentConfig with per-agent LLM settings
  - RankingAgent - KG query + LLM scoring
  - ContinuationAgent - problem context + LLM proposal
  - EvaluationAgent - code generation + Docker sandbox execution
  - SynthesisAgent - report generation + KG writeback
  - DockerSandbox - isolated code execution
  - LangGraph StateGraph workflow definition
  - CheckpointManager for HITL decisions
  - WorkflowRunner for session management
  - WebSocket infrastructure for real-time updates
  - Agent API router (REST + WebSocket)
  - Workflow UI pages (list, detail, stepper, checkpoint forms)
- **Dependencies**: Phases 1-4 complete

### Phase 6: Full-Stack Integration
- **Objective**: Wire all components together, deploy to GCP
- **Key Deliverables**: Docker Compose, Terraform IaC, CI/CD, staging environment
- **Design Status**: Complete
- **Implementation Status**: Complete (Merged)
- **Sprint**: [sprint-06-integration.md](../construction/sprints/sprint-06-integration.md)
- **Components Built**:
  - WorkflowRunner wired into API lifespan
  - Event bus for workflow step transitions
  - Background task execution
  - Docker Compose for local development
  - Makefile for monorepo commands
  - Terraform IaC for GCP infrastructure
  - Staging environment deployed (Neo4j + Cloud Run)

### Phase 7: End-to-End Testing
- **Objective**: Validate full pipeline against live staging
- **Key Deliverables**: E2E test suite, smoke test script, pytest markers
- **Design Status**: Complete
- **Implementation Status**: Complete (Merged)
- **Sprint**: [sprint-07-e2e-testing.md](../construction/sprints/sprint-07-e2e-testing.md)
- **Components Built**:
  - E2E test configuration and fixtures
  - E2E test utilities (wait, cleanup, retry)
  - Paper acquisition E2E tests
  - Extraction pipeline E2E tests
  - KG population E2E tests
  - API endpoints E2E tests
  - Agent workflow E2E tests
  - WebSocket E2E tests
  - Smoke test script (scripts/smoke_test.py)
  - Pytest markers: e2e, slow, costly

---

## Agent Coordination

### Memory-Agent Responsibilities
- Update phase status when milestones complete
- Archive completed work from `progress.md` to `archive/progress/`
- Archive historical decisions from `activeContext.md` to `archive/decisions/`
- Flag stale or inconsistent content across memory-bank files
- Maintain cross-references between files

### Construction-Agent Responsibilities
- Create design docs in `construction/design/` before implementation
- Use `spec_builder.md` workflow for new features
- Update sprint docs during development
- Signal "Design Complete" by updating this file's Phase Registry
- Ensure designs reference relevant ADRs

### Handoff Protocol

```
Construction-Agent                    Memory-Agent
       │                                   │
       │ 1. Creates design doc             │
       │ 2. Updates phases.md              │
       │    (Status → Design Complete)     │
       │                                   │
       │ ─────── HANDOFF SIGNAL ─────────► │
       │                                   │
       │                      3. Updates activeContext.md
       │                      4. Updates progress.md
       │                      5. Archives stale content
       │                                   │
       │ ◄──────── READY SIGNAL ────────── │
       │                                   │
       │ 6. Begins implementation          │
       │ 7. Updates sprint docs            │
       │                                   │
```

---

## Archive Policy

### What Gets Archived
- **Completed milestones** from `progress.md` (quarterly or when section grows large)
- **Historical decisions** from `activeContext.md` (when superseded or > 30 days old)
- **Session summaries** (optional, for long-running projects)

### Archive Location
```
memory-bank/archive/
├── progress/      # Archived milestone completions (by quarter/phase)
├── decisions/     # Historical context and decisions
└── sessions/      # Session summaries (optional)
```

### Archive Naming Convention
- `progress/phase-0-infrastructure-2025-Q4.md`
- `decisions/decisions-2025-12.md`
- `sessions/session-2025-01-03.md`

---

## Cross-Reference Index

### Memory-Bank → Construction
| Memory-Bank File | References | Construction Files |
|------------------|------------|-------------------|
| activeContext.md | Current phase | design/, sprints/ |
| progress.md | Task tracking | sprints/ |
| architecturalDecisions.md | ADRs | design/ (implementations) |
| systemPatterns.md | Architecture | design/ (detailed specs) |

### Construction → Memory-Bank
| Construction File | Should Update | Memory-Bank Files |
|-------------------|---------------|-------------------|
| design/*.md | When complete | phases.md, activeContext.md |
| sprints/*.md | On progress | progress.md, activeContext.md |
| requirements/*.md | When defined | productContext.md (if scope changes) |

---

## Notes

- This file should be updated whenever phase status changes
- Both agents should check this file before major operations
- Archive operations should preserve original timestamps and context
