# Progress

Last updated: 2026-05-20

## What Is Built and Working

- **Infrastructure**: GCP staging environment (Cloud Run API + Compute Engine Neo4j + Terraform IaC)
- **Knowledge Graph**: Neo4j with Problem/Paper/Author entities, CRUD, vector indexes, hybrid retrieval
- **Data Acquisition**: Semantic Scholar, arXiv, OpenAlex API clients with rate limiting, caching, circuit breakers
- **Extraction Pipeline**: PDF → text → sections → LLM extraction → KG integration (batch processing with SQLite queue)
- **API**: FastAPI with Problem/Paper CRUD, hybrid search, extraction triggers, review queue endpoints
- **Frontend**: Next.js 14 with dashboard, problem/paper views, graph visualization (react-force-graph)
- **Research Agents**: Ranking, Continuation, Evaluation, Synthesis agents with LangGraph workflows and HITL checkpoints
- **Canonical Problem Architecture**: Dual-entity model (ProblemMention/ProblemConcept) with vector similarity matching
- **Confidence-Based Matching**: AUTO (>95%), EvaluatorAgent (80-95%), Maker/Hater/Arbiter (<80%), human review queue
- **Concept Refinement**: Automatic canonical statement synthesis at mention thresholds
- **Documentation**: Jekyll `just-the-docs` site with PR previews, HTMLProofer link validation, data-driven dashboards
- **Tests**: 1312 core unit tests passing, integration green against staging Neo4j
- **Feature Planning**: `llm/features/BACKLOG.md` with 28 features, dependency graph
- **Ingestion Pipeline**: End-to-end `ingest_papers()` orchestration + CLI `ingest` command + async API endpoints
- **Cloud Run Job**: `job_runner.py` + `Dockerfile.job` + Terraform resource deployed to GCP staging
- **GCP Staging**: Cloud Run Job + API deployed, Neo4j with 282 nodes / 151 edges, schema initialized with vector indexes
- **Topic entities (E-1)**: First-class `Topic` nodes, 3-level hierarchy (domain/area/subtopic), seeded taxonomy, v2→v3 domain→topic migration, Topic repository CRUD + hierarchy + counts, CLI commands (`load-taxonomy`, `export-taxonomy`, `assign-topic`), `/api/topics` router. Merged 2026-05-19 (`60b3f8a`).
- **ResearchConcept entities (E-2)**: Generic research concepts with embedding-based dedup (0.90 cosine threshold), ResearchConcept CRUD, `/api/concepts` router, `create-concept` / `link-concept` CLI, dedup threshold calibration harness. Merged 2026-05-19 (`1d32bf5`).
- **GitHub Pages overhaul (enhance-github-pages Phase A)**: Jekyll skeleton with `about/` and `status/` sections, Liquid templates for backlog/sprints/status-badge, `generate_site_data.py` Pydantic-validated YAML pipeline, PR preview deploys via `rossjrw/pr-preview-action`, HTMLProofer link validation (with `--swap-urls` for baseurl + `--no-enforce-https`). Merged 2026-05-19 (`42ee5fe`).
- **CI health**: master lint debt cleared, integration-tests workflow installs core/api packages directly, e2e tests no longer leak into the unit job, `Problem` double-JSON-encoding bug fixed, `_problem_from_neo4j` legacy-tolerant, integration test fixtures isolated by per-run TEST_-marked statements with setup+teardown cleanup.

## What Remains to Be Built

- **E-8 Extraction prompt expansion** (SPECIFIED 2026-05-18, **spec untracked in working tree**, ready to implement) — adds Topic + Concept extractors to the ingestion pipeline. Locked decisions: Route B parallel calls, per-instance taxonomy snapshot, per-paper alias-only B3 linking, structured `ExtractionFailure` records, dual eval gates (avg + per-paper floor) + cross-area lock-in, purge-then-rewrite re-ingestion, explicit completeness query contract, `Paper.taxonomy_hash` for staleness audit, structured-YAML deny-list, concept recall ≥ 0.50 anti-gaming tripwire. See `llm/features/extraction-prompt-expansion.md`.
- **enhance-github-pages Phase B** (AC-3, 13, 14) — content for placeholder pages, full Lighthouse CI, deferred from Phase A.
- **E-3 Model entity** (next on backlog after E-8)
- **E-4 Method entity** (next on backlog after E-8)
- **E-5 onwards** (per `llm/features/BACKLOG.md`)
- **T-1 Taxonomy management at scale** (flagged by E-1 spec, needs spec)
- Add OpenAI client timeout (60s) to prevent hanging extraction calls
- Add `instructor` to `pyproject.toml` dependencies
- Complete 20-paper ingestion + human review (AC-10: ≥90% coherence)
- GCS ingestion research log (D-1b — follow-on to D-1a)
- Community detection and hierarchical summarization (BACKLOG.md C-1 through C-3)
- Graph-based RAG retrieval endpoints (BACKLOG.md R-1 through R-5)
- Production deployment (Terraform prod vars ready, needs execution)
- Scrub staging Neo4j IP from `docs/status/service-inventory.html`
- Triage stale PR #16 (Cloud Build triggers, no CI ever ran)

## Known Bugs / Tech Debt

- OpenAI API intermittently hangs on extraction calls (no timeout) — blocks larger ingestion runs
- `instructor` package not declared in `pyproject.toml`
- `mentions_linked_to_paper` sometimes fails on pre-existing papers (DOI casing in EXTRACTED_FROM)
- Denario core: `arXiv_pdf` variable scope bug in `literature.py:114` (external)
- Legacy `memory-bank/` directory is stale; `llm/memory_bank/` is authoritative
- `docs/status/service-inventory.html` exposes the staging Neo4j browser endpoint

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
| M9: Entity Expansion + CI Health | 2026-05-19 | E-1 Topic, E-2 ResearchConcept, enhance-github-pages Phase A merged. Double-encoding bug + 7 latent CI/feature bugs fixed |
| M10: Production Ready | Not started | All tests passing, real data ingested |

## Completed Sprints (11 total)

Sprint 00 (Infrastructure) → Sprint 01 (KG Foundation) → Sprint 02 (Data Acquisition) → Sprint 03 (Extraction) → Sprint 04 (API + UI) → Sprint 05 (Agents) → Sprint 06 (Integration) → Sprint 07 (E2E Testing) → Sprint 08 (Docs) → Sprint 09 (Canonical Phase 1) → Sprint 10 (Canonical Phase 2)
