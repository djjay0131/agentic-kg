# Active Context

**Last Updated:** 2026-01-25

## Current Work Phase

**Phase 2: Data Acquisition Layer - COMPLETE**

Sprint 02 (Data Acquisition Layer) implementation is complete on branch `claude/sprint-02-data-acquisition`. Ready for PR and merge.

## Immediate Next Steps

**Session Status (2026-01-25):**
- On branch: `claude/sprint-02-data-acquisition`
- Sprint 02 implementation complete (13/14 tasks - integration tests deferred)
- Ready for PR creation and merge to master

**Sprint 02 Completed (On Branch):**
- [x] Data acquisition module structure (`data_acquisition/`)
- [x] Configuration module: `config.py` (API keys, rate limits, cache TTL)
- [x] Base API client: `base.py` (httpx async client)
- [x] Exceptions: `exceptions.py` (APIError, RateLimitError, etc.)
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
- [x] Unit tests: 11 test files, 3627 lines of tests
- [ ] Integration tests (deferred - requires test environment)

**Sprint 01 Completed (Merged to Master):**
- [x] Project structure with `packages/core/src/agentic_kg/`
- [x] Configuration module: `config.py`
- [x] Pydantic models: `knowledge_graph/models.py` (Problem, Paper, Author, Relations)
- [x] Neo4j Docker setup: `docker/docker-compose.yml`
- [x] Repository layer: `knowledge_graph/repository.py` (CRUD + auto-embedding)
- [x] Schema initialization: `knowledge_graph/schema.py`
- [x] Embedding integration: `knowledge_graph/embeddings.py`
- [x] Hybrid search: `knowledge_graph/search.py`
- [x] Relation operations: `knowledge_graph/relations.py`
- [x] 221 tests (171 unit + 50 integration)

**Priority Tasks:**

1. **Merge Sprint 02** (Immediate)
   - Create PR from `claude/sprint-02-data-acquisition` to master
   - Review and merge

2. **Plan Sprint 03** (Next Session)
   - Phase 3: Information Extraction Layer
   - LLM-based entity and relation extraction from papers

## Recent Decisions

### Decision 8: Data Acquisition Architecture
- **Date:** 2026-01-25
- **Decision:** Implement token bucket rate limiting with per-source configuration
- **Rationale:** Academic APIs have different rate limits; centralized registry simplifies management
- **Impact:** Robust API usage across Semantic Scholar, arXiv, and OpenAlex

### Decision 7: Deferred Items to Backlog
- **Date:** 2026-01-07
- **Decision:** Document Sprint 01 deferred items in `construction/backlog/sprint-01-deferred.md`
- **Rationale:** Keep sprint focused on core deliverables, track non-blocking items separately
- **Impact:** Clear separation between done and deferred work

### Decision 6: Auto-Embedding on Problem Creation
- **Date:** 2026-01-07
- **Decision:** `create_problem()` auto-generates embeddings by default; opt-out via `generate_embedding=False`
- **Rationale:** Ensures all problems are searchable by default; graceful degradation on API failure
- **Impact:** Simplified workflow - no separate step needed for embedding generation

### Decision 5: Claude Code Sub-Agents for Administrative Tasks
- **Date:** 2025-01-04
- **Decision:** Implement memory-agent and construction-agent as Claude Code sub-agents
- **Rationale:** Native integration with Claude Code, auto-discovery, tool access control
- **Impact:** Standardized workflow for memory-bank and construction folder management

*Note: Decisions 1-4 archived*

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

### Agent Patterns
- Sub-agents defined in `.claude/agents/` folder
- memory-agent manages memory-bank folder
- construction-agent manages construction folder
- Coordination via `phases.md` updates

### Architecture Patterns
- Three-layer architecture (Knowledge, Extraction, Agentic)
- Problems as first-class entities
- Hybrid symbolic-semantic retrieval
- Human-in-the-loop governance

## Important Learnings

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

### About Denario
- Multiagent system using AG2 and LangGraph
- Streamlit GUI on port 8501
- Supports OpenAI, Anthropic, Gemini (Vertex AI), Perplexity
- Docker images include LaTeX for paper generation

### About the Agentic KG Architecture
- Three layers: Knowledge, Extraction, Agentic
- Research problems (not papers) are central entities
- Four agent types: Ranking, Continuation, Evaluation, Synthesis
- Closed-loop: results write back to graph

## Open Questions

1. **Extraction Strategy**: Which LLM models best for structured extraction?
2. **Evaluation Metrics**: How to measure extraction reliability vs. human annotations?
3. **Integration Testing**: Set up test environment with API credentials and Neo4j?

## Reference Materials

- **Paper**: [files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf](../files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf)
- **Denario Docs**: https://denario.readthedocs.io/
- **Phase Coordination**: [phases.md](phases.md)
- **Sub-Agents**: [.claude/agents/](../.claude/agents/)

## Notes for Next Session

- Read ALL memory-bank files on context reset (7 core files including phases.md)
- Check phases.md for current phase status
- **Sprint 02 COMPLETE** - Data Acquisition Layer on branch, ready for PR
- Sprint 02 docs: `construction/sprints/sprint-02-data-acquisition.md`
- Requirements: `construction/requirements/data-acquisition-requirements.md`
- Deferred items tracked in: `construction/backlog/sprint-01-deferred.md`
- Administrative agents ready: `@memory-agent update`, `@construction-agent validate`
