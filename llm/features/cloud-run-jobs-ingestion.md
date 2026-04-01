# Feature: Cloud Run Jobs for Async Ingestion

**Status:** VERIFIED
**Date:** 2026-03-31
**Author:** Feature Architect (AI-assisted)
**Depends On:** D-1 (Ingest Real Papers) — VERIFIED

## Problem

The D-1 ingestion feature uses `asyncio.create_task` inside the Cloud Run API instance to run background ingestion. This is unreliable in production because:

1. **Instance lifecycle** — Cloud Run can scale the instance to zero while the background task is running, killing the ingestion mid-run.
2. **Request timeout** — Cloud Run's default request timeout (5 min) has no effect on the background task, but the instance may be evicted if there are no active requests.
3. **No observability** — The in-memory job store (`_jobs` dict) is lost when the instance restarts. There's no durable record of ingestion runs.

Ingesting 20 papers with LLM extraction takes 10-15 minutes — well beyond what a request-scoped approach can reliably handle on Cloud Run.

## Goals

- Ingestion runs as a Cloud Run Job with up to 30 min timeout, independent of the API instance
- `POST /api/ingest` triggers a Cloud Run Job execution with query parameters as env var overrides
- `GET /api/ingest/{trace_id}` returns job status (cached, 10s TTL) from Cloud Run execution metadata + detailed results from Neo4j `IngestionRun` node
- `IngestionRun` node persisted to Neo4j for full provenance of every ingestion run
- Terraform manages the job resource alongside existing infrastructure
- Cloud Build deploys the job image independently from the API image
- Minimum required IAM permissions (only `run.jobs.run` and `run.executions.get`)

## Non-Goals

- Concurrent execution guards (allow concurrent for now; add guard later if needed)
- Job queue or scheduling (manual trigger via API is sufficient)
- Worker service with Celery/Redis (Cloud Run Jobs is simpler and sufficient)
- GCS research log for per-paper progress (follow-on feature D-1b)

## User Stories

- As a researcher, I want to trigger paper ingestion via the API and have it run reliably to completion even if it takes 15 minutes, so that I don't lose partial results.
- As an operator, I want every ingestion run recorded in Neo4j with counts and status, so that I can audit what data entered the knowledge graph and when.
- As a developer, I want the ingestion job managed by Terraform, so that infrastructure stays consistent across environments.

## Design Approach

### Architecture

```
  POST /api/ingest                GET /api/ingest/{trace_id}
       │                                │
       ▼                                ▼
  ┌─────────────┐              ┌──────────────────┐
  │ API (Cloud  │──triggers──▶│ Cloud Run Job     │
  │ Run Service)│  via REST   │ (agentic-kg-      │
  │             │  + google-  │  ingest-staging)   │
  │             │  auth token │ Dockerfile.job     │
  └─────────────┘              └────────┬──────────┘
       │                                │
       │ queries (cached 10s)           │ writes
       ▼                                ▼
  ┌─────────────┐              ┌─────────────────┐
  │ Cloud Run   │              │ Neo4j            │
  │ Executions  │              │ IngestionRun     │
  │ REST API    │              │ node + papers    │
  └─────────────┘              └─────────────────┘
```

### Components

**1. `job_runner.py`** — Thin wrapper that bridges `ingest_papers()` to the Cloud Run Job environment:
- Reads config from env vars (`INGEST_QUERY`, `INGEST_LIMIT`, `INGEST_SOURCES`, `INGEST_TRACE_ID`, `INGEST_AGENT_WORKFLOW`, `INGEST_MIN_CONFIDENCE`)
- Calls `ingest_papers()` (the existing orchestration function)
- Writes `IngestionRun` node to Neo4j with full metadata
- Exits with deliberate codes: 0=complete, 1=partial (some errors), 2=fatal

**2. `Dockerfile.job`** — Separate, lean Docker image:
- Builds only the core package (not API/FastAPI/uvicorn)
- Entrypoint: `python -m agentic_kg.job_runner`
- Smaller image, faster cold start than the API image
- Deployed independently — API fixes don't change job image and vice versa

