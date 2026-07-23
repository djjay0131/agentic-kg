# Feature Catalog — Master Index

Last updated: 2026-07-21

Single source of truth for every feature ever spec'd in this project — one line per feature plus a link to the full spec. Backlog items (unbuilt) live in the second half.

**Maintenance:** Kept current by `/constellize:memory:update`. When a spec's status changes (SPECIFIED → IMPLEMENTED → VERIFIED) or a new spec lands in `llm/features/`, the memory-update skill refreshes the tables below alongside the other memory-bank files.

Status key: **VERIFIED** (shipped + all four gates pass) · **IMPLEMENTED** (built, gates pending) · **SPECIFIED** (spec written, no code) · **BACKLOG** (needs spec).

---

## Shipped Specs (`llm/features/`)

Every spec that has reached SPECIFIED or beyond. Newest first within each theme.

### Entity expansion arc — E-1..E-8 + orchestration

| # | Feature | Status | One-liner |
|---|---------|--------|-----------|
| E-1 | [Topic / Research Area entities](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/topic-research-area-entities.md) | VERIFIED | First-class Topic nodes with hierarchy; replaces flat `domain` string; enables `BELONGS_TO` graph edges. |
| E-2 | [ResearchConcept entities](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/research-concept-entities.md) | VERIFIED | Generic research concepts as nodes; `INVOLVES_CONCEPT` / `DISCUSSES` edges; embedding-based dedup. |
| E-3 | [Model / Architecture entities](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/model-entity.md) | VERIFIED | ML models as first-class nodes (extracted from Baseline strings); `USES_MODEL`, `VARIANT_OF`. |
| E-4 | [Method / Methodology entities](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/method-entity.md) | VERIFIED | Research methods as nodes; `APPLIES_METHOD` edges from papers. |
| E-5 | [Citation graph](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/citation-graph.md) | VERIFIED | `CITES` edges from Semantic Scholar reference lists; influence chains + hub analysis. |
| E-6 | [Entity descriptions at create-time](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/entity-descriptions.md) | VERIFIED | Backfills `description` on Topic/Concept/Model/Method for richer `{name}: {description}` embeddings. |
| E-7 | [Cross-entity normalization](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/cross-entity-normalization.md) | VERIFIED | LLM router disambiguates Concept vs Model vs Method for the same surface form (e.g. "attention mechanism"). |
| E-8 V1 | [Extraction prompt expansion (Topics + Concepts)](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/extraction-prompt-expansion.md) | VERIFIED | Extends ingestion extractor to populate Topic + ResearchConcept from paper text. |
| E-8 V2 | [Extraction prompt expansion V2 (Models + Methods + Citations)](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/extraction-prompt-expansion-v2.md) | VERIFIED | Adds Model + Method extractors and wires citation population into `PaperImporter`. |
| — | [Entity pipeline orchestration](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/entity-pipeline-orchestration.md) | VERIFIED | Wires E-1..E-8 V2 + E-7 into production `ingest_papers`; default-on with skip-check + audit trail. |

### Ingestion + infra

| # | Feature | Status | One-liner |
|---|---------|--------|-----------|
| D-1 | [Ingest real papers into KG](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/d1-ingest-real-papers.md) | VERIFIED | End-to-end ingestion CLI: search → import metadata → extract Problems → integrate. |
| D-1a | [Cloud Run Jobs async ingestion](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/cloud-run-jobs-ingestion.md) | VERIFIED | Terraform-managed Cloud Run Job for durable async ingestion; env-var driven, no in-memory job store. |
| — | [CI smoke test (ingestion loop)](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/ci-smoke-test-ingestion.md) | VERIFIED | GHA workflow — daily cron + PR path-filter + `workflow_dispatch` — asserts entity edges land in ephemeral Neo4j. |
| — | [Deploy pipeline fix + version pinning](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/deploy-pipeline-fix.md) | IMPLEMENTED (PR-1) | PR-1 DONE 2026-07-14: Deploy Master first green in repo history, ingest-Job deploy step, `/version`, SHA-parity (AC-6) verified. Remaining: PR-2 (TF lifecycle + AC-8 lint), PR-3 (version pinning). |
| — | [PDF Acquisition Reliability](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/content-acquisition-resilience.md) | DRAFT | Source-selection + fetch hardening for PDF acquisition (missing `pdf_url` / empty text); follow-on to the SM-1 normalizer investigation. |

