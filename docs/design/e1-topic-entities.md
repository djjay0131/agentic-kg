---
title: E-1 · Topic entities
parent: Design
nav_order: 1
---

# E-1 · Topic / Research Area entities

{: .label .label-green }
VERIFIED

**Backlog ID:** E-1 · **Depends on:** none · **Enabled:** community detection
(C-1), RAG retrieval (R-1) · **Spec:**
[`topic-research-area-entities.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/topic-research-area-entities.md)

## Why

Research topics used to live as flat `domain` strings (`"NLP"`,
`"Computer Vision"`) attached to problems. Strings can't be traversed, can't be
searched by similarity, and can't express that *Machine Translation* sits under
*Natural Language Processing* under *Computer Science*. A researcher couldn't ask
"show me every open problem in NLP" as a graph query, and topic-based navigation,
community detection, and retrieval were all blocked. The gap analysis flagged
this as the highest-priority missing entity type.

## What shipped

A first-class `Topic` node type forming a three-level hierarchy
(`domain → area → subtopic`), with a hand-curated seed taxonomy loaded into
Neo4j and semantic embeddings on every node. Topics connect to the rest of the
graph via three edges — `SUBTOPIC_OF` (hierarchy), `BELONGS_TO` (problem-side
classification), and `RESEARCHES` (papers). See the
[Topic Taxonomy]({{ site.baseurl }}/reference/topic-taxonomy) reference for the
full shipped hierarchy.

## Design decisions

**Closed-set taxonomy, not open string migration.** The original spec proposed
migrating existing `domain` strings into `Topic` nodes. What shipped instead is a
*closed-set* taxonomy: topics exist only if they are in the curated
[`seed_taxonomy.yml`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/data/seed_taxonomy.yml),
loaded fresh rather than migrated. This keeps the vocabulary clean and makes
classification auditable — extraction (E-8) binds the LLM to a `Literal` over
these names, so a paper can never be filed under an invented topic. It also
matches the project's "re-extract over migrate" stance.

**Three fixed levels, enforced structurally.** `TopicLevel` is an enum of exactly
`domain` / `area` / `subtopic`, and the loader validates parent→child transitions
(`∅ → domain → area → subtopic`). Domain nodes may not have a parent. This makes
the hierarchy predictable for both queries and the extractor.

**Curated, not exhaustive.** Rather than importing all ~65k OpenAlex concepts,
the taxonomy is ~30 nodes hand-focused on the project's corpus (graph retrieval,
knowledge graphs, NLP, ML, IR). Scaling it — versioning, branching, merge — is
deliberately deferred to backlog item **T-1**.

**Idempotent loading.** `load_taxonomy` merges each node on
`(name, level, parent_id)` and reports `{created, matched}`, so re-running against
an existing graph is a no-op. The taxonomy round-trips: `export_taxonomy` reads
the live graph back to the YAML shape (embeddings regenerated on import to keep
the file human-readable).

## How it works

- **Model:** `Topic` in
  [`models/entities.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/models/entities.py)
  — `name`, `description`, `level`, `parent_id`, `source`, `openalex_id`,
  1536-dim `embedding`, and denormalized `problem_count` / `paper_count`.
- **Loader / validator / exporter:**
  [`taxonomy.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/taxonomy.py)
  (`parse_taxonomy`, `load_taxonomy`, `export_taxonomy`, `flatten_taxonomy`).
- **Schema:** `topic_id_unique` constraint; `topic_name/level/source` indexes;
  `topic_embedding_idx` vector index (1536-dim, cosine) — all in
  [`schema.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/knowledge_graph/schema.py).
- **Embedding strategy:** each node embeds `"{name}: {description}"`, so the
  one-line description in the YAML directly enriches semantic matching.
- **Assignment:** E-1 shipped the *infrastructure*; automatic per-paper topic
  assignment during ingestion arrived with the closed-set extractor in
  [E-8]({{ site.baseurl }}/design/e8-extraction-prompt-expansion).

For the node's attributes and its edges in context, see the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog#topic-e-1) and
[Entity Relationships]({{ site.baseurl }}/reference/entity-relationships).

## Verification

- **Tests:** Topic model validation, schema init, idempotent taxonomy load,
  export round-trip, and CRUD.
- **CI smoke:** the [CI smoke test]({{ site.baseurl }}/design/ci-smoke-test)
  asserts `Topic` nodes and `BELONGS_TO` / `RESEARCHES` edges land in an
  ephemeral Neo4j on every ingestion run.
- **Status:** VERIFIED — shipped via the E-8 V1 extractor and
  entity-pipeline-orchestration cycles.

## Related

- Reference: [Topic Taxonomy]({{ site.baseurl }}/reference/topic-taxonomy)
- Follows: [E-8 · Extraction prompt expansion]({{ site.baseurl }}/design/e8-extraction-prompt-expansion) (populates topics from text)
- Deferred: **T-1** — taxonomy management at scale (versioning / branching / merge)
