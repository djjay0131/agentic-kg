# Feature: Ingest Real Papers into Knowledge Graph

**Status:** VERIFIED
**Date:** 2026-03-28
**Author:** Feature Architect (AI-assisted)

## Problem

The knowledge graph contains only test data. All infrastructure exists independently — data acquisition clients (Semantic Scholar, arXiv, OpenAlex), extraction pipeline (PDF → problems), and canonical architecture (KGIntegratorV2 with mention-to-concept routing) — but there is no orchestrated workflow that chains them together. A researcher cannot point the system at a topic and get a populated, navigable knowledge graph.

Without real data, the system cannot be validated, the graph visualization is empty, and the project's core value proposition remains untested.

## Goals

- End-to-end ingestion: search query → paper metadata → PDF extraction → problem integration → canonical deduplication
- Both API endpoint and CLI command, sharing a common orchestration function
- Successfully ingest 10-20 papers on "Graph-Based Retrieval" as the proving dataset
- Automated sanity checks confirming structural integrity of the resulting graph
- Human-reviewable graph with deduplicated problems, labeled edges, and full provenance chains
- ≥90% of extracted problems are semantically coherent as judged by human reviewer

## Non-Goals

- Ingesting hundreds or thousands of papers (scale optimization is future work)
- Building a new UI for ingestion (existing graph visualization is sufficient for review)
- Changing the extraction pipeline or canonical architecture (use as-is, except EXTRACTED_FROM fix)
- Real-time streaming progress via WebSocket (async job with polling is sufficient for v1)

## User Stories

- As a researcher, I want to provide a topic query and have the system populate a knowledge graph from real papers, so that I can explore research problems in that area.
- As a developer, I want a CLI command to ingest papers locally for testing, so that I can validate the pipeline without deploying.
- As a reviewer, I want to see a populated graph with labeled edges and provenance, so that I can verify the system produces sensible results.

## Design Approach

### Architecture

The feature extends the existing `BatchProcessor` in `batch.py` with a search-driven entry point, adding search + import + sanity check phases around it. A new `ingest_papers()` orchestration function is the shared entry point for both CLI and API. This reuses BatchProcessor's SQLite job queue, retry logic, resume capability, and progress callbacks rather than creating a parallel orchestration path.

```
                         ┌──────────┐   ┌───────────────┐
                         │ CLI cmd  │   │ API POST      │
                         │ `ingest` │   │ /ingest       │
                         │ (sync)   │   │ (async job)   │
                         └────┬─────┘   └────┬──────────┘
                              │              │
                              └──────┬───────┘
                                     │
                          ┌──────────▼──────────┐
                          │   ingest_papers()   │
                          │   (orchestration)    │
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
    ┌─────────▼────────┐  ┌─────────▼────────┐  ┌─────────▼────────┐
    │ Phase 1: Search  │  │ Phase 2: Extract │  │ Phase 3: Integrate│
    │ & Import Metadata│  │ via BatchProcessor│  │ into Canonical   │
    │ (PaperAggregator │  │ (retry, resume,  │  │ Architecture     │
    │  + PaperImporter)│  │  job queue)      │  │ (KGIntegratorV2) │
    └──────────────────┘  └──────────────────┘  └──────────┬───────┘
                                                           │
                                                ┌──────────▼────────┐
                                                │ Phase 4: Sanity   │
                                                │ Checks            │
                                                └───────────────────┘
```

### Data Flow

1. **Phase 1 — Search & Import Metadata**
   - `PaperAggregator.search_papers(query, limit=N)` across configured sources
   - Deduplicate by DOI
   - `PaperImporter.batch_import()` creates Paper and Author nodes in Neo4j
   - Skip papers already in the graph (idempotent)

2. **Phase 2 — Extract Problems via BatchProcessor**
   - Build batch job list from papers with `pdf_url`
   - `BatchProcessor.process_batch()` handles extraction with:
     - SQLite job queue for state persistence
     - Semaphore-limited concurrency (default 3)
     - Retry with backoff on failure
     - Resume capability for interrupted runs
   - Papers without PDF URLs are logged and skipped (not a failure)

