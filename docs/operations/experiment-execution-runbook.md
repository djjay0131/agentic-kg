---
title: Experiment execution (EvaluationAgent)
nav_exclude: true
---

# Experiment execution (EvaluationAgent)

How the `EvaluationAgent` runs generated experiment code in staging, and what to
do when a run does not execute. The design decision is
[ADR-0008](../../llm/governance/adr/0008-cloud-run-job-execution-backend.md).

## Backends

| `EXPERIMENT_BACKEND` | Implementation | Where it runs | Use |
|---|---|---|---|
| `docker` (default) | `DockerSandbox` | Local Docker daemon | Development, unit tests |
| `cloudrun_job` | `CloudRunJobBackend` | Dedicated Cloud Run Job | Staging/production API |

Terraform sets `cloudrun_job` on the staging API. Local shells with no
`EXPERIMENT_BACKEND` get `docker`, so existing local workflows are unchanged.

Relevant API environment (owned by Terraform, not by the deploy workflows):

| Variable | Staging value |
|---|---|
| `EXPERIMENT_BACKEND` | `cloudrun_job` |
| `EXPERIMENT_GCS_BUCKET` | `vt-gcp-00042-agentic-kg-experiments` |
| `EXPERIMENT_GCS_PREFIX` | `staging` |
| `EXPERIMENT_GCS_ENDPOINT` | `https://private.googleapis.com` |
| `EXPERIMENT_JOB_NAME` | `agentic-kg-experiment-staging` |

## What happens on one execution

1. The API generates a unique experiment id and uploads `script.py` and
   `inputs.json` to
   `gs://vt-gcp-00042-agentic-kg-experiments/staging/<experiment-id>/`.
2. It calls `projects/vt-gcp-00042/locations/us-central1/jobs/agentic-kg-experiment-staging:run`
   with `containerOverrides.args = [gs://.../script.py]` and a small env
   override (`EXPERIMENT_ID`, `EXPERIMENT_RESULT_URI`, `EXPERIMENT_TIMEOUT_S`).
3. The Job downloads the script into `/tmp`, runs it with the experiment image,
   and writes `stdout.txt`, `stderr.txt`, `metrics.json` and `result.json` back
   to the same prefix.
4. The API polls the execution until `completionTime` or the per-run deadline,
   then reads the four result objects.
5. `EvaluationResult.execution_provenance` records `backend`, `experiment_id`,
   `result_uri`, `code_hash`, `image_digest`, `started_at` and `finished_at`.

Objects are deleted by the bucket's 30-day lifecycle rule.

## Isolation model

The Job is deliberately boring and constrained:

- dedicated runtime service account `agentic-kg-experiment-staging`; no
  project-level roles and no Secret Manager access. The only non-storage grant
  is `roles/compute.networkUser` scoped to the experiment subnet, which Direct
  VPC egress requires;
- GCS permissions are bucket-only `objectViewer` + `objectCreator`, further
  conditioned to the `staging/` prefix;
- dedicated VPC `agentic-kg-experiments-staging` with `ALL_TRAFFIC` Direct VPC
  egress. It has no peering to the application network, so Neo4j is not
  reachable;
- egress firewall denies `0.0.0.0/0`; only TCP/443 to
  `199.36.153.4/30` and `199.36.153.8/30` (Private Google APIs) is allowed;
- the runner forces the GCS client to `https://private.googleapis.com`, so it
  uses the allowed VIP instead of a public GCS address;
- the image is `docker/Dockerfile.experiment`: non-root UID 10001, no
  agentic-kg application code, no Neo4j driver, no Docker client, no secrets;
- Cloud Run's container filesystem is read-only except `/tmp`.

The Job has no path to the API service either because both are separate
identities and the VPC has no route to the API's network.

## Check a run

From an authenticated operator shell (not an agent):

```bash
# Recent executions and their state
gcloud run jobs executions list \
  --job agentic-kg-experiment-staging \
  --region us-central1 \
  --project vt-gcp-00042 \
  --limit 10

# One execution's terminal condition and counts
gcloud run jobs executions describe <execution-name> \
  --region us-central1 --project vt-gcp-00042 \
  --format='value(status.conditions,status.succeededCount,status.failedCount)'

# Artifacts for an experiment id from the API response
gcloud storage ls -r \
  gs://vt-gcp-00042-agentic-kg-experiments/staging/<experiment-id>/

gcloud storage cat \
  gs://vt-gcp-00042-agentic-kg-experiments/staging/<experiment-id>/stderr.txt
```

`EvaluationResult.execution_provenance.result_uri` is the exact prefix; use it
rather than reconstructing the id by hand.

## Troubleshooting

### `docker package required` or `Failed to connect to Docker`

The API is still on the Docker backend. Check that Terraform applied the
staging API env (`EXPERIMENT_BACKEND=cloudrun_job`) and that the service was
rolled after the apply.

### `EXPERIMENT_GCS_BUCKET is required`

`EXPERIMENT_BACKEND=cloudrun_job` was set without the bucket. Terraform sets
both together when `experiment_bucket` is non-empty in `envs/staging.tfvars`.

### `403` on `:run` or `executions.get`

The API service account is missing the job-scoped
`roles/run.jobsExecutorWithOverrides` or `roles/run.invoker`. Terraform owns
those bindings; re-apply rather than adding a project-wide role.

### `403` uploading or reading GCS objects

The object name must start with the configured prefix (`staging/`). The IAM
condition is a prefix condition, not a project role. Check that
`EXPERIMENT_GCS_PREFIX` matches the Terraform value.

### Job execution fails with a GCS/network error

Check the firewall rules first. The only permitted egress destinations are the
Private Google APIs VIPs on TCP/443. If the client endpoint was changed away
from `https://private.googleapis.com`, DNS may resolve a public address that the
deny rule drops.

### `timed_out` in the API result

The API's poll deadline elapsed. Inspect the execution and the objects under
`result_uri`; the Job keeps running up to `experiment_job_timeout` (300s in
staging) and may still write outputs.

### `image_digest` is null

Provenance records the digest only when the backend knows it. The Job template
uses the mutable `experiment:latest` tag unless `EXPERIMENT_IMAGE_DIGEST` is
set, so a null is an honest "not known", not a fabricated digest.

## Deploying the experiment image

`.github/workflows/deploy-master.yml` builds `docker/Dockerfile.experiment` when
the runner or Dockerfile changes and rolls
`agentic-kg-experiment-staging` if Terraform has created the Job. If the Job
does not exist yet, the step logs that Terraform owns creation and exits
successfully; after the Terraform apply, the Job template already points at
`:latest`, which the same build pushed.

To build/roll manually from an authenticated operator shell:

```bash
REGISTRY=us-central1-docker.pkg.dev/vt-gcp-00042/agentic-kg
gcloud auth configure-docker us-central1-docker.pkg.dev --quiet
docker build -f docker/Dockerfile.experiment -t "$REGISTRY/experiment:latest" .
docker push "$REGISTRY/experiment:latest"
gcloud run jobs update agentic-kg-experiment-staging \
  --image="$REGISTRY/experiment:latest" \
  --region=us-central1 --project=vt-gcp-00042 \
  --update-labels=environment=staging
```

Terraform remains the owner of env, IAM, VPC and scaling; manual runs must not
pass `--set-env-vars`, `--set-secrets` or resource flags.

## Related

- [ADR-0008 — Cloud Run Job execution backend](../../llm/governance/adr/0008-cloud-run-job-execution-backend.md)
- [Deploy paths](deploy-runbook.html)
- [Neo4j hardening](neo4j-hardening-runbook.html)
