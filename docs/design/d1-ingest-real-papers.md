---
title: D-1 · Ingest real papers
parent: Design
nav_order: 10
---

# D-1 · Ingest real papers

{: .label .label-green }
VERIFIED

**Backlog ID:** D-1 · **Depends on:** data-acquisition clients, extraction
pipeline, KGIntegratorV2 · **Enables:** every populated-graph feature
(community detection, RAG retrieval, visualization) · **Spec:**
[`d1-ingest-real-papers.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/d1-ingest-real-papers.md)

## Why

Every piece of the machine existed independently — clients that could search
Semantic Scholar / arXiv / OpenAlex, a pipeline that could turn a PDF into
problems, and a canonical integrator that could deduplicate them — but nothing
chained them together. The graph held only test data, so the visualization was
empty and the core value proposition ("point at a topic, get a navigable
knowledge graph") was untested. D-1 is the end-to-end backbone: one orchestration
function that turns a search query into a populated, provenance-complete graph,
exposed as both a CLI command and an API job. Everything else in the project
rides on it.

## What shipped

A single `ingest_papers()` orchestrator with four phases — **search & import →
extract → integrate → sanity-check** — plus the `ingest` CLI subcommand and a
`POST /api/ingest` endpoint that both call it. Papers are found across three
sources, deduplicated by DOI, imported as `Paper`/`Author` nodes, run through a
5-way parallel extraction, and integrated into the canonical architecture with a
full provenance chain (`ProblemMention -[:EXTRACTED_FROM]-> Paper
-[:AUTHORED_BY]-> Author`). A run ends with five structural sanity checks and a
counted `IngestionResult`.

## Design decisions

**Per-paper loop, not BatchProcessor.** The spec proposed reusing
`BatchProcessor`'s SQLite job queue for the extract phase. What shipped is a
plain per-paper `for` loop over `PaperProcessingPipeline.process_pdf_url()`,
wrapped in per-paper `try/except` so one bad PDF never sinks the batch (AC-14).
Resume/queue durability moved to the API layer's Cloud Run Job instead of an
in-loop SQLite queue.

**Grew into the entity pipeline.** D-1 began as problem-only (Paper → Problem).
As later features landed it became the orchestration seam that runs all five
extractors in parallel — problems (V1) plus Topic / ResearchConcept / Model /
Method (V2) — then E-7 cross-entity normalization, then E-5 citation-graph
population. Each is gated by a flag (`--no-extract-entities`,
`--no-normalize-cross-entity`, `--no-populate-citations`) so a V1-only caller
pays zero extra LLM cost.

**Re-ingest is safe by construction.** Two guards protect existing work. The
AC-13 purge guardrail refuses to clobber a paper that carries non-extraction
edges (manual `SOLVED_BY`, curated tags) unless `--force-rewrite` is passed. The
AC-21 cost guard skips any paper already extracted under the current taxonomy
hash unless `--force-reextract` is passed — so re-running a query is cheap and
idempotent rather than a full re-spend.

**Fix, don't crash, on missing provenance.** The required EXTRACTED_FROM fix
shipped in `_store_mention_node`, but as an `OPTIONAL MATCH` + `FOREACH` guard:
a mention still writes even if its Paper isn't present, and the missing-edge case
surfaces through sanity check #2 instead of an exception.

## How it works

- **Orchestrator:** `ingest_papers()` and `run_sanity_checks()` in
  [`ingestion.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/ingestion.py)
  — the four-phase pipeline and its five sanity checks.
- **Phase 1 (search + import):** `PaperAggregator.search_papers()` in
  [`aggregator.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/data_acquisition/aggregator.py)
  fans out over Semantic Scholar / arXiv / OpenAlex and dedups by DOI;
  [`importer.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/data_acquisition/importer.py)
  normalizes each result (`normalized_to_kg_paper` / `normalized_to_kg_author`)
  and merges `Paper` + `Author` nodes.
- **Phase 2/3 (extract + integrate):** the per-paper loop resolves text
  (PDF sections, else abstract), runs `extract_all_entities()`, routes problems
  through `KGIntegratorV2.integrate_extracted_problems()` and entities through
  `integrate_paper_entities()`. The entity path is documented in
  [E-8]({{ site.baseurl }}/design/e8-extraction-prompt-expansion) and
  [entity-pipeline-orchestration]({{ site.baseurl }}/design/entity-pipeline-orchestration).
- **CLI:** the `ingest` subcommand in
  [`cli.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/cli.py)
  runs synchronously (`--query`, `--limit`, `--sources`, `--dry-run`,
  `--sanity-check-only`, `--json`, plus the re-ingest flags above).
- **API:** `POST /api/ingest` in
  [`routers/ingest.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/api/src/agentic_kg_api/routers/ingest.py)
  triggers a Cloud Run **Job** whose entrypoint is
  [`job_runner.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/job_runner.py);
  it returns a `trace_id` immediately, persists an `IngestionRun` node, and
  `GET /api/ingest/{trace_id}` polls status from Neo4j.

For the nodes and edges this pipeline writes, see the
[Entity Catalog]({{ site.baseurl }}/reference/entity-catalog) and
[Entity Relationships]({{ site.baseurl }}/reference/entity-relationships).

## Verification

- **Tests:** orchestration flow, CLI wiring, force-rewrite / force-reextract
  guards, V2 entity orchestration, purge, and sanity checks
  (`test_ingestion*.py`, `test_cli_ingest.py`, `test_smoke_assert.py`).
- **CI smoke:** an ingestion run against ephemeral Neo4j asserts the expected
  node and edge types land — the same gate referenced by the entity design notes.
- **Status:** VERIFIED — proved on a "graph-based retrieval" corpus; all five
  sanity checks pass and the provenance chain is complete end to end.

## Related

- Reference: [Entity Catalog]({{ site.baseurl }}/reference/entity-catalog) ·
  [Entity Relationships]({{ site.baseurl }}/reference/entity-relationships)
- Feeds: [E-8 · Extraction prompt expansion]({{ site.baseurl }}/design/e8-extraction-prompt-expansion)
  and [entity-pipeline-orchestration]({{ site.baseurl }}/design/entity-pipeline-orchestration)
  (what runs inside Phase 2/3)
- Deferred: scale ingestion (hundreds+ papers) and richer resume/progress
