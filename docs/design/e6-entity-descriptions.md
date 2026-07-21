---
title: E-6 · Entity descriptions
parent: Design
nav_order: 6
---

# E-6 · Entity descriptions at create-time

{: .label .label-green }
VERIFIED

**Backlog ID:** E-6 · **Depends on:** E-2 (Concept), E-3 (Model), E-4
(Method) · **Enabled:** richer vector search / cleaner dedup · **Spec:**
[`entity-descriptions.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/entity-descriptions.md)

## Why

Concept, Model, and Method nodes all carry a `description` field that the
embedding pipeline folds into the vector as `"{name}: {description}"` — but the
field was almost always NULL. Only hand-curated seed entries had descriptions;
every node born from ingestion-time dedup-merge persisted with `description=None`.
Bare-name embeddings collide aggressively in similarity search — "fine-tuning"
and "transfer learning" land close together when neither has a description —
which pollutes dedup decisions and ranking. The fix is structural: generate a
description **at create time**, so the field is populated when the embedding is
computed, not as an afterthought.

## What shipped

An opt-in `generate_description` path on the create-or-merge flow for three
entity types — `ResearchConcept`, `Model`, `Method`. When enabled, the node's
description is written by an LLM that **self-validates its own output** in the
same call, and the validated text flows straight into the embedding. The
operator-facing CLI commands (`create-concept`, `create-model`, `create-method`)
turn generation on by default with a `--no-generate-description` opt-out. See the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#classification--concept-nodes)
for how `description` feeds each node's embedding.

## Design decisions

**Self-validation, not a separate critic.** The LLM returns a
`DescriptionWithSelfCheck` Pydantic model carrying the description *and* four
boolean gates it grades itself against — `is_factually_grounded`, `is_concise`,
`is_specific`, `is_not_tautological`. The description is accepted only if all
four are True; otherwise the helper logs the `rejection_reason` and returns None.
One `instructor.extract()` call, no second round-trip, per the project's
`feedback_llm_self_validation` stance.

**Never blocks the create.** On self-validation rejection *or* any LLM failure,
the helper returns None, a WARN is logged, and the node still persists with
`description=None`. Generation is a best-effort enrichment, never a gate on
entity creation.

**Async-only for the generating path.** The LLM helper is async, and the
create-or-merge helpers run inside live FastAPI event loops where `asyncio.run`
would crash. Rather than smuggle a loop into the sync method, the sync
`create_or_merge_X` raises `NotImplementedError` if `generate_description=True` is
passed, and callers use the new `acreate_or_merge_X` async siblings instead. Sync
callers with `generate_description=False` (all existing ingestion-path call sites)
are completely unchanged and cost-neutral.

**Missing API key degrades quietly.** The CLI builds its LLM client only when
generation is requested; if `OPENAI_API_KEY` is unset it logs one WARN, falls
back to the plain sync create, and still exits 0. "Create one node by hand"
never hard-fails for want of credentials.

**Topic was left out.** The spec framed E-6 over four types, but `Topic` has no
`create_or_merge_topic` — topics are loaded from the curated
[seed taxonomy]({{ site.baseurl }}/design/e1-topic-entities), which already ships
descriptions. E-6 targets the three open-set types whose nodes are minted at
ingestion time.

## How it works

- **Helper:** `generate_description_with_self_check` and the
  `DescriptionWithSelfCheck` schema live in
  [`description_generation.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/description_generation.py).
  `passes_self_validation` is the `all([...])` over the four gates.
- **Async siblings:** `acreate_or_merge_research_concept`, `acreate_or_merge_model`,
  and `acreate_or_merge_method` in
  [`repository.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/repository.py)
  resolve the description via `_aresolve_description` (explicit description wins →
  skip if disabled / no client → else call the helper), then delegate to the sync
  method with `generate_description=False`. The sync methods raise
  `NotImplementedError` on `generate_description=True`.
- **CLI:** `_llm_client_for_description` in
  [`cli.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/cli.py)
  builds the client (or returns None with a WARN), and each `create-*` handler
  runs `asyncio.run(repo.acreate_or_merge_X(..., generate_description=True))`.
- **Prompts:** `DESCRIPTION_GENERATION_SYSTEM_PROMPT_V1` /
  `..._USER_PROMPT_TEMPLATE_V1` in
  [`prompts/templates.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/prompts/templates.py);
  the user prompt injects an `(also known as: …)` hint from up to three aliases.
- **Embeddings unchanged:** whatever description survives — LLM-generated,
  caller-supplied, or None — is what the existing `"{name}: {description}"`
  embedding step sees. On a merge into a node with `description=None`, the
  generated value fills it in and the embedding is regenerated.

For each node's attributes and edges, see the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog).

## Verification

- **Tests:** schema gates, the helper's happy-path / rejection / exception
  behavior, `_aresolve_description` branching, the sync `NotImplementedError`
  guard, description-in-embedding, and CLI flag handling
  (`test_description_generation_*.py`, `test_aresolve_description.py`,
  `test_description_in_embedding.py`, `test_cli_descriptions.py`).
- **Sentinel:** a mocked failing-gate response asserts the node persists with
  `description IS NULL` — pinning that a future refactor can't accept a weak
  description.
- **Status:** VERIFIED — 2026-06-14.

## Related

- Reference: [Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#classification--concept-nodes)
- Enriches: [E-2 · Research concepts]({{ site.baseurl }}/design/e2-research-concepts),
  [E-3 · Model entities]({{ site.baseurl }}/design/e3-model-entities),
  [E-4 · Method entities]({{ site.baseurl }}/design/e4-method-entities)
- Not covered: `Topic` (seed-taxonomy descriptions, see
  [E-1]({{ site.baseurl }}/design/e1-topic-entities)); `Author` / `Paper` /
  `Problem` (own field semantics — deferred)
