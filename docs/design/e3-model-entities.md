---
title: E-3 · Model entities
parent: Design
nav_order: 3
---

# E-3 · Model / Architecture entities

{: .label .label-green }
VERIFIED

**Backlog ID:** E-3 · **Depends on:** none (inherits E-1 / E-2 patterns) ·
**Enabled:** model-adoption queries, E-8-V2 model extraction · **Spec:**
[`model-entity.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/model-entity.md)

## Why

ML models used to live only as opaque substrings inside `Baseline.name` fields
embedded in `Problem` nodes — `Baseline(name="GPT-4 baseline trained for 100
epochs", ...)`. A researcher asking "which papers in the corpus actually used
BERT?" got nothing from the graph: the data was there, but it wasn't searchable,
wasn't deduplicated, and wasn't linked to Papers as real structure. The gap
analysis flagged Model as the third-most-common entity type in the reference
paper's results and the third-highest-priority missing node type.

## What shipped

A first-class `Model` node type with a `USES_MODEL` edge (Paper → Model), so the
graph can answer which papers use or benchmark against a given model. Models
carry `architecture`, `model_type`, `year_introduced`, `introducing_paper_doi`,
`aliases`, a 1536-dim embedding, and a denormalized `usage_count`. A curated
seed set of canonical models is loaded from YAML, and every create attempt runs
embedding-based dedup so surface variants (`bert-base`, `BERT-large`) collapse
onto one node. See the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog) for the node in
context.

## Design decisions

**Hybrid open-set, not closed vocabulary.** Unlike E-1's closed taxonomy, any
name can become a `Model` — the extractor is not bound to a fixed list. But the
curated seed entries in
[`seed_models.yml`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/data/seed_models.yml)
are flagged `is_canonical=True` and **write-protected** during merges: an
incoming non-canonical name can never rename or downgrade a canonical node — it
is folded into that node's aliases instead. A canonical seed landing *after* a
community near-duplicate promotes the existing node (prior name moves to
aliases, `usage_count` survives). This keeps well-known model names clean and
governable by PR while still admitting the long tail.

**Dedup threshold 0.95 cosine — stricter than E-2.** ResearchConcept dedup runs
at 0.90; Model runs at `DEFAULT_MODEL_DEDUP_THRESHOLD = 0.95` because the
model-name space is more collision-prone (`BERT` vs `DistilBERT` must *not*
merge). A 10-pair hand-labeled eval gates the threshold against both false
merges and silent recall loss.

**No migration of `Baseline` strings.** Existing baseline substrings stay put;
Model nodes are populated only by the seed loader, the API, and (later) the
E-8-V2 extractor. Dirty baseline strings would pollute a curated set that never
recovers — a clean forward path beats backward-compat migration, matching the
project's rebuild-over-migrate stance.

**One generalized link helper.** Rather than copy E-2's concept-linking code,
E-3 generalized `_link_entity_to_concept` into `_link_entity_to_node` backed by
`_NODE_LINK_RELATIONSHIPS`, which now registers `DISCUSSES`, `INVOLVES_CONCEPT`,
and `USES_MODEL` (E-4's `APPLIES_METHOD` reuses it). Existing concept call sites
route through the generalized helper unchanged.

## How it works

- **Model:** `Model` in
  [`models/entities.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/models/entities.py)
  — `name`, `description`, `aliases`, `architecture`, `model_type`,
  `year_introduced`, `introducing_paper_doi`, `is_canonical`, 1536-dim
  `embedding`, and denormalized `usage_count`.
- **Schema:** `model_id_unique` constraint; `model_name_idx` and
  `model_is_canonical_idx` indexes; `model_embedding_idx` vector index (1536-dim,
  cosine) — all in
  [`schema.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/schema.py).
- **Dedup + canonical protection:** `create_or_merge_model` in
  [`repository.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/repository.py)
  embeds `"{name}: {description}"`, vector-searches at ≥ 0.95, and applies the
  merge-direction rule (non-canonical → canonical allowed; the reverse is not).
  On embedding-service failure it falls back to create-without-embedding, skips
  dedup, and logs a WARN.
- **Edge + counter:** `link_paper_to_model` / `unlink_paper_from_model` MERGE the
  `USES_MODEL` edge idempotently and tick `usage_count` transactionally;
  `get_papers_for_model` is the inverse traversal.
- **Seed loader:**
  [`seed_models.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/seed_models.py)
  (`load_seed_models`) idempotently loads the canonical YAML — re-running is a
  no-op, reporting `{created, merged}`.

For the node's attributes and its edges in context, see the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#model-e-3) and
[Entity Relationships]({{ site.baseurl }}/reference/entity-relationships).

## Verification

- **Tests:** Model model validation, schema init, idempotent seed load,
  canonical-protection merge directions, `USES_MODEL` edge + `usage_count`
  idempotence, and the CRUD surface.
- **Dedup eval:** a 10-pair hand-labeled fixture gates the 0.95 threshold with a
  10/10 precision requirement plus a 6-of-8 merge tripwire so threshold tuning
  can't silently trade recall for precision (`@pytest.mark.costly`).
- **Integration:** a testcontainers Neo4j test seeds Papers, links them via the
  CLI/repository, and asserts `GET /api/models/{id}/papers` returns them.
- **Status:** VERIFIED — Units 1-10 landed and verify gates passed 2026-06-08.

## Related

- Reference: [Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#model-e-3)
- Reference: [Entity Relationships]({{ site.baseurl }}/reference/entity-relationships)
- Sibling: **E-4** — Method entities (pure open-set; reuses the link helper)
- Deferred: **`VARIANT_OF`** lineage edges (BERT → RoBERTa → DeBERTa) — specified
  as a v1 non-goal and **not yet shipped**; only `USES_MODEL` exists today.
- Follows: **E-8-V2** — LLM-based Model extraction from paper text
