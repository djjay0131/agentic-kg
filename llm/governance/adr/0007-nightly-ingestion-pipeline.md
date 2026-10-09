---
title: "ADR-0007: Nightly ingestion pipeline on Cloud Scheduler + Workflows"
nav_exclude: true
---

# ADR-0007: Nightly ingestion pipeline on Cloud Scheduler + Workflows

Status: Proposed
Date: 2026-10-09

## Context

The staging KGIS -> KGCS -> canonical path is runnable but **manual**: an
operator executes the ingest Cloud Run Job by hand per the
`docs/operations/kgis-kgcs-staging-runbook.md`. Nothing ingests new papers on a
cadence, so the graph only moves when a human remembers to move it, and there is
no per-run record an operator can read after the fact.

The nightly-pipeline design (P1) asks for a scheduled run that:

1. plans a rotating set of discovery queries within a paper budget,
2. runs the ingest path in-process for the plan,
3. writes a durable `PipelineRun` report, and
4. emits one structured log line a notification can key on.

The pieces already exist: the `job` image carries the ingest and migration code
(`packages/core`), the durable KGIS ledger is a GCS volume on the ingest Job
(nightly design P0-2), the Semantic Scholar key is wired (P0-4), and Terraform
owns the staging stack through `.github/workflows/terraform.yml`. What is
missing is the schedule, the orchestrator entrypoint, and the report.

The CI Terraform principal is deliberately narrow (viewer, compute.admin,
run.admin, secretmanager.admin, artifactregistry.admin, serviceusage admin,
resourcemanager projectIam admin, iam.serviceAccountUser, state-bucket object
admin). It **cannot** create service accounts, workflows or scheduler jobs, or
create buckets. Apply therefore needs an owner bootstrap; this ADR records it.

## Decision

**Ship P1 as a Cloud Scheduler -> Cloud Workflows -> Cloud Run Job chain, all
Terraform-managed and gated by `nightly_enabled`.**

1. **Trigger.** `google_cloud_scheduler_job` runs `30 2 * * *` in
   `America/New_York` and POSTs an execution to the Cloud Workflows execution
   API with an OAuth token for `agentic-kg-nightly@`. No GitHub-hosted cron.
2. **Orchestration.** `google_workflows_workflow agentic-kg-nightly-<env>` calls
   `googleapis.run.v2...jobs.run` for the nightly Job and **waits** by polling
   the returned operation; on error it logs `pipeline_run_failed` and re-raises.
   The Workflow is deliberately trivial — the logic lives in the Job.
3. **Execution.** `google_cloud_run_v2_job agentic-kg-nightly-<env>` runs the
   same `job` image with `python -m agentic_kg.pipeline.nightly`. It plans with
   `agentic_kg.pipeline.plan` (deterministic, budget-bounded rotation over
   `config/ingest-queries.yaml`), runs each query in-process through the same
   entrypoint the ingest Job uses (`INGEST_MODE` selects legacy vs `kgis_kgcs`;
   no `gcloud` shell-out), enforces `NIGHTLY_MAX_PAPERS` / `NIGHTLY_MAX_LLM_USD`
   as graceful stops, and writes the report.
4. **Report.** One `PipelineRun` per execution, stored twice from one object
   (see `docs/design/nightly-pipeline-contract.md`):
   (a) JSON at `gs://vt-gcp-00042-agentic-kg-runs-staging/nightly/<run_id>.json`
   (written through a GCS volume mount, no GCS client dependency), and
   (b) a `(:PipelineRun {run_id})` Neo4j node, label **not** under `Canon__*`.
   The API reads Neo4j; it needs no GCS access.
5. **Notification.** The Job logs exactly one structured JSON line
   (`pipeline_run_completed` | `pipeline_run_failed`, `run_id`, `status`,
   `totals`, `url`). P1 stops at the log line; the email channel is a Cloud
   Monitoring log-based alert added in a later phase.
6. **Catalog and proposals.** Queries live in a seed YAML file
   (`config/ingest-queries.yaml`), validated by a pydantic model. Proposed new
   queries are **logged only** in P1. The adviser-proposes / human-approves loop
   and the review queue are P2; the UI Runs page and proposal surface are P3.
7. **Least privilege.** `agentic-kg-nightly@` gets `run.invoker` on the nightly
   Job only, `workflows.invoker`, `secretmanager.secretAccessor` and
   `compute.networkUser` at project scope (the latter two matching the compute
   SA), and `storage.objectAdmin` on the runs bucket and the ledger bucket
   **only** — never project-wide storage. If the applying principal cannot
   create service accounts, `nightly_service_account_email` reuses the existing
   compute runtime SA (documented fallback).
8. **Gating.** Everything above is `count = var.nightly_enabled ? 1 : 0`
   (default `false`); staging sets it `true`. A prod apply creates nothing.

## Rationale

