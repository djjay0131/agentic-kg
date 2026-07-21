---
title: Design
nav_order: 5
has_children: true
permalink: /design/
---

# Design & Architecture

Human-readable design notes for every **completed** feature in the project — the
*why*, the key decisions, and how the shipped system actually works. These are
distilled from the full engineering specs in
[`llm/features/`](https://github.com/djjay0131/agentic-kg/tree/master/llm/features)
and reconciled against the code that shipped, so where the implementation
diverged from the original plan, these notes describe **what was built**, not
what was proposed.

For the graph's node/edge definitions these features produced, see the
[Reference → Domain Model & Taxonomy]({{ site.baseurl }}/reference/) section.

## How to read these

Each page follows the same shape:

- **Why** — the gap it closed.
- **What shipped** — the outcome now in the system.
- **Design decisions** — the choices that mattered, with rationale.
- **How it works** — data model, edges, and flow, with code pointers.
- **Verification** — how we know it's done (tests, CI smoke, acceptance).

Status key: **VERIFIED** = shipped and all quality gates pass.

## Completed features

### Entity-expansion arc

The arc that turned the graph from *problems + papers* into a richly typed
research graph. Each entity type is a first-class node with embeddings and dedup.

| Feature | What it added |
|---------|---------------|
| [E-1 · Topic entities]({{ site.baseurl }}/design/e1-topic-entities) | First-class `Topic` hierarchy; closed-set taxonomy; `BELONGS_TO` / `RESEARCHES` |
| [E-2 · ResearchConcept entities]({{ site.baseurl }}/design/e2-research-concepts) | Named concepts as nodes; `INVOLVES_CONCEPT` / `DISCUSSES`; embedding dedup |
| [E-3 · Model entities]({{ site.baseurl }}/design/e3-model-entities) | ML models as nodes; hybrid open-set + canonical seeds; `USES_MODEL` |
| [E-4 · Method entities]({{ site.baseurl }}/design/e4-method-entities) | Research methods as nodes; pure open-set; `APPLIES_METHOD` |
| [E-5 · Citation graph]({{ site.baseurl }}/design/e5-citation-graph) | `CITES` edges from reference lists; stub papers; influence analysis |
| [E-6 · Entity descriptions]({{ site.baseurl }}/design/e6-entity-descriptions) | Create-time descriptions for richer `{name}: {description}` embeddings |
| [E-7 · Cross-entity normalization]({{ site.baseurl }}/design/e7-cross-entity-normalization) | LLM router disambiguates Concept vs Model vs Method |
| [E-8 · Extraction prompt expansion]({{ site.baseurl }}/design/e8-extraction-prompt-expansion) | Extractors that populate the new entity types from paper text (V1 + V2) |
| [Entity pipeline orchestration]({{ site.baseurl }}/design/entity-pipeline-orchestration) | Wires the whole arc into production ingestion, default-on with audit |

### Ingestion &amp; infrastructure

| Feature | What it added |
|---------|---------------|
| [D-1 · Ingest real papers]({{ site.baseurl }}/design/d1-ingest-real-papers) | End-to-end ingestion CLI: search → import → extract → integrate |
| [D-1a · Cloud Run Jobs ingestion]({{ site.baseurl }}/design/d1a-cloud-run-jobs) | Durable async ingestion as a Terraform-managed Cloud Run Job |
| [CI smoke test]({{ site.baseurl }}/design/ci-smoke-test) | GHA workflow asserting entity edges land in an ephemeral Neo4j |

### Docs &amp; site

| Feature | What it added |
|---------|---------------|
| [Enhance GitHub Pages]({{ site.baseurl }}/design/enhance-github-pages) | Docs generator reading `llm/memory_bank/`; unified nav; auto-published backlog |
