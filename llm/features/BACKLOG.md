# Feature Catalog — Master Index

Last updated: 2026-09-07

Single source of truth for every feature ever spec'd in this project — one line per feature plus a link to the full spec. Backlog items (unbuilt) live in the second half.

**Maintenance:** Kept current by `/constellize-memory-update`. When a spec's status changes (SPECIFIED → IMPLEMENTED → VERIFIED) or a new spec lands in `llm/features/`, the memory-update skill refreshes the tables below alongside the other memory-bank files.

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
| SM-1 | [PDF Acquisition Reliability](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/content-acquisition-resilience.md) | VERIFIED | Published/`openAccessPdf` source first, arXiv PDF fallback; no abstract fallback — a paper with no usable full text fails loudly, categorized by reason. |

### Extraction hardening — smoke-driven fixes (SM-*)

Each traced to a live `smoke-ingest` run; each unmasked the next.

| # | Feature | Status | One-liner |
|---|---------|--------|-----------|
| SM-4 | [Extraction dependency conflict + honest import errors](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/extraction-dep-pinning.md) | IMPLEMENTED | Removes the unused `denario` dep that transitively hard-pinned `openai==1.99.9` and made a working `instructor` unresolvable. |
| SM-6 | [V2 integration blocked by wrong attribute](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/v2-integration-mention-attr-fix.md) | IMPLEMENTED | `ingestion.py` read `v1_integration.mentions`; the real field is `mention_results`, so every V2 counter sat at 0 behind a swallowed `AttributeError`. |
| SM-7 | [Extraction rate-limit resilience](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/extraction-rate-limit-resilience.md) | VERIFIED | OpenAI TPM throttle + model lever so a 5-way parallel extraction batch stops self-inflicting 429s. |
| SM-8 | [B3 linker crash — `MentionIntegrationResult` lacks `.statement`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/b3-linker-mention-statement-fix.md) | IMPLEMENTED | Third latent `AttributeError` in the same chain; gives the result object the `ProblemMention`-like shape the B3 linker documents. Addendum SM-8b seeds the Topic taxonomy in smoke. |

### Segmenter defects

| # | Feature | Status | One-liner |
|---|---------|--------|-----------|
| SEG-1 | [Roman-numeral section headings never match](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/seg1-roman-numeral-headings.md) | VERIFIED | `SECTION_PATTERNS` admits only Arabic numbering, so no IEEE heading is recognized; `fact_completion` goes 0 → 25,586 chars of extractor input. |

### Docs / site

| # | Feature | Status | One-liner |
|---|---------|--------|-----------|
| — | [Enhance GitHub Pages site](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/enhance-github-pages.md) | VERIFIED (Phase A) | Rebuilds docs generator to read `llm/memory_bank/`; unified nav; auto-published backlog. |

---

## Backlog — Unbuilt or Partial

Ordered by category, roughly by priority within category.

### Segmenter defects — blocks the ground-truth diff (found 2026-07-27, extended 2026-08-09)