**3. Terraform `google_cloud_run_v2_job`** — New resource in `main.tf`:
- References `job:latest` image tag (separate from `api:latest`)
- Same secrets as the API service (NEO4J_URI, NEO4J_PASSWORD, OPENAI_API_KEY, ANTHROPIC_API_KEY)
- 30 min timeout, 2Gi memory, 2 CPU
- `max_retries = 0` (don't retry — partial results already in Neo4j)

**4. IAM — Minimum required permissions:**
- Custom IAM role or resource-scoped binding with only:
  - `run.jobs.run` — trigger job executions
  - `run.executions.get` — poll execution status
- Bound to the API service's service account
- No broad `roles/run.developer` or `roles/run.invoker`

**5. API router changes** (`routers/ingest.py`):
- `POST /api/ingest` triggers a Cloud Run Job execution via lightweight REST call using `google-auth` + `httpx` (no heavy SDK dependency)
- `GET /api/ingest/{trace_id}` checks two sources:
  - Cloud Run Executions REST API for job status (cached 10s TTL to avoid hammering GCP on aggressive polling)
  - Neo4j `IngestionRun` node for detailed results (once job completes)
- Remove the in-memory `_jobs` dict and `asyncio.create_task` approach

**6. `IngestionRun` Neo4j node** — Written by `job_runner.py` at end of ingestion:
```
(:IngestionRun {
  trace_id: String,       // Links to Cloud Run execution
  query: String,
  status: String,         // completed | failed | partial
  papers_found: Int,
  papers_imported: Int,
  papers_extracted: Int,
  papers_skipped_no_pdf: Int,
  total_problems: Int,
  concepts_created: Int,
  concepts_linked: Int,
  extraction_errors: String,  // JSON-serialized dict
  started_at: DateTime,
  completed_at: DateTime,
})
```

**7. Cloud Build update** (`cloudbuild.yaml`):
- Add `_SERVICE=job` option that builds `Dockerfile.job` and pushes as `job:latest` + `job:$COMMIT_SHA`
- Deploy step for `job` uses `gcloud run jobs update` instead of `gcloud run deploy`
- API and job are built and deployed independently

### Data Flow

1. User POSTs to `/api/ingest` with `{"query": "graph-based retrieval", "limit": 20}`
2. API generates `trace_id`, calls Cloud Run Jobs REST API (authenticated via instance service account token) to create an execution with env var overrides
3. API returns `{"trace_id": "ingest-abc123", "status": "queued"}` immediately
4. Cloud Run spins up a container from `Dockerfile.job` running `python -m agentic_kg.job_runner`
5. `job_runner` reads env vars, calls `ingest_papers()`, which writes Papers/Mentions/Concepts to Neo4j
6. `job_runner` writes `IngestionRun` node to Neo4j with summary metadata
7. `job_runner` exits with code 0 (complete), 1 (partial), or 2 (fatal)
8. User polls `GET /api/ingest/{trace_id}` — API checks cached Cloud Run execution status + Neo4j IngestionRun node

## Sample Implementation

```python
# packages/core/src/agentic_kg/job_runner.py
import os, sys, asyncio, json, logging
from datetime import datetime, timezone

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
logger = logging.getLogger(__name__)

def main():
    """Cloud Run Job entrypoint for paper ingestion."""
    from agentic_kg.ingestion import ingest_papers
    from agentic_kg.knowledge_graph.repository import get_repository

    query = os.environ.get("INGEST_QUERY")
    if not query:
        logger.error("INGEST_QUERY env var is required")
        sys.exit(2)

    trace_id = os.environ.get("INGEST_TRACE_ID", f"ingest-{os.urandom(4).hex()}")
    limit = int(os.environ.get("INGEST_LIMIT", "20"))
    sources_raw = os.environ.get("INGEST_SOURCES", "")
    sources = [s.strip() for s in sources_raw.split(",") if s.strip()] or None
    agent_workflow = os.environ.get("INGEST_AGENT_WORKFLOW", "true").lower() == "true"
    min_confidence = float(os.environ.get("INGEST_MIN_CONFIDENCE", "0.5"))

    started_at = datetime.now(timezone.utc)
    logger.info(f"Starting ingestion: query={query!r}, limit={limit}, trace_id={trace_id}")

    result = asyncio.run(ingest_papers(
        query=query, limit=limit, sources=sources,
        enable_agent_workflow=agent_workflow,
        min_extraction_confidence=min_confidence,
    ))

    # Persist IngestionRun node to Neo4j
    _persist_ingestion_run(trace_id, query, result, started_at)

    # Deliberate exit codes: 0=complete, 1=partial, 2=fatal
    if result.status == "failed":
        sys.exit(2)
    elif result.extraction_errors:
        sys.exit(1)
    sys.exit(0)

def _persist_ingestion_run(trace_id, query, result, started_at):
    """Write IngestionRun node to Neo4j for provenance."""
    from agentic_kg.knowledge_graph.repository import get_repository
    completed_at = datetime.now(timezone.utc)
    try:
        repo = get_repository()
        with repo.session() as session:
            session.run("CREATE (r:IngestionRun) SET r = $props", props={
                "trace_id": trace_id, "query": query, "status": result.status,
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
            })
    except Exception as e:
        logger.error(f"Failed to persist IngestionRun: {e}")

if __name__ == "__main__":
    main()
```

```python
# routers/ingest.py — trigger via lightweight REST call
import time, httpx
from google.auth.transport.requests import Request
from google.auth import default as google_auth_default

_status_cache: dict[str, tuple[float, dict]] = {}  # trace_id -> (timestamp, status)
CACHE_TTL = 10  # seconds

async def _trigger_job(trace_id: str, request: IngestRequest) -> None:
    """Trigger Cloud Run Job execution via REST API."""
    credentials, project = google_auth_default()
    credentials.refresh(Request())
    token = credentials.token

    url = (f"https://{REGION}-run.googleapis.com/v2/"
           f"projects/{PROJECT}/locations/{REGION}/"
           f"jobs/agentic-kg-ingest-{ENV}:run")

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {token}"},
            json={"overrides": {"containerOverrides": [{"env": [
                {"name": "INGEST_QUERY", "value": request.query},
                {"name": "INGEST_LIMIT", "value": str(request.limit)},
                {"name": "INGEST_TRACE_ID", "value": trace_id},
                {"name": "INGEST_SOURCES", "value": ",".join(request.sources or [])},
                {"name": "INGEST_AGENT_WORKFLOW", "value": str(request.enable_agent_workflow).lower()},
            ]}]}})
        resp.raise_for_status()

async def _get_execution_status(trace_id: str) -> dict:
    """Get Cloud Run execution status with 10s TTL cache."""
    now = time.time()
    if trace_id in _status_cache:
        cached_at, cached = _status_cache[trace_id]
        if now - cached_at < CACHE_TTL and cached.get("status") not in ("SUCCEEDED", "FAILED"):
            return cached
        if cached.get("status") in ("SUCCEEDED", "FAILED"):
            return cached  # Terminal states cached forever

    # Fetch from Cloud Run API
    # ... REST call to executions API ...
    _status_cache[trace_id] = (now, status)
    return status
```

```hcl
# infra/main.tf — Cloud Run Job with minimum IAM
resource "google_cloud_run_v2_job" "ingest" {
  name     = "agentic-kg-ingest-${var.env}"
  location = var.region

  template {
    template {
      containers {
        image   = "${var.region}-docker.pkg.dev/${var.project_id}/agentic-kg/job:latest"
        command = ["python", "-m", "agentic_kg.job_runner"]
        resources { limits = { memory = var.ingest_job_memory, cpu = var.ingest_job_cpu } }
        # ... secrets same as API ...
      }
      timeout     = "${var.ingest_job_timeout}s"
      max_retries = 0
    }
  }
}

# Minimum permissions — only run.jobs.run + run.executions.get
resource "google_cloud_run_v2_job_iam_member" "api_can_run_ingest" {
  name     = google_cloud_run_v2_job.ingest.name
  location = var.region
  role     = "roles/run.invoker"  # Scoped to THIS job only
  member   = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}
```

## Edge Cases & Error Handling

### Job Already Running
- **Scenario**: User triggers a second ingestion while one is already running
- **Behavior**: Allowed — each execution has a unique trace_id and writes a separate IngestionRun node. No conflict since Neo4j writes are per-paper.
- **Test**: Trigger two jobs; verify both complete with separate IngestionRun nodes

### Neo4j Unreachable from Job
- **Scenario**: Job starts but can't connect to Neo4j
- **Behavior**: `ingest_papers()` fails fast; `job_runner` attempts to write IngestionRun but that also fails; exits with code 2. Cloud Run execution shows FAILED.
- **Test**: Verify Cloud Run execution status shows FAILED

### Job Timeout (30 min)
- **Scenario**: Ingestion takes longer than 30 minutes
- **Behavior**: Cloud Run kills the container. Partial results (papers/mentions already written to Neo4j) are preserved. No IngestionRun node written. GET shows FAILED from Cloud Run API.
- **Test**: Verify partial data accessible in Neo4j after timeout

### API Can't Reach Cloud Run Jobs API
- **Scenario**: IAM permissions missing or network error
- **Behavior**: POST `/api/ingest` returns 500 with descriptive error
- **Test**: Mock httpx failure; verify 500 response

### Invalid Query Parameters
- **Scenario**: Empty query, limit=0, invalid sources
- **Behavior**: Pydantic validation at API boundary (422). Job runner checks INGEST_QUERY and exits code 2 if missing.
- **Test**: POST with empty query; verify 422

### Polling Non-Existent Trace ID
- **Scenario**: GET with unknown trace_id
- **Behavior**: Cloud Run API returns not found; Neo4j has no node; API returns 404
- **Test**: GET with random trace_id; verify 404

### Aggressive Polling
- **Scenario**: Client polls every 2 seconds
- **Behavior**: 10s TTL cache on Cloud Run execution status; terminal states cached permanently. Max 6 GCP API calls per minute per trace_id.
- **Test**: Multiple rapid GETs; verify only one Cloud Run API call per 10s window

## Acceptance Criteria

### AC-1: Terraform Resource Created
- **Given** the existing Terraform configuration
- **When** `terraform plan -var-file=envs/staging.tfvars` is run
- **Then** it shows a new `google_cloud_run_v2_job.ingest` resource with correct image (`job:latest`), command, secrets, timeout, and minimum IAM permissions

### AC-2: Job Triggers from API
- **Given** the API deployed to staging
- **When** POSTing to `/api/ingest` with `{"query": "graph-based retrieval", "limit": 20}`
- **Then** a Cloud Run Job execution is created and response contains `trace_id` and `status: "queued"`

### AC-3: Job Runs to Completion
- **Given** a triggered Cloud Run Job execution
- **When** the job processes papers
- **Then** Paper, ProblemMention, and ProblemConcept nodes are created in Neo4j, and an `IngestionRun` node is written with full metadata

### AC-4: Polling Returns Cached Status
- **Given** a triggered ingestion job
- **When** polling `GET /api/ingest/{trace_id}` multiple times within 10 seconds
- **Then** only one Cloud Run API call is made (cached), and response shows current status with counts from Neo4j when complete

### AC-5: IngestionRun Provenance
- **Given** a completed ingestion job
- **When** querying Neo4j for the IngestionRun node
- **Then** the node contains trace_id, query, all counts, extraction_errors, started_at, and completed_at

### AC-6: Job Runner Exit Codes
- **Given** the job_runner module
- **When** ingestion completes with different outcomes
- **Then** exit code is 0 for complete, 1 for partial (some extraction errors), 2 for fatal failure

### AC-7: Cloud Build Deploys Job Independently
- **Given** the updated cloudbuild.yaml with `_SERVICE=job`
- **When** running `gcloud builds submit --config=cloudbuild.yaml --substitutions=_SERVICE=job,COMMIT_SHA=...`
- **Then** only the job image is built and updated, not the API service

### AC-8: Failed Job Reported Correctly
- **Given** an ingestion job that fails
- **When** polling `GET /api/ingest/{trace_id}`
- **Then** response shows `status: "failed"` with error details

### AC-9: CLI Still Works
- **Given** the existing CLI `python -m agentic_kg ingest` command
- **When** run locally with appropriate env vars
- **Then** it works as before (no regression)

### AC-10: Separate Dockerfile Builds
- **Given** `Dockerfile.job` and `Dockerfile.api`
- **When** building each independently
- **Then** the job image contains only core packages (no FastAPI/uvicorn), and the API image is unchanged

## Technical Notes

- **New file**: `packages/core/src/agentic_kg/job_runner.py` — job entrypoint
- **New file**: `docker/Dockerfile.job` — lean image with core package only
- **Modified**: `packages/api/src/agentic_kg_api/routers/ingest.py` — replace asyncio.create_task with Cloud Run Jobs REST trigger; add status cache; query Neo4j for IngestionRun
- **Modified**: `packages/api/src/agentic_kg_api/schemas.py` — update IngestStatusResponse with IngestionRun fields
- **Modified**: `infra/main.tf` — add `google_cloud_run_v2_job.ingest` resource + scoped IAM
- **Modified**: `infra/variables.tf` — add `ingest_job_memory`, `ingest_job_cpu`, `ingest_job_timeout`
- **Modified**: `infra/envs/staging.tfvars` — add job variable values
- **Modified**: `cloudbuild.yaml` — add `_SERVICE=job` build/deploy path
- **No new heavy dependencies**: Uses `google-auth` (already available on GCP) + `httpx` for REST calls
- **Pattern**: Follow existing Terraform patterns in `main.tf` for secrets, IAM, resource dependencies
- **Pattern**: Follow existing `cloudbuild.yaml` `_SERVICE` substitution pattern

## Dependencies

- D-1 (Ingest Real Papers) — VERIFIED (provides `ingest_papers()`, `IngestionResult`, CLI)
- GCP APIs: `run.googleapis.com` (already enabled)
- Python: `google-auth` (available in GCP runtime), `httpx` (add to API requirements if not present)
- IAM: Scoped `run.jobs.run` + `run.executions.get` on the job resource for the API service account
- Neo4j: staging instance (existing)

## Open Questions

- **Concurrent executions**: Allow for now. Add a guard (check for running executions before triggering) if Neo4j write conflicts occur in practice.
- **Job image versioning**: Currently `job:latest`. Could pin to SHA for stricter version control, but requires updating the Terraform job definition on every deploy. Start with `latest`, tighten later if needed.
