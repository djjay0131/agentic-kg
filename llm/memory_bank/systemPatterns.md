# System Patterns

Last updated: 2026-07-21

> **Published design docs (2026-07-21):** the node/edge ontology and per-feature
> design rationale now live on the docs site, not only here.
> `docs/reference/` = Domain Model & Taxonomy (entity-catalog, entity-relationships,
> topic-taxonomy). `docs/design/` = one design note per completed feature
> (E-1..E-8, orchestration, D-1, D-1a, CI smoke, enhance-github-pages), each
> reconciled against shipped code. This file remains the interim design authority
> (per the governance delta / ADR-0001); the published docs are the human-facing
> companion.

## High-Level Architecture

Three-layer architecture (ADR-002) as described in the Agentic Knowledge Graphs paper:

1. **Knowledge Representation Layer** — Neo4j graph with problems + entity-expansion nodes (Topic, ResearchConcept, Model, Method, Citation edges) as first-class entities
2. **Automation and Extraction Layer** — PDF ingestion, segmentation, 5-way parallel LLM-based extraction (E-8 V1 + V2)
3. **Agentic Orchestration Layer** — LangGraph/AG2 agents operating over the graph

## Directory and Module Structure

```
agentic-kg/
├── packages/
│   ├── core/src/agentic_kg/          # Core library
│   │   ├── cli.py                    # CLI entrypoint (ingest, create-*, load-*, citation-graph, ...)
│   │   ├── ingestion.py              # ingest_papers() — the unified per-paper orchestration loop
│   │   ├── job_runner.py             # Cloud Run Job wrapper (reads env vars, writes IngestionRun node)
│   │   ├── agents/                   # LangGraph research + matching agents
│   │   │   └── matching/             # Phase 2 confidence-routed agents
│   │   ├── data_acquisition/         # API clients (Semantic Scholar, arXiv, OpenAlex)
│   │   │   └── importer.py           # PaperImporter (metadata + populate_citations hook)
│   │   ├── extraction/               # PDF → KG pipeline
│   │   │   ├── pdf_extractor.py      # PyMuPDF text extraction
│   │   │   ├── section_segmenter.py  # Section identification (returns SegmentedDocument)
│   │   │   ├── problem_extractor.py  # V1 LLM extraction (problems only)
│   │   │   ├── topic_extractor.py    # E-8 V1: closed-set Literal over taxonomy
│   │   │   ├── concept_extractor.py  # E-8 V1: open-set with create_or_merge dedup
│   │   │   ├── model_extractor.py    # E-8 V2: open-set model extraction
│   │   │   ├── method_extractor.py   # E-8 V2: open-set method extraction
│   │   │   ├── pipeline.py           # extract_all_entities: 5-way asyncio.gather + _run failure isolation
│   │   │   ├── cross_entity_normalizer.py  # E-7: routing LLM disambiguates Concept↔Model↔Method collisions
│   │   │   ├── kg_integration.py     # V1 KG integration
│   │   │   ├── kg_integration_v2.py  # V1 problem-mention path + V2 entity writers + audit metadata
│   │   │   ├── re_ingestion.py       # AC-13 purge-then-rewrite path
│   │   │   ├── b3_linker.py          # E-8 V1: problem↔concept surface-form linker
│   │   │   ├── taxonomy_hash.py      # Canonical taxonomy snapshot hashing
│   │   │   └── prompts/templates.py  # System + user prompt templates (V1 + V2 + description-gen + disambiguation)
│   │   └── knowledge_graph/          # Graph operations
│   │       ├── models/entities.py    # Pydantic entity models with to_neo4j_properties()
│   │       ├── schema.py             # SchemaManager — SCHEMA_VERSION=7 (10 constraints, 7 vector indexes)
│   │       ├── repository.py         # Neo4jRepository: CRUD + create_or_merge_X + acreate_or_merge_X
│   │       ├── citation_graph.py     # E-5: populate_citations() async helper
│   │       ├── description_generation.py  # E-6: LLM description-gen with self-validation gates
│   │       ├── concept_matcher.py    # Vector similarity matching
│   │       ├── auto_linker.py        # HIGH confidence auto-linking
│   │       ├── review_queue.py       # Human review queue service
│   │       └── concept_refinement.py # Canonical statement synthesis
│   ├── api/src/agentic_kg_api/       # FastAPI application
│   │   ├── main.py                   # App entrypoint (includes ingest router)
│   │   ├── routers/                  # Route handlers
│   │   │   ├── ingest.py            # POST /api/ingest (triggers Cloud Run Job), GET /api/ingest/{trace_id}
│   │   │   ├── topics.py            # E-1: /api/topics
│   │   │   ├── concepts.py          # E-2: /api/concepts
│   │   │   ├── models.py            # E-3: /api/models
│   │   │   ├── methods.py           # E-4: /api/methods
│   │   │   ├── papers.py            # E-5 citation endpoints (references/citations/counts)
│   │   │   └── reviews.py           # Review queue endpoints
│   │   ├── schemas.py               # API response models (includes IngestionRequest/Response)
│   │   └── dependencies.py          # FastAPI dependency injection
│   └── ui/                           # Next.js 14 frontend
├── .github/workflows/                # GHA CI
│   ├── smoke-ingest.yml              # ci-smoke-test-ingestion: PR + daily cron + dispatch
│   ├── integration-tests.yml         # Existing integration suite against staging Neo4j
│   ├── test.yml                      # Unit tests
│   └── deploy-*.yml                  # Cloud Build deploy pipelines
├── scripts/                          # Utilities
│   ├── smoke_test.py                 # Legacy smoke against staging
│   ├── smoke_assert.py               # ci-smoke-test-ingestion assertions (6-count Cypher)
│   └── ...
├── docker/
│   └── Dockerfile.job                # Core-only image for Cloud Run Job (no API deps)
├── infra/                            # Terraform IaC (includes google_cloud_run_v2_job.ingest)
├── llm/                              # LLM-related project files
│   ├── features/                     # Feature specs (BACKLOG.md + 16 spec files)
│   └── memory_bank/                  # Authoritative project context (this directory)
├── docs/                             # Jekyll just-the-docs site → GitHub Pages
│   ├── reference/                    # Domain Model & Taxonomy (entity-catalog, entity-relationships, topic-taxonomy; Mermaid)
│   ├── design/                       # Per-completed-feature design notes (E-1..E-8, D-1/D-1a, CI smoke, ...)
│   ├── adr/                          # Architecture Decision Records (ADR-0001 = governance adoption)
│   ├── about/ · status/             # Overview + auto-generated dashboards
│   └── governance-delta.md           # agentic-governance v0.2 localization
├── construction/sprints/             # Sprint history archive (read by GitHub Pages generator)
├── .claude/                          # Claude Code config
│   ├── agents/                       # Project agent definitions
│   └── skills/                       # Constellize skills (specify / implement / verify / memory)
└── Makefile                          # install, test, smoke-test (staging), smoke-local (Docker Neo4j)
```

