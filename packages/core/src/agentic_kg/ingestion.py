"""
End-to-end paper ingestion: search → import → extract → integrate.

Orchestrates existing components (PaperAggregator, PaperImporter,
PaperProcessingPipeline, KGIntegratorV2) into a single workflow
for populating the knowledge graph from a search query.
"""

import logging
import uuid
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from agentic_kg.data_acquisition.aggregator import get_paper_aggregator
from agentic_kg.data_acquisition.importer import get_paper_importer
from agentic_kg.extraction.pipeline import get_pipeline
from agentic_kg.extraction.kg_integration_v2 import KGIntegratorV2
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
    papers_skipped_no_pdf: int = Field(0, description="Papers skipped (no PDF URL)")
    extraction_errors: dict[str, str] = Field(
        default_factory=dict, description="DOI → error message for failed extractions"
    )

    # Phase 3 counts
    total_problems: int = Field(0, description="Total problems extracted")
    concepts_created: int = Field(0, description="New ProblemConcepts created")
    concepts_linked: int = Field(0, description="Mentions linked to existing concepts")

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


async def ingest_papers(
    query: str,
    limit: int = 20,
    sources: Optional[list[str]] = None,
    dry_run: bool = False,
    enable_agent_workflow: bool = True,
    min_extraction_confidence: float = 0.5,
    on_progress: Optional[Callable[[str, Optional[str], Any], None]] = None,
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
        import_batch = await importer.batch_import(dois, create_authors=True)
        result.papers_imported = import_batch.created + import_batch.updated
        _notify(on_progress, "metadata_imported", None, result.papers_imported)

        # Phase 2: Extract problems from papers with PDFs
        pipeline = get_pipeline()
        papers_with_pdf = [p for p in search.papers if p.pdf_url and p.doi]
        result.papers_skipped_no_pdf = len(search.papers) - len(papers_with_pdf)

        extraction_results = []
        for paper in papers_with_pdf:
            try:
                proc = await pipeline.process_pdf_url(
                    url=paper.pdf_url,
                    paper_title=paper.title,
                    paper_doi=paper.doi,
                    authors=[a.name for a in paper.authors],
                )
                if proc.success and proc.problem_count > 0:
                    extraction_results.append((paper.doi, paper.title, proc))
                    _notify(on_progress, "extracted", paper.doi, proc.problem_count)
            except Exception as e:
                result.extraction_errors[paper.doi] = str(e)
                logger.warning(f"[{trace_id}] Extract failed {paper.doi}: {e}")

        result.papers_extracted = len(extraction_results)

        # Phase 3: Integrate into canonical architecture
        integrator = KGIntegratorV2(
            enable_agent_workflow=enable_agent_workflow,
            enable_concept_refinement=True,
        )
        for doi, title, proc in extraction_results:
            problems = proc.get_high_confidence_problems(min_extraction_confidence)
            if not problems:
                continue
            try:
                integration = integrator.integrate_extracted_problems(
                    extracted_problems=problems,
                    paper_doi=doi,
                    paper_title=title,
                    session_trace_id=trace_id,
                )
                result.total_problems += len(problems)
                result.concepts_created += integration.mentions_new_concepts
                result.concepts_linked += integration.mentions_linked
                _notify(on_progress, "integrated", doi, integration.mentions_created)
            except Exception as e:
                result.extraction_errors[doi] = f"Integration failed: {e}"
                logger.error(f"[{trace_id}] Integration failed {doi}: {e}")

        # Phase 4: Sanity checks
        result.sanity_checks = run_sanity_checks()
        result.status = "completed"

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
