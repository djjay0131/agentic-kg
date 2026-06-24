# Feature: Entity Pipeline Orchestration

**Status:** VERIFIED
**Date:** 2026-06-21
**Implementation Date:** 2026-06-23
**Verification Date:** 2026-06-24
**Author:** Feature Architect (AI-assisted)
**Backlog ID:** (loop-close; no explicit backlog item — surfaced after E-7 verify)
**Depends On:** E-1 (Topic, VERIFIED), E-2 (ResearchConcept, VERIFIED), E-3 (Model, VERIFIED), E-4 (Method, VERIFIED), E-5 (Citation graph, VERIFIED), E-6 (Entity descriptions, VERIFIED), E-7 (Cross-entity normalization, VERIFIED), E-8 V1 (Topic+Concept extractors, VERIFIED), E-8 V2 (Model+Method extractors + citation wiring, VERIFIED)
**Decoupled From:** L-1 (low-cost SLM — orchestration is L-1-ready via per-extractor client injection)

## Problem

Every feature shipped over the entity-expansion arc (E-1 through E-8 V2 plus E-7) added machinery: 4 new extractors, a 5-way orchestrator, a cross-entity disambiguation router, a V2 entity integrator with audit trails, and citation wiring. **None of it runs in production today.**

`ingest_papers` (the orchestration entry point for both `agentic-kg ingest` and the Cloud Run Job) currently does only three things:

1. Phase 1 — Search + import metadata; citations populate via the E-8 V2 hook on `PaperImporter.import_paper` (already wired).
2. Phase 2 — `pipeline.process_pdf_url` runs `problem_extractor` against the PDF; produces `ExtractedProblem[]`.
3. Phase 3 — `KGIntegratorV2.integrate_extracted_problems` (the V1 problem-routing integrator) creates ProblemMention nodes + routes them through the concept-matching pipeline.

The 4 dormant extractors (Topic, ResearchConcept, Model, Method), the 5-way `extract_all_entities` orchestrator, `normalize_cross_entity`, and `integrate_paper_entities` are all built and unit-tested but **never invoked from the production path**. After ingesting N papers today, the graph contains ProblemMention/ProblemConcept + Paper + Author + CITES — and zero of: Topic edges, ResearchConcept nodes, USES_MODEL, APPLIES_METHOD, INVOLVES_CONCEPT, `Paper.normalization_audit`, `Paper.extraction_incomplete`, `Paper.taxonomy_hash`.

The fix is one orchestration feature: refactor `ingest_papers` Phase 2/3 to wire `extract_all_entities` → `normalize_cross_entity` → `KGIntegratorV2.integrate_extracted_problems` (V1 mention path) → `integrate_paper_entities` (V2 entity path) into the per-paper loop. Citations stay where they are (Phase 1, in the importer). All four entity extractors run on every paper (with title + abstract fallback when no PDF) per Q1 decision. Operators get two opt-out flags (Q2 decision) for cost control.

## Goals

