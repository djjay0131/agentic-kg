# Feature Catalog — Master Index

Last updated: 2026-07-07

Single source of truth for every feature ever spec'd in this project — one line per feature plus a link to the full spec. Backlog items (unbuilt) live in the second half.

**Maintenance:** Kept current by `/constellize:memory:update`. When a spec's status changes (SPECIFIED → IMPLEMENTED → VERIFIED) or a new spec lands in `llm/features/`, the memory-update skill refreshes the tables below alongside the other memory-bank files.

Status key: **VERIFIED** (shipped + all four gates pass) · **IMPLEMENTED** (built, gates pending) · **SPECIFIED** (spec written, no code) · **BACKLOG** (needs spec).

---

## Shipped Specs (`llm/features/`)

Every spec that has reached SPECIFIED or beyond. Newest first within each theme.

### Entity expansion arc — E-1..E-8 + orchestration

| # | Feature | Status | One-liner |
|---|---------|--------|-----------|
| E-1 | [Topic / Research Area entities](topic-research-area-entities.md) | VERIFIED | First-class Topic nodes with hierarchy; replaces flat `domain` string; enables `BELONGS_TO` graph edges. |
| E-2 | [ResearchConcept entities](research-concept-entities.md) | VERIFIED | Generic research concepts as nodes; `INVOLVES_CONCEPT` / `DISCUSSES` edges; embedding-based dedup. |
| E-3 | [Model / Architecture entities](model-entity.md) | VERIFIED | ML models as first-class nodes (extracted from Baseline strings); `USES_MODEL`, `VARIANT_OF`. |
| E-4 | [Method / Methodology entities](method-entity.md) | VERIFIED | Research methods as nodes; `APPLIES_METHOD` edges from papers. |
| E-5 | [Citation graph](citation-graph.md) | VERIFIED | `CITES` edges from Semantic Scholar reference lists; influence chains + hub analysis. |
| E-6 | [Entity descriptions at create-time](entity-descriptions.md) | VERIFIED | Backfills `description` on Topic/Concept/Model/Method for richer `{name}: {description}` embeddings. |
| E-7 | [Cross-entity normalization](cross-entity-normalization.md) | VERIFIED | LLM router disambiguates Concept vs Model vs Method for the same surface form (e.g. "attention mechanism"). |
| E-8 V1 | [Extraction prompt expansion (Topics + Concepts)](extraction-prompt-expansion.md) | VERIFIED | Extends ingestion extractor to populate Topic + ResearchConcept from paper text. |
| E-8 V2 | [Extraction prompt expansion V2 (Models + Methods + Citations)](extraction-prompt-expansion-v2.md) | VERIFIED | Adds Model + Method extractors and wires citation population into `PaperImporter`. |
| — | [Entity pipeline orchestration](entity-pipeline-orchestration.md) | VERIFIED | Wires E-1..E-8 V2 + E-7 into production `ingest_papers`; default-on with skip-check + audit trail. |

### Ingestion + infra

| # | Feature | Status | One-liner |
|---|---------|--------|-----------|
| D-1 | [Ingest real papers into KG](d1-ingest-real-papers.md) | VERIFIED | End-to-end ingestion CLI: search → import metadata → extract Problems → integrate. |
| D-1a | [Cloud Run Jobs async ingestion](cloud-run-jobs-ingestion.md) | VERIFIED | Terraform-managed Cloud Run Job for durable async ingestion; env-var driven, no in-memory job store. |
| — | [CI smoke test (ingestion loop)](ci-smoke-test-ingestion.md) | VERIFIED | GHA workflow — daily cron + PR path-filter + `workflow_dispatch` — asserts entity edges land in ephemeral Neo4j. |

### Docs / site

| # | Feature | Status | One-liner |
|---|---------|--------|-----------|
| — | [Enhance GitHub Pages site](enhance-github-pages.md) | VERIFIED (Phase A) | Rebuilds docs generator to read `llm/memory_bank/`; unified nav; auto-published backlog. |

---

## Backlog — Unbuilt or Partial

Ordered by category, roughly by priority within category.

### Validation & metrics (post real-data)

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| D-1b | GCS ingestion research log | Needs Spec | Per-paper markdown progress files to GCS; durable provenance surviving crashes. Follow-on to D-1a. |
| D-2 | Extraction reliability metrics | Needs Spec | F1 vs. inter-annotator agreement — success criterion from productContext. |
| D-3 | Retrieval quality benchmarks | Needs Spec | MRR / nDCG vs. keyword + citation baselines. |
| D-4 | Agent decision accuracy validation | Needs Spec | EvaluatorAgent >90% human agreement; consensus workflow >85%. |
| S-3 | Sprint 10 integration tests (golden dataset) | Partial Spec | Golden dataset benchmarks against live Neo4j not yet run. |