3. **Phase 3 — Integrate into Canonical Architecture**
   - `KGIntegratorV2.integrate_extracted_problems()` for each paper's problems
   - **EXTRACTED_FROM edge must be created** linking ProblemMention → Paper (currently missing — see Required Fix below)
   - HIGH confidence matches auto-link to existing ProblemConcepts
   - MEDIUM/LOW routed through agent workflow (enabled by default)
   - New concepts created for novel problems
   - Concept refinement triggers at mention thresholds

4. **Phase 4 — Sanity Checks**
   - Run automated structural integrity queries against Neo4j
   - Return pass/fail for each check with counts

### Required Fix: EXTRACTED_FROM Edge

`KGIntegratorV2._store_mention_node()` currently runs a bare `CREATE (m:ProblemMention) SET m = $properties` with no relationship to the Paper node. The `paper_doi` is stored as a property but there is no actual `EXTRACTED_FROM` edge. This must be fixed as part of this feature to satisfy AC-4 (full provenance chain). The fix should:

1. After creating the ProblemMention node, create the edge: `(m:ProblemMention)-[:EXTRACTED_FROM]->(p:Paper {doi: $paper_doi})`
2. This goes in `KGIntegratorV2._store_mention_node()` or a new method called from `_process_extracted_problem()`

### Entry Points

**CLI (synchronous):**
```bash
# Search and ingest
python -m agentic_kg ingest --query "graph-based retrieval" --limit 20

# With source filtering
python -m agentic_kg ingest --query "graph-based retrieval" --limit 20 --sources semantic_scholar arxiv

# Dry run (search only, no extraction)
python -m agentic_kg ingest --query "graph-based retrieval" --limit 20 --dry-run

# Run sanity checks only (against existing graph)
python -m agentic_kg ingest --sanity-check-only

# JSON output
python -m agentic_kg ingest --query "graph-based retrieval" --limit 20 --json

# Disable agent workflows for faster run
python -m agentic_kg ingest --query "graph-based retrieval" --limit 20 --no-agent-workflow
```

**API (async with polling):**
```
POST /ingest
{
    "query": "graph-based retrieval",
    "limit": 20,
    "sources": ["semantic_scholar", "arxiv"],
    "dry_run": false,
    "enable_agent_workflow": true
}

Response (immediate):
{
    "trace_id": "ingest-a1b2c3d4",
    "status": "queued"
}

GET /ingest/{trace_id}

Response (while running):
{
    "trace_id": "ingest-a1b2c3d4",
    "status": "running",
    "phase": "extracting",
    "papers_found": 23,
    "papers_imported": 18,
    "papers_extracted": 7,
    "papers_remaining": 7
}

Response (complete):
{
    "trace_id": "ingest-a1b2c3d4",
    "status": "completed",
    "papers_found": 23,
    "papers_imported": 18,
    "papers_extracted": 14,
    "papers_skipped_no_pdf": 4,
    "total_problems_extracted": 47,
    "concepts_created": 31,
    "concepts_linked": 16,
    "sanity_checks": [
        {"name": "mentions_have_instance_of", "passed": true, "count": 0, "description": "..."},
        {"name": "mentions_linked_to_paper", "passed": true, "count": 0, "description": "..."},
        {"name": "papers_have_authors", "passed": true, "count": 0, "description": "..."},
        {"name": "no_orphan_concepts", "passed": true, "count": 0, "description": "..."},
        {"name": "graph_populated", "passed": true, "count": 156, "description": "156 nodes, 203 edges"}
    ]
}
```

## Sample Implementation

