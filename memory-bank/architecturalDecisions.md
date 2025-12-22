# Architectural Decisions

This file tracks significant architectural and technical decisions made throughout the project lifecycle.

## Decision Format

Each decision should include:
- **Date:** When the decision was made
- **Status:** Proposed | Accepted | Deprecated | Superseded
- **Context:** What prompted this decision
- **Decision:** What was decided
- **Consequences:** Impact and trade-offs
- **Alternatives Considered:** Other options evaluated

---

## ADR-001: Use Denario as Core Framework

**Date:** 2025-12-18
**Status:** Accepted

**Context:**
Need a foundation for document processing, agent orchestration, and scientific workflow automation to build the Agentic Knowledge Graph system.

**Decision:**
Adopt Denario as the core framework, extending it for knowledge graph functionality. Denario provides:
- AG2 and LangGraph agent frameworks
- Document processing capabilities
- Streamlit GUI infrastructure
- LLM provider integrations (OpenAI, Anthropic, Gemini)

**Consequences:**
- Pros: Mature agent infrastructure, existing deployment patterns, active community
- Cons: Must work within Denario's architecture, may need to contribute upstream changes
- Impact: Accelerated development, proven foundation

**Alternatives Considered:**
- Build from scratch: Rejected due to time/effort required
- LangChain only: Rejected, Denario provides more complete research workflow support
- AutoGPT/similar: Rejected, less suitable for structured scientific workflows

---

## ADR-002: Three-Layer Architecture

**Date:** 2025-12-18
**Status:** Accepted

**Context:**
Need to organize the system into coherent components as described in the Agentic Knowledge Graphs paper.

**Decision:**
Implement three conceptual layers:
1. **Knowledge Representation Layer**: Research knowledge graph with problems as first-class entities
2. **Automation and Extraction Layer**: Document processing and structured extraction pipelines
3. **Agentic Orchestration Layer**: Specialized agents operating over the graph

**Consequences:**
- Pros: Clear separation of concerns, independent development/testing, aligns with paper architecture
- Cons: More components to integrate, potential latency between layers
- Impact: Clean architecture, easier to extend and maintain

**Alternatives Considered:**
- Monolithic design: Rejected, harder to test and evolve
- Two-layer (extraction + agents): Rejected, knowledge graph deserves distinct treatment

---

## ADR-003: Problems as First-Class Entities

**Date:** 2025-12-18
**Status:** Accepted

**Context:**
Existing scholarly knowledge graphs (ORKG, CS-KG) model papers or claims as central units, but this doesn't support research progression - understanding what's open and how to advance it.

**Decision:**
Model research problems as first-class graph entities with:
- Structured attributes (assumptions, constraints, datasets, metrics, baselines)
- Evidence spans linking to source papers
- Semantic relations to other problems (extends, contradicts, depends-on)
- Confidence scores and provenance metadata

**Consequences:**
- Pros: Enables progression-focused queries, supports agent reasoning over actionable items
- Cons: More complex extraction, requires problem-centric schema design
- Impact: Differentiates from existing SKGs, enables core value proposition

**Alternatives Considered:**
- Paper-centric model: Rejected, doesn't capture "what's open"
- Claim-centric model: Rejected, too granular for research progression
- Hybrid paper+problem: Considered, problems primary with paper links

---

## ADR-004: GCP Cloud Run for Initial Deployment

**Date:** 2025-12-18
**Status:** Accepted

**Context:**
Need to deploy Denario to GCP for the project. Multiple options available: Cloud Run, GKE, Compute Engine.

**Decision:**
Use Cloud Run for initial deployment:
- Containerized deployment (existing Dockerfile)
- Serverless scaling
- Simpler than GKE for initial phase
- Can migrate to GKE later if needed

**Consequences:**
- Pros: Quick deployment, automatic scaling, lower operational overhead
- Cons: Cold start latency, 60-minute request timeout, memory limits
- Impact: Fast time to deployment, may need GKE for heavy workloads

**Alternatives Considered:**
- GKE: Rejected for initial phase due to complexity, consider for production scale
- Compute Engine: Rejected, more manual management than necessary
- HuggingFace Spaces: Already exists for demo, not suitable for custom deployment

---

## ADR-005: Hybrid Symbolic-Semantic Retrieval

**Date:** 2025-12-18
**Status:** Accepted

**Context:**
Need to retrieve relevant research problems for researchers. Pure keyword search misses conceptual similarity; pure vector search can't filter by structured constraints.

**Decision:**
Implement hybrid retrieval combining:
- Property graph queries for structured filtering (domain, dataset availability, etc.)
- Vector similarity search for semantic matching
- Combined ranking for final results

**Consequences:**
- Pros: Best of both approaches, enables complex queries with semantic understanding
- Cons: More infrastructure (graph DB + vector index), ranking fusion complexity
- Impact: Superior retrieval quality, key differentiator

**Alternatives Considered:**
- Graph-only: Rejected, misses conceptual similarity
- Vector-only: Rejected, can't filter by structured constraints
- BM25 + vectors: Considered, graph queries more expressive for our needs

---

## ADR-006: Human-in-the-Loop Governance

**Date:** 2025-12-18
**Status:** Accepted

**Context:**
Agents operating autonomously on research decisions raises trust and safety concerns. Users need confidence in system outputs.

**Decision:**
Integrate human oversight at key decision points:
- Problem selection approval
- Continuation proposal review (approve/modify/reject)
- Evaluation result validation
- Gradual autonomy increases based on track record

