# Project Brief: Agentic Knowledge Graphs for Research Progression

## One-Line Description

A system that builds research knowledge graphs from scientific papers and uses AI agents to identify, deduplicate, and advance open research problems.

## Purpose

Enhance the Denario framework to support agentic knowledge graphs that go beyond literature discovery — enabling structured continuation, evaluation, and validation of scientific work. Research problems are modeled as first-class entities with rich operational context (assumptions, constraints, datasets, metrics), and specialized agents operate over the graph to rank problems, propose continuations, execute evaluations, and synthesize results.

## Target Users

- Researchers seeking to identify tractable open problems in their field
- Research teams wanting structured tracking of problem evolution across papers
- Anyone using the Denario framework for scientific workflow automation

## Scope Boundaries

### In Scope

- GCP-deployed infrastructure (Cloud Run, Neo4j, Terraform IaC)
- Knowledge graph construction from academic papers (arXiv, Semantic Scholar, OpenAlex)
- LLM-based extraction of research problems with provenance tracking
- Canonical problem deduplication via dual-entity architecture (ProblemMention/ProblemConcept)
- Agent orchestration: Ranking, Continuation, Evaluation, Synthesis agents
- Confidence-based matching workflow with human review queue
- Human-in-the-loop oversight at key decision points
- FastAPI backend + Next.js frontend + graph visualization

### Out of Scope (Current Phase)

- Autonomous experimentation execution
- Cross-domain longitudinal studies
- Community governance features
- Full mechanistic interpretability of agent decisions

## Key Constraints

- Python 3.12+ required (Denario dependency)
- Neo4j 5.x+ for native vector index support (1536-dim OpenAI embeddings)
- Must integrate with Denario's AG2 and LangGraph agent frameworks
- GCP deployment target (Cloud Run for API, Compute Engine for Neo4j)
- API keys required for OpenAI, Anthropic, and optionally Google Gemini / Perplexity
