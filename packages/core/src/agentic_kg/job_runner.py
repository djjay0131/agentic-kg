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
    elif result.extraction_errors:
        return 1
    return 0


def main() -> None:
    """Cloud Run Job entrypoint for paper ingestion."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

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
