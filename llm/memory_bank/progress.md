# Progress

Last updated: 2026-04-16

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
- **Documentation**: GitHub Pages site with CI-driven updates
- **Tests**: 1217 tests passing (1059 core + 158 API), 0 failures; 32 E2E tests collecting cleanly
- **Feature Planning**: `llm/features/BACKLOG.md` with 28 features, dependency graph, and 5 open design questions
- **Ingestion Pipeline**: End-to-end `ingest_papers()` orchestration + CLI `ingest` command + async API endpoints
- **Cloud Run Job**: `job_runner.py` + `Dockerfile.job` + Terraform resource deployed to GCP staging
- **GCP Staging**: Cloud Run Job + API deployed, Neo4j with 282 nodes / 151 edges, schema initialized with vector indexes

## What Remains to Be Built

- **enhance-github-pages** (SPECIFIED 2026-04-16, Phase A ready to implement) — Jekyll `just-the-docs` migration, data-driven dashboards, PR previews, Lighthouse CI. See `llm/features/enhance-github-pages.md`
- Act on KG schema enhancement gap analysis (design doc 2026-04-15)
- Add OpenAI client timeout (60s) to prevent hanging extraction calls
- Add `instructor` to `pyproject.toml` dependencies
- Complete 20-paper ingestion + human review (AC-10: ≥90% coherence)
- GCS ingestion research log (D-1b — follow-on to D-1a)
- Entity ecosystem expansion: Topics, ResearchConcepts, Models, Methods (BACKLOG.md E-1 through E-8)
- Community detection and hierarchical summarization (BACKLOG.md C-1 through C-3)
- Graph-based RAG retrieval endpoints (BACKLOG.md R-1 through R-5)
- Production deployment (Terraform prod vars ready, needs execution)

## Known Bugs / Tech Debt

- OpenAI API intermittently hangs on extraction calls (no timeout) — blocks larger ingestion runs
- `instructor` package not declared in `pyproject.toml`
- `mentions_linked_to_paper` sometimes fails on pre-existing papers (DOI casing in EXTRACTED_FROM)
- Denario core: `arXiv_pdf` variable scope bug in `literature.py:114` (external)
- Legacy `memory-bank/` directory is stale; `llm/memory_bank/` is authoritative

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
| M8.5: Ingestion Pipeline | 2026-04-01 | End-to-end ingestion (D-1 + D-1a), Cloud Run Job, GCP deployment. Serialization fix, schema init, 18 problems + 18 concepts from 2 real papers. Committed as `01d67f9`. |
| M9: Production Ready | Not started | All tests passing, real data ingested |

## Completed Sprints (11 total)

Sprint 00 (Infrastructure) → Sprint 01 (KG Foundation) → Sprint 02 (Data Acquisition) → Sprint 03 (Extraction) → Sprint 04 (API + UI) → Sprint 05 (Agents) → Sprint 06 (Integration) → Sprint 07 (E2E Testing) → Sprint 08 (Docs) → Sprint 09 (Canonical Phase 1) → Sprint 10 (Canonical Phase 2)
