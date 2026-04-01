"""Ingestion API endpoints — triggers Cloud Run Jobs for paper ingestion."""

import logging
import os
import time
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from agentic_kg_api.dependencies import get_repo
from agentic_kg_api.schemas import (
    IngestRequest,
    IngestStatusResponse,
    SanityCheckResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ingest", tags=["ingest"])

# Configuration from environment
GCP_PROJECT = os.environ.get("GCP_PROJECT", "")
GCP_REGION = os.environ.get("GCP_REGION", "us-central1")
GCP_ENV = os.environ.get("GCP_ENV", "staging")
JOB_NAME = os.environ.get("INGEST_JOB_NAME", "")

# Status cache: trace_id -> (timestamp, response_dict)
_status_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 10  # seconds


def _get_job_name() -> str:
    """Get the full Cloud Run Job resource name."""
    job = JOB_NAME or f"agentic-kg-ingest-{GCP_ENV}"
    return f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/jobs/{job}"


def _get_auth_token() -> str:
    """Get OAuth2 token from GCP metadata server or application default credentials.

    Returns:
        Bearer token string.

    Raises:
        RuntimeError: If authentication fails.
    """
    try:
        import google.auth
        from google.auth.transport.requests import Request

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(Request())
        return credentials.token
    except Exception as e:
        raise RuntimeError(f"Failed to obtain GCP auth token: {e}") from e


async def _trigger_cloud_run_job(trace_id: str, request: IngestRequest) -> dict:
    """Trigger a Cloud Run Job execution via REST API.

    Args:
        trace_id: Unique trace ID for this execution.
        request: Ingestion request parameters.

    Returns:
        Cloud Run execution response dict.

    Raises:
        httpx.HTTPStatusError: If the API call fails.
        RuntimeError: If authentication fails.
    """
    token = _get_auth_token()
    job_name = _get_job_name()
    url = f"https://{GCP_REGION}-run.googleapis.com/v2/{job_name}:run"

    env_overrides = [
        {"name": "INGEST_QUERY", "value": request.query},
        {"name": "INGEST_LIMIT", "value": str(request.limit)},
        {"name": "INGEST_TRACE_ID", "value": trace_id},
        {"name": "INGEST_AGENT_WORKFLOW", "value": str(request.enable_agent_workflow).lower()},
        {"name": "INGEST_MIN_CONFIDENCE", "value": str(request.min_extraction_confidence)},
    ]
    if request.sources:
        env_overrides.append(
            {"name": "INGEST_SOURCES", "value": ",".join(request.sources)}
        )

    body = {
        "overrides": {
            "containerOverrides": [{"env": env_overrides}],
        }
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


async def _get_execution_status_from_gcp(trace_id: str) -> Optional[dict]:
    """Get Cloud Run execution status, with 10s TTL cache.

    For terminal states (SUCCEEDED, FAILED, CANCELLED), the cache never expires.
    For non-terminal states, the cache expires after CACHE_TTL seconds.

    Args:
        trace_id: Trace ID to look up.

    Returns:
        Status dict with 'status' key, or None if not found.
    """
    now = time.time()

    # Check cache
    if trace_id in _status_cache:
        cached_at, cached = _status_cache[trace_id]
        status = cached.get("status", "")
        # Terminal states cached forever
        if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return cached
        # Non-terminal states cached for TTL
        if now - cached_at < CACHE_TTL:
            return cached

    # Fetch from Cloud Run Executions API
    # We search executions by listing and filtering by trace_id in labels/env
    # For simplicity, we query Neo4j for the IngestionRun node instead
    # The Cloud Run execution status is best-effort from the jobs API
    return None


def _get_ingestion_run_from_neo4j(trace_id: str) -> Optional[dict]:
    """Query Neo4j for an IngestionRun node by trace_id.

    Args:
        trace_id: Trace ID to look up.

    Returns:
        Dict with IngestionRun properties, or None if not found.
    """
    try:
        repo = get_repo()
        with repo.session() as session:
            result = session.run(
                "MATCH (r:IngestionRun {trace_id: $trace_id}) RETURN r",
                trace_id=trace_id,
            )
            record = result.single()
            if record:
                return dict(record["r"])
    except Exception as e:
        logger.warning(f"Failed to query IngestionRun from Neo4j: {e}")
    return None


def _build_status_response(
    trace_id: str,
    status: str,
    query: str = "",
    neo4j_data: Optional[dict] = None,
    error: Optional[str] = None,
) -> IngestStatusResponse:
    """Build an IngestStatusResponse from available data."""
    if neo4j_data:
        import json
        extraction_errors_raw = neo4j_data.get("extraction_errors", "{}")
        try:
            extraction_errors = json.loads(extraction_errors_raw)
        except (json.JSONDecodeError, TypeError):
            extraction_errors = {}

        return IngestStatusResponse(
            trace_id=trace_id,
            status=neo4j_data.get("status", status),
            query=neo4j_data.get("query", query),
            papers_found=neo4j_data.get("papers_found", 0),
            papers_imported=neo4j_data.get("papers_imported", 0),
            papers_extracted=neo4j_data.get("papers_extracted", 0),
            papers_skipped_no_pdf=neo4j_data.get("papers_skipped_no_pdf", 0),
            total_problems=neo4j_data.get("total_problems", 0),
            concepts_created=neo4j_data.get("concepts_created", 0),
            concepts_linked=neo4j_data.get("concepts_linked", 0),
            extraction_errors=extraction_errors,
            error=error,
        )

    return IngestStatusResponse(
        trace_id=trace_id,
        status=status,
        query=query,
        error=error,
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.post("", response_model=IngestStatusResponse)
async def start_ingestion(request: IngestRequest) -> IngestStatusResponse:
    """
    Start an async paper ingestion job via Cloud Run Jobs.

    Returns immediately with trace_id and status "queued".
    Poll GET /api/ingest/{trace_id} for progress.
    """
    trace_id = f"ingest-{uuid.uuid4().hex[:8]}"

    try:
        await _trigger_cloud_run_job(trace_id, request)
    except Exception as e:
        logger.error(f"Failed to trigger ingestion job: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger ingestion job: {e}",
        )

    logger.info(f"[IngestAPI] Triggered job for trace_id={trace_id}, query={request.query!r}")

    return IngestStatusResponse(
        trace_id=trace_id,
        status="queued",
        query=request.query,
    )


@router.get("/{trace_id}", response_model=IngestStatusResponse)
async def get_ingestion_status(trace_id: str) -> IngestStatusResponse:
    """
    Get the status of an ingestion job.

    Checks Neo4j for an IngestionRun node (written when job completes).
    If not found, the job is either still running or unknown.
    """
    # Check Neo4j for completed IngestionRun
    neo4j_data = _get_ingestion_run_from_neo4j(trace_id)

    if neo4j_data:
        response = _build_status_response(
            trace_id=trace_id,
            status=neo4j_data.get("status", "completed"),
            neo4j_data=neo4j_data,
        )
        # Cache terminal state
        _status_cache[trace_id] = (time.time(), {"status": response.status})
        return response

    # Check cache for in-progress status
    if trace_id in _status_cache:
        cached_at, cached = _status_cache[trace_id]
        return _build_status_response(
            trace_id=trace_id,
            status=cached.get("status", "running"),
            query=cached.get("query", ""),
        )

    # Unknown trace_id
    raise HTTPException(status_code=404, detail=f"Ingestion job not found: {trace_id}")


def reset_status_cache() -> None:
    """Reset the status cache (for testing)."""
    _status_cache.clear()