All from [`docs/ground-truth/segmenter-findings.md`](https://github.com/djjay0131/agentic-kg/blob/master/docs/ground-truth/segmenter-findings.md). **`SEG-n` numbering matches that document's root-cause sections**, so `SEG-3` ↔ cause *(3)*. Affects `packages/core/src/agentic_kg/extraction/section_segmenter.py` and therefore every entity extracted by `ingest_papers`.

**Severity: High.** Running the current segmenter over the 8-paper ground-truth chain, not one paper produced a clean abstract + introduction + methods + experiments block; only 1 of 8 produced an abstract; only 2 of 6 papers with a methods section had it recognized; `fact_completion` yields **zero** extractor input.

**Correction (2026-09-06, from the SEG-1 spec):** the findings doc's "zero entities, silently and with no error" is **stale** — SM-1 moved `_build_extractor_section_text` ahead of the `MIN_USABLE_CHARS` gate, so `fact_completion` is now logged, counted as `failed_thin`, and **skipped**. The cost is a *dropped* paper carrying 32 of the 53 chain entities, not a silent zero. What is still silent is **partial** segmentation: `cskg` clears the gate at 8,917 chars with no abstract and no methods and is indistinguishable from a healthy paper.

**Read before scheduling:** SEG-1..SEG-6 are about *locating* section boundaries and do **not** affect the gold labels (`scripts/segment_ground_truth.py` uses hand-verified boundaries by construction). SEG-7 is about which sections are *kept* once located, survives fixing all the others, and **is the one that reaches the labels**. Fix these before running the importer-vs-gold diff, or large recall gaps that are segmenter failures will be read as extractor failures.

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| SEG-7 | Four-section keep-list discards content even when boundaries are perfect | **Needs Decision** (then spec) | Highest priority — the only cause that reaches the gold labels. `_build_extractor_section_text` (`ingestion.py:198`) keeps exactly `abstract`/`introduction`/`methods`/`experiments`. Measured body coverage ranges 3.9%–71.7%; `kg_construction_survey` loses 18 of 22 chain entities, `llm_ontology_gen` 8 of 12 (including `scientific knowledge graph`, the chain's spine concept). Sharpest case is `fact_completion`: Section V (`Use Case: AI-KG`, 6,502 chars) is excluded as `use_case` and is the densest source of chain-recurring entities — `support score` occurs **zero** times in the extractor input. Three fix options in increasing order of change: (1) add `use_case`/`application`; (2) also add `results`/`background` (recovers `empire` at 35.2% and `llm_ontology_gen`); (3) invert the filter — keep everything except references/acknowledgments/appendices/author bios. Option 3 **requires SEG-6 first** (the survey would send 181k chars to one prompt). **Decide before diffing**: if the keep-list changes, the affected `paper_<slug>.txt` fixtures must be regenerated and their reviews redone, not patched. |
| SEG-6 | No upper length guard between segmenter and the LLM call | Needs Spec | 244,748 chars would be sent as a single extractor prompt (`kg_construction_survey`). Only `MIN_USABLE_CHARS = 250` guards the lower end; nothing guards the upper. Cheap on its own and a **prerequisite for SEG-7 option 3**. |
| SEG-5 | Methods/experiments sections are usually not named "Methods" or "Experiments" | **Needs Decision** (then spec) | The stubborn one: **7 of 12 wanted sections unmatched, only 2 of 6 methods sections found**, and **5 of the 7 misses survive a perfect SEG-1 fix**, for four distinct reasons. (a) *Named after the contribution* — `The Computer Science Knowledge Graph`, `III. SciCheck`; no keyword list can catch these. (b) *Journal vocabulary* — `Technical Validation`; fixable via SEG-4. (c) *Descriptive sentence headings* — `4. Integrating LLMs and HiL into the SCICERO validation`, containing no method keyword at all. (d) *Pattern too strict* — `5. Experiment design and implementation` fails because `experiment(?:s\|al)?\s*(?:setup\|settings)?\s*$` allows only `setup`/`settings` as suffix; `IV. RESEARCH APPROACH` fails because the approach pattern admits only `our`/`proposed` as prefix. **(d) is cheap** — relax the anchors so a heading *containing* a keyword matches instead of requiring near-exact equality; recovers two misses immediately. (a) and (c) cannot be fixed by pattern-matching; options are a **positional fallback** (span between last front-matter section and first results/evaluation section → methods; works for `cskg`, `fact_completion`, `kg_validation_hitl`), **don't filter at all** (see SEG-7 option 3), or LLM-assisted heading classification (likely more machinery than warranted). Note the current failure is **silent** — an unmatched methods heading quietly removes the paper's core technical content from every extractor's input. |
| SEG-1 | Roman-numeral section headings never match | **VERIFIED 2026-09-07** (spec: `seg1-roman-numeral-headings.md`, 15/15 ACs, +129 tests, 100% coverage on all three modules) | Every pattern in `SECTION_PATTERNS` admits only Arabic numbering (`^(?:\d+\.?\s*)?introduction\s*$`), but IEEE-format papers use Roman numerals. In `fact_completion` the only heading that matched anything was the unnumbered `REFERENCES`. **Measured over all 8 PDFs while specifying:** `fact_completion` 0 → **25,586** chars, `empire` 12,405 → **17,953**, other six byte-identical. The spec **rejects the letter half** of the fix proposed here: `[A-Z]` promotes IEEE lettered *subsections* (`B. RESULTS AND DISCUSSION`, a child of `IV. EVALUATION`) to top level and costs 13,137 chars on `fact_completion`; stripping hierarchical `4.4.` costs `hypothesis_generation` 5,585 the same way. Chosen shape: strip a flat Roman/Arabic enumerator once in `_classify_heading` and delete the prefix group from all 34 literals, so SEG-3/4/5 vocabulary inherits numbering. Two corrections it carries: the "silent" framing below is stale (SM-1 makes this a loud `failed_thin` skip — the real cost is a **dropped** paper worth 32 chain entities), and a new root cause is filed as **SEG-11**. Still fixes only 2 of the 7 SEG-5 misses. Shipped result reproduced against the corpus; guard is `scripts/measure_segmentation.py`. Next: `/constellize-feature-verify`. |
| SEG-3 | Run-in abstracts defeat the standalone-line pattern | Needs Spec | `r"^abstract\s*$"` requires `Abstract` alone on a line. **Five distinct conventions across eight papers match none of it**: IEEE ESEM `Abstract—[Background.]`, IEEE Access `ABSTRACT In the last...`, Springer `Abstract. In recent years...`, Elsevier `A B S T R A C T` (letter-spaced by the PDF extractor), Nature SD (no label at all — the abstract is the lead paragraph). Fix: allow a run-in delimiter (`[—\-–.:]` or whitespace) after the keyword, and handle the letter-spaced form. |
| SEG-4 | An unrecognized heading lets the previous section swallow the rest of the paper | Needs Spec | Section spans run from one recognized heading to the next, so a journal whose headings aren't in the pattern table produces one enormous section. `cskg2` (Nature *Scientific Data*: `Background & Summary`, `Methods`, `Data Records`, `Technical Validation`, `Usage Notes`) matches only `Methods`, which absorbs 39k chars through to the references; same mechanism inflated the survey's `introduction` to 16,715 words. The **semantic mapping is also missing**: `Background & Summary` is that journal's introduction and `Technical Validation` its evaluation, but neither name is in `SECTION_PATTERNS`. Fix: add the Nature/`Scientific Data` vocabulary, plus a sanity check flagging any single section over some word count as probable under-segmentation rather than passing it downstream. |
| SEG-2 | `segment_with_abstract` can never match an abstract — **fix or delete** | **Needs Decision** (cheap either way) | `section_segmenter.py:377` builds its regex with `re.IGNORECASE \| re.DOTALL` but **no `re.MULTILINE`**, so `^` anchors to the start of the whole document; every real PDF opens with a title/author block, so it never fires. Empirically confirmed: `segment()` and `segment_with_abstract()` returned byte-identical output on all 8 papers. **Currently moot in production** — `pipeline.py:475` calls plain `.segment()`, making this dead code on the ingest path. Decide whether to fix (add `re.MULTILINE`; the lookahead also needs SEG-1's Roman-numeral fix to terminate correctly) or delete it. |
| SEG-8 | Segmenter test fixtures for the failure modes above | Needs Spec | Current tests appear to exercise Arabic-numbered, standalone-heading papers only. Worth adding: Roman-numeral headings (IEEE); each of SEG-3's five abstract conventions; a paper with journal-specific headings (Nature *Scientific Data*); a methods section named after the contribution (`III. SciCheck`); a descriptive sentence heading with no method keyword; an assertion that no single section exceeds a plausible word count; and an assertion that a paper with a known methods section actually yields one. **The last one matters most** — the failure mode is silent, so a test that only checks "segmentation returned some sections" passes on every paper in this set. |
| SEG-9 | Inconsistent hyphen handling splits the same model name differently across papers | Needs Spec | Surfaced as a caveat in the SEG-7 measurement, and **not a segmenter bug** — the extractor renders `paraphrase-distilroberta-base-v2` as `paraphrase-distilrobertabase-v2` in `fact_completion` and as `paraphrasedistilroberta-base-v2` in `cskg`, so a gold alias curated from one paper fails to match the other. Inflates apparent recall gaps and cross-paper entity dedup. Needs surface-form normalization at extraction or alias-match time. |
| SEG-11 | Single-word body lines classify as headings — **root cause (8)** | Needs Spec | **Found 2026-09-06 while measuring SEG-1.** `_find_headings` accepts any line under `max_heading_length` (100) that matches a pattern, requiring nothing heading-shaped of it — no blank-line delimitation, no caps check, no repetition filter. Several METHODS patterns are bare single words, so `_classify_heading("MODEL") == methods`: a table column header becomes a top-level methods section. Measured: **5** phantom `methods` sections in `llm_ontology_gen` (`MODEL` ×5), **4** in `kg_validation_hitl`, **2** in `kg_construction_survey` (`Model`, `Approach`), **1** in `empire`. **Distorts the other causes:** `empire`'s only `methods` block is a phantom — its real one (`IV. RESEARCH APPROACH`) is a SEG-5 miss — so any recall number read off that paper today is wrong, and a phantom heading also *splits* the real section that contained the line. **Schedule with SEG-5**, whose cheap fix (relax the method/experiment anchors) widens this surface. Fix is a heading-context heuristic, not vocabulary; needs its own measurement. Note the ID: `SEG-8`..`SEG-10` were already taken by non-cause items, so cause (8) ↔ `SEG-11` and the numbering convention above stops holding past (7). |
| SEG-10 | Decide whether `kg_construction_survey` earns its place in the ground-truth set | **Needs Decision** | Raised by SEG-7. The paper gets 3.9% of its body fed to the extractors, because the honest keep-list for a 94-page survey with no methods or experiments sections is `["abstract", "introduction"]`. Consequence: 18 of its 22 chain entities are invisible and the paper contributes almost nothing to a validation set built around cross-paper concept accumulation. Either drop it, replace it, or accept it as a deliberate low-yield member — but record the choice, because it silently skews any recall number computed over the set. Depends on the SEG-7 outcome. |

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
| SM-1 | PDF acquisition reliability (source selection + fetch hardening) | **DONE — merged (#44), VERIFIED** (spec: `content-acquisition-resilience.md`) | Post-SM-4 smoke (papers=3, entities=0) root-caused: PDF download failed ("Server disconnected" — publisher `ojs.aaai.org` blocks CI IPs) and the pipeline never tried the paper's arXiv PDF though the normalizer captures the arXiv ID. Spec: **published/`openAccessPdf` source first, arXiv PDF fallback** when unreachable (published is authoritative; breadth-first latency guard); request headers + bounded transient retry on PDF fetch; S2 429 bounded retry (no silent drops); **NO abstract fallback — full text or the paper fails loudly** (user decision); failures categorized by reason (`failed_blocked`/`failed_404`/`failed_thin`/`failed_no_pdf_source`) in run metrics. 7 ACs. Next: `/constellize:feature:implement`. |
| SM-4 | Extraction dies on `instructor` import — **root cause: unused `denario` dep transitively pins `openai==1.99.9`** | **DONE — merged (#36) & deployed 2026-07-14** (spec: `extraction-dep-pinning.md`; AC-6 SHA-parity verified on this deploy) — the floors-first plan hit `resolution-too-deep`; `uv` diagnosed the real cause: `denario 1.0.1` → `cmbagent-autogen` **hard-pins `openai==1.99.9`**, and the only instructor accepting that (`<1.14`) fails to import, while `instructor>=1.14` needs `openai>=2.0` → unsatisfiable. `denario` is imported NOWHERE in `packages/` (dead weight). Fix: **remove `denario` from `packages/core/pyproject.toml` + root `pyproject.toml`**; add `instructor>=1.14` + `openai>=2.0`; fix both `_get_instructor_client` except blocks (`ModuleNotFoundError` vs `ImportError`, surface real error). Verified: clean resolve 0.8s → instructor 1.15.4/openai 2.46.0; 32 extraction tests pass in a fresh clean-resolve venv. New `test_instructor_import.py`. Next: merge → first real staging build+deploy (verifies deploy AC-6) → smoke-ingest entities>0 (AC-4). **If denario is ever actually integrated, wire it as an optional extra so it can't re-pin openai.** | PR #27 smoke (key present, abstract present) fails: `problem_extractor - ERROR - Failed to extract from SectionType.ABSTRACT: instructor package not installed` — but instructor IS installed. Root cause: `packages/core/pyproject.toml` floor-only pins (`instructor>=1.0.0`, `pydantic>=2.0.0`, openai unpinned) + the heavy `denario`/`cmbagent` tree resolve `instructor 1.12.0`/`openai 1.99.9` in CI/Docker, where `import instructor` raises `ImportError`; `llm_client.py:194` masks it as "not installed." Two fixes: (1) pin a known-good `instructor`+`openai`+`pydantic` set (or add upper bounds) so CI/Docker resolve the same working versions as a dev `.venv`; (2) fix the `except ImportError` at `llm_client.py:194` + `:311` to surface the real import error instead of the misleading "not installed" message. **Likely affects the DEPLOYED Cloud Run Job too** (same unpinned `pip install ./packages/core`), so it blocks node extraction in staging, not just CI. |
| SM-1b | Broader open-access PDF resolution (Unpaywall) for non-arXiv papers | Deferred (fast-follow to SM-1) | SM-1 covers arXiv-backed papers (published-first, arXiv fallback). Papers with a blocked published URL and no arXiv ID still `failed_blocked`/`failed_no_pdf_source`. Add Unpaywall (DOI → best OA PDF, often a CI-reachable repo) as an additional candidate source. Prioritize based on SM-1's categorized failure metrics (only worth it if the non-arXiv tail is large). |
| SM-4b | Whole-graph lock for extraction deps (`uv.lock`) | Deferred (low priority) | With `denario` removed (SM-4) pip resolves core in <1s, so a lock is no longer needed for correctness. A committed `uv.lock` would still add whole-graph reproducibility against future transitive drift. Only pursue if drift recurs; adds `uv` to the critical-path toolchain. |
| SM-3 | Docs-site link check fails on backlog.html | Needs Spec | `build-preview` (HTML-Proofer) red: generated `backlog.html` links to spec `.md` files + `../memory_bank/productContext.md` not published into `_site`. Fix the Pages generator link rewriting or the BACKLOG relative links. Pre-existing since the docs-consolidation commits. |
| SM-5 | Lint debt in `packages/core/tests` (172 ruff violations) | Needs Spec | Surfaced when deploy-master's `test` job first ran (2026-07-13): `ruff check packages/core/tests` → 172 errors (71 F401, 49 E501, 44 I001, 8 F841); 115 auto-fixable via `ruff check --fix`. The deploy workflows were narrowed to `src`-only (matching code-review.yml) to unblock deploys. Clean up the test tree, then re-add `packages/core/tests` to the deploy-master/deploy-tag lint step so test-file quality is gated too. |
| SM-2 | Preflight WARN on empty section_text | **Largely subsumed** — SM-1 made the *empty* case loud (`failed_thin` + skip); SEG-1 AC-13 covers the *partial* case (passes the 250-char gate, missing wanted types). Residual scope is only the batch-level "100% of papers empty" roll-up. | When 100% of imported papers have empty `section_text`, log ERROR / fail batch loudly; current behavior is silent zero counters. Small fix-forward. |
| SM-6 | V2 entity integration blocked by wrong attribute (`.mentions` → `.mention_results`) | **DONE — merged (#47)** (spec: `v2-integration-mention-attr-fix.md`); it unmasked SM-8, the next `AttributeError` one line deeper | With SM-4 unblocking extraction, the daily smoke now reaches graph assertions and V1 populates (problems/concepts 20/20/20) — but **all V2 counters are 0**. Root cause: `ingestion.py:550` reads `v1_integration.mentions`, but `integrate_extracted_problems` returns an `IntegrationResultV2` whose mention list is `mention_results` (no `.mentions` attr) → `AttributeError` per paper, swallowed by the per-paper `try/except` at `:529` into `extraction_errors` → V2 (Topic/Concept/Model/Method) skipped. Introduced in loop-closure commit `7289588`; hidden until SM-4 let the path execute. **One-line fix + regression test** (the test must be red on the current line, since the outer catch hides the crash). With SM-1 (#44) resolved, this is the only remaining blocker to a green smoke. Next: `/constellize:feature:implement v2-integration-mention-attr-fix`. |

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

Segmenter defects — gates the ground-truth diff, and through it D-2 / V-1
    SEG-6 (length guard) ──► SEG-7 option 3 (invert the keep-list)
    SEG-7 (keep-list) ─────► SEG-10 (is kg_construction_survey worth keeping?)
    SEG-1 (Roman numerals) ─► SEG-2 (lookahead needs it to terminate)
    SEG-4 (journal vocabulary) ──► SEG-5 reason (b)
    SEG-7 decided ──► regenerate paper_<slug>.txt fixtures + redo reviews
                  ──► THEN importer-vs-gold diff is safe to read
```
