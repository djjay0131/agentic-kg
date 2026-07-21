---
title: E-4 · Method entities
parent: Design
nav_order: 4
---

# E-4 · Method / Methodology entities

{: .label .label-green }
VERIFIED

**Backlog ID:** E-4 · **Depends on:** E-3 (Model) · **Enabled:** method-adoption
queries, E-8 V2 extraction · **Spec:**
[`method-entity.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/method-entity.md)

## Why

The graph couldn't answer "which papers use fine-tuning?", "what methods does
this problem area rely on?", or trace methodology adoption over time. Methods
like *fine-tuning*, *transfer learning*, *contrastive learning*, and *data
augmentation* existed only as loose strings buried inside `Baseline.name` and
`Constraint.text` — unsearchable, undeduplicated, and unlinked. The gap analysis
flagged Method as the fourth-most-common entity type in the reference corpus
(~200 nodes), making it the next entity worth promoting to a first-class node.

## What shipped

A first-class `Method` node with `name`, optional `description`, `aliases`
(max 20), an optional free-form `method_type`, a 1536-dim embedding, and a
denormalized `usage_count`. Methods connect to the rest of the graph through a
single new edge — `APPLIES_METHOD` (`Paper → Method`) — whose write ticks the
node's `usage_count` transactionally. The full node shape and its place in the
graph live in the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#method-e-4) and
[Entity Relationships]({{ site.baseurl }}/reference/entity-relationships).

## Design decisions

**Pure open-set — not the E-3 hybrid.** This is the defining choice. E-3 Model
shipped *hybrid* open-set machinery: an `is_canonical` flag, a curated seed YAML,
a `--canonical` CLI flag, and canonical-protection rules that stop an incoming
name from renaming or downgrading a curated node. All of that exists because
model names are *identities* — "BERT", "GPT-4", "ResNet-50" — where a clear,
curator-owned canonical form matters. Method names are not identities; they are
*conceptual phrases* ("fine-tuning", "contrastive learning") with no single
authoritative surface form worth protecting. So E-4 deliberately follows the
**E-2 ResearchConcept** pattern instead: no `is_canonical` field, no seed YAML,
no `load-methods` command, no canonical-protection branch, no `--force` on
delete. The hybrid apparatus would have added surface area and earned nothing.

**Dedup at 0.90 cosine, not 0.95.** `create_or_merge_method` embeds
`"{name}: {description}"`, vector-searches the top-5 neighbours, and merges into
any candidate scoring ≥ **0.90** — matching E-2's
`DEFAULT_CONCEPT_DEDUP_THRESHOLD` rather than E-3 Model's stricter 0.95. Merges
are a plain alias union (existing name wins; `description` / `method_type` fill
from the incoming call only when the existing value is `None`). E-3 needed 0.95
to avoid collapsing distinct model identities; conceptual phrases tolerate the
looser bound.

**One-line relationship absorption.** E-3 generalized edge handling into a
`_NODE_LINK_RELATIONSHIPS` map, so E-4 added `APPLIES_METHOD` as a single map
entry and got `link_paper_to_method` / `unlink_paper_from_method` for free — no
new traversal code.

**Rebuild over migrate.** Existing `Baseline.name` / `Constraint.text` strings
are left alone. Method nodes are populated only via the API, the CLI, and (later)
the E-8 V2 extractor — the same re-extract-don't-migrate stance as E-1 and E-3.

## How it works

- **Model:** `Method` in
  [`models/entities.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/models/entities.py)
  — `name`, `description`, `aliases` (max 20), `method_type`, 1536-dim
  `embedding`, `usage_count`, timestamps. Notably **no `is_canonical` field**.
- **Repository:** `create_or_merge_method` (0.90-cosine dedup), `create_method`,
  `get_method`, `get_method_by_name`, `update_method`, `delete_method`
  (`DETACH DELETE`, no force flag), `search_methods_by_embedding`,
  `link_paper_to_method`, `unlink_paper_from_method`, `get_papers_for_method` —
  all in
  [`repository.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/repository.py).
  `APPLIES_METHOD` is one entry in `_NODE_LINK_RELATIONSHIPS`.
- **Schema:** `method_id_unique` constraint, `method_name_idx` index, and
  `method_embedding_idx` vector index (1536-dim, cosine); schema version bumped
  v5 → v6 — all in
  [`schema.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/schema.py).
- **Operator escape valve:** passing `threshold=1.01` (API body or
  `create-method --threshold`) forces the dedup search to return no matches
  (cosine ≤ 1.0), so a distinct node is always created.
- **Embedding-service failure:** falls back to create-without-embedding, logs a
  WARN, and skips dedup for that call — the call never raises.

For the node's attributes and its edges in context, see the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#method-e-4) and
[Entity Relationships]({{ site.baseurl }}/reference/entity-relationships).

## Verification

- **Tests:** Method model + alias-cap validation, schema init, embedding dedup,
  and repository CRUD.
- **Done demo (verify gate):** a testcontainers integration test seeds 5
  synthetic Papers, creates 5 well-known methods via `create_or_merge_method`
  (asserting the second same-name call *merges*), links each pair, and confirms
  `get_papers_for_method` returns the linked papers with `usage_count` matching.
- **Dedup sentinel:** a single smoke test creates "fine-tuning" + "Fine Tuning"
  at the default 0.90 threshold and asserts they merge — a loud regression guard
  against a broken or absurdly-tuned threshold (not a full eval set).
- **Status:** VERIFIED — Units 1-9 landed and all verify gates passed.

## Related

- Reference: [Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#method-e-4)
- Follows: [E-1 · Topic entities]({{ site.baseurl }}/design/e1-topic-entities) (same first-class-node pattern)
- Contrast: E-3 Model (hybrid open-set with canonical protection) — E-4 deliberately drops that machinery
- Deferred to v2: `ADDRESSED_BY` (`ProblemConcept → Method`), `EXTENDS_METHOD` lineage edges, LLM extraction (E-8 V2)