## Key Design Patterns

### Dual-Entity Problem Architecture (ADR-003, ADR-005)

Two-tier model for problem deduplication:
- **ProblemMention** (`packages/core/src/agentic_kg/knowledge_graph/models/entities.py`) — paper-specific problem statement with context, linked to source paper
- **ProblemConcept** (same file) — canonical representation that mentions link to via `INSTANCE_OF` relationship
- **MatchCandidate** (same file) — similarity result with confidence classification

### Confidence-Based Routing

Extracted mentions are matched to existing concepts via vector similarity, then routed by confidence:
- **HIGH (>95%)**: Auto-linked via `auto_linker.py`
- **MEDIUM (80-95%)**: Single-agent review via `EvaluatorAgent` (`agents/matching/`)
- **LOW (50-80%)**: Multi-agent consensus via Maker/Hater/Arbiter pattern
- **Escalation**: Human review queue (`review_queue.py`) for disputed matches

### Entity Expansion Architecture (E-1 through E-8 V2)

Four new first-class entity types added alongside `Problem` / `Paper` / `Author`:

| Entity | Repo method (sync) | Async sibling (E-6) | Dedup threshold | Edge from Paper |
|--------|-------------------|---------------------|-----------------|-----------------|
| Topic (E-1) | `assign_entity_to_topic` | — | Closed-set (seed taxonomy) | `BELONGS_TO` |
| ResearchConcept (E-2) | `create_or_merge_research_concept` | `acreate_or_merge_research_concept` | 0.90 cosine | `DISCUSSES` |
| Model (E-3) | `create_or_merge_model` | `acreate_or_merge_model` | 0.95 cosine + canonical seed | `USES_MODEL` |
| Method (E-4) | `create_or_merge_method` | `acreate_or_merge_method` | 0.90 cosine | `APPLIES_METHOD` |

