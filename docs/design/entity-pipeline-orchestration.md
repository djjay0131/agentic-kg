---
title: Entity pipeline orchestration
parent: Design
nav_order: 9
---

# Entity pipeline orchestration

{: .label .label-green }
VERIFIED

**Backlog:** entity-pipeline-orchestration (loop-close; surfaced after E-7 verify) ·
**Depends on:** [E-1](../design/e1-topic-entities) … [E-8](../design/e8-extraction-prompt-expansion)
+ [E-7](../design/e7-cross-entity-normalization) · **Spec:**
[`entity-pipeline-orchestration.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/entity-pipeline-orchestration.md)

## Why

The entity-expansion arc built a lot of machinery — four new extractors
(Topic / ResearchConcept / Model / Method), a five-way parallel orchestrator, the
E-7 cross-entity router, and a V2 entity integrator with audit trails — and
**none of it ran in production.** `ingest_papers`, the single entry point behind
both `agentic-kg ingest` and the Cloud Run Job, still did only V1: import
metadata, extract *problems* from the PDF, route ProblemMentions. After ingesting
N papers the graph had Paper / Author / CITES / ProblemMention — and zero Topic
edges, ResearchConcept nodes, `USES_MODEL`, `APPLIES_METHOD`, or the Paper audit
fields. This is the capstone: it wires the whole dormant arc into the production
ingestion path, default-on, so an ordinary ingest run finally produces the richly
typed graph the arc was built for.

## What shipped

One refactor of `ingest_papers` Phase 2/3 into a **single per-paper loop** that
runs, for every paper: problem extraction (V1), the four entity extractors in
parallel via `extract_all_entities`, E-7 cross-entity normalization, V1
mention/concept integration, then V2 entity integration via
`integrate_paper_entities`. It is **on by default** (`extract_entities=True`,
`normalize_cross_entity_collisions=True`), guarded by a **per-paper skip check**
so re-running the same query is near-free, and it writes an **audit trail** onto
each Paper node (`taxonomy_hash`, `extraction_incomplete`, `normalization_audit`)
plus additive counters on `IngestionResult`. No new entity types, extractors,
prompts, schema, or repository methods — pure orchestration of existing parts.

## Design decisions

**Default-on — the cost is the point.** Shipping with the extractors defaulted
*off* would have left the pipeline dormant indefinitely (E-6's
`generate_description=False` default never got flipped in months). So this is a
deliberate **breaking change** for operators: every ingest now runs ~5-6 extra LLM
calls per new paper. The skip check and the two opt-out flags are the mitigations.

**Universal extraction scope, with abstract fallback.** All four entity
extractors run on *every* paper. Papers with a PDF use the rich
`abstract + introduction + methods + experiments` text; PDF-less papers fall back
to the abstract alone; truly empty papers hit each extractor's empty-input
short-circuit (zero LLM cost) and persist as metadata only.

**A skip check, not a migration.** Rather than a one-off backfill, re-ingestion is
made cheap: a paper already extracted under the current taxonomy snapshot and not
flagged incomplete is skipped end-to-end for the LLM-touching body. Only *new*
papers pay the extraction bill. This composes for free with the AC-13 purge path,
which clears `taxonomy_hash` and so forces re-extraction on the next run.

**One LLM client per batch — the L-1 swap point.** `get_openai_client()` is called
once and threaded into all four extractors *and* the normalizer. Swapping that one
factory for a local-SLM factory flips the entire pipeline's model with no other
code change.

## How it works

- **Orchestrator:** `ingest_papers` in
  [`ingestion.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/ingestion.py)
  — per-batch deps (`TopicExtractor`/`ConceptExtractor`/`ModelExtractor`/`MethodExtractor`,
  one `EmbeddingService`, one LLM client) build once inside the `if extract_entities`
  block; the per-paper loop then does purge-guard → skip-check → text resolution →
  `extract_all_entities` → `normalize_cross_entity` → V1 `integrate_extracted_problems`
  → V2 `integrate_paper_entities`, all inside one `try/except` for per-paper failure
  isolation.