- **Cloud Scheduler + Workflows is the platform-native scheduler.** It triggers
  inside the project with the project's identity, needs no long-lived GitHub
  credential, and its executions are visible in the same audit trail as the rest
  of the stack. The alternative (GitHub Actions cron) runs a job that must then
  cross into GCP on every tick.
- **The Workflow is thin and the Job is fat** so that all logic is unit-testable
  Python running in the same image as the ingest path, not a YAML program.
- **The report is dual-written** because the two readers want different things:
  an operator/backfill wants an immutable GCS object; the API wants a cheap
  indexed Neo4j lookup without a GCS dependency.
- **A deterministic planner** (date-seeded rotation) makes "why did tonight run
  these queries?" reproducible from the date alone — a random or stateful
  planner is un-debuggable after the fact.
- **Budgets are honest stops,** not silent truncation: a stopped run is
  `partial` with `stopped_by_budget = true` and per-query status recorded.

## Alternatives Considered

### Alternative 1 — GitHub Actions scheduled workflow (`cron:`)

An Actions `schedule` running `gcloud run jobs execute --wait`. Rejected: it
depends on a GitHub-hosted scheduler and a WIF round-trip per tick, has weaker
GCP-native observability, is disabled after repository inactivity, and splits
ownership of the schedule across two systems. Cloud Scheduler keeps the schedule
in the same Terraform state as everything else.

### Alternative 2 — Cloud Run Job scheduled natively (no Workflows)

Cloud Run Jobs have no built-in cron. A Scheduler job can call the Cloud Run
Admin `jobs:run` API directly, but then the "wait for completion and report
failure" logic has nowhere to live. The Workflow is the thin layer that waits
and escalates.

### Alternative 3 — Orchestrate inside the Job only (no Workflow)

Have the nightly Job itself do the planning, and have Scheduler call it
directly. This was rejected as the default because it makes the schedule
un-Terraform-able behind the LRO API and loses the explicit `pipeline_run_failed`
log at the orchestration layer. The Job still does the real work; the Workflow
only triggers and waits.

### Alternative 4 — One Job execution per query (fan-out)

The design's first instinct was one ingest Job execution per planned query. P1
runs the plan serially in one Job for simplicity and budget accounting;
fan-out is a later optimisation if a night's plan outgrows the timeout.

## Consequences

### Positive

- Staging ingests on a cadence with a durable, queryable run record, with no
  operator action.
- The report contract is shared and explicit, so the Runs page (P2/P3) can be
  built against it without touching the pipeline.
- The whole stack is gated and Terraform-managed; a prod apply is inert until
  an owner opts in.
- The Job reuses the existing image, secrets and VPC path — no new runtime
  surface beyond an entrypoint and a bucket.

### Negative / Tradeoffs

- **Apply needs an owner bootstrap.** The CI principal must additionally be
  granted: `roles/iam.serviceAccountAdmin` (create the SA and its IAM
  bindings), `roles/workflows.admin`, `roles/cloudscheduler.admin`, and
  `roles/storage.admin` (or a storage-bucket-create custom role). The manual
  `nightly-run.yml` additionally needs the CI SA to hold
  `roles/workflows.invoker`. None of this is added to Terraform — the owner
  grants it out of band.
- **The `kgis_kgcs` backend does not yet consume a free-text query.** In this
  slice it runs the committed corpus; the plan is recorded and each planned
  query is executed as its own migration run, with an `honest_null`
  (`query_scoping`) saying the query did not select live papers. Live
  query-driven acquisition over that path is P3.
- **`stopped_by_budget` conflates two conditions** (the planner leaving queries
  out of tonight's rotation, and the runtime stopping early). Both are recorded
  truthfully; only the runtime stop makes a run `partial`. A dedicated field can
  separate them later.
- **No email notification in P1.** The structured log line exists but the
  log-based alert/email is deferred.

### Risks

- **Permission drift.** If the bootstrap roles are not granted, the Plan check
  still passes (plan is read-only) but the apply 403s. The PR body lists them
  explicitly; the apply is owner-run.
- **FUSE semantics.** The runs bucket is mounted like the ledger bucket. GCS
  FUSE does not provide file locking; this is safe here because the Job is
  `parallelism = 1` and each run writes a unique object.

## Impacted Areas

- [x] Product
- [ ] Domain model
- [x] Data architecture
- [ ] AI architecture
- [ ] Domain-specific systems (see governance delta)
- [x] Integrations
- [ ] UX
- [x] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `docs/design/nightly-pipeline-contract.md` (data plane — the shared report
  contract)
- `config/ingest-queries.yaml`, `packages/core/src/agentic_kg/pipeline/`
- `infra/main.tf`, `infra/variables.tf`, `infra/envs/staging.tfvars`
- `.github/workflows/nightly-run.yml`, `.github/workflows/deploy-master.yml`
- `docs/operations/kgis-kgcs-staging-runbook.md`

## Related Issues / PRs

- Refs the nightly-pipeline P1 task (T29); sibling P1 task T30 builds the Runs
  page/API against the same contract.

## Supersedes

None.

## Superseded By

None.
