# System Patterns

## High-Level Architecture

Three-layer architecture (ADR-002) as described in the Agentic Knowledge Graphs paper:

1. **Knowledge Representation Layer** — Neo4j graph with problems as first-class entities
2. **Automation and Extraction Layer** — PDF ingestion, segmentation, LLM-based extraction
3. **Agentic Orchestration Layer** — LangGraph/AG2 agents operating over the graph

## Directory and Module Structure

```
agentic-kg/
├── packages/
│   ├── core/src/agentic_kg/          # Core library
│   │   ├── cli.py                    # CLI entrypoint (includes `ingest` command)
│   │   ├── ingestion.py              # ingest_papers() orchestration (search → import → extract → integrate)
│   │   ├── job_runner.py             # Cloud Run Job wrapper (reads env vars, writes IngestionRun node)
│   │   ├── agents/                   # Agent implementations
│   │   │   └── matching/             # Phase 2 matching agents
│   │   ├── data_acquisition/         # API clients (Semantic Scholar, arXiv, OpenAlex)
│   │   │   └── importer.py           # PaperImporter: metadata → Neo4j Paper/Author nodes
│   │   ├── extraction/               # PDF → KG pipeline
│   │   │   ├── pdf_extractor.py      # PyMuPDF text extraction
│   │   │   ├── section_segmenter.py  # Section identification
│   │   │   ├── problem_extractor.py  # LLM extraction via instructor
│   │   │   ├── pipeline.py           # Per-paper extraction orchestration
│   │   │   ├── kg_integration.py     # V1 KG integration
│   │   │   └── kg_integration_v2.py  # V2: store mentions, embed, match to concepts, create EXTRACTED_FROM
│   │   └── knowledge_graph/          # Graph operations
│   │       ├── models/entities.py    # Pydantic entity models (to_neo4j_properties() with JSON serialization)
│   │       ├── schema.py             # Neo4j schema definitions (SchemaManager)
│   │       ├── repository.py         # Neo4j CRUD (includes link_paper_to_author)
│   │       ├── concept_matcher.py    # Vector similarity matching
│   │       ├── auto_linker.py        # HIGH confidence auto-linking
│   │       ├── review_queue.py       # Human review queue service
│   │       └── concept_refinement.py # Canonical statement synthesis
│   ├── api/src/agentic_kg_api/       # FastAPI application
│   │   ├── main.py                   # App entrypoint (includes ingest router)
│   │   ├── routers/                  # Route handlers
│   │   │   ├── ingest.py            # POST /api/ingest (triggers Cloud Run Job), GET /api/ingest/{trace_id}
│   │   │   └── reviews.py           # Review queue endpoints
│   │   ├── schemas.py               # API response models (includes IngestionRequest/Response)
│   │   └── dependencies.py          # FastAPI dependency injection
│   └── ui/                           # Next.js 14 frontend
├── docker/
│   └── Dockerfile.job                # Core-only image for Cloud Run Job (no API deps)
├── infra/                            # Terraform IaC (includes google_cloud_run_v2_job.ingest)
├── llm/                              # LLM-related project files
│   ├── features/                     # Feature specs (BACKLOG.md, d1-ingest-real-papers.md, etc.)
│   └── memory_bank/                  # Authoritative project context (this directory)
├── construction/                     # Design docs and sprint tracking
│   ├── design/                       # Feature specifications (incl. kg-schema-enhancement-gap-analysis.md)
│   └── sprints/                      # Sprint task lists (11 completed: 00–10)
├── .claude/                          # Claude Code config
│   ├── agents/                       # Project agent definitions (construction-lead, feature-architect, knowledge-steward, memory-agent, code-review)
│   └── skills/                       # Project-local skills
├── memory-bank/                      # Legacy project context (STALE — use llm/memory_bank/)
└── scripts/                          # Utilities (smoke_test.py, etc.)
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

### LangGraph Workflow Pattern

Agent workflows use LangGraph `StateGraph` with typed state dictionaries:
- Node functions implement each processing step
- Routing functions handle conditional edges
- `MemorySaver` provides checkpoint persistence
- Pattern used in both research agents (`agents/`) and matching agents (`agents/matching/`)

### Human-in-the-Loop

- `CheckpointManager` for research workflow decisions
- `ReviewQueueService` for matching review with priority-based SLA (24h/7d/30d)
- API endpoints for review assignment and resolution (`routers/reviews.py`)

### Dependency Injection (FastAPI)

- `dependencies.py` provides `get_review_queue`, `get_neo4j_driver`, etc.
- Services instantiated per-request with FastAPI `Depends()`

### Data Acquisition Resilience

- Token bucket rate limiting per API source
- Circuit breaker with exponential backoff retry
- TTL-based response caching (`cachetools`)
- Multi-source paper aggregation with metadata normalization

## Data Flow: Full Ingestion Pipeline

```
CLI `ingest` / API POST /api/ingest → ingestion.ingest_papers()
  Phase 1: Search & Import
    → data_acquisition clients (OpenAlex, arXiv, Semantic Scholar)
    → PaperImporter → Neo4j Paper + Author nodes + AUTHORED_BY edges
  Phase 2: Extraction (per paper with PDF)
    → pdf_extractor → section_segmenter → problem_extractor (instructor + OpenAI)
  Phase 3: Integration
    → kg_integration_v2 → store ProblemMention → embed → concept_matcher
      → [routing by confidence]
        → auto_linker (HIGH) / agents (MEDIUM/LOW) / review_queue (escalation)
          → concept_refinement (at 5/10/25/50 mention thresholds)
  Phase 4: Sanity Checks
    → 5 automated checks against Neo4j graph integrity
```

### Cloud Run Job Pattern

For async/long-running ingestion:
- API triggers `google_cloud_run_v2_job.ingest` via REST
- `job_runner.py` reads config from env vars, calls `ingest_papers()`, writes `IngestionRun` node
- API polls Neo4j for `IngestionRun` status via `GET /api/ingest/{trace_id}`

## Naming Conventions

- **Python style**: snake_case functions/variables, PascalCase classes
- **File naming**: snake_case modules
- **Test files**: `test_<module>.py` mirroring source structure
- **Pydantic models**: Strict validation, field descriptions
- **Line length**: 100 characters (Ruff config)
- **Sprint numbering**: Zero-indexed (Sprint 00 through Sprint 10)
