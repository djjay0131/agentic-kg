---
title: "ADR-0008: Cloud Run Job execution backend for evaluation agents"
nav_exclude: true
---

# ADR-0008: Cloud Run Job execution backend for evaluation agents

Status: Proposed
Date: 2026-10-26

## Context

`EvaluationAgent` generates a Python script, executes it, and interprets the
output. Until now the only backend was `DockerSandbox`, which calls
`docker.from_env()`. That works on a developer machine, but the deployed API
runs on Cloud Run, where there is no Docker daemon and no privileged socket.
Staging therefore could not execute an experiment at all: the agent reached the
evaluation step and failed at container creation.

The execution backend is a security boundary. Generated code is untrusted. It
must not be able to read secrets, call Neo4j, reach the API, or exfiltrate data
to arbitrary internet hosts. It also needs reproducible provenance: the exact
code, image, inputs, outputs and timing must survive the request.

The existing infrastructure already has two Cloud Run Jobs (ingest, rotation)
and uses Direct VPC egress to reach private Neo4j. Reusing that network for
experiments would give generated code a path to the database, so a separate
isolation boundary is required.

## Decision

Add an `ExecutionBackend` protocol and two implementations:

- `DockerSandbox` remains the local/dev backend and the default
  (`EXPERIMENT_BACKEND=docker`).
- `CloudRunJobBackend` is the deployed backend
  (`EXPERIMENT_BACKEND=cloudrun_job`). It uploads `script.py` and `inputs.json`
  to a per-experiment GCS prefix, invokes one dedicated Cloud Run Job with the
  script's `gs://` URI as its only argument, polls the execution to a deadline,
  and reads `stdout.txt`, `stderr.txt`, `metrics.json` and `result.json` back
  from the same prefix.

Terraform creates, only when `var.experiment_bucket` is set:

| Resource | Purpose |
|---|---|
| `vt-gcp-00042-agentic-kg-experiments` bucket | Scripts, inputs and outputs; 30-day lifecycle delete |
| `agentic-kg-experiment-<env>` Job | One execution per experiment, `parallelism = 1`, `max_retries = 0` |
| `agentic-kg-experiment-<env>` service account | Runtime identity, no project roles, no secrets; one subnet-scoped `networkUser` to attach to the isolation VPC |
| Dedicated VPC + subnet | Isolation network; no peering to the application network |
| Egress firewall rules | Deny all internet; allow only the Private Google APIs VIPs |
| Bucket IAM (prefix-conditioned) | `objectViewer` + `objectCreator` for the runner and API, scoped to `<prefix>/` |
| Job IAM | API SA gets `run.jobsExecutorWithOverrides` and `run.invoker` on this Job only |

The container image is `docker/Dockerfile.experiment`: Python 3.12 slim with
NumPy, pandas, SciPy and scikit-learn, the GCS client, and the standalone
`experiment_runner.py`. It contains no agentic-kg application code, Neo4j
driver, Docker client or secrets. It runs as UID 10001 and writes only under
`/tmp` (Cloud Run's root filesystem is read-only).

### Isolation mechanism

Cloud Run creates a `0.0.0.0/0` route for every VPC, and that system route
cannot be deleted. The chosen mechanism is therefore firewall-based egress
control on a dedicated network:

1. `vpc_access` uses `ALL_TRAFFIC` and the dedicated subnet, so every packet
   leaves through the experiment VPC. The Job is never attached to the
   application VPC/subnet that reaches Neo4j.
2. An egress firewall rule at priority 1000 denies all destinations
   (`0.0.0.0/0`).
3. An egress allow at priority 100 permits TCP/443 only to
   `199.36.153.4/30` (`private.googleapis.com`) and `199.36.153.8/30`
   (`restricted.googleapis.com`).
4. Both the API and the runner force `EXPERIMENT_GCS_ENDPOINT` to
   `https://private.googleapis.com`, so GCS traffic uses the allowed VIP rather
   than a public address.

Consequences: generated code can reach GCS (needed for the hand-off) and the
metadata server (needed for auth), but has no route to Neo4j, the API service,
or the public internet. Neo4j is on a different VPC with no peering; the
default route is blocked by the deny rule even if a private address is guessed.

## Rationale

A separate backend keeps local development simple while making the deployed
path real. The GCS hand-off also gives provenance for free: the exact script,
inputs and outputs are retained for 30 days and referenced from
`EvaluationResult.execution_provenance` (`experiment_id`, `result_uri`,
`code_hash`, `image_digest`, `started_at`, `finished_at`, `backend`).

Firewall-based denial is used instead of "no VPC connector" because a Cloud Run
Job without a connector still has normal internet egress. Attaching a connector
or Direct VPC egress with a default route alone is not isolation. The explicit
deny is the control that can be tested and reviewed.

## Alternatives Considered

### Keep Docker and mount a Docker socket into Cloud Run

Not possible: Cloud Run does not provide a Docker daemon, and exposing a socket
would grant the agent host-level control.

### Run generated code in the API process with a restricted `exec`

Removes the isolation boundary entirely and gives generated code the API's
secrets, Neo4j credentials and network identity.

### Reuse the ingest Job and its VPC

Shortest path, but the ingest Job reaches private Neo4j and reads vendor
secrets. Generated code must never share that identity.

### Just add a VPC connector with no route

A connector without a route still leaves Cloud Run's default internet path in
place unless egress is set to `ALL_TRAFFIC` and a deny rule is applied. The
firewall deny is explicit and auditable; "no connector" is not.

## Consequences

### Positive

- EvaluationAgent actually executes in staging.
- Generated code has no path to Neo4j, secrets, the API or the internet.
- Every execution has a durable GCS prefix and code/image provenance.
- Local Docker tests and behaviour are unchanged.

### Negative / Tradeoffs

- One extra Job, bucket, service account, VPC, subnet and two firewall rules
  per environment that enables it.
- Job startup adds latency compared with a warm local container.
- The experiment image must be built and rolled separately from the API.

### Risks

- `roles/storage.objectCreator` cannot overwrite an existing object. The runner
  writes to a fresh per-experiment prefix, so this is acceptable; a retry with
  the same id is not supported by design.
- The image digest is only populated when the backend/Job response carries one
  or `EXPERIMENT_IMAGE_DIGEST` is set. Otherwise provenance records an honest
  null rather than the mutable `:latest` tag.
- A Cloud Run Job execution that outlives the API's poll deadline keeps running
  until the Job timeout. The API returns `timed_out` and the Job's own
  300-second cap bounds the cost.

## Impacted Areas

- [ ] Product
- [ ] Domain model
- [ ] Data architecture
- [x] AI architecture
- [ ] Domain-specific systems (see governance delta)
- [x] Integrations
- [ ] UX
- [x] Security/privacy
- [x] Implementation
- [x] Documentation

## Related Documents

- `docs/operations/experiment-execution-runbook.md`
- `llm/governance/adr/0006-staging-neo4j-private.md`

## Related Issues / PRs

- PR: `feat(agents): Cloud Run Job execution backend for EvaluationAgent`

## Supersedes

None.

## Superseded By

None.
