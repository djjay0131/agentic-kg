---
title: E-2 · ResearchConcept entities
parent: Design
nav_order: 2
---

# E-2 · ResearchConcept entities

{: .label .label-green }
VERIFIED

**Backlog ID:** E-2 · **Depends on:** none (benefits from E-1 Topics for
`BELONGS_TO`, not blocked by them) · **Spec:**
[`research-concept-entities.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/research-concept-entities.md)

## Why

The graph modelled research *problems* but not the intellectual building blocks
that connect them — techniques and ideas like *attention mechanism*, *transfer
learning*, or *graph neural networks*. Without them a researcher couldn't ask
"which problems involve attention mechanisms?", couldn't discover that two
unrelated problems share a common technique, and couldn't trace how an idea such
as *retrieval-augmented generation* travels across research areas. The gap
analysis flagged this as the second-highest-priority missing entity type, with
~520 concept nodes seen in the reference paper's results.

## What shipped

A first-class `ResearchConcept` node type with `name`, `description`, an
`aliases` list, and a 1536-dim semantic embedding. Concepts are an *open-set*
vocabulary — any name may become one — with duplicates folded together at create
time by embedding similarity. They connect to the rest of the graph via two
edges: `INVOLVES_CONCEPT` (from a `ProblemConcept`) and `DISCUSSES` (from a
`Paper`), plus an optional `BELONGS_TO` to a `Topic` where E-1 nodes exist. See
the [Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#researchconcept-e-2)
for the full attribute list.

## Design decisions

### Open-set vocabulary, governed by dedup

Where E-1 Topics are a *closed set* bound to a curated taxonomy, ResearchConcepts
are deliberately *open* — no seed list, any extracted or manually created name can
become a node. The vocabulary is kept clean not by a fixed enum but by
**embedding-based deduplication on write**: every create embeds `"{name}:
{description}"`, vector-searches existing concepts, and if the best match scores
at or above **0.90 cosine** the incoming name is merged into that concept's
`aliases` rather than creating a duplicate. This matches the reality that
technique names are unbounded and constantly coined, whereas the topic hierarchy
is small and auditable.

### Aliases capture surface forms

A concept carries an `aliases` list so that *self-attention* and *scaled
dot-product attention* can resolve to one canonical *attention mechanism* node.
Merges append to this list, so aliases accumulate the surface forms the corpus
actually uses. The threshold is configurable and 0.90 was fixed by a small
calibration study (29 labelled pairs) rather than guessed.

### No concept_type enum, no typed concept-to-concept edges

Concepts are intentionally untyped in v1 — graph structure provides the typing.
Typed concept-to-concept relationships (`ENABLES`, `BUILDS_ON`, `ALTERNATIVE_TO`,
…) were deferred because they emerge from LLM extraction (E-8) or community
detection (C-1), not manual creation. A generic `RELATED_TO` was deliberately
omitted so that edge names always carry meaning.

### Shared entity abstraction descoped

The spec originally scoped a shared `BaseGraphEntity` + `EntityService` and a
refactor of Topic and ResearchConcept onto them. This was **descoped during
verification (AC-7)**: with only two entity types in hand the abstraction wasn't
paying for itself, so both shipped with their own boilerplate. The consolidation
is deferred until a third call site (E-3 Model / E-4 Method) lands — both of
which have since shipped, following the same per-entity pattern.

## How it works

- **Model:** `ResearchConcept` in
  [`models/entities.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/models/entities.py)
  — `id`, `name`, `description`, `aliases`, 1536-dim `embedding`, and denormalized
  `mention_count` (`INVOLVES_CONCEPT`) / `paper_count` (`DISCUSSES`).
- **Dedup / CRUD:** `create_or_merge_research_concept` in
  [`repository.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/repository.py)
  embeds, searches, and merges at `DEFAULT_CONCEPT_DEDUP_THRESHOLD = 0.90`,
  returning `(concept, created)`. Linking flows through a generalized
  `_link_entity_to_node` helper that increments the target count only when a new
  edge is created; `INVOLVES_CONCEPT` and `DISCUSSES` are registered there.
- **Schema:** `research_concept_id_unique` constraint, `research_concept_name_idx`,
  and the `research_concept_embedding_idx` vector index (1536-dim, cosine) — all in
  [`schema.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/schema.py).
- **Counts:** `mention_count` and `paper_count` are updated transactionally on
  link/unlink, with a periodic reconciliation query correcting any drift — the
  same pattern as E-1 Topic.
- **Extraction:** E-2 shipped the *infrastructure* (model, dedup, CRUD, API, CLI);
  automatic per-paper concept extraction arrived later with the LLM extractor
  (E-8), and an async description-generating sibling
  (`acreate_or_merge_research_concept`) arrived with E-6.

For the node's attributes and its edges in context, see the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#researchconcept-e-2)
and [Entity Relationships]({{ site.baseurl }}/reference/entity-relationships).

## Verification

- **Tests:** 99 E-2 unit tests (77 core + 22 API) covering model validation,
  schema init, CRUD, dedup merge/no-merge logic, the calibration harness, and the
  API/CLI surfaces; 22 repository integration tests run against live Neo4j.
- **Calibration:** the 0.90 threshold is backed by a bundled fixture of 29
  labelled pairs (17 same / 12 different) and is overridable.
- **Status:** VERIFIED (2026-04-21) via `/constellize:feature:verify` — all four
  gates passed at E-2 scope. Staging graph inspection (AC-13) was left as a
  pending operator check.

## Related

- Reference: [Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#researchconcept-e-2)
- Contrast: [E-1 · Topic entities]({{ site.baseurl }}/design/e1-topic-entities) (closed-set taxonomy vs this open-set vocabulary)
- Follows: E-8 · Extraction (populates concepts from paper text); E-6 (async description generation)
- Deferred: typed concept-to-concept edges (E-8 / C-1); shared `BaseGraphEntity` / `EntityService` (AC-7)
