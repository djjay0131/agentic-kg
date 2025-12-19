# Active Context

**Last Updated:** 2025-12-18

## Current Work Phase

**Phase 0: Infrastructure Setup** - Deploying Denario to GCP and establishing project foundation.

## Immediate Next Steps

1. **Deploy Denario to GCP** (Current Priority)
   - Set up GCP project with required APIs
   - Configure Vertex AI service account
   - Build and push Docker image
   - Deploy to Cloud Run

2. Test Denario deployment
   - Verify GUI accessible
   - Test LLM provider connectivity
   - Run example project through system

3. Begin knowledge graph design
   - Define problem entity schema
   - Select graph database (Neo4j vs. alternatives)
   - Design extraction pipeline architecture

## Recent Decisions

### Decision 1: Project Scope Definition
- **Date:** 2025-12-18
- **Decision:** Focus on enhancing Denario for Agentic Knowledge Graphs as described in the reference paper
- **Rationale:** Clear research direction with concrete architecture proposal
- **Impact:** All development aligned toward three-layer architecture

### Decision 2: GCP Deployment First
- **Date:** 2025-12-18
- **Decision:** Deploy base Denario to GCP before building extensions
- **Rationale:** Establishes infrastructure foundation, validates deployment patterns
- **Impact:** Can iterate on extensions with working deployment target

### Decision 3: Cloud Run for Initial Deployment
- **Date:** 2025-12-18
- **Decision:** Use Cloud Run over GKE for initial phase
- **Rationale:** Simpler, faster deployment; can migrate later if needed
- **Impact:** Faster time to working deployment

## Key Patterns and Preferences

### Documentation Patterns
- Use markdown for all documentation
- Include ASCII diagrams for architecture
- Update activeContext.md after every significant change
- Reference ADR numbers when implementing decisions

### Development Patterns
- Python 3.12+ (Denario requirement)
- Use Denario's existing agent frameworks (AG2, LangGraph)
- Docker containers for deployment
- Environment variables for secrets (never commit keys)

### Architecture Patterns
- Three-layer architecture (Knowledge, Extraction, Agentic)
- Problems as first-class entities
- Hybrid symbolic-semantic retrieval
- Human-in-the-loop governance

## Important Learnings

### About Denario
- Multiagent system using AG2 and LangGraph
- Streamlit GUI on port 8501
- Supports OpenAI, Anthropic, Gemini (Vertex AI), Perplexity
- Docker images include LaTeX for paper generation
- Vertex AI requires service account JSON key

### About the Agentic KG Architecture
- Three layers: Knowledge, Extraction, Agentic
- Research problems (not papers) are central entities
- Four agent types: Ranking, Continuation, Evaluation, Synthesis
- Closed-loop: results write back to graph
- Provenance-first: all extractions link to sources

### About GCP Deployment
- Cloud Run is simplest option for containers
- Need to enable: run, artifactregistry, aiplatform APIs
- Vertex AI requires "Vertex AI User" role on service account
- Container registry: gcr.io/<project-id>/denario

## Open Questions

1. **Knowledge Graph Backend**: Neo4j vs. Amazon Neptune vs. native GCP options?
2. **Vector Index**: Pinecone vs. Weaviate vs. pgvector for semantic search?
3. **Schema Design**: What fields for Problem entities? How granular?
4. **Extraction Strategy**: Which LLM models best for structured extraction?
5. **Evaluation Metrics**: How to measure extraction reliability vs. human annotations?

## Reference Materials

- **Paper**: [files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf](../files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf)
- **Denario Docs**: https://denario.readthedocs.io/
- **Vertex AI Setup**: [docs/llm_api_keys/vertex-ai-setup.md](../docs/llm_api_keys/vertex-ai-setup.md)
- **Docker Docs**: [docs/docker.md](../docs/docker.md)

## Notes for Next Session

- Read ALL memory-bank files on context reset
- Check progress.md for completed items
- Current focus: GCP deployment
- Key files: techContext.md has deployment commands
- Reference paper in files/ folder for architecture details
