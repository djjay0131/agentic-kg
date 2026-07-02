# Progress

Last updated: 2026-07-02

## What Is Built and Working

### Infrastructure + Core Platform
- **Infrastructure**: GCP staging environment (Cloud Run API + Compute Engine Neo4j + Terraform IaC)
- **Knowledge Graph**: Neo4j 5.x with 6 constraints + 25 indexes (3 vector) initialized via `SchemaManager`
- **Data Acquisition**: Semantic Scholar, arXiv, OpenAlex API clients with rate limiting, caching, circuit breakers
- **API**: FastAPI with Problem/Paper CRUD, hybrid search, extraction triggers, review queue endpoints
- **Frontend**: Next.js 14 with dashboard, problem/paper views, graph visualization (react-force-graph)
- **Documentation**: Jekyll `just-the-docs` site with PR previews, HTMLProofer link validation, data-driven dashboards

### Extraction + Integration Pipeline
- **Problem extraction** (V1): PDF → text → sections → LLM extraction → KG integration; batch processing with SQLite queue
- **Research Agents**: Ranking, Continuation, Evaluation, Synthesis agents with LangGraph workflows + HITL checkpoints
- **Canonical Problem Architecture**: Dual-entity model (ProblemMention/ProblemConcept) with vector similarity matching
- **Confidence-Based Matching**: AUTO (>95%), EvaluatorAgent (80-95%), Maker/Hater/Arbiter (<80%), human review queue
- **Concept Refinement**: Automatic canonical statement synthesis at mention thresholds
- **Cloud Run Job**: `job_runner.py` + `Dockerfile.job` + Terraform resource deployed to GCP staging
- **E-8 V1 (Topic + Concept extractors, VERIFIED)**: `TopicExtractor` with per-instance taxonomy snapshot + closed-set `Literal` schema; `ConceptExtractor` open-set with `create_or_merge_research_concept` dedup; parallel `extract_all_entities` with `_run` failure isolation; B3 problem→concept alias linker; `Paper.taxonomy_hash` + `extraction_incomplete` metadata; purge-then-rewrite re-ingestion; completeness query contract; 5-paper eval scaffolding
- **E-8 V2 (Model + Method extractors + citation wiring, VERIFIED)**: `ModelExtractor` + `MethodExtractor` (open-set, paper-level, LLMError catch, empty-section skip); 5-way `extract_all_entities` orchestrator; `USES_MODEL` + `APPLIES_METHOD` writers; `PaperImporter.import_paper` gains `populate_citations=True` default + `s2_client` kwarg; CLI `--no-populate-citations` flag; `POPULATE_CITATIONS` env var; autouse conftest fixture guards unit tests from S2
- **E-7 (Cross-entity normalization, VERIFIED)**: `DisambiguationDecision` Pydantic with 2 self-validation gates + confidence gate; `_cheap_collisions` (exact + alias, O(n+m)); `_embedding_collisions` (embedding cosine ≥ 0.85, per-paper cache, embedder-failure absorbed); `disambiguate_pair` single LLM call per collision; reject path KEEPS both extractions (TL Q1 review); `Paper.normalization_audit` JSON on Paper node; prompt-injection mitigation via `<paper-excerpt>` pseudo-XML delimiters + system-prompt security clause
- **entity-pipeline-orchestration (VERIFIED, LOOP CLOSED)**: `ingest_papers` gains `extract_entities=True`, `normalize_cross_entity_collisions=True`, `force_reextract=False` kwargs; per-batch shared deps built once; per-paper skip check on `Paper.taxonomy_hash + extraction_incomplete` (AC-21); text source resolution (PDF `segmented_document` → abstract fallback); CLI `--no-extract-entities` / `--no-normalize-cross-entity` / `--force-reextract` flags; `EXTRACT_ENTITIES` / `NORMALIZE_CROSS_ENTITY` / `FORCE_REEXTRACT` env vars; 7 new `IngestionResult` counters; per-paper try/except failure isolation. **⚠️ Default-on BREAKING CHANGE**: every ingest run since 2026-06-23 makes ~5-6 extra LLM calls per paper vs V1 baseline.

