# Feature: Deploy Pipeline Fix + Version Pinning

**Status:** SPECIFIED
**Date:** 2026-07-12
**Author:** Feature Architect (AI-assisted)

## Problem

Every push to `master` since 2026-05-19 has failed at workflow startup with `startup_failure` on `Deploy Master`. Root cause: the `deploy-staging` job in `.github/workflows/deploy-master.yml` declares `environment: staging`, but the `staging` GitHub environment was never created on the repository (only `github-pages` exists). GitHub Actions rejects the run before any step executes — no logs, no image build, no `gcloud run deploy`.

As a result, **no code has been auto-deployed to Cloud Run for ~2 months**. Every entity-expansion feature (E-3 Model, E-4 Method, E-5 Citations, E-6 Descriptions, E-7 Cross-entity normalization, E-8 V1+V2 extractors, entity-pipeline-orchestration, ci-smoke-test-ingestion) is not running in the staging Cloud Run Job. The ingestion pipeline in staging still extracts Problems only.

Compounding this, the `deploy-master.yml` workflow only touches Cloud Run **Services** (`api-staging`, `ui-staging`) and never updates the `agentic-kg-ingest-staging` Cloud Run **Job** — which is what actually runs ingestion. Even after fixing the startup failure, the Job would keep running stale code because the workflow ignores it.

Compounding *that*, there is no way to look at the running app and know what commit is deployed. `/health` returns a hardcoded `"0.1.0"` (unchanged since Sprint 04). Cloud Run has `--labels=commit=<sha>` set at deploy but nothing exposes it via an endpoint or the UI. Operators must SSH-equivalent (`gcloud describe`) to answer the question "what's live?"

Concrete failure scenarios this blocks:
- Running a larger ingestion to validate the E-1..E-8 arc against real papers — impossible while ingest runs pre-E-3 code
- Human review of ResearchConcept / Model / Method nodes in the UI — impossible because those extractors never fire in staging
- Testing the review-queue action items — impossible because the review UI needs current API code and there's no visibility into whether it's current

## Goals

- **First successful automated deploy since May.** A commit to `master` triggers `Deploy Master`, runs green end-to-end, and updates all three deployment targets (API, UI, ingest Job) to the pushed SHA.
- **Verifiable version pinning.** From a browser: `curl <api-url>/version` returns the deployed commit SHA; the UI footer shows `v0.1.0 · abc1234` linking to that commit on GitHub. Nobody has to run `gcloud` to answer "what's deployed."
- **Terraform-safe.** The next `terraform apply` after a deploy does NOT revert the Job image.
- **All three deploy workflows smoke-tested at least once.** `deploy-branch.yml` and `deploy-tag.yml` have never been run — they get one triggered dry-run each before this ships.

## Non-Goals

- **Production environment.** This ships staging-only, matching the current codebase's naming (`*-staging`). A separate spec covers prod deploy.
- **CI test suite improvements.** The workflow's existing `test` job stays as-is; if it currently passes, it continues to pass.
- **Trivy vulnerability scan tightening.** Currently `--exit-code 0` (warn-only) in `build-images.yml`. Deferred as an Open Question — needs its own research turn on false-positive rates and CVE lifecycle before we gate deploys on it.
- **Rollback tooling.** If a deploy is bad, operators re-deploy the previous SHA manually via `cloudbuild.yaml`. A proper rollback workflow is a separate feature.
- **Rewriting `cloudbuild.yaml`.** It stays as the manual-deploy escape hatch. This spec makes the automated path work; the manual path continues to work in parallel.

## User Stories

- As a developer, I want every merged PR to master to auto-deploy to staging, so I can validate my change against real infrastructure without manual `gcloud builds submit`.
- As an operator, I want to open the UI and read the deployed commit SHA in the footer, so I can answer "is my fix live?" in one glance without gcloud.
- As a reviewer running a larger ingestion, I want the Cloud Run Job to run the same code as master, so entity extraction actually populates Topics/Concepts/Models/Methods/Citations end-to-end.
- As a developer running `terraform apply` for an unrelated infra change, I want Terraform not to silently revert the Job image, so my infra change doesn't un-deploy the code.

## Design Approach

Six coordinated changes, sequenced so each is independently verifiable:

