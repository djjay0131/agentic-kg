# Active Context

**Last Updated:** 2026-01-29

## Current Work Phase

**Phase 5: Agent Implementation - COMPLETE (PR #12)**

Sprint 05 complete. Four research agents with LangGraph workflow, HITL checkpoints, Docker sandbox, WebSocket updates, and workflow UI. 215 tests passing. PR #12 open for merge.

## Immediate Next Steps

**Session Status (2026-01-29):**

- On branch: `claude/sprint-05-agent-implementation`
- Sprint 05 implementation 100% complete (17/17 tasks)
- PR #12 open: https://github.com/djjay0131/agentic-kg/pull/12
- All 215 tests passing (126 agent + 89 API)
- Next.js build succeeds

**Sprint 05 Progress:**

- [x] Tasks 1-4: Agent Foundation (schemas, state, base, config, prompts)
- [x] Tasks 5-8: Individual Agents (ranking, continuation, evaluation+sandbox, synthesis)
- [x] Tasks 9-11: Orchestration (LangGraph workflow, checkpoints, runner)
- [x] Tasks 12-14: API + UI (WebSocket, agent router, workflow pages)
- [x] Tasks 15-17: Testing + Documentation (126 tests, sprint docs)

**Sprint 04 Complete (Merged):**

- [x] Task 1: FastAPI Scaffolding
- [x] Task 2: Problem Endpoints (CRUD)
- [x] Task 3: Paper Endpoints
- [x] Task 4: Search Endpoint (hybrid)
- [x] Task 5: Extraction Trigger Endpoints
- [ ] Task 6: API Tests (Pending)
- [x] Task 7: Next.js Project Setup
- [x] Task 8: Layout & Navigation
- [x] Task 9: Dashboard Page
- [x] Task 10: Problem Browser
- [x] Task 11: Paper Browser & Extraction Form
- [x] Task 12: Knowledge Graph Visualization
- [x] Task 13: Docker & Deployment Updates
- [x] Task 14: Documentation & Sprint Docs

**Sprint 03 Completed (Merged to Master):**

- [x] All extraction pipeline tasks (1-12)

**Sprint 02 Completed (Merged to Master):**

- [x] Data acquisition module structure (`data_acquisition/`)
- [x] Configuration module: `config.py`
- [x] Base API client: `base.py`
- [x] Exceptions: `exceptions.py`
- [x] Rate limiting: `rate_limiter.py` (Token bucket with registry)
- [x] Resilience: `resilience.py` (Circuit breaker, retry with backoff)
- [x] Caching: `cache.py` (TTL cache with cachetools)
- [x] Semantic Scholar client: `semantic_scholar.py`
- [x] arXiv client: `arxiv.py` (with Atom feed parsing)
- [x] OpenAlex client: `openalex.py` (with abstract reconstruction)
- [x] Paper normalization: `normalizer.py` (unified schema)
- [x] Multi-source aggregator: `aggregator.py`
- [x] KG importer: `importer.py`
- [x] CLI script: `scripts/import_papers.py`
- [x] Unit tests: 11 test files

**Sprint 01 Completed (Merged to Master):**

- [x] Pydantic models: `knowledge_graph/models.py`
- [x] Neo4j Docker setup: `docker/docker-compose.yml`
- [x] Repository layer: `knowledge_graph/repository.py`
- [x] Schema initialization: `knowledge_graph/schema.py`
- [x] Embedding integration: `knowledge_graph/embeddings.py`
- [x] Hybrid search: `knowledge_graph/search.py`
- [x] Relation operations: `knowledge_graph/relations.py`
- [x] 221 tests

**Priority Tasks:**

1. **Complete Sprint 03** (Immediate)
   - Tasks 11-12: Complete ✅
   - Task 13: Integration tests (deferred)
   - Merge PR #11

2. **Begin Sprint 04 Planning** (Next)
   - Agent Implementation (Ranking, Continuation, Evaluation, Synthesis)
   - LangGraph workflows
   - API service (`packages/api/`) - FastAPI + GraphQL
   - UI service (`packages/ui/`) - Streamlit

## Recent Decisions

### Decision 9: LLM-Based Structured Extraction (ADR-013)
- **Date:** 2026-01-26
- **Decision:** Use LLM-based extraction with `instructor` library for structured output
- **Rationale:** High quality extraction, schema validation, better than rule-based for diverse papers
- **Impact:** Enables automated problem extraction at scale

### Decision 8: Data Acquisition Architecture (ADR-012)
- **Date:** 2026-01-25
- **Decision:** Implement token bucket rate limiting with per-source configuration
- **Rationale:** Academic APIs have different rate limits; centralized registry simplifies management
- **Impact:** Robust API usage across Semantic Scholar, arXiv, and OpenAlex

### Decision 7: Deferred Items to Backlog
- **Date:** 2026-01-07
- **Decision:** Document Sprint 01 deferred items in `construction/backlog/sprint-01-deferred.md`
- **Rationale:** Keep sprint focused on core deliverables, track non-blocking items separately
- **Impact:** Clear separation between done and deferred work

*Note: Decisions 1-6 archived*

## Key Patterns and Preferences

### Documentation Patterns
- Use markdown for all documentation
- Include ASCII diagrams for architecture
- Update activeContext.md after every significant change
- Reference ADR numbers when implementing decisions
- Use `phases.md` as coordination hub between memory-bank and construction

### Development Patterns
- Python 3.12+ (Denario requirement)
- Use Denario's existing agent frameworks (AG2, LangGraph)
- Docker containers for deployment
- Environment variables for secrets (never commit keys)
- Design-first workflow: complete design docs before implementation

### Extraction Patterns (New for Phase 3)
- PyMuPDF for PDF text extraction
- Heuristic-first, LLM-fallback for section detection
- Instructor library for structured LLM output
- Multi-pass extraction: identify → extract → validate
- Confidence scoring: LLM self-assessment + schema completeness

### Architecture Patterns
- Three-layer architecture (Knowledge, Extraction, Agentic)
- Problems as first-class entities
- Hybrid symbolic-semantic retrieval
- Human-in-the-loop governance

## Important Learnings

### About Information Extraction (Phase 3)
- Section segmentation critical for targeting limitations/future work sections
- LLM structured output via instructor reduces parsing errors
- Confidence scoring should combine multiple signals
- Batch processing needs job state persistence for resume

### About Data Acquisition Layer
- Token bucket rate limiting with registry pattern for per-source limits
- Circuit breaker protects against cascading failures
- Paper normalization unifies different API response schemas
- Multi-source aggregation merges data by DOI for best metadata

### About Claude Code Sub-Agents
- Defined as markdown files with YAML frontmatter in `.claude/agents/`
- Invoked via `@agent-name command` syntax
- Tool access controlled per agent
- Can coordinate via shared files (phases.md)

## Open Questions

1. **Extraction Quality**: What F1 score is achievable on structured extraction vs manual annotation?
2. **Prompt Optimization**: How many iterations needed to stabilize extraction prompts?
3. **Cost Management**: Token usage per paper extraction?
4. **Integration Testing**: Set up test environment with API credentials and Neo4j?

## Reference Materials

- **Paper**: [files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf](../files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf)
- **Denario Docs**: https://denario.readthedocs.io/
- **Phase Coordination**: [phases.md](phases.md)
- **Sub-Agents**: [.claude/agents/](../.claude/agents/)

## Notes for Next Session

- Read ALL memory-bank files on context reset (7 core files including phases.md)
- Check phases.md for current phase status
- **Sprint 03 IN PROGRESS** - 92% complete (Tasks 1-12 done)
- Sprint 03 docs: `construction/sprints/sprint-03-extraction-pipeline.md`
- Requirements: `construction/requirements/extraction-pipeline-requirements.md`
- ADR-013 documents extraction approach decision
- PR #11 open with extraction pipeline implementation
- Next: Merge PR #11, begin Sprint 04 planning (API/UI/Agents)