**Consequences:**
- Pros: Builds trust, catches agent errors, responsible AI practice
- Cons: Slower workflows, coordination overhead
- Impact: Higher trust, lower risk of problematic agent actions

**Alternatives Considered:**
- Full autonomy: Rejected, too risky for research decisions
- Notification-only: Rejected, insufficient control
- Human-only with AI suggestions: Considered, current design allows evolution toward this

---

## ADR-007: Documentation Structure - Memory Bank + Construction

**Date:** 2025-12-18
**Status:** Accepted (Carried from previous project)

**Context:**
Need robust documentation system that survives context resets and tracks active development.

**Decision:**
Implement two-folder system:
- `memory-bank/`: Persistent knowledge, context retention (7 core files)
- `construction/`: Active development tracking (design, requirements, sprints)

**Consequences:**
- Pros: Clear separation between persistent and active knowledge, systematic context recovery
- Cons: Requires discipline to maintain
- Impact: Better context retention, clearer development workflow

**Alternatives Considered:**
- Single docs/ folder: Rejected, lacks separation
- Wiki or external tool: Rejected, want everything in repository

---

## ADR-008: Use GCP Cloud Build for Container Images

**Date:** 2025-12-18
**Status:** Accepted

**Context:**
Need to build Docker images for Cloud Run deployment. Options include building locally with Docker and pushing to registry, or using GCP Cloud Build for server-side builds.

**Decision:**
Use GCP Cloud Build instead of local Docker builds:
- Cloud Build handles build and push in one step
- No need for local Docker installation or resources
- Consistent build environment (not dependent on developer machine)
- Integrated with Artifact Registry
- Configuration stored in `cloudbuild.yaml`

**Consequences:**
- Pros: No local Docker required, consistent builds, faster for large images (cloud bandwidth), automatic integration with GCP services
- Cons: Requires Cloud Build API enabled, costs for build minutes (free tier available), slightly more complex initial setup
- Impact: Simpler developer experience, reproducible builds, better CI/CD foundation

**Alternatives Considered:**
- Local Docker build + push: Rejected, requires local Docker, large image uploads, inconsistent environments
- GitHub Actions + push: Considered, would work but adds complexity and separate credential management
- Pre-built public images: Rejected, need customization for project-specific dependencies

---

## ADR-009: GitHub-Triggered CI/CD Pipeline

**Date:** 2025-12-19
**Status:** Accepted

**Context:**
Need automated deployment pipeline to deploy Denario to Cloud Run when code changes are pushed. Manual deployments are error-prone and slow down development iteration.

**Decision:**
Implement full CI/CD pipeline using GCP Cloud Build with GitHub triggers:
- **Production trigger**: Builds and deploys on push to `master` branch
- **Development trigger**: Builds and deploys on push to `dev/*` branches
- **Secret management**: API keys stored in GCP Secret Manager, injected at runtime
- **Image tagging**: Each build tagged with commit SHA for traceability

Pipeline stages:
1. Build Docker image with commit SHA tag
2. Tag as `latest`
3. Push both tags to Artifact Registry
4. Deploy to Cloud Run with secrets

**Consequences:**
- Pros: Automated deployments, consistent builds, traceable versions, secure secrets
- Cons: Build costs (mitigated by free tier), initial setup complexity
- Impact: Faster development cycle, reliable deployments, production-ready infrastructure

**Alternatives Considered:**
- Manual deployment: Rejected, error-prone and slow
- GitHub Actions: Rejected, would require separate credential management
- Cloud Build without triggers: Rejected, still requires manual invocation

---

## ADR-010: Neo4j for Knowledge Graph Storage

**Date:** 2025-12-22
**Status:** Accepted

**Context:**
Phase 1 requires a graph database to store research problems as first-class entities with explicit relations (extends, contradicts, depends-on) and hybrid symbolic-semantic retrieval capabilities.

**Decision:**
Use Neo4j as the knowledge graph database:
- Property graph model fits entity-relation design
- Native vector index support (Neo4j 5.x+) for embeddings
- Mature Python driver (`neo4j` package)
- Cypher query language for expressive graph traversal
- Docker support for local development
- Neo4j Aura available for production deployment

**Consequences:**
- Pros: Well-documented, strong ecosystem, supports hybrid retrieval natively, good visualization tools
- Cons: Learning curve for Cypher, Community Edition lacks some enterprise features, potential cost at scale
- Impact: Enables core knowledge graph functionality, supports the three-layer architecture

**Alternatives Considered:**
- Amazon Neptune: Rejected, more complex setup, AWS-locked, less Python tooling
- Memgraph: Considered, good performance but smaller ecosystem
- NetworkX + SQLite: Rejected, not suitable for production scale or vector search
- PostgreSQL + pgvector: Considered, but graph queries would be complex

---

## Template for Future Decisions

```markdown
## ADR-XXX: [Decision Title]

**Date:** YYYY-MM-DD
**Status:** Proposed | Accepted | Deprecated | Superseded

**Context:**
[What is the issue that we're seeing that is motivating this decision or change?]

**Decision:**
[What is the change that we're actually proposing or doing?]

**Consequences:**
- Pros: [Positive outcomes]
- Cons: [Negative outcomes or trade-offs]
- Impact: [Overall impact on the project]

**Alternatives Considered:**
- [Alternative 1]: [Why rejected]
- [Alternative 2]: [Why rejected]
```

---

## Notes

- Update this file when making significant technical or architectural decisions
- Reference ADR numbers in code comments when implementing decisions
- Mark decisions as Deprecated or Superseded when they change, don't delete them
- Keep historical context even when decisions change
