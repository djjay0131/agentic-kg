---
title: Architecture
parent: About
nav_order: 2
---

# Architecture

<!-- TODO(phase-b): replace with full three-layer diagram (ASCII or image) plus layer-by-layer responsibilities and key code pointers. -->

Three-layer architecture (ADR-002), mirroring the Agentic Knowledge Graphs paper:

1. **Knowledge Representation Layer** — Neo4j 5.x property graph with native vector indexes; research problems modeled as first-class nodes.
2. **Automation and Extraction Layer** — PDF ingestion, section segmentation, LLM-based extraction with `instructor`.
3. **Agentic Orchestration Layer** — LangGraph + AG2 agents operating over the graph (Ranking, Continuation, Evaluation, Synthesis).

The FastAPI backend + Next.js UI sit on top. Infrastructure is GCP (Cloud Run for API, Cloud Run Jobs for ingestion, Compute Engine for Neo4j, Terraform-managed).
