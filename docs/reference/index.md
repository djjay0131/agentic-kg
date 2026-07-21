---
title: Reference
nav_order: 4
has_children: true
permalink: /reference/
---

# Domain Model & Taxonomy

The authoritative, human-readable description of the agentic-kg knowledge graph:
the node types (entities), the edge types (relationships), and the **Topic
taxonomy** that classifies research work. Everything here is derived from — and
kept in sync with — the Pydantic models and Neo4j schema under
`packages/core/src/agentic_kg/knowledge_graph/`.

If the code and this document ever disagree, the code
(`models/entities.py`, `models/relationships.py`, `models/enums.py`,
`schema.py`, `taxonomy.py`) is the source of truth — please open a PR to fix
the doc.

## Document map

| Document | What it defines | Source of truth |
|----------|-----------------|-----------------|
| [Entity Catalog](entity-catalog) | Every **node type** — definition, purpose, key attributes, status | `models/entities.py`, `schema.py` |
| [Entity Relationships](entity-relationships) | Every **edge type** — endpoints, cardinality, semantics, diagram | `models/relationships.py`, `repository.py` Cypher |
| [Topic Taxonomy](topic-taxonomy) | The 3-level research-topic classification (domain → area → subtopic) | `taxonomy.py`, `data/seed_taxonomy.yml` |

## What "taxonomy" means here

The word is used in two complementary senses, both documented above:

1. **The Topic taxonomy** (the literal one) — a hand-curated, three-level
   hierarchy of research topics (`domain → area → subtopic`) that acts as a
   *closed-set controlled vocabulary*. Extraction is bound to it so papers can
   only be classified into known topics. See [Topic Taxonomy](topic-taxonomy).

2. **The graph ontology** (the implicit one) — the fixed, versioned set of
   ~9 node labels and ~14 edge types that every ingested paper is decomposed
   into. See the [Entity Catalog](entity-catalog) and
   [Entity Relationships](entity-relationships).

## Modeling principles

These conventions hold across the whole graph. They are described in prose here
and enforced in `models/` and `schema.py`:

1. **Problems are first-class.** A research problem is a node
   (`Problem` / `ProblemConcept`), not a string attached to a paper. This is
   the central modeling bet of the project (ADR-002).
2. **Mention vs. concept.** Paper-specific wording (`ProblemMention`) is kept
   separate from the canonical, cross-paper meaning (`ProblemConcept`), joined
   by `INSTANCE_OF`. This preserves provenance while allowing deduplication.
3. **Closed-set vs. open-set vocabularies.** `Topic` is *closed-set* (only seed
   taxonomy names). `Model` is *hybrid* (open-set + write-protected canonical
   seeds). `ResearchConcept` and `Method` are *open-set* (any name, deduped by
   embedding similarity).
4. **Denormalized counts.** Nodes cache their own edge counts
   (`mention_count`, `paper_count`, `usage_count`, `citation_count`, …) to keep
   common queries cheap; these are maintained by the repository layer.
5. **Embeddings everywhere.** Most nodes carry a 1536-dim embedding backed by a
   Neo4j vector index for semantic search and dedup.
6. **Provenance is required.** Terminal states (a `RESOLVED` / `DEPRECATED`
   problem) require evidence pointing at the paper that justifies them.
7. **Schema is versioned.** A `SchemaVersion` node records the applied schema
   version (currently **7**); `SchemaManager` applies constraints and indexes
   idempotently.

## Node types at a glance

| Node label | Role | Vocabulary | Unit |
|------------|------|-----------|------|
| `Problem` | A research problem extracted from a paper | open | core |
| `ProblemMention` | Paper-specific statement of a problem | open | canonical |
| `ProblemConcept` | Canonical, cross-paper problem | open | canonical |
| `Paper` | A scientific paper (source node) | — | core |
| `Author` | A paper author | — | core |
| `Topic` | Research topic in the taxonomy | **closed-set** | E-1 |
| `ResearchConcept` | Named intellectual building block | open | E-2 |
| `Model` | Named ML model / architecture | hybrid | E-3 |
| `Method` | Named research method / methodology | open | E-4 |

Plus `SchemaVersion` (schema metadata) and operational review-queue nodes
(e.g. `PendingReview`) that live outside the core research graph.
