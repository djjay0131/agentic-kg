---
title: E-8 · Extraction prompt expansion
parent: Design
nav_order: 8
---

# E-8 · Extraction prompt expansion

{: .label .label-green }
VERIFIED

**Backlog ID:** E-8 V1 + V2 · **Depends on:** E-1..E-6 · **Enables:** every
downstream query over topics / concepts / models / methods · **Specs:**
[`extraction-prompt-expansion.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/extraction-prompt-expansion.md)
(V1) ·
[`extraction-prompt-expansion-v2.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/extraction-prompt-expansion-v2.md)
(V2)

## Why

E-1 through E-5 shipped first-class entity types — `Topic`, `ResearchConcept`,
`Model`, `Method`, and the citation graph — with repository CRUD, APIs, and CLI
commands. But **nothing populated them from paper text.** Every `Topic`
assignment, every `ResearchConcept`, `Model`, and `Method` node had to be created
by hand (`assign-topic`, `create-concept`, `create-model`, …), and every
`Paper -CITES-> Paper` edge came from a manual `citation-graph` run. The
ingestion pipeline extracted only `Problem` statements; it *saw* a paper's topic,
concepts, models, methods, and references and wrote none of them. As real
ingestion scaled, that investment sat idle. E-8 is the feature that turns paper
text into E-1..E-5 graph structure automatically — it is what actually
**populates** those entity types.

## What shipped

**V1 (Topics + Concepts).** Two new paper-level extractors join `ProblemExtractor`
as parallel siblings. `TopicExtractor` classifies a paper against the
[Topic Taxonomy]({{ site.baseurl }}/reference/topic-taxonomy) and writes
`BELONGS_TO` edges; `ConceptExtractor` pulls the concepts a paper discusses and
writes `DISCUSSES` edges (deduping via E-2's embedding merge). A pure-Python B3
heuristic then draws `INVOLVES_CONCEPT` edges from the paper's problems to those
concepts using only the surface forms *this paper's* extractor emitted.

**V2 (Models + Methods + Citations).** Two more sibling extractors —
`ModelExtractor` and `MethodExtractor` — write `USES_MODEL` and `APPLIES_METHOD`
edges via E-3/E-4 create-or-merge. Separately, `PaperImporter.import_paper` now
calls E-5's `populate_citations` after persisting a Paper, so citation
neighbourhoods are non-empty on day one. The orchestrator runs **all five**
extractors in one `asyncio.gather`; one failing extractor never blocks the
others. See the [Entity Catalog]({{ site.baseurl }}/reference/entity-catalog) for
the node shapes these edges connect.

## Design decisions

**Topic is closed-set; everything else is open-set.** `TopicExtractor` binds the
LLM to a `Literal` over the taxonomy names snapshotted at construction — a paper
can never be filed under an invented topic; an out-of-taxonomy name fails Pydantic
validation and is dropped. `Concept`, `Model`, and `Method` extractors are
deliberately *open-set*: there is no fixed vocabulary, so they emit free-form
names and rely on E-2/E-3/E-4 embedding-dedup at write time to converge
("bert-base" → canonical `BERT`). Mirroring Topic's closed `Literal` for Models
was explicitly rejected — new models ship weekly.

**Per-instance taxonomy snapshot.** Each `TopicExtractor` reads
`seed_taxonomy.yml` once in `__init__` and builds its `Literal` schema and prompt
from that one snapshot. One Cloud Run Job → one extractor → one consistent
taxonomy for the whole batch; a mid-flight taxonomy edit only affects the *next*
job. `Paper.taxonomy_hash` records which snapshot classified each paper.

**Silent-degrade-but-accountable.** A known `LLMError` inside an extractor returns
`[]` with a WARN — no failure record. Any *unexpected* exception is caught by the
orchestrator's `_run` wrapper (which catches `BaseException`, including
`CancelledError`), recorded as a structured `ExtractionFailure`, and the Paper is
marked `extraction_incomplete=true` for a re-ingestion audit query. Partial
results are always committed.