### Docs / site

| # | Feature | Status | One-liner |
|---|---------|--------|-----------|
| — | [Enhance GitHub Pages site](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/enhance-github-pages.md) | VERIFIED (Phase A) | Rebuilds docs generator to read `llm/memory_bank/`; unified nav; auto-published backlog. |

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
| P-8 | Tighten deploy SA to least-privilege | Needs Spec | Follow-up to `deploy-pipeline-fix`. Replace `roles/run.admin` on `gh-deploy@vt-gcp-00042` with `roles/run.developer` + resource-level `roles/run.invoker` on the 3 known Cloud Run targets. Ships after PR-3 lands and staging is stable. |
| T-1 | Taxonomy management at scale | Needs Spec | Versioned taxonomy with branching + merge + conflict resolution; flagged by E-1. |
| L-1 | Local / low-cost SLM client | Needs Spec | Third `BaseLLMClient` backed by Llama 3.x / Gemma / Phi-3 for narrow tasks (description-gen, dedup tie-breaking, routing). |

### Follow-ups from the first live smoke run (2026-07-02)

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| SM-1 | PDF acquisition reliability (source selection + fetch hardening) | **SPECIFIED 2026-07-14** (spec: `content-acquisition-resilience.md`) | Post-SM-4 smoke (papers=3, entities=0) root-caused: PDF download failed ("Server disconnected" — publisher `ojs.aaai.org` blocks CI IPs) and the pipeline never tried the paper's arXiv PDF though the normalizer captures the arXiv ID. Spec: **published/`openAccessPdf` source first, arXiv PDF fallback** when unreachable (published is authoritative; breadth-first latency guard); request headers + bounded transient retry on PDF fetch; S2 429 bounded retry (no silent drops); **NO abstract fallback — full text or the paper fails loudly** (user decision); failures categorized by reason (`failed_blocked`/`failed_404`/`failed_thin`/`failed_no_pdf_source`) in run metrics. 7 ACs. Next: `/constellize:feature:implement`. |
| SM-4 | Extraction dies on `instructor` import — **root cause: unused `denario` dep transitively pins `openai==1.99.9`** | **DONE — merged (#36) & deployed 2026-07-14** (spec: `extraction-dep-pinning.md`; AC-6 SHA-parity verified on this deploy) — the floors-first plan hit `resolution-too-deep`; `uv` diagnosed the real cause: `denario 1.0.1` → `cmbagent-autogen` **hard-pins `openai==1.99.9`**, and the only instructor accepting that (`<1.14`) fails to import, while `instructor>=1.14` needs `openai>=2.0` → unsatisfiable. `denario` is imported NOWHERE in `packages/` (dead weight). Fix: **remove `denario` from `packages/core/pyproject.toml` + root `pyproject.toml`**; add `instructor>=1.14` + `openai>=2.0`; fix both `_get_instructor_client` except blocks (`ModuleNotFoundError` vs `ImportError`, surface real error). Verified: clean resolve 0.8s → instructor 1.15.4/openai 2.46.0; 32 extraction tests pass in a fresh clean-resolve venv. New `test_instructor_import.py`. Next: merge → first real staging build+deploy (verifies deploy AC-6) → smoke-ingest entities>0 (AC-4). **If denario is ever actually integrated, wire it as an optional extra so it can't re-pin openai.** | PR #27 smoke (key present, abstract present) fails: `problem_extractor - ERROR - Failed to extract from SectionType.ABSTRACT: instructor package not installed` — but instructor IS installed. Root cause: `packages/core/pyproject.toml` floor-only pins (`instructor>=1.0.0`, `pydantic>=2.0.0`, openai unpinned) + the heavy `denario`/`cmbagent` tree resolve `instructor 1.12.0`/`openai 1.99.9` in CI/Docker, where `import instructor` raises `ImportError`; `llm_client.py:194` masks it as "not installed." Two fixes: (1) pin a known-good `instructor`+`openai`+`pydantic` set (or add upper bounds) so CI/Docker resolve the same working versions as a dev `.venv`; (2) fix the `except ImportError` at `llm_client.py:194` + `:311` to surface the real import error instead of the misleading "not installed" message. **Likely affects the DEPLOYED Cloud Run Job too** (same unpinned `pip install ./packages/core`), so it blocks node extraction in staging, not just CI. |
| SM-1b | Broader open-access PDF resolution (Unpaywall) for non-arXiv papers | Deferred (fast-follow to SM-1) | SM-1 covers arXiv-backed papers (published-first, arXiv fallback). Papers with a blocked published URL and no arXiv ID still `failed_blocked`/`failed_no_pdf_source`. Add Unpaywall (DOI → best OA PDF, often a CI-reachable repo) as an additional candidate source. Prioritize based on SM-1's categorized failure metrics (only worth it if the non-arXiv tail is large). |
| SM-4b | Whole-graph lock for extraction deps (`uv.lock`) | Deferred (low priority) | With `denario` removed (SM-4) pip resolves core in <1s, so a lock is no longer needed for correctness. A committed `uv.lock` would still add whole-graph reproducibility against future transitive drift. Only pursue if drift recurs; adds `uv` to the critical-path toolchain. |
| SM-3 | Docs-site link check fails on backlog.html | Needs Spec | `build-preview` (HTML-Proofer) red: generated `backlog.html` links to spec `.md` files + `../memory_bank/productContext.md` not published into `_site`. Fix the Pages generator link rewriting or the BACKLOG relative links. Pre-existing since the docs-consolidation commits. |
| SM-5 | Lint debt in `packages/core/tests` (172 ruff violations) | Needs Spec | Surfaced when deploy-master's `test` job first ran (2026-07-13): `ruff check packages/core/tests` → 172 errors (71 F401, 49 E501, 44 I001, 8 F841); 115 auto-fixable via `ruff check --fix`. The deploy workflows were narrowed to `src`-only (matching code-review.yml) to unblock deploys. Clean up the test tree, then re-add `packages/core/tests` to the deploy-master/deploy-tag lint step so test-file quality is gated too. |
| SM-2 | Preflight WARN on empty section_text | Needs Spec | When 100% of imported papers have empty `section_text`, log ERROR / fail batch loudly; current behavior is silent zero counters. Small fix-forward. |
| SM-6 | V2 entity integration blocked by wrong attribute (`.mentions` → `.mention_results`) | **SPECIFIED 2026-07-23** (spec: `v2-integration-mention-attr-fix.md`) | With SM-4 unblocking extraction, the daily smoke now reaches graph assertions and V1 populates (problems/concepts 20/20/20) — but **all V2 counters are 0**. Root cause: `ingestion.py:550` reads `v1_integration.mentions`, but `integrate_extracted_problems` returns an `IntegrationResultV2` whose mention list is `mention_results` (no `.mentions` attr) → `AttributeError` per paper, swallowed by the per-paper `try/except` at `:529` into `extraction_errors` → V2 (Topic/Concept/Model/Method) skipped. Introduced in loop-closure commit `7289588`; hidden until SM-4 let the path execute. **One-line fix + regression test** (the test must be red on the current line, since the outer catch hides the crash). With SM-1 (#44) resolved, this is the only remaining blocker to a green smoke. Next: `/constellize:feature:implement v2-integration-mention-attr-fix`. |

---

## Validation success criteria (not features)

Require spec of *how* to measure.

| # | Criterion | Source | Status |
|---|-----------|--------|--------|
| V-1 | Extraction F1 within 10% of inter-annotator agreement | [productContext](https://github.com/djjay0131/agentic-kg/blob/master/llm/memory_bank/productContext.md) | Not measured |
| V-2 | MRR/nDCG improvement over keyword + citation baselines | [productContext](https://github.com/djjay0131/agentic-kg/blob/master/llm/memory_bank/productContext.md) | Not measured |
| V-3 | Faster time to actionable continuation | [productContext](https://github.com/djjay0131/agentic-kg/blob/master/llm/memory_bank/productContext.md) | Not measured |
| V-4 | Higher user-reported confidence vs. opaque AI | [productContext](https://github.com/djjay0131/agentic-kg/blob/master/llm/memory_bank/productContext.md) | Not measured |
| V-5 | Active use by research teams | [productContext](https://github.com/djjay0131/agentic-kg/blob/master/llm/memory_bank/productContext.md) | Not achieved |

---

## Dependency graph (backlog)

```
Entity expansion arc ✓ (E-1..E-8 + orchestration + CI smoke)
    └──► Community detection (C-1, C-2, C-3)
            └──► RAG retrieval (R-1..R-5)
    └──► Validation & metrics (D-2, D-3, D-4)

Production readiness (P-1..P-7) — parallel track
```