### Entity Types (Category 3 backlog)
- **E-1 Topic entities (VERIFIED)**: First-class `Topic` nodes, 3-level hierarchy, seeded taxonomy, CLI + `/api/topics` (merged 2026-05-19, `60b3f8a`)
- **E-2 ResearchConcept entities (VERIFIED)**: Embedding-based dedup 0.90 cosine, `create_or_merge_research_concept`, CLI + `/api/concepts` (merged 2026-05-19, `1d32bf5`)
- **E-3 Model entities (VERIFIED)**: `Model` with `architecture` / `model_type` / `year_introduced` / `is_canonical`; 19-entry canonical seed YAML with write-protection; `create_or_merge_model` embedding-dedup at 0.95 threshold; CLI + `/api/models`
- **E-4 Method entities (VERIFIED)**: `Method` with `method_type`; `create_or_merge_method` embedding-dedup at 0.90; CLI + `/api/methods`; `--threshold 1.01` escape valve
- **E-5 Citation graph (VERIFIED)**: `(:Paper)-[:CITES]->(:Paper)` self-reference; stub Paper promotion; `populate_citations` async helper (wired into `PaperImporter` by E-8 V2); citation-graph CLI subcommand
- **E-6 Entity descriptions (VERIFIED)**: `generate_description=False` kwarg on `create_or_merge_X` (sync guards raise `NotImplementedError`); async siblings `acreate_or_merge_X`; `DescriptionWithSelfCheck` Pydantic with 4 self-validation gates; CLI `--no-generate-description` flag; silent fallback on missing `OPENAI_API_KEY`

### CI + Ops
- **CI health**: master lint debt cleared, integration-tests workflow installs core/api packages directly, e2e tests isolated
- **ci-smoke-test-ingestion (VERIFIED)**: `.github/workflows/smoke-ingest.yml` triggers on PR (path-filtered) + daily cron (06:17 UTC) + `workflow_dispatch`; testcontainers Neo4j inside runner; single-retry ingest with 30s sleep; `scripts/smoke_assert.py` runs 6-count Cypher check; artifact upload with 14-day retention; concurrency group cancels stale runs; `make smoke-local` mirrors CI workflow for local reproduction
- **GCP Staging**: Cloud Run Job + API deployed, Neo4j with 282+ nodes, schema initialized with vector indexes

### Tests
- **1994 core unit tests passing**, 234 skipped (e2e + testcontainers Docker-gated), 0 failures
- Integration tests green against staging Neo4j (per E-8/E-7 verify gates)

## What Remains to Be Built

### Immediate / actionable
- **First real-data shakedown of entity-pipeline-orchestration**: smoke workflow triggered manually 2026-07-02 (run 28621965589); if green, LOOP CLOSED is validated against real papers end-to-end
- **Real-data eval calibration** (E-7 AC-21 + E-8 V2 AC-17 + entity-pipeline-orchestration follow-up): hand-labeled 5-10 collision-pair fixture set + precision/recall floors for the routing LLM
- **Cloud Run Job verification post-orchestration**: production `agentic-kg-ingest-staging` Cloud Run Job hasn't been executed since the BREAKING CHANGE landed; needs a controlled `gcloud run jobs execute` with `INGEST_LIMIT=3` to validate the deploy-path wiring
- **Cost telemetry** (deferred from E-7, E-8 V2, entity-pipeline-orchestration): per-batch LLM-call counter surfaced in `IngestionResult`; needed once bulk ingestion runs land

### Next backlog features
- **R-1 Query-facing vector search** (unblocked; graph is now populated end-to-end) — expose vector search across all entity types for user queries; first step toward graph-RAG
- **C-1 Community detection** (unblocked) — Leiden/Louvain on the entity graph; requires non-trivial paper corpus first
- **C-2 Hierarchical summarization** (depends on C-1)
- **R-2 Graph neighbor expansion**
- **R-3 LLM synthesis endpoint** (POST /api/query)
- **L-1 Local / low-cost SLM client** (BACKLOG.md; unblocks cost reduction for description-gen, normalization router, extractors)
- **T-1 Taxonomy management at scale** (flagged by E-1 spec)

### Ops / production
- **Production deployment** — Terraform prod vars ready; needs execution
- **enhance-github-pages Phase B** (AC-3, 13, 14) — content for placeholder pages, full Lighthouse CI
- **Cross-paper canonicalization** — "attention mechanism" as Concept in paper A and Method in paper B; separate from E-7's per-paper scope
- Complete 20-paper ingestion + human review (AC-10: ≥90% coherence)
- Scrub staging Neo4j IP from `docs/status/service-inventory.html` (still-open; flagged in prior progress)
- Triage stale PR #16 (Cloud Build triggers, no CI ever ran)

## Known Bugs / Tech Debt

