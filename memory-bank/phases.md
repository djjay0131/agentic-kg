# Phase Lifecycle

**Last Updated:** 2026-01-07

This file serves as the **coordination hub** between the memory-bank and construction folders. It tracks the lifecycle of each project phase and provides handoff signals between agents.

---

## Phase Registry

| Phase | Status | Design Doc | Sprint | ADRs | Implementation Ready |
|-------|--------|------------|--------|------|---------------------|
| 0: Infrastructure | Complete | N/A | [sprint-00](../construction/sprints/sprint-00-gcp-deployment.md) | ADR-004, ADR-008, ADR-009 | N/A |
| 1: Knowledge Graph | Complete (Ready for Merge) | [phase-1-knowledge-graph.md](../construction/design/phase-1-knowledge-graph.md) | [sprint-01](../construction/sprints/sprint-01-knowledge-graph.md) | ADR-010, ADR-011 | Yes |
| 2: Data Acquisition | Planning | - | - | - | No |
| 3: Extraction Pipeline | Not Started | - | - | - | No |
| 4: Agent Implementation | Not Started | - | - | - | No |

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
- **Design Status**: Planning (requirements and sprint docs to be created)
- **Estimated Tasks**: 14 tasks
- **Dependencies**: Phase 1 complete (satisfied)

### Phase 3: Extraction Pipeline
- **Objective**: LLM-based extraction of research problems from papers
- **Key Deliverables**: Section segmentation, structured extraction, provenance tracking
- **Design Status**: Not started
- **Dependencies**: Phase 2 complete

### Phase 4: Agent Implementation
- **Objective**: Implement Ranking, Continuation, Evaluation, Synthesis agents
- **Key Deliverables**: LangGraph workflows, agent orchestration, human-in-the-loop
- **Design Status**: Not started
- **Dependencies**: Phases 2-3 complete

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