```python
# Extends packages/core/src/agentic_kg/extraction/batch.py
# (or new file packages/core/src/agentic_kg/ingestion.py that imports BatchProcessor)

async def ingest_papers(
    query: str,
    limit: int = 20,
    sources: list[str] | None = None,
    dry_run: bool = False,
    enable_agent_workflow: bool = True,
    min_extraction_confidence: float = 0.5,
    on_progress: Callable[[str, str, Any], None] | None = None,
) -> IngestionResult:
    """
    End-to-end paper ingestion: search → import → extract → integrate.
    Reuses BatchProcessor for extraction phase (retry, resume, job queue).
    """
    aggregator = get_paper_aggregator()
    importer = get_paper_importer()
    trace_id = f"ingest-{uuid.uuid4().hex[:8]}"
    result = IngestionResult(trace_id=trace_id, query=query)

    # Phase 1: Search across sources
    search = await aggregator.search_papers(query, sources=sources, limit=limit)
    result.papers_found = len(search.papers)

    if dry_run:
        result.status = "dry_run"
        result.dry_run_papers = [
            {"doi": p.doi, "title": p.title, "pdf_url": p.pdf_url}
            for p in search.papers
        ]
        return result

    # Phase 1b: Import metadata to KG
    dois = [p.doi for p in search.papers if p.doi]
    import_batch = await importer.batch_import(dois, create_authors=True)
    result.papers_imported = import_batch.created + import_batch.updated
    _notify(on_progress, "metadata_imported", None, import_batch.to_dict())

    # Phase 2: Extract via BatchProcessor (gets retry, resume, job queue)
    papers_with_pdf = [p for p in search.papers if p.pdf_url and p.doi]
    batch_papers = [
        {"doi": p.doi, "url": p.pdf_url, "title": p.title}
        for p in papers_with_pdf
    ]

    batch_config = BatchConfig(max_concurrent=3, store_to_kg=False)  # We integrate separately
    processor = BatchProcessor(config=batch_config)
    batch_result = await processor.process_batch(batch_papers, batch_id=trace_id)

    result.papers_extracted = batch_result.progress.completed_jobs
    result.papers_skipped_no_pdf = len(search.papers) - len(papers_with_pdf)

    # Phase 3: Integrate into canonical architecture
    integrator = KGIntegratorV2(
        enable_agent_workflow=enable_agent_workflow,
        enable_concept_refinement=True,
    )
    for job in batch_result.jobs:
        if job.status != JobStatus.COMPLETED:
            continue
        # Re-process to get ExtractedProblems (BatchProcessor stores count only)
        # Implementation detail: either store proc results in batch, or re-read
        # For now, illustrative — actual impl will cache PaperProcessingResult
        pass  # integrate each paper's problems

    # Phase 4: Sanity checks
    result.sanity_checks = await run_sanity_checks()
    result.status = "completed"
    return result


async def run_sanity_checks() -> list[SanityCheck]:
    """Run structural integrity checks against Neo4j."""
    repo = get_repository()
    checks = []

    with repo.session() as session:
        # Check 1: ProblemMentions with INSTANCE_OF (excluding PENDING review)
        orphan_mentions = session.run("""
            MATCH (m:ProblemMention)
            WHERE NOT (m)-[:INSTANCE_OF]->()
              AND m.review_status <> 'PENDING'
            RETURN count(m)
        """).single()[0]
        checks.append(SanityCheck(
            name="mentions_have_instance_of",
            passed=orphan_mentions == 0,
            count=orphan_mentions,
            description="ProblemMentions without INSTANCE_OF (excl. pending review)",
        ))

        # Check 2: Every ProblemMention traces to a Paper
        unlinked = session.run("""
            MATCH (m:ProblemMention)
            WHERE NOT (m)-[:EXTRACTED_FROM]->(:Paper)
            RETURN count(m)
        """).single()[0]
        checks.append(SanityCheck(
            name="mentions_linked_to_paper",
            passed=unlinked == 0,
            count=unlinked,
            description="ProblemMentions without EXTRACTED_FROM Paper",
        ))

        # Check 3: Every Paper has at least one Author
        authorless = session.run("""
            MATCH (p:Paper)
            WHERE NOT (p)-[:AUTHORED_BY]->(:Author)
            RETURN count(p)
        """).single()[0]
        checks.append(SanityCheck(
            name="papers_have_authors",
            passed=authorless == 0,
            count=authorless,
            description="Papers without any AUTHORED_BY edges",
        ))

        # Check 4: No orphan ProblemConcepts
        orphan_concepts = session.run("""
            MATCH (c:ProblemConcept)
            WHERE NOT ()-[:INSTANCE_OF]->(c)
            RETURN count(c)
        """).single()[0]
        checks.append(SanityCheck(
            name="no_orphan_concepts",
            passed=orphan_concepts == 0,
            count=orphan_concepts,
            description="ProblemConcepts with no linked mentions",
        ))

        # Check 5: Graph population summary
        node_count = session.run("MATCH (n) RETURN count(n)").single()[0]
        edge_count = session.run("MATCH ()-[r]->() RETURN count(r)").single()[0]
        checks.append(SanityCheck(
            name="graph_populated",
            passed=node_count > 0 and edge_count > 0,
            count=node_count,
            description=f"{node_count} nodes, {edge_count} edges",
        ))

    return checks
```

