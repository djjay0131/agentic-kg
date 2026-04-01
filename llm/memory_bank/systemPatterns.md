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
│   │   ├── agents/                   # Agent implementations
│   │   │   └── matching/             # Phase 2 matching agents
│   │   ├── data_acquisition/         # API clients (Semantic Scholar, arXiv, OpenAlex)
│   │   ├── extraction/               # PDF → KG pipeline
│   │   │   ├── pdf_extractor.py      # PyMuPDF text extraction
│   │   │   ├── section_segmenter.py  # Section identification
│   │   │   ├── problem_extractor.py  # LLM extraction logic
│   │   │   ├── pipeline.py           # End-to-end orchestration
│   │   │   ├── kg_integration.py     # V1 KG integration
│   │   │   └── kg_integration_v2.py  # V2 with canonical problem routing
│   │   └── knowledge_graph/          # Graph operations
│   │       ├── models/entities.py    # Pydantic entity models
│   │       ├── schema.py             # Neo4j schema definitions
│   │       ├── concept_matcher.py    # Vector similarity matching
│   │       ├── auto_linker.py        # HIGH confidence auto-linking
│   │       ├── review_queue.py       # Human review queue service
│   │       └── concept_refinement.py # Canonical statement synthesis
│   ├── api/src/agentic_kg_api/       # FastAPI application
│   │   ├── main.py                   # App entrypoint
│   │   ├── routers/                  # Route handlers
│   │   │   └── reviews.py           # Review queue endpoints
│   │   ├── schemas.py               # API response models
│   │   └── dependencies.py          # FastAPI dependency injection
│   └── ui/                           # Next.js 14 frontend
├── infra/                            # Terraform IaC
├── construction/                     # Design docs and sprint tracking
│   ├── design/                       # Feature specifications
│   └── sprints/                      # Sprint task lists
├── memory-bank/                      # Legacy project context
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

## Data Flow: Paper → Knowledge Graph

```
PDF → pdf_extractor → section_segmenter → problem_extractor → pipeline
  → kg_integration_v2 → concept_matcher → [routing by confidence]
    → auto_linker (HIGH) / agents (MEDIUM/LOW) / review_queue (escalation)
      → concept_refinement (at 5/10/25/50 mention thresholds)
```

## Naming Conventions

- **Python style**: snake_case functions/variables, PascalCase classes
- **File naming**: snake_case modules
- **Test files**: `test_<module>.py` mirroring source structure
- **Pydantic models**: Strict validation, field descriptions
- **Line length**: 100 characters (Ruff config)
- **Sprint numbering**: Zero-indexed (Sprint 00 through Sprint 10)