- **Single per-paper loop** that runs (a) Problem extraction via existing `pipeline.process_pdf_url`, (b) Topic + Concept + Model + Method extraction in parallel via `extract_all_entities`, (c) E-7 cross-entity normalization, (d) V1 mention/concept integration via `KGIntegratorV2.integrate_extracted_problems`, (e) V2 entity integration via `integrate_paper_entities`.
- **Universal extraction scope** (Q1): all 4 new extractors run on every paper in the batch. Papers with PDFs use the rich `section_text` (abstract + intro + methodology + experiments); papers without PDFs fall back to `paper.abstract` alone. The empty-section short-circuit inside each extractor (returns `[]` cleanly when input is empty) makes PDF-less, abstract-less papers safe.
- **Two opt-out flags** (Q2): `--no-extract-entities` (skip the 4 new extractors AND the normalizer → V1 problem-only behavior) and `--no-normalize-cross-entity` (keep the extractors but skip the routing LLM → accept double-edge risk for cost control). Default both on.
- **V2 integration runs on any successful extraction** (Q3): if ANY of {problem, topic, concept, model, method} produced results, the V2 integrator runs. PDF-less papers with non-empty abstracts can still land Topic/Concept extractions without the V1 problem path firing.
- **Per-batch shared dependencies**: one `TopicExtractor` instance per `ingest_papers` invocation (taxonomy snapshot is consistent for the batch, matching E-8 V1 AC's per-batch lifecycle). One LLM client singleton; one EmbeddingService. Constructed at the top of the function, threaded into the per-paper loop.
- **Per-paper failure isolation** (carries from E-8 V1's `_run` wrapper): a paper that fails extraction or integration records the error in `IngestionResult.extraction_errors[doi]` and continues. A partial-extraction paper carries `extraction_incomplete=true` and the audit fields on its Paper node (already implemented in `integrate_paper_entities`; this feature just wires the data through).
- **No regression** on the existing V1 problem-extraction + mention routing behavior. Same `KGIntegratorV2.integrate_extracted_problems` call, same parameters, same per-paper gating (`problems` non-empty).
- **CLI + Cloud Run Job parity**: same opt-out flags surface as CLI args and as env vars (`EXTRACT_ENTITIES`, `NORMALIZE_CROSS_ENTITY`), defaulting on. Matches the existing `POPULATE_CITATIONS` / `INGEST_AGENT_WORKFLOW` env-var pattern.

## Non-Goals

- **New entity types, new extractors, new prompts.** This feature only orchestrates existing machinery.
- **New repository methods or schema changes.** All write paths exist (`assign_entity_to_topic`, `create_or_merge_research_concept`, `create_or_merge_model`, `create_or_merge_method`, `link_paper_to_*`, the audit SETs on Paper).
- **Cross-paper canonicalization** ("attention mechanism" extracted as Concept in paper A and Method in paper B). E-7 is per-paper; cross-paper coordination is a separate spec.
- **`KGIntegratorV2` refactor.** The V1 problem-mention integrator stays as-is. The new V2 entity integrator runs alongside it per paper. Both are needed; merging them is out of scope.
- **Re-routing the existing `populate_citations` hook.** It already runs in `PaperImporter.import_paper` (E-8 V2). Orchestration leaves it there.
- **Live LLM eval calibration** (E-7 AC-21 and E-8 V2 AC-17 deferred steps). Orchestration creates the conditions for a calibration run — actually running it against a labeled fixture set is a separate operational step.
- **Restructuring the IngestionResult fields beyond additive counters.** New counters (`topics_linked`, `concepts_linked`, `models_linked`, `methods_linked`, `papers_marked_incomplete`, `papers_with_normalization_audit`) are added; existing fields keep their meaning.
- **Async refactor of `integrate_paper_entities`** (sync today). It stays sync — the orchestrator awaits `normalize_cross_entity` before calling the sync integrator with the resulting `NormalizationResult`. Matches E-7's architectural decision.

## User Stories

- **As a researcher running `agentic-kg ingest --query "retrieval augmented generation"`**, I want the resulting graph to contain Topic + Concept + Model + Method nodes for each ingested paper, so that vector search and graph queries return meaningful results without me having to manually invoke create-* commands.
- **As a Cloud Run Job operator running a 5000-paper bulk import**, I want `EXTRACT_ENTITIES=false` to disable the 4 new extractors (and the normalizer) so I can do a metadata + citations refresh without paying for ~5000-6000 extra LLM calls.
- **As a developer testing the pipeline locally**, I want `--no-normalize-cross-entity` so I can iterate on extractor prompts without paying for the routing LLM (which adds variance).
- **As an operator debugging a paper that landed half-extracted in the graph**, I want `Paper.extraction_incomplete=true` plus `Paper.extraction_failed_extractors` plus (if applicable) `Paper.normalization_audit` to land on the Paper node automatically so I can use a single Cypher query to find what went wrong.
- **As a future spec author who wants to add a new entity type**, I want the per-paper loop's structure to be the canonical template — one new extractor goes into the parallel batch + one new writer goes into `integrate_paper_entities`; the orchestrator doesn't need to grow.

## Design Approach

### High-level data flow per paper

```
┌────────────────────────────────────────────────────────────────────┐
│                       Per-batch (once)                             │
│  llm_client = get_openai_client()                                  │
│  topic_x   = TopicExtractor(client=llm_client)   # taxonomy snap   │
│  concept_x = ConceptExtractor(client=llm_client)                   │
│  model_x   = ModelExtractor(client=llm_client)                     │
│  method_x  = MethodExtractor(client=llm_client)                    │
│  embedder  = EmbeddingService()                                    │
│  taxonomy_hash = compute_taxonomy_hash(topic_x.taxonomy)           │
│  v1_integrator = KGIntegratorV2(...)                               │
└────────────────────────────────────────────────────────────────────┘

For each paper:
  ┌─────────────────────────────────────────────────────────────────┐
  │ 1. Purge guardrail (existing AC-13 wiring; unchanged)           │
  │ 2. Resolve text source:                                         │
  │     - PDF path: pipeline.process_pdf_url() →                    │
  │       (problems, section_text)                                  │
  │     - No PDF: section_text = paper.abstract (Q1 fallback)       │
  │ 3. extract_all_entities(...) — 5-way parallel (NEW)             │
  │ 4. normalize_cross_entity(...) — async, per-paper (NEW)         │
  │ 5. V1: integrate_extracted_problems(...) — runs if problems     │
  │    non-empty (unchanged gate)                                   │
  │ 6. V2: integrate_paper_entities(...) — runs if ANY extraction   │
  │    non-empty (Q3) OR if V1 ran                                  │
  └─────────────────────────────────────────────────────────────────┘
```

### Why the parallelism stays exactly where it is

- **Phase 1** (search + batch_import + populate_citations): no change. `PaperImporter.batch_import` already threads `populate_citations` through.
- **Per-extractor parallelism**: `extract_all_entities` already runs the 5 extractors via `asyncio.gather` with `_run` per-extractor failure isolation. The orchestrator just feeds it 5 awaitables.
- **Across-paper parallelism**: NOT added in this feature. The loop is sequential per-paper. Adding `asyncio.gather` across papers is a separate cost/benefit decision (rate-limit and ordering concerns); deferred.

### Text source resolution

`PaperProcessingResult` exposes a `segmented_document: SegmentedDocument` carrying the per-section text, NOT a top-level `section_text` string. The orchestrator joins the relevant sections via a small helper:

```python
def _build_extractor_section_text(seg: SegmentedDocument | None) -> str:
    """Concatenate abstract + intro + methodology + experiments from a
    SegmentedDocument. Returns "" when the document is None or no
    matching sections exist. Each entity extractor's own empty-input
    short-circuit handles "" cleanly (returns [])."""
    if seg is None:
        return ""
    wanted = ("abstract", "introduction", "methodology", "experiments")
    parts = [
        s.text for s in seg.sections
        if s.section_type and s.section_type.lower() in wanted and s.text
    ]
    return "\n\n".join(parts)
```

Wiring in the per-paper loop:

```python
if paper.pdf_url:
    proc = await pipeline.process_pdf_url(
        url=paper.pdf_url, paper_title=paper.title,
        paper_doi=paper.doi, authors=[...],
    )
    if proc.success:
        section_text = _build_extractor_section_text(proc.segmented_document)
        problems = proc.get_high_confidence_problems(min_extraction_confidence)
    else:
        section_text = ""
        problems = []
else:
    proc         = None
    section_text = (paper.abstract or "")     # abstract-only fallback (Q1)
    problems     = []
```

Fallback ordering: if the PDF segmenter didn't tag any of the four wanted section types (workshop papers, weird preprints), `_build_extractor_section_text` returns `""` and the per-extractor empty-input short-circuit kicks in (no LLM cost). The orchestrator does NOT additionally fall back to `paper.abstract` in this branch; if a paper has a PDF but no usable sections, we accept zero entity extractions for that paper rather than risk feeding the extractors abstract-only context that contradicts the rich-text contract.

The empty-section short-circuit inside each extractor (`if not sections_text.strip(): return []`) makes truly-empty papers (no PDF, no abstract) safe — the 5-way orchestrator gets 5 empty lists, the normalizer finds no pairs, and the V2 integrator's Q3 trigger evaluates to `False`, so the paper just persists with metadata + citations.

### Per-paper integration sequencing

The V1 problem integrator (`KGIntegratorV2.integrate_extracted_problems`) MUST run BEFORE the V2 entity integrator (`integrate_paper_entities`) because the V2 integrator's B3 problem↔concept linker (from E-8 V1) consumes the `ProblemMention[]` that the V1 integrator just created.

```python
# V1 (existing) — runs first if any problems extracted.
v1_integration = None
if problems:
    v1_integration = v1_integrator.integrate_extracted_problems(
        extracted_problems=problems, paper_doi=paper.doi,
        paper_title=paper.title, session_trace_id=trace_id,
    )

# V2 (new) — gates per Q3: ANY successful extraction OR V1 ran.
any_v2_extractions = extract_entities and bool(
    extraction_result.topics or extraction_result.concepts
    or extraction_result.models or extraction_result.methods
)
if any_v2_extractions or v1_integration:
    mentions = (
        [m for m in v1_integration.mentions if m.concept_id]
        if v1_integration else []
    )
    v2_integration = integrate_paper_entities(
        paper_doi=paper.doi,
        extraction_result=extraction_result,
        mentions=mentions,
        taxonomy_hash=taxonomy_hash if extract_entities else "",
        repo=repo,
        normalization_result=norm_result,
    )
```

### Per-paper skip check (re-ingest cost guard, QA Q3 review)

To prevent re-running the 4 entity extractors + normalization for a paper that was already successfully extracted under the current taxonomy, the per-paper loop checks the existing Paper node before invoking any LLM-touching step:

```python
def _can_skip_entity_extraction(
    repo: Neo4jRepository,
    paper_doi: str,
    current_taxonomy_hash: str,
) -> bool:
    """True when the paper already has complete extraction under the
    current taxonomy snapshot. AC-13 purge clears these properties,
    forcing re-extraction on the next ingest after a purge."""
    if not paper_doi:
        return False
    existing = repo.get_paper(paper_doi)  # raises NotFoundError if missing
    return (
        existing.taxonomy_hash == current_taxonomy_hash
        and existing.extraction_incomplete is not True
    )
```

The skip semantics:

- **What gets skipped:** the 4 entity extractors (Topic/Concept/Model/Method), cross-entity normalization, V1 problem extraction (`pipeline.process_pdf_url`), V1 mention integration, and V2 entity integration — i.e., the entire LLM-touching extraction body for that paper.
- **What still runs:** Phase 1 metadata refresh + `populate_citations` (which refreshes the citation graph). The skip is "don't re-do entity extraction"; it doesn't mean "don't touch the paper at all".
- **The escape hatch:** a new `--force-reextract` CLI flag (default `False`) bypasses the skip check entirely. Set when an operator wants to re-run extraction (e.g., after a prompt rework) without going through AC-13's purge path.

The skip check naturally composes with AC-13 purge: purging clears `taxonomy_hash` (per `_set_paper_extraction_metadata`'s zero-state), so the next ingest sees `existing.taxonomy_hash == ""` ≠ current hash → skip check fails → extraction runs. No new code in the purge path needed.

### Opt-out flags

Three flags now (two extraction-control + one re-ingest control). All default ON for the extraction-control side and `--force-reextract` default OFF. All work the same way as `--no-populate-citations` from E-8 V2:

```python
# CLI (argparse)
ingest.add_argument(
    "--no-extract-entities",
    action="store_false", dest="extract_entities", default=True,
    help=(
        "Skip the 4 entity extractors (Topic/Concept/Model/Method) and "
        "the cross-entity normalizer. Falls back to V1 problem-only "
        "behavior. Adds zero LLM calls vs ~5-6 per paper with extraction on."
    ),
)
ingest.add_argument(
    "--no-normalize-cross-entity",
    action="store_false", dest="normalize_cross_entity_collisions",
    default=True,
    help=(
        "Run entity extractors but skip the cross-entity routing LLM. "
        "Concept ↔ Method double-edges may land in the graph; trade "
        "quality for cost ceiling."
    ),
)
ingest.add_argument(
    "--force-reextract", action="store_true", default=False,
    help=(
        "Bypass the per-paper skip check that avoids re-running entity "
        "extraction on previously-complete papers under the current "
        "taxonomy. Use after prompt reworks or extractor changes when "
        "you want all papers re-extracted without going through the "
        "AC-13 purge path."
    ),
)

# Cloud Run Job (job_runner.py _parse_env)
"extract_entities": os.environ.get("EXTRACT_ENTITIES", "true").lower() != "false",
"normalize_cross_entity_collisions": os.environ.get(
    "NORMALIZE_CROSS_ENTITY", "true",
).lower() != "false",
"force_reextract": os.environ.get(
    "FORCE_REEXTRACT", "false",
).lower() == "true",
```

### Default-on cost-spike callout (TL Q3 review)

**BREAKING CHANGE for production operators.** This feature ships with `extract_entities=True` and `normalize_cross_entity_collisions=True` as the defaults. The moment this lands, every production ingest run (nightly Cloud Run Job, manual `agentic-kg ingest` invocations) starts running ~5-6 additional LLM calls per paper compared to the V1 baseline. For a 1000-paper batch that's ~5-6K LLM calls per run.

Mitigation surfaces:
- The skip check (QA Q3) means re-running the same query day-over-day is near-zero-cost on previously-extracted papers; only NEW papers pay the full extraction bill.
- Operators who want to defer the cost spike entirely can set `EXTRACT_ENTITIES=false` in the Cloud Run Job env or pass `--no-extract-entities` at the CLI.
- The implementation report and the release notes MUST flag this loudly.

The default-on choice is intentional: orchestration's whole point is to wire the dormant pipeline. Defaulting off would leave the loop dormant indefinitely (E-6's `generate_description=False` defaults persisted that way for months because no follow-up PR ever flipped it). For this feature, the cost is the point.

### Per-batch dependency construction

All shared dependencies build once at the top of `ingest_papers`, INSIDE the `if extract_entities` block (so they only construct when actually needed):

```python
if extract_entities:
    llm_client = get_openai_client()                  # singleton
    topic_x    = TopicExtractor(client=llm_client)    # snapshots taxonomy
    concept_x  = ConceptExtractor(client=llm_client)
    model_x    = ModelExtractor(client=llm_client)
    method_x   = MethodExtractor(client=llm_client)
    taxonomy_hash = compute_taxonomy_hash(...)

    if normalize_cross_entity_collisions:
        embedder = EmbeddingService()

# V1 integrator is independent of extract_entities (might still need to
# handle V1 problem extraction even with extractors off).
v1_integrator = KGIntegratorV2(
    enable_agent_workflow=enable_agent_workflow,
    enable_concept_refinement=True,
)
```

### IngestionResult — additive counters

```python
@dataclass
class IngestionResult:
    # ... existing fields preserved ...
    topics_linked: int = 0                  # NEW
    concepts_v2_linked: int = 0             # NEW (V2 DISCUSSES — distinct from
                                            # existing `concepts_linked` which
                                            # counts V1 ProblemConcept routing)
    models_linked: int = 0                  # NEW
    methods_linked: int = 0                 # NEW
    papers_marked_incomplete: int = 0       # NEW
    papers_with_normalization_audit: int = 0  # NEW
```

### Per-paper try/except — failure isolation

The whole new per-paper body sits inside a single `try/except` block so any failure records the error and lets the loop proceed:

```python
for paper in search.papers:
    try:
        # ... purge check, text resolution, extraction, normalization,
        #     V1 and V2 integration ...
    except Exception as e:
        result.extraction_errors[paper.doi or paper.title] = str(e)
        logger.warning(f"[{trace_id}] Per-paper failure {paper.doi}: {e}")
        continue
```

Sub-extractor failures (within `extract_all_entities`) are already absorbed by `_run` — the orchestrator sees an `ExtractionFailure` record and `extraction_incomplete=true` flows through `integrate_paper_entities` to the Paper node. Nothing new for those.

### Progress callbacks

Existing callbacks (`search_complete`, `metadata_imported`, `extracted`, `integrated`, `purged`) all preserved. Two new phases emitted:

- `normalized` per paper: payload = `{"pairs_detected": N, "pairs_resolved": M, "pairs_rejected": K}`. Empty payload when normalize is off.
- `entity_integrated` per paper: payload = `{"topics": N, "concepts_v2": M, "models": K, "methods": L}`.

CLI prints these alongside the existing phases when not in `--json` mode.

## Sample Implementation

```python
# === packages/core/src/agentic_kg/ingestion.py — refactored Phase 2/3 ===

async def ingest_papers(
    query: str,
    limit: int = 20,
    sources: Optional[list[str]] = None,
    dry_run: bool = False,
    enable_agent_workflow: bool = True,
    min_extraction_confidence: float = 0.5,
    on_progress: Optional[Callable[[str, Optional[str], Any], None]] = None,
    force_rewrite: bool = False,
    populate_citations: bool = True,
    extract_entities: bool = True,                    # NEW
    normalize_cross_entity_collisions: bool = True,   # NEW
) -> IngestionResult:
    trace_id = f"ingest-{uuid.uuid4().hex[:8]}"
    result = IngestionResult(trace_id=trace_id, query=query, status="running")

    try:
        # ---- Phase 1: Search + import (unchanged; citations via importer) ----
        aggregator = get_paper_aggregator()
        search = await aggregator.search_papers(
            query, sources=sources, limit=limit,
        )
        result.papers_found = len(search.papers)
        _notify(on_progress, "search_complete", None, result.papers_found)

        if dry_run:
            result.status = "dry_run"
            return result

        importer = get_paper_importer()
        dois = [p.doi for p in search.papers if p.doi]
        import_batch = await importer.batch_import(
            dois, create_authors=True,
            populate_citations=populate_citations,
        )
        result.papers_imported = import_batch.created + import_batch.updated

        # ---- Per-batch dependencies ----
        repo = get_repository()
        pipeline = get_pipeline()
        v1_integrator = KGIntegratorV2(
            enable_agent_workflow=enable_agent_workflow,
            enable_concept_refinement=True,
        )

        topic_x = concept_x = model_x = method_x = None
        embedder = None
        llm_client = None
        taxonomy_hash = ""
        if extract_entities:
            from agentic_kg.extraction.llm_client import get_openai_client
            from agentic_kg.extraction.topic_extractor import TopicExtractor
            from agentic_kg.extraction.concept_extractor import ConceptExtractor
            from agentic_kg.extraction.model_extractor import ModelExtractor
            from agentic_kg.extraction.method_extractor import MethodExtractor
            from agentic_kg.extraction.taxonomy_hash import compute_taxonomy_hash

            llm_client = get_openai_client()
            topic_x    = TopicExtractor(client=llm_client)
            concept_x  = ConceptExtractor(client=llm_client)
            model_x    = ModelExtractor(client=llm_client)
            method_x   = MethodExtractor(client=llm_client)
            taxonomy_hash = compute_taxonomy_hash(topic_x.taxonomy_path)

            if normalize_cross_entity_collisions:
                from agentic_kg.knowledge_graph.embeddings import EmbeddingService
                embedder = EmbeddingService()

        # ---- Phase 2 + 3: Per-paper unified loop ----
        for paper in search.papers:
            try:
                # Purge guardrail (existing AC-13).
                if paper.doi and _paper_has_footprint(repo, paper.doi):
                    try:
                        purge_paper_extraction(
                            repo, paper.doi, force_rewrite=force_rewrite,
                        )
                        result.papers_purged += 1
                    except PurgeBlocked as e:
                        result.papers_blocked_by_guardrail += 1
                        result.extraction_errors[paper.doi] = str(e)
                        continue

                # Text source resolution (Q1).
                if paper.pdf_url:
                    proc = await pipeline.process_pdf_url(
                        url=paper.pdf_url,
                        paper_title=paper.title,
                        paper_doi=paper.doi,
                        authors=[a.name for a in paper.authors],
                    )
                    if proc.success:
                        section_text = _build_extractor_section_text(
                            proc.segmented_document,
                        )
                        problems = proc.get_high_confidence_problems(
                            min_extraction_confidence,
                        )
                    else:
                        section_text = ""
                        problems = []
                else:
                    section_text = paper.abstract or ""
                    problems = []

                # 5-way parallel extraction (E-8 V1 + V2).
                if extract_entities:
                    extraction_result = await extract_all_entities(
                        problem_call=_returns(problems),
                        topic_call=topic_x.extract(paper.title, section_text),
                        concept_call=concept_x.extract(paper.title, section_text),
                        model_call=model_x.extract(paper.title, section_text),
                        method_call=method_x.extract(paper.title, section_text),
                        paper_doi=paper.doi,
                    )
                else:
                    extraction_result = PaperExtractionResult(problems=problems)

                # E-7 cross-entity normalization.
                norm_result = None
                if (
                    extract_entities
                    and normalize_cross_entity_collisions
                    and embedder is not None
                ):
                    norm_result = await normalize_cross_entity(
                        extraction_result,
                        paper_title=paper.title,
                        embedder=embedder,
                        llm_client=llm_client,
                    )
                    if norm_result and not norm_result.is_clean:
                        result.papers_with_normalization_audit += 1
                    _notify(on_progress, "normalized", paper.doi, {
                        "pairs_detected": norm_result.pairs_detected if norm_result else 0,
                        "pairs_resolved": norm_result.pairs_resolved if norm_result else 0,
                        "pairs_rejected": norm_result.pairs_rejected if norm_result else 0,
                    })

                # V1: mention/concept routing (existing).
                v1_integration = None
                if problems:
                    v1_integration = v1_integrator.integrate_extracted_problems(
                        extracted_problems=problems,
                        paper_doi=paper.doi,
                        paper_title=paper.title,
                        session_trace_id=trace_id,
                    )
                    result.total_problems += len(problems)
                    result.concepts_created += v1_integration.mentions_new_concepts
                    result.concepts_linked += v1_integration.mentions_linked

                # V2: entity integration (Q3 gate).
                any_v2_extractions = extract_entities and bool(
                    extraction_result.topics
                    or extraction_result.concepts
                    or extraction_result.models
                    or extraction_result.methods
                )
                if (any_v2_extractions or v1_integration) and paper.doi:
                    mentions = (
                        [m for m in v1_integration.mentions if m.concept_id]
                        if v1_integration else []
                    )
                    v2_integration = integrate_paper_entities(
                        paper_doi=paper.doi,
                        extraction_result=extraction_result,
                        mentions=mentions,
                        taxonomy_hash=taxonomy_hash,
                        repo=repo,
                        normalization_result=norm_result,
                    )
                    result.topics_linked     += v2_integration.topics_assigned
                    result.concepts_v2_linked += v2_integration.concepts_linked
                    result.models_linked     += v2_integration.models_linked
                    result.methods_linked    += v2_integration.methods_linked
                    if v2_integration.paper_marked_incomplete:
                        result.papers_marked_incomplete += 1
                    _notify(on_progress, "entity_integrated", paper.doi, {
                        "topics": v2_integration.topics_assigned,
                        "concepts_v2": v2_integration.concepts_linked,
                        "models": v2_integration.models_linked,
                        "methods": v2_integration.methods_linked,
                    })

                result.papers_extracted += 1

            except Exception as e:
                logger.warning(
                    f"[{trace_id}] Per-paper failure {paper.doi}: {e}",
                )
                result.extraction_errors[paper.doi or paper.title] = str(e)
                continue

        # ---- Phase 4: Sanity checks (unchanged) ----
        result.sanity_checks = run_sanity_checks()
        result.status = "completed"

    except Exception as e:
        logger.error(f"[{trace_id}] Ingestion failed: {e}", exc_info=True)
        result.status = "failed"

    return result


# Inline helper used by extract_all_entities.
async def _returns(value):
    return value
```

## Edge Cases & Error Handling

### Paper has no PDF AND no abstract
- **Scenario**: Metadata paper with `pdf_url=None` and `abstract=None` (rare; arXiv-only ID with missing metadata).
- **Behavior**: `section_text = ""`. All four entity extractors short-circuit to `[]` via their existing empty-input guard. Normalizer sees zero pairs (`is_clean=True`). V2 integration's Q3 trigger is False (no extractions, no problems). Paper persists with metadata + (possibly empty) citation graph. No errors logged.
- **Test**: Build a NormalizedPaper with no pdf_url and no abstract; call `ingest_papers` with mocked extractors; assert zero LLM calls, paper count = 1.

### Paper has PDF but PDF processing fails
- **Scenario**: `pipeline.process_pdf_url` raises (timeout, malformed PDF, S3 404).
- **Behavior**: The existing inner `try/except` catches; `section_text = ""`, `problems = []`. The 4 entity extractors still run against the (empty) text — they short-circuit to `[]`. Paper records `extraction_errors[doi] = "PDF processing failed: ..."` but otherwise proceeds.
- **Test**: Patch `pipeline.process_pdf_url` to raise; assert per-paper error recorded, V1 + V2 integration both skipped for that paper, batch continues to next paper.

### `extract_entities=False` AND paper has no PDF
- **Scenario**: Operator running metadata + citation refresh on the abstract-less paper above.
- **Behavior**: No entity extraction at all; `extraction_result = PaperExtractionResult(problems=[])`. V1 integration skipped (no problems). V2 integration skipped (no extractions). Paper exists in graph from Phase 1 import only. Clean no-op.
- **Test**: `ingest_papers(extract_entities=False)` on a PDF-less, abstract-less paper; assert no LLM calls; no `Paper.normalization_audit`; no `Paper.taxonomy_hash`.

### One extractor of the five fails (E-8 V1 contract)
- **Scenario**: `model_x.extract` raises `LLMError` after retries. The 4 sibling extractors succeed.
- **Behavior**: `_run` inside `extract_all_entities` absorbs the failure, records `ExtractionFailure(extractor="model")`, returns empty list for models. V2 integration runs with `extraction_result.failures = [ExtractionFailure(extractor="model")]`. `integrate_paper_entities` writes `Paper.extraction_incomplete = true` and `Paper.extraction_failed_extractors = "model"`. The Topic/Concept/Method edges still land.
- **Test**: Patch `ModelExtractor.extract` to raise; assert other 3 extractions land, Paper carries `extraction_incomplete=true`, `papers_marked_incomplete` counter increments.

### Cross-entity normalization fails for one paper
- **Scenario**: `normalize_cross_entity` itself raises (not an inner LLM call — the normalizer's own logic bugs out).
- **Behavior**: The per-paper outer `try/except` catches; `extraction_errors[doi]` records the failure. V2 integration is skipped for that paper because the exception interrupted the body. Batch continues. The paper's extraction work is lost for that ingestion run; re-ingestion via the AC-13 purge path is the recovery.
- **Test**: Patch `normalize_cross_entity` to raise `RuntimeError`; assert per-paper error recorded, V1 integration still ran (it came before the normalize call OR was already done — needs spec to settle ordering).

### V1 integration fails for one paper
- **Scenario**: `KGIntegratorV2.integrate_extracted_problems` raises (Neo4j down).
- **Behavior**: Per-paper outer try/except catches; `extraction_errors[doi] = "Integration failed: ..."`. V2 integration is skipped (sequencing constraint: V2 needs V1's mentions). Batch continues.
- **Test**: Patch V1 integrator to raise; assert per-paper error recorded, V2 skipped for that paper, batch continues.

### V2 integration fails for one paper
- **Scenario**: `integrate_paper_entities` raises (Cypher syntax error, repo bug).
- **Behavior**: Outer try/except catches. V1 work already committed. `extraction_errors[doi]` records the V2 failure. Operator can re-ingest the paper via AC-13 to retry V2.
- **Test**: Patch `integrate_paper_entities` to raise; assert V1 succeeded, V2 error recorded, batch continues.

### `extract_entities=True` but `paper.doi is None`
- **Scenario**: A paper with extraction-worthy text but no DOI (edge case in some sources).
- **Behavior**: `integrate_paper_entities` requires `paper_doi` (Paper match-key). The orchestrator guards with `if (any_v2_extractions or v1_integration) and paper.doi`. Without DOI, V2 integration is skipped silently; the extractions are lost for graph persistence. WARN log includes the paper title.
- **Test**: NormalizedPaper with `doi=None` and extractable abstract; assert V2 integration NOT called, WARN logged with the title.

### `extract_entities=True` AND `normalize_cross_entity_collisions=False`
- **Scenario**: Operator wants extraction but no normalization (cost control or prompt debugging).
- **Behavior**: Embedder is NOT constructed. Normalize call is skipped. `norm_result = None` passes through to `integrate_paper_entities`, which already handles None per E-7 AC-17 (no audit written, clean Paper.normalization_audit). Both Concept "attention" and Method "attention" land as separate edges from the same paper.
- **Test**: With `normalize_cross_entity_collisions=False` and a paper whose Concept + Method extractors both emit "attention", assert both edges land in graph, `Paper.normalization_audit IS NULL`.

### Taxonomy file changes mid-batch (E-8 V1 detectability)
- **Scenario**: Operator edits `seed_taxonomy.yml` while `ingest_papers` is mid-flight.
- **Behavior**: `TopicExtractor` snapshots at instance construction (per-batch). The mid-flight batch uses the original snapshot for all papers. `Paper.taxonomy_hash` records the original snapshot's hash. The next `ingest_papers` invocation picks up the new taxonomy and a new hash. Operators can query `MATCH (p:Paper) WHERE p.taxonomy_hash <> $current_hash` to find papers ingested under stale taxonomies.
- **Test**: Per E-8 V1's taxonomy snapshot test; this feature inherits the behavior unchanged. Add a sanity assertion that `taxonomy_hash` lands on every successfully-integrated Paper.

## Acceptance Criteria

### AC-1: New CLI flags
- **Given** the `agentic-kg ingest` CLI
- **When** `--no-extract-entities` is passed
- **Then** `args.extract_entities is False`
- **And** when `--no-normalize-cross-entity` is passed, `args.normalize_cross_entity_collisions is False`
- **And** when both flags are omitted, both default `True`

### AC-2: New env vars
- **Given** the Cloud Run Job entrypoint
- **When** `EXTRACT_ENTITIES=false` env var is set, `config["extract_entities"] is False`
- **And** when `NORMALIZE_CROSS_ENTITY=false`, `config["normalize_cross_entity_collisions"] is False`
- **And** when either env var is unset or any non-`false` value (case-insensitive), the corresponding default `True` holds

### AC-3: Per-batch shared deps construct once
- **Given** `extract_entities=True` and N papers in the batch
- **When** `ingest_papers` runs
- **Then** `TopicExtractor.__init__` is called exactly once (taxonomy snapshot is per-batch, matching E-8 V1)
- **And** `ConceptExtractor.__init__`, `ModelExtractor.__init__`, `MethodExtractor.__init__` are each called once
- **And** `EmbeddingService.__init__` is called at most once (only when `normalize_cross_entity_collisions=True`)

### AC-4: All four extractors run on every paper
- **Given** `extract_entities=True` and a paper with `section_text` non-empty
- **When** the per-paper loop processes this paper
- **Then** `extract_all_entities` is called with all five `*_call` awaitables (problem, topic, concept, model, method)
- **And** the four entity extractors (`topic_x.extract`, `concept_x.extract`, `model_x.extract`, `method_x.extract`) are invoked

### AC-5: PDF-less papers fall back to abstract
- **Given** a paper with `pdf_url=None` and a non-empty `abstract`
- **When** `ingest_papers` processes the paper with `extract_entities=True`
- **Then** `section_text` is sourced from `paper.abstract`
- **And** the 4 entity extractors are called with this abstract-only text
- **And** `problems = []` (no V1 problem extraction without PDF)

### AC-6: Empty section text — clean short-circuit
- **Given** a paper with `pdf_url=None` and `abstract=None`
- **When** `ingest_papers` processes it with `extract_entities=True`
- **Then** the 4 extractors receive empty `section_text`
- **And** each returns `[]` via its existing empty-input guard
- **And** no LLM calls happen for this paper's entity extraction
- **And** no V2 integration runs (Q3 trigger evaluates False)

### AC-7: V1 problem integration unchanged
- **Given** a paper with successful PDF problem extraction
- **When** the per-paper loop processes it
- **Then** `v1_integrator.integrate_extracted_problems` is called with the same kwargs as today (`extracted_problems`, `paper_doi`, `paper_title`, `session_trace_id`)
- **And** `result.total_problems`, `result.concepts_created`, `result.concepts_linked` increment as before

### AC-8: V2 entity integration runs after V1 mention path
- **Given** a paper with both Problem and Topic+Concept+Model+Method extractions
- **When** the per-paper loop processes it
- **Then** `integrate_paper_entities` is called AFTER `integrate_extracted_problems`
- **And** the `mentions` kwarg to `integrate_paper_entities` is the filtered list of V1 mentions with `concept_id` populated

### AC-9: V2 integration runs without V1 when only entity extractions land
- **Given** a paper with `problems=[]` AND non-empty Topic/Concept/Model/Method extractions
- **When** the per-paper loop processes it
- **Then** `v1_integrator.integrate_extracted_problems` is NOT called
- **And** `integrate_paper_entities` IS called with `mentions=[]`

### AC-10: V2 integration skipped when extractions empty AND V1 didn't run
- **Given** a paper with `problems=[]` AND all 4 entity extractions empty
- **When** the per-paper loop processes it
- **Then** `integrate_paper_entities` is NOT called
- **And** the paper persists from Phase 1 metadata import only

### AC-11: Cross-entity normalization wired
- **Given** `extract_entities=True` AND `normalize_cross_entity_collisions=True` AND a paper with cross-entity collision
- **When** the per-paper loop processes it
- **Then** `normalize_cross_entity` is called with the live `extraction_result`, paper title, injected embedder, and injected LLM client
- **And** the returned `NormalizationResult` is passed to `integrate_paper_entities` as `normalization_result`
- **And** `result.papers_with_normalization_audit` increments when `norm_result.is_clean is False`

### AC-12: Normalization skipped when flag off
- **Given** `extract_entities=True` AND `normalize_cross_entity_collisions=False`
- **When** the per-paper loop processes a paper with a potential collision
- **Then** `normalize_cross_entity` is NOT called
- **And** `EmbeddingService.__init__` is NOT called for the batch
- **And** `integrate_paper_entities` is called with `normalization_result=None`
- **And** the resulting Paper node carries no `normalization_audit` property

### AC-13: extract_entities=False short-circuits the entire extractor path
- **Given** `extract_entities=False`
- **When** the per-paper loop processes any paper
- **Then** no `TopicExtractor`, `ConceptExtractor`, `ModelExtractor`, or `MethodExtractor` is constructed for the batch
- **And** `extract_all_entities` is NOT called for any paper
- **And** `normalize_cross_entity` is NOT called for any paper
- **And** V1 problem extraction + V1 mention integration STILL run on papers with PDFs (the extract_entities flag governs only the new entity extractors)

### AC-14: Per-paper failure isolation
- **Given** an extractor or integrator raises `Exception` mid-processing for paper P
- **When** the batch continues
- **Then** `result.extraction_errors[P.doi or P.title]` carries the exception's str
- **And** the next paper in `search.papers` is processed normally
- **And** the batch's overall `result.status` remains "completed" unless every paper failed

### AC-15: extraction_incomplete propagates to Paper node
- **Given** a paper where one extractor (say ModelExtractor) raised during `extract_all_entities`
- **When** `integrate_paper_entities` runs for this paper
- **Then** `Paper.extraction_incomplete = true` on the Neo4j node
- **And** `Paper.extraction_failed_extractors` contains "model"
- **And** `result.papers_marked_incomplete` increments

### AC-16: taxonomy_hash on every successfully integrated Paper
- **Given** a paper that completed V2 integration
- **When** the Paper node is inspected
- **Then** `Paper.taxonomy_hash` is the per-batch taxonomy snapshot hash
- **And** the value is consistent across all papers in the same batch
- **And** when re-ingestion via AC-13 purge happens in a new batch, the new hash is written

### AC-17: V2 integration counters surface in IngestionResult
- **Given** a successful batch with N V2 integrations
- **When** `IngestionResult` is inspected
- **Then** `result.topics_linked`, `result.concepts_v2_linked`, `result.models_linked`, `result.methods_linked` reflect the sum across the batch
- **And** these counters are distinct from V1's `result.concepts_created` / `result.concepts_linked`

### AC-18: Progress callbacks emit normalized + entity_integrated phases
- **Given** an `on_progress` callback registered
- **When** the loop processes a paper with extraction enabled
- **Then** the callback is invoked with `phase="normalized"` carrying `{pairs_detected, pairs_resolved, pairs_rejected}`
- **And** with `phase="entity_integrated"` carrying `{topics, concepts_v2, models, methods}`
- **And** when `normalize_cross_entity_collisions=False`, the `normalized` callback is NOT emitted (only `entity_integrated`)

### AC-19: No regression in existing tests
- **Given** the full pre-orchestration test suite
- **When** this feature is merged
- **Then** all existing `ingest_papers`, `extract_all_entities`, `normalize_cross_entity`, `integrate_paper_entities` tests pass with at most the additive counter changes (and any tests that asserted exact `batch_import` kwargs may need the `populate_citations=True` style fix that E-8 V2 introduced; same shape)

### AC-20: Acquired LLM client is the same across the batch (cost guard)
- **Given** `extract_entities=True` AND N papers in the batch
- **When** the loop runs
- **Then** `get_openai_client` is called at most once (singleton)
- **And** the same client instance is passed to all 4 extractors AND the normalizer
- **And** this contract is documented as the L-1 swap point — replacing `get_openai_client` with the SLM factory swaps the client across the entire pipeline without code change

### AC-21: Skip check on previously-complete papers (QA Q3)
- **Given** a Paper node P with `taxonomy_hash` equal to the current batch's hash AND `extraction_incomplete IS NOT true`
- **When** `ingest_papers` processes P with `extract_entities=True` AND `force_reextract=False`
- **Then** `pipeline.process_pdf_url` is NOT called for P (no V1 problem extraction)
- **And** `extract_all_entities` is NOT called for P (no V2 entity extraction)
- **And** `normalize_cross_entity` is NOT called for P
- **And** `KGIntegratorV2.integrate_extracted_problems` is NOT called for P
- **And** `integrate_paper_entities` is NOT called for P
- **And** Phase 1 metadata import + `populate_citations` STILL run for P
- **And** `result.papers_skipped_complete += 1` (new counter)

### AC-22: `--force-reextract` bypasses the skip check
- **Given** a Paper node P that would otherwise satisfy AC-21's skip conditions
- **When** `ingest_papers` runs with `force_reextract=True`
- **Then** the skip check is bypassed
- **And** the full extraction pipeline runs for P (V1 + V2 + normalization, subject to the other flags)
- **And** `result.papers_skipped_complete` does NOT increment for P

### AC-23: AC-13 purge naturally enables re-extraction
- **Given** a Paper P that was previously extracted (Paper.taxonomy_hash set)
- **When** AC-13's `purge_paper_extraction` runs against P
- **Then** P's `taxonomy_hash` is cleared (set to empty string by `_set_paper_extraction_metadata` zero-state)
- **And** the next `ingest_papers` run sees `existing.taxonomy_hash == ""` ≠ current_hash
- **And** the skip check fails for P → P is re-extracted in the next batch
- **And** this contract holds without any code change in the purge path (skip check just composes with existing zero-state semantics)

### AC-24: Default-true rollout is loud
- **Given** an operator deploys this feature without explicit flag overrides
- **When** the first ingest runs post-deploy
- **Then** the existing behavior changes: ~5-6 extra LLM calls per paper compared to V1 baseline
- **And** the implementation report flags this as a BREAKING CHANGE for production callers
- **And** the runbook / release notes explicitly document `EXTRACT_ENTITIES=false` as the env-var opt-out
- **And** a smoke test confirms a 1-paper batch with default flags produces all expected V2 audit fields on the Paper node

### AC-25: Test strategy — mocked integration tests cover the loop combinatorics (QA Q1)
- **Given** `packages/core/tests/test_ingestion_v2_orchestration.py` (new file)
- **When** the test suite runs
- **Then** the file contains at least 10-12 tests covering:
  - Happy path: all flags default ON, 1 paper with successful extraction, all extractors + normalizer + integrators called in the documented order.
  - Flag combo: `extract_entities=False` → skips entity extractors + normalizer + V2 integrator; V1 still runs on PDF papers.
  - Flag combo: `normalize_cross_entity_collisions=False` → extractors run, normalizer skipped, V2 integrator called with `normalization_result=None`.
  - Flag combo: both off → V1-only baseline.
  - Skip check: previously-complete paper skipped end-to-end; `papers_skipped_complete` increments.
  - Skip check: `force_reextract=True` bypasses skip.
  - Skip check: `extraction_incomplete=true` paper does NOT skip (forces re-extraction).
  - Error injection: V1 integrator raises → V2 skipped, paper error recorded, batch continues.
  - Error injection: extractor failure inside `extract_all_entities` → `extraction_incomplete=true` lands on Paper.
  - PDF-less paper → abstract fallback runs all 4 extractors against the abstract.
  - PDF-less, abstract-less paper → all extractors short-circuit; no LLM call; no V2 integration; paper persists from Phase 1.
  - DOI-less paper with extractable abstract → V2 integration SKIPPED (no Paper match-key); WARN logged.
- **And** each test mocks `extract_all_entities`, `normalize_cross_entity`, `KGIntegratorV2.integrate_extracted_problems`, `integrate_paper_entities`, and the repo — no testcontainers required, no real LLM calls.
- **And** the test file follows the existing `test_ingestion.py` mocking style.

## Technical Notes

- **Affected files:**
  - Modify: `packages/core/src/agentic_kg/ingestion.py` (the orchestration refactor — the entire Phase 2/3 body), `packages/core/src/agentic_kg/cli.py` (two new flags on `ingest`), `packages/core/src/agentic_kg/job_runner.py` (two new env vars in `_parse_env` + forwarded in `main`)
  - Create: `packages/core/tests/test_ingestion_v2_orchestration.py` (per-paper loop unit tests with mocked extractors / integrators)
  - Touch (verify, don't modify): `extract_all_entities`, `normalize_cross_entity`, `integrate_paper_entities`, `KGIntegratorV2.integrate_extracted_problems`, `PaperImporter.batch_import` — all are reused as-is
- **Reuse:** `extract_all_entities` (E-8 V1+V2), `normalize_cross_entity` (E-7), `integrate_paper_entities` (E-8 V1+V2), `KGIntegratorV2.integrate_extracted_problems` (V1), `PaperImporter.batch_import` with `populate_citations` (E-8 V2 wired)
- **No new dependencies.** Everything reuses what's already in `pyproject.toml`.
- **No new repository methods.** All graph writes go through methods that already exist.
- **CLI default-on philosophy.** Both new flags follow the `--no-populate-citations` / `--no-agent-workflow` precedent: default-on, single opt-out per concern.
- **L-1 swap point (locked).** `get_openai_client()` is called ONCE per batch and the same instance threads into all 4 extractors + the normalizer. When L-1 lands, swapping `get_openai_client()` for `get_local_slm_client()` flips the entire pipeline. AC-20 documents this contract.
- **Async / sync boundary preserved.** `extract_all_entities` async, `normalize_cross_entity` async, `integrate_paper_entities` sync, `KGIntegratorV2.integrate_extracted_problems` sync. The per-paper body is async (matching `ingest_papers`'s signature) and awaits both async calls before invoking the sync integrators. No changes to any of these signatures.

## Dependencies

- **E-1 (VERIFIED)** — Topic entities + taxonomy loader + assign_entity_to_topic.
- **E-2 (VERIFIED)** — ResearchConcept + create_or_merge + link_paper_to_concept.
- **E-3 (VERIFIED)** — Model entities + create_or_merge + link_paper_to_model.
- **E-4 (VERIFIED)** — Method entities + create_or_merge + link_paper_to_method.
- **E-5 (VERIFIED)** — Citation graph + populate_citations (wired in E-8 V2).
- **E-6 (VERIFIED)** — Description-gen + `feedback_llm_self_validation` pattern (used by E-7 normalizer).
- **E-7 (VERIFIED)** — `normalize_cross_entity`, `DisambiguationDecision`, `NormalizationResult`, audit JSON on Paper.
- **E-8 V1 (VERIFIED)** — `extract_all_entities`, `TopicExtractor`, `ConceptExtractor`, `_run` failure isolation, `taxonomy_hash`, `integrate_paper_entities`, `extraction_incomplete` semantics.
- **E-8 V2 (VERIFIED)** — `ModelExtractor`, `MethodExtractor`, `populate_citations` wired into importer, `--no-populate-citations` CLI flag (precedent for the two new flags).
- **No new external dependencies.**

## Open Questions

- **Across-paper parallelism.** The loop is sequential per-paper. `asyncio.gather` across papers would be a 5-10x throughput win on rate-limit-headroom but adds ordering and rate-limit-tracking complexity. Deferred; tracked as a possible follow-up.
- **Cost telemetry.** Per-batch LLM-call count + token usage aren't surfaced in `IngestionResult`. Operators today rely on OpenAI dashboard. A `result.llm_calls_made` counter would help close-the-loop on the L-1 cost-economics story. Deferred.
- **Mid-batch flag toggling.** Operators currently can't pause and tune `--no-normalize-cross-entity` mid-batch. Not on roadmap; raise if needed.
- **Calibration step for the full orchestrated pipeline.** E-7 AC-21 and E-8 V2 AC-17 both deferred their fixture-set calibration. With orchestration landing, the natural follow-up is a single "ingest 5 known-good papers through the full pipeline" calibration that exercises all extractors + normalization + integration together. Tracked as a follow-up, NOT part of this spec.

## Review Record

Interview decisions:

- **Q1 — Extractor scope.** Decision: **option (c)** — all four entity extractors run on every paper; PDF-less papers fall back to title + abstract. Q1's options (a) (PDF-only) and (b) (Topic-everywhere, others PDF-only) rejected for sacrificing graph density on metadata-only papers, which are common in arXiv and OpenAlex search results.
- **Q2 — Opt-out flags.** Decision: **option (a)** — two independent flags (`--no-extract-entities` and `--no-normalize-cross-entity`). Rationale: separates the high-cost extractor budget from the variable-cost normalizer budget; matches the existing `--no-populate-citations` precedent; finer-grained CLI surface would be premature.
- **Q3 — V2 integration trigger.** Decision: **option (a)** — run V2 entity integration whenever ANY successful extraction landed. Empty mentions list when V1 problem extraction yielded nothing; B3 linker simply finds no edges. Preserves graph density on entity-rich, problem-empty papers.

Dual-persona review (3 Tech Lead + 3 QA):

- **TL Q1 — V1/V2 sequencing fragility.** Decision: **option (a)** — accept the coupling; V1 failure skips V2. Current draft stands. Rationale: V1 Neo4j failures are rare; most failures take down both integrators; partial-state graphs (V2 succeeded, V1 failed) would add audit complexity that this loop-closure feature shouldn't bear. The AC-13 purge-then-rewrite path is the recovery model. Decoupling (option b) was rejected as scope creep with marginal real-world benefit; inverting order (option c) would break the V2 B3 linker entirely (mentions wouldn't exist when V2 runs).

- **QA Q1 — Test surface complexity.** Decision: **option (a)** — mocked integration tests with flag combos + per-stage error injection. AC-25 codifies the test plan: ~10-12 tests in a new `test_ingestion_v2_orchestration.py`, no testcontainers. Testcontainers integration (option b) was deferred as a follow-up; the loop's correctness can be pinned via mocks alone for V1, with Cypher integration coverage already in the V2/E-7 unit tests for each individual integrator.

- **TL Q2 — Cost telemetry visibility.** Decision: **option (a)** — defer. The spec stays scoped to loop-closure. Per-batch LLM-call counters are useful but require their own design (per-extractor breakdown, token-vs-request, surface in `IngestionResult`, CLI display, JSON shape). Open Questions section tracks it as a natural follow-up once real-data shakedown shows what operators want to see.

- **QA Q2 — CLI log verbosity.** Decision: **option (a)** — always emit `normalized` and `entity_integrated` per paper at INFO. Symmetric with existing phases (`extracted`, `integrated`); operators can pipe-filter for noise reduction; JSON output stays usable. Adding a `--verbose` toggle was rejected as premature CLI surface.

- **TL Q3 — Default-on rollout risk.** Decision: **option (a)** — `extract_entities=True` and `normalize_cross_entity_collisions=True` as defaults. AC-24 added: implementation report MUST flag this as a BREAKING CHANGE; release notes / runbook MUST document the env-var opt-out. Default-off (option b) was rejected because it leaves the loop dormant indefinitely (E-6's `generate_description=False` default never got flipped); asymmetric defaults (option c) were rejected as inconsistent semantics. For this feature, the cost IS the point: orchestration is on by default.

- **QA Q3 — Re-ingest idempotency cost.** Decision: **option (c)** — add a per-paper skip check based on `Paper.taxonomy_hash + extraction_incomplete`, with `--force-reextract` (and `FORCE_REEXTRACT` env var) as the explicit override. AC-21 / AC-22 / AC-23 codify the contract. The skip naturally composes with AC-13 purge (which clears `taxonomy_hash`, forcing re-extraction post-purge). This is the most consequential review-driven spec change — adds a real cost guard for the common case of repeatedly running the same query.

**Spec-correctness fix during review:** the original sample-implementation pseudocode assumed `PaperProcessingResult.section_text` exposed the segmented text directly. Verified during QA Q3 prep that `PaperProcessingResult` exposes `segmented_document: SegmentedDocument` instead. Added `_build_extractor_section_text(seg)` helper that joins the abstract + intro + methodology + experiments sections from the SegmentedDocument; the Sample Implementation and Edge Cases sections were updated accordingly. Caught during draft review; no AC impact (the contract is "section_text comes from PDF segmentation"; the helper is the implementation detail).
