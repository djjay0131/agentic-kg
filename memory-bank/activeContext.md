# Active Context

**Last Updated:** 2026-01-08

## Current Work Phase

**Phase 2: Data Acquisition Layer - IN PROGRESS**

Sprint 01 merged to master (PR #9, 2026-01-08). Sprint 02 is now underway with Task 3 (Semantic Scholar client) as the next task.

## Immediate Next Steps

**Session Status (2026-01-08):**
- On branch: `claude/sprint-02-data-acquisition-SqUnQ`
- Sprint 01 merged to master (PR #9)
- Sprint 02 in progress: Tasks 1-2 complete, Task 3 next
- Branch cleanup complete: old feature branches deleted

**Branch Cleanup (2026-01-08):**
- Deleted: `claude/problem-schema-design-SqUnQ` (merged)
- Deleted: `claude/create-sub-agent-k4rvf` (merged)
- Deleted: `claude/compare-kg-options-SqUnQ` (renamed)
- Current: `claude/sprint-02-data-acquisition-SqUnQ`

**Sprint 02 Progress:**
- [x] Task 1: Package structure & configuration
- [x] Task 2: Data models (PaperMetadata, AuthorRef, Citation, SourceType, DownloadStatus, DownloadResult)
- [ ] Task 3: Semantic Scholar client - NEXT
- [ ] Tasks 4-14: Pending

**Key Files Created in Sprint 02:**
- `packages/core/src/agentic_kg/data_acquisition/__init__.py`
- `packages/core/src/agentic_kg/data_acquisition/models.py`
- `packages/core/tests/data_acquisition/test_models.py` (45 unit tests)
- Updated `config.py` with `DataAcquisitionConfig`
- Updated `.env.example` with new environment variables

**Priority Tasks:**

1. **Task 3: Semantic Scholar Client** (Immediate)
   - [ ] Create `agentic_kg/data_acquisition/semantic_scholar.py`
   - [ ] Implement `SemanticScholarClient` with httpx
   - [ ] Add `search_papers()`, `get_paper()`, `get_references()`, `get_citations()`
   - [ ] Add rate limiting (1 req/sec unauthenticated, 10 req/sec authenticated)
   - [ ] Add pagination and retry logic

2. **Remaining API Clients** (Next)
   - [ ] Task 4: arXiv integration
   - [ ] Task 5: OpenAlex integration
   - [ ] Task 6: Unified PaperAcquisitionLayer

3. **Documentation Cleanup** (Medium Priority)
   - [ ] Update techContext.md with Neo4j details (deferred from Sprint 01)
   - [ ] Document Neo4j Aura production setup

## Recent Decisions

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

### Decision 4: Neo4j for Knowledge Graph (ADR-010)
- **Date:** 2025-12-22
- **Decision:** Use Neo4j as the graph database for the Knowledge Representation Layer
- **Rationale:** Native property graph model, vector index support, mature Python driver
- **Impact:** Enables Phase 1 implementation with hybrid symbolic-semantic retrieval

*Note: Decisions 1-3 (2025-12-18) archived to `archive/decisions/decisions-2025-12.md`*

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
3. **Agent Automation**: Should memory-agent auto-commit or require human review?

## Reference Materials

- **Paper**: [files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf](../files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf)
- **Denario Docs**: https://denario.readthedocs.io/
- **Phase Coordination**: [phases.md](phases.md)
- **Sub-Agents**: [.claude/agents/](../.claude/agents/)

## Notes for Next Session

- Read ALL memory-bank files on context reset (7 core files including phases.md)
- Check phases.md for current phase status
- **Sprint 01 MERGED** - PR #9 merged to master on 2026-01-08
- **Sprint 02 IN PROGRESS** - on branch `claude/sprint-02-data-acquisition-SqUnQ`
- Current task: Task 3 - Semantic Scholar client implementation
- Tasks 1-2 complete: Package structure, data models, 45 tests
- Deferred items tracked in: `construction/backlog/sprint-01-deferred.md`
- Deployment infrastructure designed in: `construction/design/deployment-infrastructure.md`
- Administrative agents ready: `@memory-agent update`, `@construction-agent validate`