Plus a self-reference on Paper (E-5): `(:Paper)-[:CITES]->(:Paper)` populated from Semantic Scholar reference lists.

### Cross-Entity Normalization (E-7)

Per-paper routing LLM call disambiguates cross-entity collisions (Concept ↔ Model ↔ Method):
- **Detection (all-in trigger)**: exact name (case-insensitive) OR alias overlap OR embedding cosine ≥ 0.85. Cheap signals run first; embedding cache per-paper.
- **Routing**: single `instructor.extract()` call returns `DisambiguationDecision(picked_kind, confidence, is_grounded_in_paper_context, is_specific_to_one_kind, rejection_reason)`.
- **Acceptance**: both self-validation gates True AND `confidence ≥ 0.7`.
- **Reject path**: keeps BOTH extractions intact (TL Q1 review) — audit records the unresolved case.
- **Audit**: `Paper.normalization_audit` as JSON on the Paper node; queryable via `MATCH (p:Paper) WHERE p.normalization_audit IS NOT NULL`.
- **Prompt-injection mitigation**: `<paper-excerpt>` + `<quote-X>` pseudo-XML delimiters + system-prompt security clause.

### Description Generation with Self-Validation (E-6)

For `create_or_merge_X` (Concept, Model, Method):
- Sync path with `generate_description=True` raises `NotImplementedError`; use the async sibling.
- Async sibling `acreate_or_merge_X` calls `generate_description_with_self_check` which invokes the LLM with a `DescriptionWithSelfCheck` response schema carrying 4 self-validation gates (`is_factually_grounded`, `is_concise`, `is_specific`, `is_not_tautological`).
- Acceptance requires ALL gates True. Reject → `description=None` with WARN log.
- CLI operator surfaces (`create-concept` / `create-model` / `create-method`) flip `generate_description=True` by default; `--no-generate-description` opts out; silent fallback on missing `OPENAI_API_KEY`.
- Ingestion path keeps `generate_description=False` per E-8 V2's Q1 decision (cost-neutral by default).

### Extractor Pattern (E-8 V1 + V2)

All 5 extractors follow the same shape:
- Constructor takes `client: BaseLLMClient` (L-1 swap point).
- Async `extract(paper_title, sections_text) -> list[ExtractedX]`.
- Empty-section short-circuit: return `[]` when `sections_text.strip() == ""`, no LLM call.
- Known `LLMError` caught internally → return `[]` + WARN log.
- Unknown exceptions propagate to `_run`, which records `ExtractionFailure`.

Orchestrated via `extract_all_entities(problem_call, topic_call, concept_call, model_call, method_call, paper_doi=...)` with per-extractor failure isolation.

### Entity Pipeline Orchestration (LOOP CLOSED)

`ingest_papers` per-paper loop body:

```
1. Purge guardrail (AC-13)
2. Skip check (AC-21): existing Paper with taxonomy_hash matching AND extraction_incomplete != true → skip
3. Text source resolution: PDF (segmented abstract+intro+methods+experiments) OR paper.abstract fallback
4. 5-way extract_all_entities (E-8 V1 + V2)
5. normalize_cross_entity (E-7) — mutates extraction_result in place
6. V1: integrate_extracted_problems (ProblemMention/Concept routing)
7. V2: integrate_paper_entities (Topic/Concept/Model/Method writers + audit)
8. Per-paper try/except records failures in extraction_errors[doi]
```

Per-batch shared deps built ONCE at top of `ingest_papers` (LLM client singleton, 4 extractors, embedder, taxonomy_hash).

### CI Smoke Testing (ci-smoke-test-ingestion)

GHA workflow `.github/workflows/smoke-ingest.yml` runs on every PR (path-filtered) + daily cron:
- Fresh `neo4j:5.26-community` testcontainers service inside the runner
- `agentic-kg ingest --query "retrieval augmented generation" --limit 3 --json`
- Single retry with 30s sleep on ingest failure
- `scripts/smoke_assert.py` runs a single Cypher round-trip and asserts 6 batch-level conditions
- Artifact upload on all runs (pass or fail) with 14-day retention
- Concurrency group cancels stale runs on rapid pushes
- Local reproduction via `make smoke-local`