### Community detection & summarization

Depends on the entity expansion arc for a richer graph.

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| C-1 | Community detection | Partial Spec | Leiden/Louvain over full graph; Community nodes at multiple levels; incremental updates. |
| C-2 | Hierarchical graph summarization | Partial Spec | LLM summaries per community at multiple resolutions (domain → topic → concept). |
| C-3 | Community browsing API + UI | Needs Spec | Endpoints and frontend for exploring landscape by community. |

### Graph-based RAG retrieval

The "killer feature". Depends on entity expansion + communities.

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| R-1 | Query-facing vector search | Partial Spec | Expose vector search across all entity types for user queries (currently internal). |
| R-2 | Graph neighbor expansion | Partial Spec | Configurable-depth traversal from vector hits; context assembly from graph paths. |
| R-3 | LLM synthesis endpoint | Partial Spec | `POST /api/query` — NL question → answer with provenance from graph. |
| R-4 | Community-aware retrieval | Partial Spec | Include community summaries in retrieval context for global/thematic questions. |
| R-5 | RAG evaluation & benchmarks | Needs Spec | Improvement vs. vector-only for multi-hop questions. |

### Production readiness

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| P-1 | Production deployment | Needs Spec | `terraform apply -var-file=envs/prod.tfvars`; needs runbook + monitoring + alerting. |
| P-2 | Neo4j production hosting docs | Needs Spec | Aura vs. self-managed decision; backup + failover procedures. |
| P-3 | Multi-hop graph traversal | Needs Spec | FR-2.3.4 from Sprint 01; `max_depth` on `get_related_problems()`. |
| P-4 | Referential integrity on delete | Needs Spec | Check `EXTRACTED_FROM` before paper delete; prefer soft delete. |
| P-5 | Scalability testing | Needs Spec | Validate at 100+ papers; current system untested beyond small datasets. |
| P-6 | Auto-publish backlog to Pages | Has Spec | Extend `.github/scripts/generate_docs.py` to regenerate from this file on every push. |
| ~~P-7~~ | ~~Migrate `update-docs.yml` trigger paths~~ | **Resolved** | Workflows already watch `llm/memory_bank/**`. Verified 2026-07-07. |
| T-1 | Taxonomy management at scale | Needs Spec | Versioned taxonomy with branching + merge + conflict resolution; flagged by E-1. |
| L-1 | Local / low-cost SLM client | Needs Spec | Third `BaseLLMClient` backed by Llama 3.x / Gemma / Phi-3 for narrow tasks (description-gen, dedup tie-breaking, routing). |

### Follow-ups from the first live smoke run (2026-07-02)

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| SM-1 | Investigate aggregator normalizer | Needs Spec | First real smoke run showed `NormalizedPaper.pdf_url=None` + empty abstract on all 3 imported papers; root-cause the OpenAlex / Semantic Scholar normalizers. |
| SM-2 | Preflight WARN on empty section_text | Needs Spec | When 100% of imported papers have empty `section_text`, log ERROR / fail batch loudly; current behavior is silent zero counters. Small fix-forward. |

---

## Validation success criteria (not features)

Require spec of *how* to measure.

| # | Criterion | Source | Status |
|---|-----------|--------|--------|
| V-1 | Extraction F1 within 10% of inter-annotator agreement | [productContext](../memory_bank/productContext.md) | Not measured |
| V-2 | MRR/nDCG improvement over keyword + citation baselines | [productContext](../memory_bank/productContext.md) | Not measured |
| V-3 | Faster time to actionable continuation | [productContext](../memory_bank/productContext.md) | Not measured |
| V-4 | Higher user-reported confidence vs. opaque AI | [productContext](../memory_bank/productContext.md) | Not measured |
| V-5 | Active use by research teams | [productContext](../memory_bank/productContext.md) | Not achieved |

---

## Dependency graph (backlog)

```
Entity expansion arc ✓ (E-1..E-8 + orchestration + CI smoke)
    └──► Community detection (C-1, C-2, C-3)
            └──► RAG retrieval (R-1..R-5)
    └──► Validation & metrics (D-2, D-3, D-4)

Production readiness (P-1..P-7) — parallel track
```