### 1. GCP-side Workload Identity Federation (one-time operator setup)

Chosen over long-lived service-account JSON keys because:
- The workflow YAML already references WIF (`google-github-actions/auth@v2` with `workload_identity_provider`). Switching to keys would mean editing more code, not less.
- WIF is the current GCP best practice; no long-lived credential in GitHub Secrets.
- Rotation is automatic (short-lived OIDC tokens per workflow run).

The setup creates a workload-identity pool bound to this specific repository (attribute condition on `assertion.repository`), a `gh-deploy` service account with `roles/run.admin`, `roles/artifactregistry.writer`, `roles/iam.serviceAccountUser`, and grants the WIF principal `iam.workloadIdentityUser` on that SA.

### 2. GitHub-side wiring (one-time operator setup)

- Create the missing `staging` GitHub environment (`gh api repos/... /environments/staging -X PUT`). No protection rules — matches current intent (no reviewer configured, no wait timer needed).
- Set three items: `secrets.GCP_WORKLOAD_IDENTITY_PROVIDER`, `secrets.GCP_SERVICE_ACCOUNT`, `vars.GCP_PROJECT_ID` (the workflow already reads them).

Alternative considered: **delete the `environment: staging` line** from the workflow. Rejected — the user wants the environment gate available for future features (required reviewers, environment-scoped secrets). The gate itself isn't the bug; the missing environment is.

### 3. `deploy-master.yml` — add ingest-Job deploy step

The workflow currently deploys `api-staging` and `ui-staging` Services but ignores `agentic-kg-ingest-staging` (Job). Add a step that runs after `build` completes:

```yaml
- name: Deploy ingest Job to staging
  if: needs.changes.outputs.job == 'true' || needs.changes.outputs.core == 'true'
  run: |
    gcloud run jobs update agentic-kg-ingest-staging \
      --image=${{ needs.build.outputs.job_image }} \
      --region=${{ env.REGION }} \
      --labels=environment=staging,commit=${{ github.sha }}
```

The `changes` job's `paths-filter` gets a new `job` output tracking `packages/core/**`, `packages/api/src/agentic_kg_api/job_runner.py` (if it moves there — currently in core), and `docker/Dockerfile.job`.

### 4. `worker` → `job` naming reconciliation

`build-images.yml` builds a `worker` image using `docker/Dockerfile.worker`. `cloudbuild.yaml` uses `_SERVICE=job` with `docker/Dockerfile.job`. Terraform names the resource `ingest`. This is confusing and `Dockerfile.worker` is orphaned January legacy (untouched since 2026-01-20; nothing references it).

- Rename all `worker` → `job` in `build-images.yml` (input parsing, meta outputs, build step, Trivy scan step)
- Delete `docker/Dockerfile.worker` (orphaned)
- Add `job_image` output on `build-images.yml`
- Update `deploy-master.yml` `changes` job to include a `job` filter output

### 5. `infra/main.tf` — Terraform lifecycle guardrail

Without this, `terraform apply` reverts the Job image to whatever's in HCL, silently un-deploying the code the workflow just shipped.

```hcl
resource "google_cloud_run_v2_job" "ingest" {
  # ...existing config unchanged...
  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
    ]
  }
}
```

Only the image is ignored — env vars, secrets, and resource limits stay Terraform-managed. Same pattern should later apply to the Cloud Run Services (`api`, `ui`), but scope-limited to the Job here since it's the active bug.

### 6. Version pinning (`/version` endpoint, UI badge, Job SHA log)

Docker build injects the commit SHA and build timestamp via `ARG`:

```dockerfile
# All three Dockerfiles: docker/Dockerfile.{api,ui,job}
ARG BUILD_SHA=dev
ARG BUILD_TIME=dev
ENV BUILD_SHA=$BUILD_SHA BUILD_TIME=$BUILD_TIME
```

`build-images.yml` passes them:

```yaml
- uses: docker/build-push-action@v5
  with:
    build-args: |
      BUILD_SHA=${{ github.sha }}
      BUILD_TIME=${{ github.event.head_commit.timestamp }}
```

**API** — new endpoint + enriched `/health`:

```python
# packages/api/src/agentic_kg_api/version.py
import os
__version__ = "0.1.0"
BUILD_SHA = os.environ.get("BUILD_SHA", "dev")
BUILD_TIME = os.environ.get("BUILD_TIME", "dev")

def commit_url() -> str:
    if BUILD_SHA == "dev":
        return ""
    return f"https://github.com/djjay0131/agentic-kg/commit/{BUILD_SHA}"

# packages/api/src/agentic_kg_api/main.py
@app.get("/version", tags=["health"])
def version():
    return {
        "version": __version__,
        "commit_sha": BUILD_SHA,
        "commit_short": BUILD_SHA[:7] if BUILD_SHA != "dev" else "dev",
        "build_time": BUILD_TIME,
        "commit_url": commit_url(),
    }

# /health also returns commit_sha (backward compatible — new field)
```

**UI** — footer badge:

```tsx
// packages/ui/src/components/VersionBadge.tsx
const sha = process.env.NEXT_PUBLIC_BUILD_SHA ?? 'dev'
const isDev = sha === 'dev'
export function VersionBadge() {
  return (
    <a
      href={isDev ? '#' : `https://github.com/djjay0131/agentic-kg/commit/${sha}`}
      className="text-xs text-muted-foreground"
      target={isDev ? undefined : '_blank'}
      rel={isDev ? undefined : 'noopener noreferrer'}
    >
      v0.1.0 · {sha.slice(0, 7)}
    </a>
  )
}
```

`NEXT_PUBLIC_BUILD_SHA` is compiled in at Docker build time via `build-args` (Next.js public env vars are baked into the bundle). Mounted in the Next.js app layout footer.

**Job** — log SHA at run start:

```python
# packages/core/src/agentic_kg/job_runner.py, top of main()
build_sha = os.environ.get("BUILD_SHA", "dev")
build_time = os.environ.get("BUILD_TIME", "dev")
logger.info(
    "agentic-kg ingest job starting",
    extra={"commit_sha": build_sha, "build_time": build_time},
)
```

### 7. Post-deploy verification step

The workflow asserts what it thinks it deployed matches what Cloud Run actually reports:

```yaml
- name: Assert deployed SHA matches
  run: |
    set -e
    for svc in api-staging ui-staging; do
      DEPLOYED=$(gcloud run services describe agentic-kg-${svc} --region=${REGION} \
        --format='value(spec.template.metadata.labels.commit)')
      [ "$DEPLOYED" = "${{ github.sha }}" ] || { echo "Drift on ${svc}: '$DEPLOYED' != '${{ github.sha }}'"; exit 1; }
    done
    JOB_SHA=$(gcloud run jobs describe agentic-kg-ingest-staging --region=${REGION} \
      --format='value(metadata.labels.commit)')
    [ "$JOB_SHA" = "${{ github.sha }}" ] || { echo "Job drift: '$JOB_SHA' != '${{ github.sha }}'"; exit 1; }
    API_URL=$(gcloud run services describe agentic-kg-api-staging --region=${REGION} --format='value(status.url)')
    curl -sf "${API_URL}/version" | jq -e ".commit_sha == \"${{ github.sha }}\"" > /dev/null
    echo "✓ SHA parity: services + job + /version all match ${{ github.sha }}"
```

### 8. Smoke-test the two never-run workflows

`deploy-branch.yml` and `deploy-tag.yml` exist but have zero run history. Once WIF is set up, trigger each via `workflow_dispatch` with a benign no-op branch/tag to prove they don't have latent `startup_failure` bugs of their own. Any failures uncovered → fix in this same PR.

## Sample Implementation

See "Design Approach" — the sample code sits inline in each numbered section rather than duplicated here.

Core sequencing (what has to ship together to work):

```
GCP setup (WIF pool, SA, IAM)        ← operator, one-time, prerequisite
  └─► GitHub setup (env, secrets)     ← operator, one-time, prerequisite
       └─► Terraform lifecycle change ← code, ship first (prevents revert once deploys work)
            └─► build-images.yml worker→job rename + build-args
                 └─► deploy-master.yml ingest-Job step + verify step
                      └─► Dockerfile ARG injections
                           └─► API /version + /health enrichment
                                └─► UI VersionBadge component + footer mount
                                     └─► Job SHA logging
                                          └─► Smoke-test deploy-branch + deploy-tag
                                               └─► First real master push validates end-to-end
