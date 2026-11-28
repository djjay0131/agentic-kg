---
title: Deploy paths (automatic vs manual)
parent: Operations
nav_order: 5
---

# Deploy runbook — one automatic path, manual Cloud Build as escape hatch

**Applies to:** the staging stack in GCP project `vt-gcp-00042`.

## Approval environments

Routine staging deploys and privileged infra/secret changes use different
GitHub environments (ADR-0014):

| Operation | GitHub environment | Required reviewer |
|---|---|---|
| `deploy-master.yml`, `deploy-branch.yml`, `nightly-run.yml`, `gcp-diagnose.yml` | `staging` | no |
| `terraform.yml` **apply**, `roll-vendor-keys.yml`, `rotate-api-key.yml`, `rotate-neo4j-password.yml` | `staging-infra` | yes |
| `deploy-tag.yml` | `production` | yes |

A routine staging deploy therefore proceeds once CI is green; an infrastructure
apply or credential rotation waits for the owner. Full matrix and owner setup:
[Deploy approvals (who approves what)](deploy-approvals.html).

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

## Serial staging deploys (newest commit wins)

Two merges to `master` a few seconds apart used to race. On 2026-11-27 the
Deploy Master runs for `#157` and `#158` started 13s apart, the older commit's
deploy finished last, and staging ended on `3f67f44` while the newer run failed
its drift check (`Drift on api-staging: 3f67f44 != a4dc270`).

Staging rolls are now **strictly serial and newest-wins**:

- `deploy-master.yml` declares a workflow-level `concurrency` group named
  `deploy-staging` with `cancel-in-progress: false`. A queued run waits for the
  in-flight run; a cancellation can never leave a half-rolled environment.
  With GitHub's default single queue, a newer pending run also replaces an older
  pending one.
- `deploy-branch.yml` uses the same group (`deploy-<environment>`, so
  `deploy-staging`) for its `deploy` job. A manual branch deploy to staging is
  therefore serialised with master deploys; `dev` gets its own group.
- Before building, the `staging-head-guard` job runs
  `scripts/staging_deploy_guard.sh`. If `origin/master` has moved past this
  commit **in a path that triggers deploy-master** (`packages/**`, `docker/**`,
  `pyproject.toml`, `.github/workflows/deploy-*.yml`), the run sets
  `superseded=true` and both `build` and `deploy-staging` are skipped, so the
  newest commit's run is the one that rolls staging. Skipping the build matters
  too: an older build pushing `latest` after a newer deploy would otherwise
  regress the image tag.
- A **docs-only** commit after this one does not supersede the deploy, because
  such a commit has no Deploy Master run of its own; skipping would strand
  staging on an older image. This is why the guard diffs the deploy paths
  instead of simply testing `github.sha == origin/master`.
- A superseded run finishes with the guard and deploy jobs skipped and its
  downstream jobs (`post-deploy-ui-tests`, `integration-test`,
  `update-manifest`) skipped by their `needs`. It is never a failure.

The structural contract is pinned by
`packages/core/tests/test_deploy_workflow_concurrency.py`, which also exercises
the guard against a temporary git repository (current tip, docs-only advance,
newer deployable commit, workflow change).

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