- `docs/status/service-inventory.html` exposes the staging Neo4j browser endpoint (still open)
- Denario core: `arXiv_pdf` variable scope bug in `literature.py:114` (external)
- Legacy `memory-bank/` directory is stale; `llm/memory_bank/` is authoritative
- Cross-entity duplicate risk between paper batches: E-7 is per-paper; cross-paper "attention mechanism" as Concept in one paper, Method in another is by-design accepted until cross-paper canonicalization ships
- OpenAI extraction cost visibility: no per-batch LLM-call counter yet; operators rely on the OpenAI dashboard for spend tracking
- (Fixed 2026-05-19) OpenAI API intermittent hangs — mitigated via 60s timeout
- (Fixed 2026-05-19) `instructor` package declared in `pyproject.toml`
- (Fixed by orchestration) `mentions_linked_to_paper` DOI casing bug — no longer triggered by the new orchestration path

## Key Milestones

| Milestone | Date | Description |
|-----------|------|-------------|
| M0: Infrastructure | 2025-12-22 | GCP deployment of Denario |
| M1: KG MVP | 2026-01-07 | Neo4j with problem entities, 221 tests |
| M2: Data Acquisition | 2026-01-25 | Semantic Scholar, arXiv, OpenAlex clients |
| M3: Extraction Pipeline | 2026-01-26 | LLM-based problem extraction from papers |
| M4: Agent System | 2026-01-28 | Ranking, Continuation, Evaluation, Synthesis agents |
| M5: Full-Stack Integration | 2026-01-30 | All components wired, staging deployed |
| M6: E2E Testing | 2026-02-03 | Validated against live staging |
| M7: Canonical Phase 1 | 2026-02-10 | ProblemMention/ProblemConcept, auto-linking (PR #18) |
| M8: Canonical Phase 2 | 2026-02-18 | Agent workflows, review queue, concept refinement (286+ tests) |
| M8.5: Ingestion Pipeline | 2026-04-01 | End-to-end ingestion (D-1 + D-1a), Cloud Run Job, GCP deployment (`01d67f9`) |
| M9: Entity Expansion + CI Health | 2026-05-19 | E-1 Topic, E-2 ResearchConcept, enhance-github-pages Phase A merged |
| M10: E-8 V1 Extraction | 2026-06-02 | Topic + Concept extractors landed in production extraction pipeline (VERIFIED 2026-06-02) |
| M11: Full Entity Coverage | 2026-06-15 | E-3 Model, E-4 Method, E-5 Citation graph, E-6 descriptions, E-8 V2 extractors all VERIFIED; every entity type + citation edge has automated creation |
| M12: Loop Closed | 2026-06-24 | E-7 Cross-entity normalization + entity-pipeline-orchestration VERIFIED; every entity-expansion feature (E-1..E-8 V2 + E-7) is invoked from production `ingest_papers` |
| M13: CI Smoke | 2026-07-02 | `ci-smoke-test-ingestion` VERIFIED; GHA workflow ingests 3 real papers end-to-end against testcontainers Neo4j on every PR + daily cron |
| M14: Production Ready | Not started | All tests passing, real data ingested at scale, R-1..R-5 (graph-RAG) shipped |

## Completed Sprints (11 total)

Sprint 00 (Infrastructure) → Sprint 01 (KG Foundation) → Sprint 02 (Data Acquisition) → Sprint 03 (Extraction) → Sprint 04 (API + UI) → Sprint 05 (Agents) → Sprint 06 (Integration) → Sprint 07 (E2E Testing) → Sprint 08 (Docs) → Sprint 09 (Canonical Phase 1) → Sprint 10 (Canonical Phase 2)

## Constellize Feature Cycles Completed (Sprint 10 onwards)

Each cycle: spec → implement → verify.

| Feature | Verified | Commit family |
|---------|----------|---------------|
| E-1 Topic entities | 2026-05-19 | `60b3f8a` |
| E-2 ResearchConcept entities | 2026-05-19 | `1d32bf5` |
| enhance-github-pages Phase A | 2026-05-19 | `42ee5fe` |
| E-8 V1 Topic+Concept extractors | 2026-06-02 | `3fb1762` |
| E-3 Model entities | 2026-06-08 | `a460fa1` |
| E-4 Method entities | 2026-06-10 | `7212533` |
| E-5 Citation graph | 2026-06-12 | `a90cff1` |
| E-6 Entity descriptions | 2026-06-14 | `b422996` |
| E-8 V2 Models + Methods + citations | 2026-06-15 | `53a9b88` |
| E-7 Cross-entity normalization | 2026-06-20 | `a3c8586` |
| entity-pipeline-orchestration | 2026-06-24 | `8ff4195` |
| ci-smoke-test-ingestion | 2026-07-02 | `330f998` |
