# Active Context

**Last Updated:** 2026-01-07

## Current Work Phase

**Phase 1: Knowledge Graph Foundation - COMPLETE**

Sprint 01 is finished. All user stories implemented, tests passing, ready for merge.

## Immediate Next Steps

**Session Status (2026-01-07):**
- On branch: `claude/problem-schema-design-SqUnQ`
- Sprint 01 complete - ready for merge to master
- Auto-embedding on problem creation working (FR-2.4.1 fixed)
- Deferred items documented in `construction/backlog/sprint-01-deferred.md`

**Sprint 01 Completed:**
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
- [x] Sample data script: `scripts/load_sample_problems.py`
- [x] Module documentation: `knowledge_graph/README.md`

**Priority Tasks:**

1. **Merge Sprint 01** (Immediate)
   - [ ] Create PR from `claude/problem-schema-design-SqUnQ` to master
   - [ ] Review and merge
   - [ ] Consider tagging v0.1.0

2. **Sprint 02 Planning** (Next)
   - [ ] Create `construction/requirements/sprint-02-requirements.md`
   - [ ] Create `construction/sprints/sprint-02-data-acquisition.md`
   - Scope: Semantic Scholar, arXiv, OpenAlex APIs
   - Estimated: 14 tasks

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
- **Sprint 01 COMPLETE** - ready to merge branch `claude/problem-schema-design-SqUnQ`
- Next steps: Create PR, merge to master, plan Sprint 02
- Sprint 02 scope: Data Acquisition Layer (Semantic Scholar, arXiv, OpenAlex APIs)
- Deferred items tracked in: `construction/backlog/sprint-01-deferred.md`
- Deployment infrastructure designed in: `construction/design/deployment-infrastructure.md`
- Administrative agents ready: `@memory-agent update`, `@construction-agent validate`
