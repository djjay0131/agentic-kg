"""
End-to-end paper ingestion: search → import → extract → integrate.

Orchestrates existing components (PaperAggregator, PaperImporter,
PaperProcessingPipeline, KGIntegratorV2) into a single workflow
for populating the knowledge graph from a search query.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from agentic_kg.data_acquisition.aggregator import get_paper_aggregator
from agentic_kg.data_acquisition.importer import get_paper_importer
from agentic_kg.extraction.cross_entity_normalizer import normalize_cross_entity
from agentic_kg.extraction.kg_integration_v2 import (
    KGIntegratorV2,
    integrate_paper_entities,
)
from agentic_kg.extraction.pipeline import (
    PaperExtractionResult,
    extract_all_entities,
    get_pipeline,
)
from agentic_kg.extraction.re_ingestion import PurgeBlocked, purge_paper_extraction
from agentic_kg.knowledge_graph.repository import get_repository

logger = logging.getLogger(__name__)


class SanityCheck(BaseModel):
    """Result of a single graph sanity check."""

    name: str = Field(..., description="Check identifier")
    passed: bool = Field(..., description="Whether the check passed")
    count: int = Field(0, description="Count of violations (0 = pass)")
    description: str = Field("", description="Human-readable description")


class IngestionResult(BaseModel):
    """Result of an end-to-end paper ingestion run."""

    trace_id: str = Field(..., description="Unique trace ID for this run")
    query: str = Field(..., description="Search query used")
    status: str = Field("pending", description="pending|running|completed|failed|dry_run")

    # Phase 1 counts
    papers_found: int = Field(0, description="Papers returned by search")
    papers_imported: int = Field(0, description="Papers imported to KG (created + updated)")

    # Phase 2 counts
    papers_extracted: int = Field(0, description="Papers with successful extraction")
    papers_skipped_no_pdf: int = Field(
        0,
        description=(
            "Papers with no acquirable full-text source (no candidate PDF URL "
            "and/or no DOI). SM-1 category: failed_no_pdf_source. No abstract "
            "fallback — these produce zero entities."
        ),
    )

    # SM-1: content-acquisition-resilience
    pdf_ok: int = Field(
        0, description="Papers where full text was acquired from a candidate PDF source"
    )
    acquisition_failures: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "SM-1 categorized full-text acquisition failures (had a source but "
            "could not extract usable text): reason -> count. Reasons: "
            "failed_blocked / failed_404 / failed_thin."
        ),
    )
    sources_rate_limited: int = Field(
        0,
        description=(
            "SM-1: number of search sources that returned a rate-limit (429) "
            "error and were dropped for this run (papers from them may be missing)."
        ),
    )
    search_errors: dict[str, str] = Field(
        default_factory=dict,
        description="SM-1: per-source search errors surfaced from the aggregator.",
    )
    papers_purged: int = Field(
        0, description="Papers whose existing extraction footprint was purged before re-extraction"
    )
    papers_blocked_by_guardrail: int = Field(
        0,
        description=(
            "Papers skipped because non-extraction edges block re-ingestion "
            "and --force-rewrite was not set (AC-13)"
        ),
    )
    extraction_errors: dict[str, str] = Field(
        default_factory=dict, description="DOI → error message for failed extractions"
    )

    # Phase 3 counts (V1 problem path)
    total_problems: int = Field(0, description="Total problems extracted")
    concepts_created: int = Field(0, description="New ProblemConcepts created")
    concepts_linked: int = Field(0, description="Mentions linked to existing concepts")

    # Phase 3 counts (V2 entity path — entity-pipeline-orchestration)
    topics_linked: int = Field(0, description="Paper-BELONGS_TO-Topic edges drawn")
    concepts_v2_linked: int = Field(
        0, description="Paper-DISCUSSES-ResearchConcept edges drawn (V2)"
    )
    models_linked: int = Field(0, description="Paper-USES_MODEL edges drawn")
    methods_linked: int = Field(0, description="Paper-APPLIES_METHOD edges drawn")
    papers_marked_incomplete: int = Field(
        0,
        description=(
            "Papers where one of the 5 extractors failed; "
            "Paper.extraction_incomplete=true on the node"
        ),
    )
    papers_with_normalization_audit: int = Field(
        0,
        description=(
            "Papers whose cross-entity normalizer detected at least one pair; "
            "Paper.normalization_audit is non-null on the node"
        ),
    )
    papers_skipped_complete: int = Field(
        0,
        description=(
            "Papers skipped because they were already extracted under the "
            "current taxonomy and extraction_incomplete=false (re-ingest "
            "cost guard, AC-21). Bypass via --force-reextract."
        ),
    )

    # Phase 4
    sanity_checks: list[SanityCheck] = Field(
        default_factory=list, description="Sanity check results"
    )

    # Dry run
    dry_run_papers: list[dict] = Field(
        default_factory=list, description="Papers found in dry run mode"
    )

    # Errors
    error: Optional[str] = Field(None, description="Fatal error message if failed")

    class Config:
        arbitrary_types_allowed = True


def _notify(
    callback: Optional[Callable],
    phase: str,
    paper_doi: Optional[str],
    detail: Any = None,
) -> None:
    """Send progress notification if callback is provided."""
    if callback:
        try:
            callback(phase, paper_doi, detail)
        except Exception:
            pass


def _paper_has_footprint(repo: Any, paper_doi: str) -> bool:
    """True if the paper already has at least one ProblemMention attached.

    A single-row check that gates the purge-then-rewrite path: if the
    paper exists as metadata-only (no extraction has run yet), the purge
    step is unnecessary and is skipped.
    """
    with repo.session() as session:
        row = session.run(
            """
            MATCH (m:ProblemMention)-[:EXTRACTED_FROM]->(p:Paper {doi: $doi})
            RETURN count(m) AS n
            """,
            doi=paper_doi,
        ).single()
    return bool(row and row["n"] > 0)


# Sections fed to the 4 V2 entity extractors. Order matters for prompt
# readability: abstract → intro → methods → experiments roughly matches
# how a human reads the paper. See entity-pipeline-orchestration spec
# AC-4/AC-5/AC-6 for the resolution contract.
_EXTRACTOR_WANTED_SECTIONS = (
    "abstract",
    "introduction",
    "methods",
    "experiments",
)


def _build_extractor_section_text(seg: Any) -> str:
    """Join the abstract + intro + methods + experiments content from a
    ``SegmentedDocument`` into a single text block for the entity
    extractors.

    Returns ``""`` when ``seg`` is None or contains none of the wanted
    sections. The per-extractor empty-input short-circuit then prevents
    a wasted LLM call. See spec edge case "Empty section text — clean
    short-circuit".
    """
    if seg is None or not getattr(seg, "sections", None):
        return ""
    parts: list[str] = []
    for section in seg.sections:
        section_type = getattr(section, "section_type", None)
        # section_type is a SectionType enum; its .value is the string.
        section_value = getattr(section_type, "value", None) or str(section_type or "")
        if section_value.lower() in _EXTRACTOR_WANTED_SECTIONS:
            content = getattr(section, "content", "") or ""
            if content.strip():
                parts.append(content.strip())
    return "\n\n".join(parts)


# SM-1: minimum extractor-input characters for a full-text acquisition to count
# as usable. Below this the source is treated as failed_thin and the next
# candidate is tried. A real paper body yields thousands of chars; this only
# rejects empty/scanned/garbage extractions.
MIN_USABLE_CHARS = 250


def _classify_pdf_failure(error_msg: str) -> str:
    """Map a PDF fetch/extract error string to an SM-1 failure reason.

    A permanent 404 (``failed_404``) is distinguished from a host that dropped
    or refused us (``failed_blocked``, the default) so run metrics can tell a
    systemic fetch problem from genuinely-missing PDFs. See spec AC-6 / QA #1.
    """
    if "404" in error_msg:
        return "failed_404"
    return "failed_blocked"


def _proc_error(proc: Any) -> str:
    """Return the first failed-stage error string on a PaperProcessingResult."""
    for stage in getattr(proc, "stages", None) or []:
        if not getattr(stage, "success", True) and getattr(stage, "error", None):
            return str(stage.error)
    return ""


@dataclass
class _AcquisitionOutcome:
    """Result of trying to acquire full text for one paper (SM-1)."""

    proc: Any  # successful PaperProcessingResult, or None
    section_text: str  # extractor input text ("" on failure)
    reason: Optional[str]  # None on success; else a failed_* reason


async def _acquire_full_text(
    pipeline: Any,
    paper: Any,
    min_chars: int = MIN_USABLE_CHARS,
) -> _AcquisitionOutcome:
    """SM-1: acquire full text by trying candidate PDF sources in order.

    Published/authoritative source first, arXiv fallback second (see
    ``NormalizedPaper.candidate_pdf_urls``). The first candidate that yields
    at least ``min_chars`` of extractor text wins. **There is no abstract
    fallback** — if every candidate fails, return a categorized failure reason
    and empty text so the caller can fail the paper loudly.

    The caller is expected to only invoke this for papers that have at least
    one candidate URL; an empty candidate list is reported upstream as
    ``failed_no_pdf_source``.
    """
    reason = "failed_blocked"
    authors = [a.name for a in getattr(paper, "authors", [])]
    for url in paper.candidate_pdf_urls():
        try:
            proc = await pipeline.process_pdf_url(
                url=url,
                paper_title=paper.title,
                paper_doi=paper.doi,
                authors=authors,
            )
        except Exception as e:
            reason = _classify_pdf_failure(str(e))
            logger.warning(f"PDF source raised ({url}): {e}")
            continue

        if proc is not None and getattr(proc, "success", False):
            text = _build_extractor_section_text(proc.segmented_document)
            if len(text.strip()) >= min_chars:
                logger.info(f"Full text acquired via {url} ({len(text)} chars)")
                return _AcquisitionOutcome(proc, text, None)
            reason = "failed_thin"
            logger.warning(f"PDF source too thin ({url}): {len(text)} chars")
        else:
            reason = _classify_pdf_failure(_proc_error(proc))
            logger.warning(f"PDF source failed ({url}): {_proc_error(proc)}")

    return _AcquisitionOutcome(None, "", reason)


def _can_skip_entity_extraction(
    repo: Any,
    paper_doi: Optional[str],
    current_taxonomy_hash: str,
) -> bool:
    """E-7-style cost guard (AC-21): True when the paper already has a
    complete extraction under the current taxonomy snapshot.

    Skip semantics:
      * Paper.taxonomy_hash equals the current batch's hash.
      * Paper.extraction_incomplete is NOT True (None or false counts as
        complete; the V2 integrator only sets it on real failure).

    The check fails (returns False) when:
      * The paper has no DOI (no graph match-key).
      * The paper doesn't exist in the graph yet (NotFoundError).
      * Any query failure (defensive — never let the cost guard crash
        the batch).
      * The taxonomy_hash mismatches (e.g., after AC-13 purge or after
        a taxonomy YAML edit).
      * extraction_incomplete is True (one of the 5 extractors failed
        last time; re-extraction is the recovery).

    Phase 1 metadata refresh + ``populate_citations`` still run for
    skipped papers; only the LLM-touching extraction body is bypassed.
    """
    if not paper_doi:
        return False
    if not current_taxonomy_hash:
        # No hash to compare against — caller is in extract_entities=False
        # mode, so there's nothing to skip.
        return False
    try:
        with repo.session() as session:
            row = session.run(
                """
                MATCH (p:Paper {doi: $doi})
                RETURN p.taxonomy_hash AS taxonomy_hash,
                       p.extraction_incomplete AS extraction_incomplete
                """,
                doi=paper_doi,
            ).single()
    except Exception:
        return False
    if row is None:
        return False
    stored_hash = row["taxonomy_hash"]
    incomplete = row["extraction_incomplete"]
    return stored_hash == current_taxonomy_hash and incomplete is not True


async def _returns(value: Any) -> Any:
    """Tiny awaitable wrapper used to satisfy ``extract_all_entities``'s
    five required ``*_call`` kwargs when one of the values is already
    materialized (e.g., problems extracted by `pipeline.process_pdf_url`).
    """
    return value


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
    extract_entities: bool = True,
    normalize_cross_entity_collisions: bool = True,
    force_reextract: bool = False,
) -> IngestionResult:
    """
    End-to-end paper ingestion: search → import → extract → integrate.

    Args:
        query: Search query for paper discovery.
        limit: Max papers to fetch.
        sources: API sources to search (default: all).
        dry_run: If True, search only — don't extract or integrate.
        enable_agent_workflow: Route MEDIUM/LOW matches through agents.
        min_extraction_confidence: Minimum problem confidence to integrate.
        on_progress: Callback(phase, paper_doi, detail) for progress.
        force_rewrite: If True, override the AC-13 guardrail so papers with
            non-extraction incident edges are still purged before
            re-extraction. Without this flag, such papers are skipped with
            a recorded error rather than crashing the run.
        populate_citations: E-8 V2 — when True, PaperImporter populates
            the citation graph during metadata import. Default True.
        extract_entities: entity-pipeline-orchestration — when True, the
            4 entity extractors (Topic / ResearchConcept / Model / Method)
            run alongside problem extraction and the V2 entity integrator
            writes the resulting nodes + edges. Default True. BREAKING
            CHANGE on deploy — see release notes.
        normalize_cross_entity_collisions: E-7 — when True AND extract_entities
            is True, the cross-entity routing LLM disambiguates collisions
            before V2 integration. Default True. Costs ~1 LLM call per
            ambiguous pair per paper.
        force_reextract: when True, bypass the per-paper skip check (AC-21)
            and re-run extraction on every paper regardless of taxonomy_hash
            match. Default False.

    Returns:
        IngestionResult with counts and sanity check results.
    """
    trace_id = f"ingest-{uuid.uuid4().hex[:8]}"
    result = IngestionResult(trace_id=trace_id, query=query, status="running")

    try:
        # Phase 1: Search across sources
        aggregator = get_paper_aggregator()
        search = await aggregator.search_papers(query, sources=sources, limit=limit)
        result.papers_found = len(search.papers)
        # SM-1: surface per-source search failures (e.g. Semantic Scholar 429)
        # so rate-limited sources are visible instead of silently dropped.
        result.search_errors = dict(getattr(search, "errors", {}) or {})
        result.sources_rate_limited = sum(
            1 for msg in result.search_errors.values()
            if "rate limit" in str(msg).lower() or "429" in str(msg)
        )
        _notify(on_progress, "search_complete", None, result.papers_found)

        if dry_run:
            result.status = "dry_run"
            result.dry_run_papers = [
                {
                    "doi": p.doi,
                    "title": p.title,
                    "pdf_url": p.pdf_url,
                }
                for p in search.papers
            ]
            return result

        # Phase 1b: Import metadata to KG
        importer = get_paper_importer()
        dois = [p.doi for p in search.papers if p.doi]
        import_batch = await importer.batch_import(
            dois,
            create_authors=True,
            populate_citations=populate_citations,
        )
        result.papers_imported = import_batch.created + import_batch.updated
        _notify(on_progress, "metadata_imported", None, result.papers_imported)

        # Phase 2/3 setup: shared per-batch dependencies.
        pipeline = get_pipeline()
        # SM-1: "no acquirable source" = no candidate PDF URL (published or
        # arXiv) or no DOI. Counted here as failed_no_pdf_source; no abstract
        # fallback is attempted for these.
        result.papers_skipped_no_pdf = sum(
            1 for p in search.papers if not (p.candidate_pdf_urls() and p.doi)
        )
        repo = get_repository()

        v1_integrator = KGIntegratorV2(
            enable_agent_workflow=enable_agent_workflow,
            enable_concept_refinement=True,
        )

        # V2 extractor / normalizer dependencies (constructed only when
        # extract_entities=True so V1-only callers pay zero import + init
        # cost).
        llm_client = None
        topic_x = concept_x = model_x = method_x = None
        embedder = None
        taxonomy_hash = ""
        if extract_entities:
            from agentic_kg.extraction.concept_extractor import ConceptExtractor
            from agentic_kg.extraction.llm_client import get_openai_client
            from agentic_kg.extraction.method_extractor import MethodExtractor
            from agentic_kg.extraction.model_extractor import ModelExtractor
            from agentic_kg.extraction.taxonomy_hash import (
                canonical_taxonomy_hash,
            )
            from agentic_kg.extraction.topic_extractor import TopicExtractor
            from agentic_kg.knowledge_graph.taxonomy import parse_taxonomy

            llm_client = get_openai_client()
            topic_x = TopicExtractor(client=llm_client)
            concept_x = ConceptExtractor(client=llm_client)
            model_x = ModelExtractor(client=llm_client)
            method_x = MethodExtractor(client=llm_client)
            taxonomy_hash = canonical_taxonomy_hash(
                parse_taxonomy(topic_x.taxonomy_path)
            )

            if normalize_cross_entity_collisions:
                from agentic_kg.knowledge_graph.embeddings import (
                    EmbeddingService,
                )
                embedder = EmbeddingService()

        # Phase 2/3: per-paper unified loop.
        for paper in search.papers:
            doi = paper.doi
            try:
                # --- AC-13 purge guardrail (unchanged from V1). ---
                if doi and _paper_has_footprint(repo, doi):
                    try:
                        purge_report = purge_paper_extraction(
                            repo, doi, force_rewrite=force_rewrite,
                        )
                        result.papers_purged += 1
                        _notify(
                            on_progress, "purged", doi,
                            {
                                "problems_deleted": purge_report.problems_deleted,
                                "mentions_deleted": purge_report.mentions_deleted,
                                "collateral": len(
                                    purge_report.collateral_edge_loss
                                ),
                            },
                        )
                    except PurgeBlocked as e:
                        result.papers_blocked_by_guardrail += 1
                        result.extraction_errors[doi] = str(e)
                        logger.warning(f"[{trace_id}] {e}")
                        continue

                # --- AC-21 skip check: re-ingest cost guard. ---
                if (
                    extract_entities
                    and not force_reextract
                    and _can_skip_entity_extraction(repo, doi, taxonomy_hash)
                ):
                    result.papers_skipped_complete += 1
                    _notify(
                        on_progress, "skipped_complete", doi,
                        {"reason": "taxonomy_hash matches; extraction complete"},
                    )
                    continue

                # --- SM-1: full-text acquisition (published source first,
                # arXiv fallback). NO abstract fallback: a paper with no
                # acquirable full text fails loudly and is skipped, categorized
                # by reason for run metrics. ---
                proc = None
                problems: list[Any] = []
                section_text = ""

                if not (doi and paper.candidate_pdf_urls()):
                    # No acquirable source / no DOI — already counted upfront as
                    # papers_skipped_no_pdf (failed_no_pdf_source). Fail loud.
                    _notify(on_progress, "acquisition_failed", doi, "failed_no_pdf_source")
                    continue

                outcome = await _acquire_full_text(pipeline, paper)
                if outcome.reason is not None:
                    result.acquisition_failures[outcome.reason] = (
                        result.acquisition_failures.get(outcome.reason, 0) + 1
                    )
                    result.extraction_errors[doi] = (
                        f"No usable full text ({outcome.reason})"
                    )
                    logger.warning(
                        f"[{trace_id}] Full-text acquisition failed "
                        f"{doi}: {outcome.reason}"
                    )
                    _notify(on_progress, "acquisition_failed", doi, outcome.reason)
                    continue

                proc = outcome.proc
                section_text = outcome.section_text
                result.pdf_ok += 1
                problems = proc.get_high_confidence_problems(
                    min_extraction_confidence,
                )
                if proc.problem_count > 0:
                    _notify(on_progress, "extracted", doi, proc.problem_count)

                # --- 5-way parallel extraction (E-8 V1 + V2). ---
                if extract_entities:
                    extraction_result = await extract_all_entities(
                        problem_call=_returns(problems),
                        topic_call=topic_x.extract(
                            paper.title, section_text,
                        ),
                        concept_call=concept_x.extract(
                            paper.title, section_text,
                        ),
                        model_call=model_x.extract(
                            paper.title, section_text,
                        ),
                        method_call=method_x.extract(
                            paper.title, section_text,
                        ),
                        paper_doi=doi,
                    )
                else:
                    extraction_result = PaperExtractionResult(
                        problems=problems,
                    )

                # --- E-7 cross-entity normalization. ---
                norm_result = None
                if (
                    extract_entities
                    and normalize_cross_entity_collisions
                    and embedder is not None
                    and llm_client is not None
                ):
                    norm_result = await normalize_cross_entity(
                        extraction_result,
                        paper_title=paper.title,
                        embedder=embedder,
                        llm_client=llm_client,
                    )
                    if norm_result and not norm_result.is_clean:
                        result.papers_with_normalization_audit += 1
                    _notify(
                        on_progress, "normalized", doi,
                        {
                            "pairs_detected": (
                                norm_result.pairs_detected
                                if norm_result else 0
                            ),
                            "pairs_resolved": (
                                norm_result.pairs_resolved
                                if norm_result else 0
                            ),
                            "pairs_rejected": (
                                norm_result.pairs_rejected
                                if norm_result else 0
                            ),
                        },
                    )

                # --- V1: ProblemMention/ProblemConcept routing. ---
                v1_integration = None
                if problems:
                    try:
                        v1_integration = (
                            v1_integrator.integrate_extracted_problems(
                                extracted_problems=problems,
                                paper_doi=doi,
                                paper_title=paper.title,
                                session_trace_id=trace_id,
                            )
                        )
                        result.total_problems += len(problems)
                        result.concepts_created += (
                            v1_integration.mentions_new_concepts
                        )
                        result.concepts_linked += (
                            v1_integration.mentions_linked
                        )
                        _notify(
                            on_progress, "integrated", doi,
                            v1_integration.mentions_created,
                        )
                    except Exception as e:
                        # TL Q1: V1 failure skips V2 (couples failure
                        # surface; AC-13 is the recovery path).
                        result.extraction_errors[doi] = (
                            f"V1 integration failed: {e}"
                        )
                        logger.error(
                            f"[{trace_id}] V1 integration failed {doi}: {e}"
                        )
                        continue

                # --- V2: Topic + Concept + Model + Method writers. ---
                any_v2_extractions = extract_entities and bool(
                    extraction_result.topics
                    or extraction_result.concepts
                    or extraction_result.models
                    or extraction_result.methods
                )
                if (any_v2_extractions or v1_integration is not None) and doi:
                    mentions = (
                        [
                            m for m in v1_integration.mentions
                            if m.concept_id
                        ]
                        if v1_integration else []
                    )
                    v2_integration = integrate_paper_entities(
                        paper_doi=doi,
                        extraction_result=extraction_result,
                        mentions=mentions,
                        taxonomy_hash=taxonomy_hash,
                        repo=repo,
                        normalization_result=norm_result,
                    )
                    result.topics_linked += v2_integration.topics_assigned
                    result.concepts_v2_linked += (
                        v2_integration.concepts_linked
                    )
                    result.models_linked += v2_integration.models_linked
                    result.methods_linked += v2_integration.methods_linked
                    if v2_integration.paper_marked_incomplete:
                        result.papers_marked_incomplete += 1
                    _notify(
                        on_progress, "entity_integrated", doi,
                        {
                            "topics": v2_integration.topics_assigned,
                            "concepts_v2": v2_integration.concepts_linked,
                            "models": v2_integration.models_linked,
                            "methods": v2_integration.methods_linked,
                        },
                    )

                # papers_extracted counts papers where ANY V1 problem
                # extraction landed (preserves V1 semantic per AC-19) OR
                # any V2 entity extraction landed.
                if (
                    (proc is not None and proc.success and proc.problem_count > 0)
                    or any_v2_extractions
                ):
                    result.papers_extracted += 1

            except Exception as e:
                # AC-14: per-paper failure isolation. Anything that
                # leaked past the inner try/except lands here so the
                # batch continues.
                key = doi or paper.title or f"paper#{id(paper)}"
                result.extraction_errors[key] = str(e)
                logger.warning(
                    f"[{trace_id}] Per-paper failure {key}: {e}"
                )
                continue

        # Phase 4: Sanity checks
        result.sanity_checks = run_sanity_checks()
        result.status = "completed"

        # SM-1: per-run coverage/failure summary (AC-6). Failures are broken
        # down by reason so a systemic fetch problem (failed_blocked) is
        # distinguishable at a glance from genuinely-missing PDFs
        # (failed_no_pdf_source) or throttling (sources_rate_limited).
        with_entities = result.pdf_ok
        pct = (100.0 * with_entities / result.papers_found) if result.papers_found else 0.0
        logger.info(
            "[%s] Ingest summary: papers=%d pdf_ok=%d (%.0f%%) "
            "failed_no_pdf_source=%d failed_blocked=%d failed_404=%d "
            "failed_thin=%d sources_rate_limited=%d",
            trace_id,
            result.papers_found,
            with_entities,
            pct,
            result.papers_skipped_no_pdf,
            result.acquisition_failures.get("failed_blocked", 0),
            result.acquisition_failures.get("failed_404", 0),
            result.acquisition_failures.get("failed_thin", 0),
            result.sources_rate_limited,
        )

    except Exception as e:
        logger.error(f"[{trace_id}] Ingestion failed: {e}", exc_info=True)
        result.status = "failed"
        result.error = str(e)

    return result


def run_sanity_checks(
    repository=None,
) -> list[SanityCheck]:
    """
    Run structural integrity checks against Neo4j.

    Args:
        repository: Neo4j repository. Uses global if not provided.

    Returns:
        List of SanityCheck results.
    """
    repo = repository or get_repository()
    checks = []

    try:
        with repo.session() as session:
            # Check 1: ProblemMentions with INSTANCE_OF (excluding PENDING review)
            result = session.run(
                "MATCH (m:ProblemMention) "
                "WHERE NOT (m)-[:INSTANCE_OF]->() "
                "AND m.review_status <> 'PENDING' "
                "RETURN count(m) as cnt"
            )
            orphan_mentions = result.single()["cnt"]
            checks.append(SanityCheck(
                name="mentions_have_instance_of",
                passed=orphan_mentions == 0,
                count=orphan_mentions,
                description="ProblemMentions without INSTANCE_OF (excl. pending review)",
            ))

            # Check 2: Every ProblemMention traces to a Paper
            result = session.run(
                "MATCH (m:ProblemMention) "
                "WHERE NOT (m)-[:EXTRACTED_FROM]->(:Paper) "
                "RETURN count(m) as cnt"
            )
            unlinked = result.single()["cnt"]
            checks.append(SanityCheck(
                name="mentions_linked_to_paper",
                passed=unlinked == 0,
                count=unlinked,
                description="ProblemMentions without EXTRACTED_FROM Paper",
            ))

            # Check 3: Every Paper has at least one Author
            # AC-14 exempt: structural check on AUTHORED_BY edge, not on
            # extracted entities (topics/concepts/problems). Partial-extraction
            # papers must still pass this check, so complete_papers_filter is
            # intentionally NOT applied.
            result = session.run(
                "MATCH (p:Paper) "
                "WHERE NOT (p)-[:AUTHORED_BY]->(:Author) "
                "RETURN count(p) as cnt"
            )
            authorless = result.single()["cnt"]
            checks.append(SanityCheck(
                name="papers_have_authors",
                passed=authorless == 0,
                count=authorless,
                description="Papers without any AUTHORED_BY edges",
            ))

            # Check 4: No orphan ProblemConcepts
            result = session.run(
                "MATCH (c:ProblemConcept) "
                "WHERE NOT ()-[:INSTANCE_OF]->(c) "
                "RETURN count(c) as cnt"
            )
            orphan_concepts = result.single()["cnt"]
            checks.append(SanityCheck(
                name="no_orphan_concepts",
                passed=orphan_concepts == 0,
                count=orphan_concepts,
                description="ProblemConcepts with no linked mentions",
            ))

            # Check 5: Graph population summary
            node_result = session.run("MATCH (n) RETURN count(n) as cnt")
            node_count = node_result.single()["cnt"]
            edge_result = session.run("MATCH ()-[r]->() RETURN count(r) as cnt")
            edge_count = edge_result.single()["cnt"]
            checks.append(SanityCheck(
                name="graph_populated",
                passed=node_count > 0 and edge_count > 0,
                count=node_count,
                description=f"{node_count} nodes, {edge_count} edges",
            ))

    except Exception as e:
        logger.error(f"Sanity check failed: {e}")
        checks.append(SanityCheck(
            name="connectivity",
            passed=False,
            count=0,
            description=f"Neo4j connection failed: {e}",
        ))

    return checks
