"""
Cloud Run Job entrypoint for paper ingestion.

Reads configuration from environment variables, runs the ingestion pipeline,
persists an IngestionRun node to Neo4j for provenance, and exits with
deliberate codes:
  0 = complete (all papers processed successfully)
  1 = partial (some extraction errors, but ingestion completed)
  2 = fatal (ingestion failed entirely or missing required config)
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

from agentic_kg.ingestion import ingest_papers
from agentic_kg.knowledge_graph.repository import get_repository

logger = logging.getLogger(__name__)


def _parse_env() -> dict:
    """Parse ingestion configuration from environment variables.

    Returns:
        Dict with parsed configuration values.

    Raises:
        SystemExit(2): If INGEST_QUERY is missing.
    """
    query = os.environ.get("INGEST_QUERY")
    if not query:
        logger.error("INGEST_QUERY env var is required")
        sys.exit(2)

    sources_raw = os.environ.get("INGEST_SOURCES", "")
    sources = [s.strip() for s in sources_raw.split(",") if s.strip()] or None

    return {
        "query": query,
        "trace_id": os.environ.get("INGEST_TRACE_ID", f"ingest-{os.urandom(4).hex()}"),
        "limit": int(os.environ.get("INGEST_LIMIT", "20")),
        "sources": sources,
        "enable_agent_workflow": os.environ.get(
            "INGEST_AGENT_WORKFLOW", "true"
        ).lower() == "true",
        "min_extraction_confidence": float(
            os.environ.get("INGEST_MIN_CONFIDENCE", "0.5")
        ),
        # E-8 V2: default True; only an explicit `false` (any casing)
        # disables citation graph population for this job.
        "populate_citations": os.environ.get(
            "POPULATE_CITATIONS", "true"
        ).lower() != "false",
        # entity-pipeline-orchestration: default True; only explicit
        # `false` (case-insensitive) disables the 4 entity extractors +
        # cross-entity normalizer for this job. BREAKING CHANGE on
        # deploy — see release notes.
        "extract_entities": os.environ.get(
            "EXTRACT_ENTITIES", "true"
        ).lower() != "false",
        "normalize_cross_entity_collisions": os.environ.get(
            "NORMALIZE_CROSS_ENTITY", "true",
        ).lower() != "false",
        "force_reextract": os.environ.get(
            "FORCE_REEXTRACT", "false",
        ).lower() == "true",
    }


def persist_ingestion_run(trace_id: str, query: str, result, started_at: datetime) -> bool:
    """Write IngestionRun node to Neo4j for provenance.

    Args:
        trace_id: Unique trace ID for this run.
        query: Search query used.
        result: IngestionResult from the pipeline.
        started_at: When the ingestion started.

    Returns:
        True if persisted successfully, False otherwise.
    """
    completed_at = datetime.now(timezone.utc)
    try:
        repo = get_repository()
        with repo.session() as session:
            session.run(
                "CREATE (r:IngestionRun) SET r = $props",
                props={
                    "trace_id": trace_id,
                    "query": query,
                    "status": result.status,
                    "papers_found": result.papers_found,
                    "papers_imported": result.papers_imported,
                    "papers_extracted": result.papers_extracted,
                    "papers_skipped_no_pdf": result.papers_skipped_no_pdf,
                    "total_problems": result.total_problems,
                    "concepts_created": result.concepts_created,
                    "concepts_linked": result.concepts_linked,
                    "extraction_errors": json.dumps(result.extraction_errors),
                    # I-58: citation observability survives into the
                    # IngestionRun audit node.
                    "citation_attempted": result.citation_population_attempted,
                    "citation_succeeded": result.citation_population_succeeded,
                    "citation_failed": result.citation_population_failed,
                    "citation_edges_created": result.citation_edges_created,
                    "citation_references_seen": result.citation_references_seen,
                    "citation_references_with_doi": (
                        result.citation_references_with_doi
                    ),
                    "citation_failures": json.dumps(result.citation_failures),
                    "citation_failure_details": json.dumps(
                        result.citation_failure_details
                    ),
                    "started_at": started_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                },
            )
        logger.info(f"IngestionRun node written: trace_id={trace_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to persist IngestionRun: {e}")
        return False


def _determine_exit_code(result) -> int:
    """Determine exit code based on ingestion result.

    Returns:
        0 for complete, 1 for partial (some errors), 2 for fatal failure.
    """
    if result.status == "failed":
        return 2
    # I-58: a run that could not measure citations at all is a partial
    # run, not a clean one. The Cloud Run Job has no second gate (no
    # smoke assertion downstream), so the exit code is the only signal.
    elif result.status == "completed_with_errors":
        return 1
    elif result.extraction_errors:
        return 1
    return 0


def main() -> None:
    """Cloud Run Job entrypoint.

    ``INGEST_MODE`` selects the path: ``legacy`` (the default, unchanged) runs
    the existing paper-ingestion pipeline; ``kgis_kgcs`` runs the opt-in
    KGIS -> KGCS -> canonical migration command and prints its JSON summary.
    """
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    mode = os.environ.get("INGEST_MODE", "legacy").strip().lower()
    if mode in ("kgis_kgcs", "kgis-kgcs"):
        sys.exit(run_kgis_kgcs_job())
    if mode not in ("legacy", ""):
        logger.error(
            "Unknown INGEST_MODE=%r; expected 'legacy' or 'kgis_kgcs'. No run "
            "was attempted.",
            mode,
        )
        sys.exit(2)
    run_legacy_job()


def _env_list(name: str) -> list[str]:
    """A comma-separated environment variable as a list of non-empty items."""
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def run_kgis_kgcs_job() -> int:
    """Run the opt-in migration command for a Cloud Run Job. Returns an exit code.

    Requires the ``migration`` extra and both opt-in flags; refuses loudly
    rather than silently running the legacy path.
    """
    from agentic_kg.migration.config import get_migration_config

    config = get_migration_config()
    if not (config.use_kgis_ingestion and config.use_kgcs_resolution):
        logger.error(
            "INGEST_MODE=kgis_kgcs requires KGIS_INGESTION_ENABLED=1 and "
            "KGCS_RESOLUTION_ENABLED=1. No run was attempted."
        )
        return 2

    try:
        from agentic_kg.migration.ingestion.pipeline import DEFAULT_RUN_ID
        from agentic_kg.migration.run import execute_migration
    except Exception as e:  # noqa: BLE001 - the failure is the actionable message
        logger.error(
            "could not import the migration pipeline (%s); the job image must "
            "install the extra: pip install './packages/core[migration]'",
            e,
        )
        return 2

    dois = _env_list("INGEST_DOIS")
    dois_file = os.environ.get("INGEST_DOIS_FILE") or None
    if dois_file and not dois:
        try:
            with open(dois_file, encoding="utf-8") as handle:
                dois = [
                    line.strip()
                    for line in handle
                    if line.strip() and not line.lstrip().startswith("#")
                ]
        except OSError as e:
            logger.error("cannot read INGEST_DOIS_FILE %r: %s", dois_file, e)
            return 2

    # `MIGRATION_SOURCE` (default `corpus`) selects the paper source. Live
    # discovery consumes `INGEST_QUERY` / `INGEST_LIMIT` / `INGEST_SOURCES`;
    # the corpus path ignores them and is unchanged.
    source = os.environ.get("MIGRATION_SOURCE") or None
    limit_raw = os.environ.get("INGEST_LIMIT")
    try:
        summary = execute_migration(
            config=config,
            slugs=_env_list("INGEST_SLUGS") or None,
            dois=dois or None,
            namespace=os.environ.get("INGEST_NAMESPACE") or None,
            ledger_dir=os.environ.get("INGEST_LEDGER_DIR") or None,
            run_id=os.environ.get("INGEST_RUN_ID") or DEFAULT_RUN_ID,
            source=source,
            query=os.environ.get("INGEST_QUERY") or None,
            limit=int(limit_raw) if limit_raw else None,
            sources=_env_list("INGEST_SOURCES") or None,
        )
    except Exception as e:  # noqa: BLE001 - the exit code is the signal
        logger.exception("KGIS/KGCS migration run failed: %s", e)
        return 2

    print(json.dumps(summary.to_dict(), indent=2, default=str))
    logger.info(
        "KGIS/KGCS migration complete: epoch=%s committed=%s committed_ops=%s",
        summary.epoch,
        summary.committed,
        summary.committed_operations,
    )
    # An idempotent re-run commits nothing but leaves an epoch behind; that is
    # success, not failure. Only a run that produced no canonical state at all
    # is a partial.
    return 0 if (summary.committed or summary.epoch > 0) else 1


def run_legacy_job() -> None:
    """The pre-existing paper-ingestion Cloud Run Job path."""
    config = _parse_env()
    started_at = datetime.now(timezone.utc)

    logger.info(
        f"Starting ingestion: query={config['query']!r}, "
        f"limit={config['limit']}, trace_id={config['trace_id']}"
    )

    result = asyncio.run(
        ingest_papers(
            query=config["query"],
            limit=config["limit"],
            sources=config["sources"],
            enable_agent_workflow=config["enable_agent_workflow"],
            min_extraction_confidence=config["min_extraction_confidence"],
            populate_citations=config["populate_citations"],
            extract_entities=config["extract_entities"],
            normalize_cross_entity_collisions=(
                config["normalize_cross_entity_collisions"]
            ),
            force_reextract=config["force_reextract"],
        )
    )

    persist_ingestion_run(config["trace_id"], config["query"], result, started_at)

    exit_code = _determine_exit_code(result)
    logger.info(
        f"Ingestion complete: status={result.status}, "
        f"problems={result.total_problems}, exit_code={exit_code}"
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