- **Text resolution:** `_build_extractor_section_text` joins the wanted
  `SegmentedDocument` sections; returns `""` (safe short-circuit) when a PDF has
  none of them.
- **Skip check:** `_can_skip_entity_extraction` reads the Paper node and returns
  `True` only when `taxonomy_hash` matches the batch hash **and**
  `extraction_incomplete` is not `True`; increments `result.papers_skipped_complete`.
- **V1 → V2 sequencing:** V1 runs first (its `ProblemMention[]` feed the V2 B3
  problem↔concept linker); V2 gates on *any* successful extraction or a V1 result.
- **Audit trail:** `integrate_paper_entities` (from
  [E-8](../design/e8-extraction-prompt-expansion)) writes `taxonomy_hash`,
  `extraction_incomplete`, and the E-7 `normalization_audit` JSON onto Paper;
  new counters (`topics_linked`, `concepts_v2_linked`, `models_linked`,
  `methods_linked`, `papers_marked_incomplete`, `papers_with_normalization_audit`,
  `papers_skipped_complete`) land on `IngestionResult`.
- **CLI + Job parity:** three flags on
  [`cli.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/cli.py)
  (`--no-extract-entities`, `--no-normalize-cross-entity`, `--force-reextract`)
  mirror three env vars in
  [`job_runner.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/job_runner.py)
  (`EXTRACT_ENTITIES`, `NORMALIZE_CROSS_ENTITY`, `FORCE_REEXTRACT`), all defaulting on.

This note ties together the whole arc — [E-1]({{ site.baseurl }}/design/e1-topic-entities),
[E-2]({{ site.baseurl }}/design/e2-research-concepts),
[E-3]({{ site.baseurl }}/design/e3-model-entities),
[E-4]({{ site.baseurl }}/design/e4-method-entities),
[E-5]({{ site.baseurl }}/design/e5-citation-graph),
[E-6]({{ site.baseurl }}/design/e6-entity-descriptions),
[E-7]({{ site.baseurl }}/design/e7-cross-entity-normalization),
and [E-8]({{ site.baseurl }}/design/e8-extraction-prompt-expansion) — into one
production path. For the nodes and edges it populates, see the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog) and
[Entity Relationships]({{ site.baseurl }}/reference/entity-relationships).

## Verification

- **Tests:** 31 mocked orchestration tests in
  [`test_ingestion_v2_orchestration.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/tests/test_ingestion_v2_orchestration.py)
  — flag combinatorics (both-on happy path, `extract_entities=False`,
  `normalize=False`, both-off V1 baseline), the skip check (skip / `--force-reextract`
  bypass / `extraction_incomplete` re-extraction), per-stage error injection, and the
  PDF-less / abstract-less / DOI-less edge cases. No testcontainers, no live LLM.
- **CI smoke:** the [CI smoke test]({{ site.baseurl }}/design/ci-smoke-test) is what
  exercises this loop against an ephemeral Neo4j — a default-flags ingest is what
  lands the Topic / Concept / Model / Method edges the smoke test asserts.
- **Status:** VERIFIED — orchestration is the cycle that flipped every E-1…E-8
  entity type from "built and unit-tested" to "written on every ingest."

## Related

- Reference: [Entity Catalog]({{ site.baseurl }}/reference/entity-catalog) ·
  [Entity Relationships]({{ site.baseurl }}/reference/entity-relationships)
- Wires: [E-1 … E-8]({{ site.baseurl }}/design/e8-extraction-prompt-expansion) +
  [E-7 cross-entity normalization]({{ site.baseurl }}/design/e7-cross-entity-normalization)
- Deferred: across-paper parallelism · per-batch cost/token telemetry
  (`result.llm_calls_made`) · full-pipeline calibration run
