# Active Context

**Last Updated:** 2025-01-05

## Current Work Phase

**Phase 1: Knowledge Graph Foundation - Implementation In Progress**

Sprint 01 has started. Core infrastructure files created, implementing the Knowledge Representation Layer.

## Immediate Next Steps

**Session Status (2025-01-05):**
- On branch: `claude/problem-schema-design-SqUnQ`
- Sprint 01 implementation in progress
- Core package structure created
- Requirements document added (ADR-011 for microservice architecture)

**Completed This Sprint:**
- [x] Project structure with `packages/core/src/agentic_kg/`
- [x] Configuration module: `config.py`
- [x] Pydantic models: `knowledge_graph/models.py`
- [x] Neo4j Docker setup: `docker/docker-compose.yml`
- [x] Requirements specification: `construction/requirements/knowledge-graph-requirements.md`

**Priority Tasks:**

1. **Continue Sprint 01 Implementation** (In Progress)
   - [ ] Create repository layer (CRUD operations)
   - [ ] Implement schema initialization
   - [ ] Add embedding integration
   - [ ] Implement hybrid search
   - Sprint doc: [sprint-01-knowledge-graph.md](../construction/sprints/sprint-01-knowledge-graph.md)

2. **Testing Infrastructure** (Next)
   - [ ] Set up pytest with Neo4j testcontainer
   - [ ] Create test fixtures
   - [ ] Write unit tests for models

## Recent Decisions

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

*Note: Decision 1 (Project Scope Definition, 2025-12-18) archived to `archive/decisions/decisions-2025-12.md`*

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
- **Phase 1 Sprint 01 in progress** - continue implementing Knowledge Graph Foundation
- Key files created: `config.py`, `models.py`, `docker-compose.yml`
- Next tasks: repository layer, schema initialization, embedding integration
- Administrative agents ready: `@memory-agent update`, `@construction-agent validate`