```

## Edge Cases & Error Handling

### WIF token exchange fails
- **Scenario:** WIF pool exists but attribute condition rejects the token (repo name mismatch, org typo)
- **Behavior:** `google-github-actions/auth@v2` fails with a specific error; workflow fails at auth step (not silent startup_failure)
- **Test:** After WIF setup, run `deploy-master.yml` once via `workflow_dispatch`; if it fails at auth, error message identifies the mismatched attribute

### `BUILD_SHA` env var missing on Cloud Run
- **Scenario:** Docker builds don't inject the ARG, or Cloud Run scrubs env vars during deploy
- **Behavior:** `/version` returns `"commit_sha": "dev"`, `commit_url: ""`. UI shows `v0.1.0 · dev` with a `#` link (not broken, clearly identifies the drift)
- **Test:** Unit test that `version()` handles `BUILD_SHA=""` and `BUILD_SHA=None` without crashing

### Ingest Job update succeeds but service deploy fails mid-workflow
- **Scenario:** Job image is updated (step 1 of 3), then API service deploy fails
- **Behavior:** Verification step catches SHA mismatch on the failed service and exits non-zero; workflow surface shows red. Job is now on the new SHA, API is on the old SHA — inconsistent state
- **Test:** Documented in the spec; recovery = manual re-run of `deploy-master.yml` via `workflow_dispatch` after fixing the failure. This spec does NOT attempt atomic multi-target deploys (that's a rollback-tooling feature).

### First deploy after Terraform lifecycle change
- **Scenario:** Operator adds `ignore_changes`, then a stale HCL image reference exists
- **Behavior:** `terraform apply` shows no diff on the image (correctly). But the state file still records the old image string until the next `terraform refresh`. Cosmetic drift only — no functional issue.
- **Test:** `terraform plan` after workflow deploys must show zero diffs on the Job resource.

### `git rev-parse HEAD` inside container returns unexpected value
- **Scenario:** Someone tries `git rev-parse` at Docker runtime instead of using `BUILD_SHA` env var
- **Behavior:** We don't do this. `BUILD_SHA` is baked at build time only. Runtime containers have no `.git`.
- **Test:** No `git rev-parse` in application code; verified by grep in the implementation phase.

### Fork PR triggers `deploy-branch.yml`
- **Scenario:** External contributor opens PR from a fork; WIF secret is unavailable
- **Behavior:** Auth step fails cryptically. Accepted limitation — matches ci-smoke-test-ingestion precedent. Documented in the workflow YAML as a `# NOTE:` comment.
- **Test:** N/A — no unit test; accepted limitation.

### `/version` endpoint added but not registered
- **Scenario:** Implementation forgets `app.include_router` equivalent (though this is a direct `@app.get` on `main.py`)
- **Behavior:** `curl /version` returns 404; verification step fails post-deploy
- **Test:** API integration test hits `/version` and asserts JSON schema (commit_sha, version, commit_url keys present)

## Acceptance Criteria

### AC-1: WIF pool exists and is repo-scoped
- **Given** the operator has run the setup script
- **When** `gcloud iam workload-identity-pools providers describe github-master --workload-identity-pool=github --location=global` runs
- **Then** the provider's `attributeCondition` contains `assertion.repository=='djjay0131/agentic-kg'` (repo-scoped, not org-wide)

### AC-2: Service account has the exact required roles
- **Given** WIF setup complete
- **When** `gcloud projects get-iam-policy vt-gcp-00042 --flatten='bindings[].members' --filter='bindings.members:serviceAccount:gh-deploy@vt-gcp-00042.iam.gserviceaccount.com'` runs
- **Then** the SA has `roles/run.admin`, `roles/artifactregistry.writer`, `roles/iam.serviceAccountUser` — no more (least privilege), no less (deploys need each of these)

### AC-3: GitHub `staging` environment exists
- **Given** the operator has run `gh api repos/djjay0131/agentic-kg/environments/staging -X PUT`
- **When** `gh api repos/djjay0131/agentic-kg/environments` runs
- **Then** the response includes both `github-pages` and `staging`

### AC-4: Required GitHub secrets and variable are set
- **Given** the operator has run `gh secret set` / `gh variable set` for all three
- **When** `gh secret list` and `gh variable list` run
- **Then** `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT` appear as secrets; `GCP_PROJECT_ID` appears as a variable

### AC-5: `deploy-master.yml` starts successfully
- **Given** all setup complete
- **When** a commit is pushed to `master`
- **Then** the `Deploy Master` workflow reaches the `deploy-staging` job (no `startup_failure`); prior failure mode is gone

### AC-6: All three services deployed with matching SHA
- **Given** `deploy-master.yml` completes green
- **When** `gcloud run services describe agentic-kg-api-staging` and `agentic-kg-ui-staging` and `gcloud run jobs describe agentic-kg-ingest-staging` are inspected
- **Then** each resource's `commit` label equals `${GITHUB_SHA}` of the triggering commit

### AC-7: `Dockerfile.worker` deleted; `worker` renamed to `job`
- **Given** the rename is complete
- **When** `find docker/ -name 'Dockerfile.worker'` and `grep -rn 'worker' .github/workflows/` run
- **Then** no `Dockerfile.worker` exists; no `worker` references remain in workflow files (except historical comments explicitly marked as such); `job_image` output exists on `build-images.yml`

### AC-8: Terraform lifecycle prevents revert
- **Given** a `deploy-master.yml` run has just updated the Job image
- **When** `terraform plan` runs in `infra/`
- **Then** the plan shows zero changes on the `google_cloud_run_v2_job.ingest` resource's image field (or the entire resource, if lifecycle covers the whole thing)

### AC-9: `/version` endpoint returns commit SHA
- **Given** the API is deployed with `BUILD_SHA` injected
- **When** `curl <api-url>/version` runs
- **Then** response JSON contains `commit_sha` matching the deployed SHA, `commit_short` (7 chars), `build_time`, `commit_url` pointing to GitHub

### AC-10: `/health` also returns commit SHA (non-breaking)
- **Given** the API is deployed
- **When** `curl <api-url>/health` runs
- **Then** response includes the existing `version` and `status` fields PLUS a new `commit_sha` field; no existing fields removed or renamed

### AC-11: UI footer shows version badge linking to GitHub
- **Given** the UI is deployed with `NEXT_PUBLIC_BUILD_SHA` baked in
- **When** any UI page is loaded
- **Then** the footer contains a link with text `v0.1.0 · <7-char SHA>` whose `href` is `https://github.com/djjay0131/agentic-kg/commit/<full SHA>`, opens in a new tab

### AC-12: Ingest Job logs SHA at start
- **Given** the Cloud Run Job runs
- **When** `gcloud run jobs executions logs` (or Cloud Logging query) is inspected
- **Then** the first log line includes `commit_sha=<sha>` and `build_time=<iso timestamp>` as structured fields

### AC-13: Docker build ARGs are wired end-to-end
- **Given** a `build-images.yml` run
- **When** the resulting image is inspected (`docker inspect --format '{{.Config.Env}}' <image>`)
- **Then** `BUILD_SHA=<sha>` and `BUILD_TIME=<iso timestamp>` appear in env vars; SHA matches `${GITHUB_SHA}`

### AC-14: Post-deploy verification step catches SHA drift
- **Given** the deploy step ran but Cloud Run reported a different SHA (simulated by editing the label)
- **When** the verification step runs
- **Then** it exits non-zero with a message identifying which target drifted (`Drift on api-staging: 'oldsha' != 'newsha'`)

### AC-15: `deploy-branch.yml` runs green at least once
- **Given** WIF setup complete
- **When** a `workflow_dispatch` on `deploy-branch.yml` is triggered (or a push to a non-master branch matching its trigger)
- **Then** the workflow completes (green OR a substantive failure, NOT `startup_failure`); any real failures fixed in this same PR

### AC-16: `deploy-tag.yml` runs green at least once
- **Given** WIF setup complete
- **When** a `workflow_dispatch` on `deploy-tag.yml` is triggered (or a real tag push)
- **Then** the workflow completes (green OR a substantive failure, NOT `startup_failure`); any real failures fixed in this same PR

### AC-17: `deployment-manifest.yaml` updates on green deploy
- **Given** the workflow's existing `update-manifest` job runs after a green deploy
- **When** the commit lands on master
- **Then** `deploy/deployment-manifest.yaml` exists and its `commit` field matches `${GITHUB_SHA}`; `deployed_at` is within 10 minutes of `now`

### AC-18: `dev` fallback for local builds
- **Given** a developer runs the API locally without `BUILD_SHA` set
- **When** `curl localhost:8000/version` runs
- **Then** response returns `commit_sha: "dev"`, `commit_short: "dev"`, `commit_url: ""` — no crash, no error, clearly identifies non-deployed state

## Technical Notes

- **Affected components**:
  - `.github/workflows/deploy-master.yml` — add job deploy step + verify step, add `job` output to changes filter
  - `.github/workflows/build-images.yml` — rename `worker` → `job`, add `build-args`, add `job_image` output
  - `.github/workflows/deploy-branch.yml`, `deploy-tag.yml` — verify runs; likely need `job` support parity
  - `infra/main.tf` — add `lifecycle` block on `google_cloud_run_v2_job.ingest`
  - `docker/Dockerfile.api`, `docker/Dockerfile.ui`, `docker/Dockerfile.job` — add `ARG BUILD_SHA` + `ARG BUILD_TIME` + `ENV`
  - `docker/Dockerfile.worker` — deleted
  - `packages/api/src/agentic_kg_api/version.py` — new module (holds `BUILD_SHA`, `BUILD_TIME`, `commit_url()`)
  - `packages/api/src/agentic_kg_api/main.py` — `/version` endpoint, enriched `/health`
  - `packages/api/src/agentic_kg_api/schemas.py` — add `commit_sha: str` to `HealthResponse`
  - `packages/api/tests/` — unit tests on `/version` + `/health` + dev fallback
  - `packages/core/src/agentic_kg/job_runner.py` — SHA logging at `main()` start
  - `packages/ui/src/components/VersionBadge.tsx` — new component
  - `packages/ui/src/app/layout.tsx` (or equivalent) — mount `<VersionBadge />` in footer
  - `packages/ui/src/components/__tests__/VersionBadge.test.tsx` — dev fallback + prod SHA rendering
- **Patterns to follow**:
  - Star: `.github/workflows/smoke-ingest.yml` for concurrency + workflow_dispatch structure
  - Star: `packages/api/src/agentic_kg_api/routers/reviews.py` for FastAPI endpoint style
  - Star: existing `HealthResponse` schema for how to add a field non-breakingly
- **Data model changes**: None. `HealthResponse` gets a new optional field but that's schema, not persistence.
- **Config**: three new env vars (`BUILD_SHA`, `BUILD_TIME`, `NEXT_PUBLIC_BUILD_SHA`). All optional at runtime with `"dev"` fallback.

## Dependencies

- **Operator access** to the `vt-gcp-00042` GCP project (Owner or IAM Admin) — required for WIF setup (steps 1-2)
- **Operator access** to the GitHub repo Settings (Admin) — required to create the `staging` environment and set secrets/variables
- **Existing Cloud Run infra** (Terraform-managed) — assumed live and healthy; this spec modifies the Job resource but doesn't re-provision it
- **Existing `cloudbuild.yaml`** — assumed intact; this spec does NOT modify it (it remains the manual-deploy escape hatch)

## Open Questions

1. **Trivy scan tightening** — should the workflow gate deploys on `--exit-code 1` for CRITICAL CVEs, or leave as warn-only (`--exit-code 0`)? Needs its own research turn on false-positive rates and CVE lifecycle. **Deferred to a follow-up spec.**
2. **Cloud Run Services lifecycle guardrail** — this spec adds `ignore_changes` on the Job only. Should Services get the same treatment now or wait until they hit the same footgun? Recommend deferring; the Services haven't been an issue yet.
3. **UI e2e test for the version badge** — a Playwright/browser test would prove the badge renders and the link is well-formed. Adds a full browser-test dependency this codebase doesn't currently have. Recommend a component-level React Testing Library test (already available), not a browser test.
4. **Rollback tooling** — not in this spec. If a bad SHA lands, operators use `cloudbuild.yaml` manually. Follow-up feature: `deploy-master.yml` with a `--rollback-to <sha>` input.