## Edge Cases & Error Handling

### No PDF URL Available
- **Scenario**: Paper found in Semantic Scholar but has no open-access PDF link
- **Behavior**: Paper metadata still imported (Paper + Author nodes created); extraction skipped; counted in `papers_skipped_no_pdf`
- **Test**: Ingest a query where some papers are paywalled; verify Paper nodes exist without ProblemMentions

### API Rate Limiting
- **Scenario**: Semantic Scholar 100 req/5min limit hit during search
- **Behavior**: Existing rate limiter and circuit breaker in data acquisition layer handle backoff automatically
- **Test**: Mock rate limit response; verify retry with backoff

### Duplicate Papers Across Sources
- **Scenario**: Same paper found in both Semantic Scholar and arXiv
- **Behavior**: `PaperAggregator` deduplicates by DOI; `PaperImporter` skips existing papers
- **Test**: Search query returning same paper from multiple sources; verify single Paper node

### LLM Extraction Failure
- **Scenario**: OpenAI API returns error during problem extraction for one paper
- **Behavior**: Error logged; BatchProcessor retries up to 2 times; if still failed, counted in extraction errors; other papers continue
- **Test**: Mock LLM failure for one paper; verify remaining papers processed successfully

### Empty Search Results
- **Scenario**: Query returns zero papers
- **Behavior**: Return IngestionResult with `papers_found=0`, status "completed", no errors
- **Test**: Search with obscure query; verify graceful empty result

### Neo4j Connection Failure
- **Scenario**: Neo4j unreachable during integration phase
- **Behavior**: Fail fast with clear error message; extraction results preserved in BatchProcessor's SQLite queue for retry
- **Test**: Mock Neo4j connection failure; verify error in result and jobs resumable

### Escalated Mentions (Human Review)
- **Scenario**: Agent workflow escalates a MEDIUM/LOW confidence match to human review
- **Behavior**: ProblemMention created with `review_status=PENDING`, no INSTANCE_OF edge; sanity check excludes these from orphan count
- **Test**: Verify escalated mentions appear in review queue and are excluded from sanity check 1

## Acceptance Criteria

### AC-1: Search and Import Metadata
- **Given** a query "graph-based retrieval" and limit of 20
- **When** `ingest_papers()` is called
- **Then** Paper and Author nodes are created in Neo4j for papers found across sources, with DOI, title, abstract, and author data

### AC-2: Extract Problems from PDFs
- **Given** imported papers with available PDF URLs
- **When** the extraction phase runs via BatchProcessor
- **Then** ProblemMentions are created with statement, domain, assumptions, constraints, datasets, metrics, and confidence scores

### AC-3: Canonical Deduplication
- **Given** extracted problems from multiple papers that describe the same underlying research problem
- **When** KGIntegratorV2 processes them
- **Then** they are linked to the same ProblemConcept via INSTANCE_OF edges (not duplicated)

### AC-4: Full Provenance Chain
- **Given** any ProblemMention in the graph
- **When** traversing its relationships
- **Then** a complete chain exists: ProblemMention → INSTANCE_OF → ProblemConcept, ProblemMention → EXTRACTED_FROM → Paper → AUTHORED_BY → Author