**B3 uses per-paper aliases only.** The problem↔concept linker matches against the
surface forms the LLM emitted *for this paper*, never the merged concept's
accumulated alias list — this prevents alias-pollution, where a popular concept
would match ever more aggressively as papers accrue. A `min_alias_length=4` filter
plus a provenance-carrying `b3_deny_list.yml` suppress generic terms. The linker
was **not** extended to Models/Methods in V2 (those are paper-scope, not
problem-scope).

**Descriptions stay OFF on the ingestion path.** V2's `create_or_merge_model` /
`create_or_merge_method` calls omit `generate_description`, defaulting to `False`
(E-6 AC-11) — ~5 LLM calls per paper is the cost ceiling. Description generation
stays operator-driven via the E-6 CLI flags.

## How it works

- **Extractors:**
  [`topic_extractor.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/topic_extractor.py)
  (closed-set `Literal`, dynamic `pydantic.create_model`),
  [`concept_extractor.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/concept_extractor.py),
  [`model_extractor.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/model_extractor.py),
  and
  [`method_extractor.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/method_extractor.py)
  — each a single paper-level call with an empty-section short-circuit and a
  `min_confidence` filter.
- **Schemas & prompts:**
  [`schemas.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/schemas.py)
  (`ExtractedResearchConcept`, `ExtractedModel`, `ExtractedMethod`) and
  [`prompts/templates.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/prompts/templates.py)
  (an `EntityKind`-dispatched `SYSTEM_PROMPT_V1` / `USER_PROMPT_TEMPLATE_V1`
  family, extensible by duplicate-rename).
- **Orchestrator:** `extract_all_entities` in
  [`pipeline.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/pipeline.py)
  — five awaitables through one `asyncio.gather`, returning a
  `PaperExtractionResult` with per-extractor `failures`.
- **Writers:** `integrate_paper_entities` in
  [`kg_integration_v2.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/extraction/kg_integration_v2.py)
  writes `BELONGS_TO` / `DISCUSSES` / `USES_MODEL` / `APPLIES_METHOD`, runs the B3
  linker (`b3_linker.py`), and pins `taxonomy_hash` + `extraction_incomplete`.
- **Citations:** `PaperImporter.import_paper` in
  [`importer.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/data_acquisition/importer.py)
  calls E-5's `populate_citations` (default-on, best-effort, never propagates) on
  both the create and `update_existing` paths.
- **Wired end-to-end:**
  [`ingestion.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/ingestion.py)
  constructs all five extractors and calls `extract_all_entities` →
  `integrate_paper_entities` per paper — this is the live path, not just library
  scaffolding.

For the resulting node attributes and edges in context, see the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog) and the
[Topic Taxonomy]({{ site.baseurl }}/reference/topic-taxonomy).

## Verification

- **Tests:** each extractor's success path, taxonomy rejection, low-confidence
  filtering, empty-section short-circuit, five-way parallel orchestration,
  per-extractor degradation (known vs. unexpected exceptions), the B3 linker +
  deny-list + pollution-immunity, each integration writer, and the
  `populate_citations` default-on / exception-absorbed / update-path contracts.
- **Eval gate (`-m costly`):** a 5-paper hand-labeled fixture set (one per area)
  with dual precision floors + an anti-gaming recall tripwire — Topic/Concept
  gates from V1, Model/Method gates added in V2, both checked at
  `/constellize:feature:verify`.
- **Status:** VERIFIED — V1 (Units 1-13) and V2 both passed their verify gates;
  the pipeline is the live ingestion path.

## Related

- Populates: [E-1]({{ site.baseurl }}/design/e1-topic-entities) ·
  [E-2]({{ site.baseurl }}/design/e2-research-concepts) ·
  [E-3]({{ site.baseurl }}/design/e3-model-entities) ·
  [E-4]({{ site.baseurl }}/design/e4-method-entities)
- Wires in: [E-5]({{ site.baseurl }}/design/e5-citation-graph) (citation
  population at import time)
- Orchestration: entity-pipeline-orchestration (the `ingest_papers` path that
  runs the five extractors and their writers)
- Deferred: E-7 (cross-entity normalization), L-1 (low-cost SLM client for
  per-extractor cost routing)
