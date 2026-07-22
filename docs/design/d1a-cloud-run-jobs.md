---
title: D-1a · Cloud Run Jobs ingestion
parent: Design
nav_order: 11
---

# D-1a · Cloud Run Jobs ingestion

{: .label .label-green }
VERIFIED

**Backlog ID:** D-1a · **Depends on:**
[D-1 · Ingest real papers]({{ site.baseurl }}/design/d1-ingest-real-papers) ·
**Spec:**
[`cloud-run-jobs-ingestion.md`](https://github.com/djjay0131/agentic-kg/blob/master/llm/features/cloud-run-jobs-ingestion.md)

## Why

D-1 ran ingestion with `asyncio.create_task` *inside* the API instance and
tracked runs in an in-memory `_jobs` dict. On Cloud Run that is unsafe: the API
instance can scale to zero mid-run (killing the background task), the request
timeout doesn't protect a detached task, and the job store evaporates on every
restart — no durable record of what entered the graph. Ingesting 20 papers with
LLM extraction takes 10-15 minutes, well past what a request-scoped instance can
reliably hold.

## What shipped

Ingestion now runs as its own **Cloud Run Job** (`agentic-kg-ingest-staging`),
managed by Terraform and triggered by the API over REST. `POST /api/ingest`
generates a `trace_id`, kicks off a job execution with the request encoded as
container env-var overrides, and returns `{"trace_id", "status": "queued"}`
immediately. The job runs to completion independently of the API, then writes a
durable `IngestionRun` node to Neo4j. `GET /api/ingest/{trace_id}` reads that
node for the final counts. The in-memory `_jobs` dict and `asyncio.create_task`
are gone.

## Design decisions

**Durable Job over in-memory job store.** The core decision: ingestion state
lives in Neo4j (`IngestionRun` node) and Cloud Run execution metadata, never in
API process memory. A restarted or scaled-to-zero API instance loses nothing —
the job is a separate container with its own 30-minute lifecycle, and every run
leaves a permanent provenance record (`trace_id`, query, all counts,
`extraction_errors`, `started_at`, `completed_at`). This directly retires the
three D-1 failure modes.

**Env-var driven entrypoint.** The job takes *all* configuration from the
environment, so the API triggers it purely by supplying `containerOverrides`.
`INGEST_QUERY` is required (exit code 2 if absent); `INGEST_TRACE_ID`,
`INGEST_LIMIT`, `INGEST_SOURCES`, `INGEST_AGENT_WORKFLOW`, and
`INGEST_MIN_CONFIDENCE` are optional with defaults. Later cycles added
`POPULATE_CITATIONS`, `EXTRACT_ENTITIES`, `NORMALIZE_CROSS_ENTITY`, and
`FORCE_REEXTRACT` — the same env-var contract absorbed them without touching the
trigger path.

**Terraform-managed, independently deployed.** The job is a first-class
`google_cloud_run_v2_job` resource alongside the API and UI services, with the
same secrets, `max_retries = 0` (partial results already in Neo4j — don't
re-run), and 2Gi / 2 CPU / 1800s limits from tfvars. A dedicated lean
`Dockerfile.job` builds only the core package (no FastAPI/uvicorn), and
`cloudbuild.yaml`'s `_SERVICE=job` path deploys it via `gcloud run jobs update`
so API and job images ship on separate cadences.

**Deliberate exit codes.** `job_runner` exits `0` complete / `1` partial (some
extraction errors) / `2` fatal, so the Cloud Run execution surfaces the outcome.

## How it works

- **Job entrypoint:**
  [`job_runner.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/core/src/agentic_kg/job_runner.py)
  — `_parse_env()` reads the env-var contract, `main()` calls the existing
  `ingest_papers()` orchestrator, and `persist_ingestion_run()` writes the
  `IngestionRun` node. Same function D-1 uses, so the CLI path is unchanged.
- **Terraform resource:**
  [`infra/main.tf`](https://github.com/djjay0131/agentic-kg/blob/master/infra/main.tf)
  — `google_cloud_run_v2_job.ingest` (image `job:latest`, command
  `python -m agentic_kg.job_runner`, four shared secrets, `max_retries = 0`) plus
  a `google_cloud_run_v2_job_iam_member` binding scoped to *this job only*.
  Sizing vars live in
  [`variables.tf`](https://github.com/djjay0131/agentic-kg/blob/master/infra/variables.tf)
  /
  [`envs/staging.tfvars`](https://github.com/djjay0131/agentic-kg/blob/master/infra/envs/staging.tfvars).
- **Lean image:**
  [`docker/Dockerfile.job`](https://github.com/djjay0131/agentic-kg/blob/master/docker/Dockerfile.job)
  — multi-stage, core-only, non-root, no port, no health check.
- **API trigger + status:**
  [`routers/ingest.py`](https://github.com/djjay0131/agentic-kg/blob/master/packages/api/src/agentic_kg_api/routers/ingest.py)
  — `_trigger_cloud_run_job()` mints a `google-auth` token and POSTs
  `…/jobs/{name}:run` via `httpx` (no heavy SDK); `GET` reads the Neo4j
  `IngestionRun` node with a 10s in-process status cache.
- **Predecessor:** ingestion orchestration itself is
  [D-1 · Ingest real papers]({{ site.baseurl }}/design/d1-ingest-real-papers).

## Verification

- **Tests:** `packages/api/tests/test_ingest.py` covers the REST trigger, the
  status cache, 404 on unknown `trace_id`, and 500 on trigger failure;
  `job_runner` exit-code and env-parsing behaviour is unit-tested.
- **Infra:** `terraform plan` shows `google_cloud_run_v2_job.ingest` with the
  correct image, command, secrets, timeout, and job-scoped IAM.
- **Status:** VERIFIED — spec marked VERIFIED (2026-03-31); job resource,
  entrypoint, and independent build path are all present in `master`.

## Related

- Depends on: [D-1 · Ingest real papers]({{ site.baseurl }}/design/d1-ingest-real-papers)
- Deferred (Non-Goals): concurrency guard, job scheduling/queue, and a GCS
  per-paper research log (follow-on **D-1b**)
- Divergence: the spec's `GET` design also polls the Cloud Run Executions REST
  API; the shipped handler queries only the Neo4j `IngestionRun` node
  (`_get_execution_status_from_gcp` is intentionally a no-op), so live status for
  a still-running job is "queued/running" from cache until the node lands.