### AC-5: Labeled Edges
- **Given** the populated graph
- **When** visualized in the graph UI or Neo4j browser
- **Then** all edges have type labels (INSTANCE_OF, EXTRACTED_FROM, AUTHORED_BY, EXTENDS, CONTRADICTS, DEPENDS_ON, REFRAMES)

### AC-6: Automated Sanity Checks Pass
- **Given** a completed ingestion run
- **When** sanity checks execute
- **Then** all 5 checks pass: mentions have INSTANCE_OF (excl. pending review), mentions link to papers, papers have authors, no orphan concepts, graph is populated

### AC-7: CLI Command Works
- **Given** a terminal with API keys configured
- **When** running `python -m agentic_kg ingest --query "graph-based retrieval" --limit 20`
- **Then** papers are ingested with progress output, and the command exits with status 0

### AC-8: API Endpoint Works (Async)
- **Given** the FastAPI server running
- **When** POSTing to `/ingest` with `{"query": "graph-based retrieval", "limit": 20}`
- **Then** response returns immediately with `trace_id` and status `"queued"`
- **And** `GET /ingest/{trace_id}` returns progress updates and final results when complete

### AC-9: Dry Run Mode
- **Given** the `--dry-run` flag or `"dry_run": true`
- **When** ingestion runs
- **Then** only search is performed; no data is written to Neo4j; response includes list of papers that would be ingested

### AC-10: Human Review Passes (≥90% Coherence)
- **Given** the graph populated with 10-20 papers on "graph-based retrieval"
- **When** a human reviews the graph visualization
- **Then** ≥90% of extracted problems are semantically coherent, deduplication is sensible, and the graph is navigable

## Technical Notes

- **Orchestration**: Extend `BatchProcessor` or create thin `ingestion.py` that delegates extraction to `BatchProcessor` (reuse retry/resume/job queue)
- **Modified**: `packages/core/src/agentic_kg/extraction/kg_integration_v2.py` — add EXTRACTED_FROM edge creation in `_store_mention_node()`
- **Modified**: `packages/core/src/agentic_kg/cli.py` — add `ingest` subcommand
- **New file**: `packages/api/src/agentic_kg_api/routers/ingest.py` — async API endpoint with polling
- **Modified**: `packages/api/src/agentic_kg_api/main.py` — register ingest router
- **Pattern**: Follow existing singleton/lazy-init pattern used by pipeline.py and batch.py
- **Pattern**: Follow existing CLI pattern in cli.py (argparse subcommands, async entry point)
- **Pattern**: Follow existing router pattern in routers/reviews.py (Pydantic request/response, dependency injection)
- **Pattern**: Async job pattern — POST returns trace_id, GET polls for status (similar to batch processing)
- **No data model changes**: Uses existing Paper, Author, ProblemMention, ProblemConcept, and all relationship types
- **LLM cost consideration**: 20 papers x ~$0.05/paper extraction + agent workflow calls ≈ $1-3 per ingestion run

## Dependencies

- Existing: `PaperAggregator`, `PaperImporter`, `BatchProcessor`, `PaperProcessingPipeline`, `KGIntegratorV2`
- External APIs: Semantic Scholar, arXiv, OpenAlex (require network access)
- LLM API: OpenAI (requires `OPENAI_API_KEY` for extraction and agent workflows)
- Neo4j: Staging instance at `bolt://34.173.74.125:7687` (requires credentials)

## Open Questions

- **BatchProcessor integration detail**: `BatchProcessor` currently calls `self.integrator.integrate_extraction_result(result)` using V1 integrator. The ingestion flow needs V2 integration. Two options: (a) make `store_to_kg=False` in batch config and run V2 integration separately, or (b) make BatchProcessor configurable to use V2 integrator. Decide during implementation.
- **PDF download failures**: Some arXiv PDFs may be temporarily unavailable. BatchProcessor retries up to 2 times. If still failed, they're reported as errors. A manual re-run via `--resume` flag could be added later if needed.
