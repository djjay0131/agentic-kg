# Project Brief: Agentic Knowledge Graphs for Research Progression

## Core Objectives

Enhance the Denario framework to support agentic knowledge graphs that enable research progression - moving beyond discovery to structured continuation, evaluation, and validation of scientific work.

## Requirements

### Functional Requirements
- Deploy Denario to GCP infrastructure
- Construct provenance-grounded research knowledge graphs from scientific papers
- Extract structured representations of research problems with operational context (assumptions, constraints, datasets, metrics)
- Model research problems as first-class entities with semantic relations (extends, contradicts, depends-on)
- Orchestrate specialized agents over the knowledge graph:
  - **Ranking agents**: Prioritize problems by tractability, dataset availability, cross-domain impact
  - **Continuation agents**: Propose follow-on experiments, proofs, or algorithmic extensions
  - **Evaluation agents**: Execute reproducible workflows using specified datasets and metrics
  - **Synthesis agents**: Summarize outcomes and write results back as new structured artifacts
- Support closed-loop research progression workflow

### Technical Requirements
- Python 3.12+ (Denario requirement)
- Integration with Denario's AG2 and LangGraph agent frameworks
- GCP deployment (Cloud Run or GKE for Docker containers)
- Vertex AI integration for Gemini model access
- Knowledge graph storage (property graph or RDF-compatible backend)
- Vector index for semantic retrieval (hybrid symbolic-semantic search)

## Success Criteria

- [ ] Denario successfully deployed to GCP project
- [ ] LLM-based extraction reliably identifies research problems from papers
- [ ] Knowledge graph populated with problems as first-class entities
- [ ] Semantic relations (extends/contradicts/depends-on) accurately identified
- [ ] Agents can prioritize and propose continuations grounded in graph context
- [ ] Human-in-the-loop oversight integrated at key decision points
- [ ] Full provenance tracking for all extracted content

## Project Constraints

### In Scope
- GCP deployment of Denario framework
- Knowledge graph construction pipeline
- Research problem extraction and structuring
- Agent orchestration for research progression
- Hybrid symbolic-semantic retrieval
- Human oversight interfaces

### Out of Scope (Phase 1)
- Autonomous experimentation execution
- Cross-domain longitudinal studies
- Community governance features
- Full mechanistic interpretability of agent decisions

## Testing Checklist

- [ ] Denario deploys and runs on GCP
- [ ] Vertex AI authentication works
- [ ] Document ingestion from arXiv/OpenAlex succeeds
- [ ] Problem extraction produces valid JSON schema
- [ ] Graph population creates correct entities and relations
- [ ] Agent ranking produces reasonable prioritizations
- [ ] Continuation proposals are grounded in problem context
- [ ] Provenance links correctly trace to source papers

## Contact & Resources

- Paper: "Agentic Knowledge Graphs for Research Progression" (files/Agentic_Knowledge_Graphs_for_Research_Progression.pdf)
- Denario Documentation: https://denario.readthedocs.io/
- Denario Repository: https://github.com/AstroPilot-AI/Denario
- Related: ORKG (https://orkg.org/), CS-KG, AI-KG
