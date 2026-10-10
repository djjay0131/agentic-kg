---
title: Deploy paths (automatic vs manual)
parent: Operations
nav_order: 5
---

# Deploy runbook — one automatic path, manual Cloud Build as escape hatch

**Applies to:** the staging stack in GCP project `vt-gcp-00042`.

## The rule

**GitHub Actions `.github/workflows/deploy-master.yml` is the only automatic
deploy path.** It rolls the Cloud Run containers with
`gcloud run services update --image=… --update-labels=…` (and
`gcloud run jobs update --image` for the ingest Job) and nothing else, so the
image and the `commit` label are the only attributes it owns.

**`cloudbuild.yaml` is manual-only.** It is the escape hatch an operator runs by
hand when they need to build and roll an image outside the merge flow. It must
never be wired to a push trigger.

| Path | Trigger | What it changes | Owner of the rest |
|---|---|---|---|
| `deploy-master.yml` | push to `master` | image + `commit`/`environment` labels | Terraform |
| `cloudbuild.yaml` (manual) | operator `gcloud builds submit` | image + `commit` label | Terraform |
| Terraform (`infra/`) | PR + dispatched apply | env, secrets, scaling, port, IAM, VPC | — |
| Cloud Build **triggers** | push (legacy, to be deleted) | *nothing now* — the repo-side guard makes them a fast no-op | — |

Env flags that Terraform owns and that a `deploy`-style command would have
dropped: `CANONICAL_API_ENABLED`, `KGCS_CANONICAL_NAMESPACE`, the `GCP_*` flags
and all scaling. See [`infra/README.md`](https://github.com/djjay0131/agentic-kg/blob/master/infra/README.md)
§Resource ownership (ADR-0006).

## The legacy triggers MUST be deleted (owner action)

Two **global** Cloud Build triggers were created 2026-02-04:

- `agentic-kg-api-staging`
- `agentic-kg-ui-staging`

They run as the **default compute service account**, use `cloudbuild.yaml`, and
fire on every push. Before 2026-10-09 `cloudbuild.yaml` ran `gcloud run deploy`,
so every fired trigger did a full `ReplaceService` and **clobbered staging** —
dropping the Terraform-declared env flags, scaling, and anything else
Terraform owns. The audit log shows the compute SA doing `ReplaceService`
immediately next to every GitHub deploy.

The triggers are **not** in Terraform state (staging sets
`enable_build_triggers = false`) and the Workload-Identity Federation CI service
account has **no Cloud Build permission**, so they cannot be disabled from CI.
**The repository owner deletes them by hand.** Do not delete them from an agent
session, and do **not** touch any `denario-*` triggers (a different app shares
the project).

**Console path:** Google Cloud Console → **Cloud Build → Triggers**, set the
region selector to **global**, select each trigger → **Delete**.

**Equivalent `gcloud` commands** (run by the owner, authenticated to the VT
project):

```bash
# Confirm what is there first (expect the two legacy triggers, and denario-*).
gcloud builds triggers list --project=vt-gcp-00042 --region=global

# Delete only the two legacy staging triggers.
gcloud builds triggers delete agentic-kg-api-staging --region=global --project=vt-gcp-00042
gcloud builds triggers delete agentic-kg-ui-staging  --region=global --project=vt-gcp-00042
```

Until they are deleted, `cloudbuild.yaml` neutralises them from the repo side: a
build that was started by a trigger (Cloud Build built-in substitution
`$TRIGGER_NAME` is non-empty) logs a line and exits 0 at the top of every step,
so the build finishes **SUCCESS in seconds** without building or pushing
anything. The guard is only skipped for a manual build (no `TRIGGER_NAME`) or
when `_ALLOW_TRIGGER_DEPLOY=true` is passed deliberately.

## Manual escape hatch

Build and roll a single service:

```bash
gcloud builds submit --config=cloudbuild.yaml \
  --project=vt-gcp-00042 \
  --substitutions=_SERVICE=api,COMMIT_SHA=$(git rev-parse HEAD)
```

`_SERVICE` is `api`, `ui` (build) or `job` (the ingest Cloud Run Job). The deploy
step rolls only the image and stamps `commit=<sha>` — the same shape as
`deploy-master.yml`. It never passes `--set-env-vars`, `--set-secrets`,
`--port`, `--memory`, `--min-instances`, `--max-instances`,
`--allow-unauthenticated` or `--platform`, so it cannot drop a Terraform-owned
attribute.

> **Do not pass `_ALLOW_TRIGGER_DEPLOY=true` while the legacy triggers still
> exist.** That override lets a trigger run the full build and deploy, which is
> exactly the clobber this guard prevents.

## Verification

- A triggered build (either legacy trigger) should appear green in seconds with
  a `cloudbuild.yaml is manual-only: build started by trigger …` log line and no
  image build, no push, no deploy.
- `infra/` resource ownership is unchanged after a manual run:
  `gcloud run services describe agentic-kg-api-staging --region=us-central1
  --format='value(spec.template.spec.containers[0].env[].name)'` still lists the
  Terraform-managed names.
- The structural contract is pinned by
  `packages/core/tests/test_cloudbuild_guard.py` (no `gcloud run deploy`; every
  step guarded; `_ALLOW_TRIGGER_DEPLOY` defaults to `"false"`).