### LangGraph Workflow Pattern

Agent workflows use LangGraph `StateGraph` with typed state dictionaries:
- Node functions implement each processing step
- Routing functions handle conditional edges
- `MemorySaver` provides checkpoint persistence

### Human-in-the-Loop

- `CheckpointManager` for research workflow decisions
- `ReviewQueueService` for matching review with priority-based SLA (24h/7d/30d)
- API endpoints for review assignment and resolution (`routers/reviews.py`)

### Dependency Injection

- FastAPI: `dependencies.py` provides `get_review_queue`, `get_neo4j_driver`, etc.
- Extractors + normalizer take `client: BaseLLMClient` in `__init__` — L-1 swap point documented across E-6 / E-7 / E-8 V2 / entity-pipeline-orchestration ACs

### Data Acquisition Resilience

- Token bucket rate limiting per API source
- Circuit breaker with exponential backoff retry
- TTL-based response caching (`cachetools`)
- Multi-source paper aggregation with metadata normalization

## Data Flow: Full Ingestion Pipeline (post-entity-pipeline-orchestration)

```
CLI `ingest` / Cloud Run Job / API POST /api/ingest → ingestion.ingest_papers()

  Phase 1: Search & Import
    → data_acquisition clients (OpenAlex, arXiv, Semantic Scholar)
    → PaperImporter.batch_import (with populate_citations=True default per E-8 V2)
    → Neo4j Paper + Author nodes + AUTHORED_BY + CITES edges

  Phase 2/3: Per-paper unified loop (default extract_entities=True per orchestration)
    for each paper:
      → AC-13 purge guardrail
      → AC-21 skip check (Paper.taxonomy_hash + extraction_incomplete)
      → Resolve section_text (PDF segmented_document → abstract fallback)
      → extract_all_entities (5-way parallel):
          problem_extractor → topic_extractor → concept_extractor
                            → model_extractor → method_extractor
      → normalize_cross_entity (E-7 routing LLM per collision)
      → V1: KGIntegratorV2.integrate_extracted_problems
          ProblemMention → concept_matcher → confidence routing
      → V2: integrate_paper_entities
          Topic edges → ResearchConcept nodes + DISCUSSES → Model + USES_MODEL
                     → Method + APPLIES_METHOD → B3 linker → Paper.normalization_audit + taxonomy_hash

  Phase 4: Sanity Checks
    → 5 automated checks against Neo4j graph integrity
```

### Cloud Run Job Pattern

For async/long-running ingestion:
- API triggers `google_cloud_run_v2_job.ingest` via REST
- `job_runner.py` reads config from env vars (including `EXTRACT_ENTITIES`, `NORMALIZE_CROSS_ENTITY`, `FORCE_REEXTRACT`, `POPULATE_CITATIONS`)
- Calls `ingest_papers()`, writes `IngestionRun` node
- API polls Neo4j for `IngestionRun` status via `GET /api/ingest/{trace_id}`

## Naming Conventions

- **Python style**: snake_case functions/variables, PascalCase classes
- **File naming**: snake_case modules
- **Test files**: `test_<module>.py` mirroring source structure
- **Pydantic models**: Strict validation, field descriptions
- **Line length**: 100 characters (Ruff config)
- **Sprint numbering**: Zero-indexed (Sprint 00 through Sprint 10)
- **CLI flags**: `--no-<feature>` for opt-outs of default-on behaviors; `--force-<action>` for opt-ins to normally-off destructive paths
- **Env vars**: uppercase snake, mirror CLI flags; `.lower() != "false"` audit-friendly pattern for default-on flags

## Constellize Workflow

Feature development follows the constellize spec → implement → verify cycle:
- `.claude/skills/constellize:feature:specify` — interview-driven spec authoring
- `.claude/skills/constellize:feature:implement` — star-gap-generate implementation
- `.claude/skills/constellize:feature:verify` — four-gate verification (test integrity, health check, deployment readiness, maintainability)
- `.claude/skills/constellize:memory:update` — memory bank sync (quick or full)

Every feature ships spec + implementation + verification as three separate commits, each with a Co-Authored-By tag.
