# Active Context

**Last Updated:** 2025-01-04

## Current Work Phase

**Administrative Agents Complete** - Sub-agents for memory-bank and construction folder management are implemented and ready for use.

## Immediate Next Steps

**Session Status (2025-01-04):**
- On branch: `claude/create-sub-agent-k4rvf`
- Administrative sub-agents created ✅
- Phase 1 design complete, ready for implementation ✅
- Neo4j selected as graph database (ADR-010) ✅

**Priority Tasks:**

1. **Test Administrative Agents** (Just Completed)
   - memory-agent: `.claude/agents/memory-agent.md`
   - construction-agent: `.claude/agents/construction-agent.md`
   - Commands: `@memory-agent status`, `@construction-agent validate`

2. **Begin Phase 1 - Knowledge Graph Implementation** (Ready to Start)
   - Design complete: [phase-1-knowledge-graph.md](../construction/design/phase-1-knowledge-graph.md)
   - Sprint 01 tasks defined: [sprint-01-knowledge-graph.md](../construction/sprints/sprint-01-knowledge-graph.md)
   - First task: Set up Neo4j in Docker

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

### Decision 1: Project Scope Definition
- **Date:** 2025-12-18
- **Decision:** Focus on enhancing Denario for Agentic Knowledge Graphs as described in the reference paper
- **Rationale:** Clear research direction with concrete architecture proposal
- **Impact:** All development aligned toward three-layer architecture

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
- Administrative agents ready: `@memory-agent`, `@construction-agent`
- Next priority: Begin Phase 1 Knowledge Graph implementation
- Use `@construction-agent create-sprint` to start new sprints
