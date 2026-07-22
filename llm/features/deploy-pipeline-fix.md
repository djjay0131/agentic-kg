# Feature: Deploy Pipeline Fix + Version Pinning

**Status:** IMPLEMENTED (PR-1 of 3) — PR-1 (recovery) DONE 2026-07-14: Deploy Master green, ingest-Job deploy, `/version`, AC-6 SHA-parity verified. Remaining: PR-2 (TF lifecycle + AC-8 lint), PR-3 (version pinning). NOTE: the "root cause: missing `staging` env" framing below was later corrected — the real startup blocker was a reusable-workflow `id-token` permissions gap (see `activeContext.md`).
**Date:** 2026-07-12
**Author:** Feature Architect (AI-assisted)
**Review:** Dual-persona adversarial review complete (2026-07-12) — 4 Tech Lead + 4 QA/Ops questions resolved. See "Review Log" at the end.

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

**Filter breadth: intentionally broad (QA Q4).** The `job` filter matches all of `packages/core/**`, even though `core` is also imported by the API — so a core change that only affects API code still rebuilds + redeploys the ingest Job (~2 min, a functionally-identical new revision). This over-triggering is deliberate: the Job *is* built from `core`, so any core change *could* affect it, and the filter never *under*-triggers (never ships a stale Job). Scoping the filter to ingestion subpaths (`ingestion/`, `extraction/`, `job_runner`) was rejected — it would require knowing the Job's exact dependency closure across shared `core` utils, which drifts over time, and a miss silently ships stale ingest code — precisely the bug class this whole spec exists to eliminate. A stale Job is a *correctness* failure; a redundant 2-min rebuild is *efficiency* noise. If Job-rebuild time ever becomes a real bottleneck, revisit with a digest-compare skip (build broadly, skip `gcloud run jobs update` when the new image digest equals the deployed one) rather than by narrowing the filter.

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

**HCL image invariant (paired with `ignore_changes`).** `ignore_changes` silences `terraform plan` diffs on the image, which means a *hardcoded stale SHA* in HCL would never surface as drift and future readers would get a wrong answer from `main.tf`. To keep HCL honest, the image string in `main.tf` MUST reference a floating tag (`:latest` or the sentinel `:managed-by-gha`), never a specific commit SHA. A CI lint enforces this — see AC-8. Semantics: HCL declares "this Job runs *the current promoted image*"; the workflow decides which SHA that is.

### 6. Version pinning (`/version` endpoint, UI badge, Job SHA log)

Docker build injects the commit SHA and build timestamp via `ARG`:

```dockerfile
# All three Dockerfiles: docker/Dockerfile.{api,ui,job}
ARG BUILD_SHA=dev
ARG COMMIT_TIME=dev
ARG BUILD_TIME=dev
ENV BUILD_SHA=$BUILD_SHA COMMIT_TIME=$COMMIT_TIME BUILD_TIME=$BUILD_TIME
```

`build-images.yml` passes them (note: `BUILD_TIME` is the workflow's wall-clock at Docker-build time, distinct from `COMMIT_TIME` which is when the commit was authored):

```yaml
- name: Capture Docker build timestamp
  id: build_ts
  run: echo "value=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> $GITHUB_OUTPUT

- uses: docker/build-push-action@v5
  with:
    build-args: |
      BUILD_SHA=${{ github.sha }}
      COMMIT_TIME=${{ github.event.head_commit.timestamp }}
      BUILD_TIME=${{ steps.build_ts.outputs.value }}
```

**Why both timestamps:** `COMMIT_TIME` is reproducible (same SHA → same value across rebuilds) and answers *"when was this code written?"* `BUILD_TIME` is unique per build and answers *"when was this specific image baked?"* — useful for debugging cache-poisoning fixes, correlating with Trivy scan timestamps, or spotting when an image sat in Artifact Registry for a week before being deployed. Both cost one extra ENV + one extra JSON field.

**API** — new endpoint + enriched `/health`:

```python
# packages/api/src/agentic_kg_api/version.py
import os
__version__ = "0.1.0"
BUILD_SHA = os.environ.get("BUILD_SHA", "dev")
COMMIT_TIME = os.environ.get("COMMIT_TIME", "dev")
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
        "commit_time": COMMIT_TIME,   # when the commit was authored (reproducible)
        "build_time": BUILD_TIME,     # when the Docker image was baked (unique per build)
        "commit_url": commit_url(),
    }

# /health also returns commit_sha (backward compatible — new field)
```

**UI framework confirmed: Next.js** (verified 2026-07-12 — `packages/ui/` has `next.config.js` with `output: 'standalone'`, `.tsx` source, tailwind; `docker/Dockerfile.ui` builds `node:20-alpine` + `CMD ["node", "server.js"]` on port 3000). The `.tsx` / `NEXT_PUBLIC_*` badge below is the correct implementation — the UI is NOT Streamlit despite the `--port=8501` in the deploy workflows.

**UI port cleanup (PR-1, low-risk).** Both `deploy-master.yml:135` and `deploy-branch.yml` deploy the UI with `--port=8501` — Streamlit's default port, a copy-paste artifact from before the UI was Next.js. It happens to *work* on Cloud Run (Cloud Run injects `PORT=8501`, overriding the image's `ENV PORT=3000`, and Next.js standalone honors `process.env.PORT`), but it contradicts `Dockerfile.ui`'s own `EXPOSE 3000` / `ENV PORT=3000` / HEALTHCHECK-on-3000 and is a latent footgun (any future path-based Cloud Run startup probe, or a CMD that hardcodes the port, breaks on the mismatch). Fix `--port=8501` → `--port=3000` in both workflows as PR-1 cleanup. Not a recovery blocker — the current value serves — so if it introduces any risk it can be dropped from PR-1 without affecting the startup_failure fix.

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

**Browser-cache behavior (QA Q3).** The baked SHA is NOT a stale-cache risk: Next.js content-hashes bundle filenames (`main-[contenthash].js`), so changing `NEXT_PUBLIC_BUILD_SHA` changes the bundle content → changes the hash → changes the filename → the browser fetches the new file (new URL). The only theoretical staleness is the HTML entry document pinning old bundle filenames — but Next.js serves the SSR HTML with revalidating cache headers by default, and no CDN currently sits in front of Cloud Run, so a page reload picks up the new SHA. **Decision: rely on Next.js defaults; document the caveat.** If a CDN is ever added in front of the UI, revisit with an explicit `Cache-Control: no-cache` on the HTML route. Runtime-fetching the SHA from `/version` was rejected — it trades a non-problem (the baked SHA is correct) for a real new failure mode (badge blank whenever `/version` is unreachable).

**Job** — log SHA at run start:

```python
# packages/core/src/agentic_kg/job_runner.py, top of main()
build_sha = os.environ.get("BUILD_SHA", "dev")
commit_time = os.environ.get("COMMIT_TIME", "dev")
build_time = os.environ.get("BUILD_TIME", "dev")
logger.info(
    "agentic-kg ingest job starting",
    extra={"commit_sha": build_sha, "commit_time": commit_time, "build_time": build_time},
)
```

### 7. Post-deploy verification — extracted to a testable script

The verify logic lives in `scripts/assert_deploy_parity.sh`, NOT inline in the YAML. Rationale (QA Q1): bash-in-YAML is untestable, and this logic gates every deploy — it must have unit coverage. The script reads the expected SHA + target list from arguments/env and is invoked by the workflow. `gcloud`/`curl` are called through small wrapper functions so tests can stub them.

```bash
#!/usr/bin/env bash
# scripts/assert_deploy_parity.sh
# Usage: EXPECTED_SHA=<sha> REGION=<region> ./assert_deploy_parity.sh [--check-version]
# Exits 0 if every target's commit label == EXPECTED_SHA; exits 1 naming the first drifted target.
set -euo pipefail

: "${EXPECTED_SHA:?EXPECTED_SHA required}"
: "${REGION:?REGION required}"

# Indirection points so tests can override with stubs.
svc_label()  { gcloud run services describe "agentic-kg-$1" --region="$REGION" --format='value(spec.template.metadata.labels.commit)'; }
job_label()  { gcloud run jobs describe "agentic-kg-$1" --region="$REGION" --format='value(metadata.labels.commit)'; }
svc_url()    { gcloud run services describe "agentic-kg-$1" --region="$REGION" --format='value(status.url)'; }
fetch_ver()  { curl -sf "$1/version"; }

fail() { echo "Drift on $1: '$2' != '$EXPECTED_SHA'" >&2; exit 1; }

for svc in api-staging ui-staging; do
  d=$(svc_label "$svc"); [ "$d" = "$EXPECTED_SHA" ] || fail "$svc" "$d"
done
d=$(job_label ingest-staging); [ "$d" = "$EXPECTED_SHA" ] || fail "ingest-staging" "$d"

# --check-version added in PR-3 once /version exists; PR-1 runs label-only.
if [ "${1:-}" = "--check-version" ]; then
  url=$(svc_url api-staging)
  echo "$(fetch_ver "$url")" | jq -e ".commit_sha == \"$EXPECTED_SHA\"" >/dev/null \
    || fail "api-staging/version" "$(fetch_ver "$url" | jq -r .commit_sha)"
fi

echo "✓ SHA parity: all targets match $EXPECTED_SHA"
```

Workflow invocation:

```yaml
- name: Assert deployed SHA matches
  env:
    EXPECTED_SHA: ${{ github.sha }}
    REGION: ${{ env.REGION }}
  run: ./scripts/assert_deploy_parity.sh   # PR-3 appends: --check-version
```

**Testing (satisfies AC-14):** `tests/test_assert_deploy_parity.py` (or a bats suite — decision at implementation, pytest preferred for consistency with `scripts/smoke_assert.py`) invokes the script in a subprocess with the `gcloud`/`curl`/`jq` wrappers shadowed by stub functions on `PATH`. Cases: all-match → exit 0; api drift → exit 1 with `Drift on api-staging`; job drift → exit 1 with `Drift on ingest-staging`; `--check-version` mismatch → exit 1 with `Drift on api-staging/version`. No cloud calls in the test.

### 8. Smoke-test the two never-run workflows

`deploy-branch.yml` and `deploy-tag.yml` exist but have zero run history. Once WIF is set up, trigger each via `workflow_dispatch` to prove they don't have latent `startup_failure` bugs of their own. Any failures uncovered → fix in this same PR.

**`deploy-branch.yml` smoke-test target — use the `dev` environment, NOT `staging` (QA Q2a).** `deploy-branch.yml`'s env-suffix logic maps `environment: staging` → the `-staging` suffix, i.e. it deploys to `agentic-kg-api-staging` / `agentic-kg-ui-staging` — **the exact same services `deploy-master.yml` owns.** Running the AC-15 smoke-test against `staging` would overwrite the master-deployed revision with a *branch* image mid-PR — a self-inflicted clobber. Instead run it with `environment: dev`, which deploys to isolated `agentic-kg-api-dev` / `-ui-dev` services (created on first deploy, scale-to-zero, ~$0 idle). No collision with staging.

**Latent breakage this surfaces (QA Q2b):** `deploy-branch.yml` still references `worker` (`services` choice options, parse step, `Deploy Worker service` step, `needs.build.outputs.worker_image`). Deleting `docker/Dockerfile.worker` and renaming `worker`→`job` in `build-images.yml` (change #4) **breaks `deploy-branch.yml`'s worker path** — the `worker_image` output vanishes. Therefore PR-1 MUST also rename `worker`→`job` in `deploy-branch.yml` (choice option, parse output, deploy step, image ref) or the AC-15 smoke-test fails. This is minimal-parity (option X): only the rename forced by the Dockerfile deletion. **Explicitly NOT in scope:** bringing `deploy-branch.yml` to full deploy-master parity (ingest-Job deploy, version-pinning ARGs, verify step). It stays a manual branch-testing tool; full parity is a follow-up if it ever matters (see Open Question 5).

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

### `BUILD_SHA` / `COMMIT_TIME` / `BUILD_TIME` env vars missing on Cloud Run
- **Scenario:** Docker builds don't inject the ARGs, or Cloud Run scrubs env vars during deploy
- **Behavior:** `/version` returns `"commit_sha": "dev"`, `"commit_time": "dev"`, `"build_time": "dev"`, `commit_url: ""`. UI shows `v0.1.0 · dev` with a `#` link (not broken, clearly identifies the drift)
- **Test:** Unit test that `version()` handles empty-string and unset values for all three vars independently without crashing

### Ingest Job update succeeds but service deploy fails mid-workflow
- **Scenario:** Job image is updated (step 1 of 3), then API service deploy fails
- **Behavior:** Verification step catches SHA mismatch on the failed service and exits non-zero; workflow surface shows red. Job is now on the new SHA, API is on the old SHA — inconsistent state
- **Test:** Documented in the spec; recovery = manual re-run of `deploy-master.yml` via `workflow_dispatch` after fixing the failure. This spec does NOT attempt atomic multi-target deploys (that's a rollback-tooling feature).

### First deploy after Terraform lifecycle change
- **Scenario:** Operator adds `ignore_changes` and switches the HCL image ref to `:latest`
- **Behavior:** `terraform apply` shows no diff on the image (correctly). State file records `:latest` until next `terraform refresh` picks up the currently-deployed SHA. Cosmetic drift only — no functional issue, and AC-8 Part B prevents the anti-pattern of hardcoding SHAs in HCL to "fix" this.
- **Test:** `terraform plan` after workflow deploys must show zero diffs on the Job resource; AC-8 Part B lint must reject any HCL image ref containing a SHA.

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
- **Then** the SA has `roles/run.admin`, `roles/artifactregistry.writer`, `roles/iam.serviceAccountUser` — no more, no less
- **Note (tracked as backlog P-8):** `roles/run.admin` is broader than strictly needed — it grants create/delete on any Cloud Run service or job in the project. A tighter binding (`roles/run.developer` + resource-level `roles/run.invoker` on the 3 known targets) is deferred to `P-8: tighten deploy SA to least-privilege` to keep PR-1 focused on recovery. The current scope is documented as *knowingly-over-privileged*, not *unaudited*.

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

### AC-8: Terraform lifecycle prevents revert AND HCL cannot silently lie about deployed image
Two paired assertions — the `ignore_changes` block plus a lint that keeps HCL honest about it.

- **Part A — `ignore_changes` is present and correct.**
  - **Given** the `google_cloud_run_v2_job.ingest` resource has been updated with a `lifecycle` block
  - **When** `terraform plan` runs in `infra/` right after a `deploy-master.yml` run has updated the Job image
  - **Then** the plan shows zero changes on the resource's `template[0].template[0].containers[0].image` field

- **Part B — HCL image ref MUST NOT be a specific commit SHA.**
  - **Given** `ignore_changes` silences drift detection on the image, so a hardcoded stale SHA in HCL would silently misrepresent what's deployed
  - **When** a CI lint step (in `.github/workflows/tf-lint.yml` or added to the existing Terraform validate job) inspects the `image =` value on `google_cloud_run_v2_job.ingest` in `infra/main.tf`
  - **Then** the value MUST match `:latest$` OR `:managed-by-gha$` (floating tag or sentinel); the lint FAILS the check if the value matches the regex `:[a-f0-9]{7,40}$` (a specific SHA). Failure message: `"main.tf hardcodes image SHA; use :latest — the workflow, not HCL, owns the deployed SHA (see deploy-pipeline-fix.md#design-approach)"`.

**Failure mode this catches** (from the TL review): six months from now, someone edits `main.tf` to hardcode a stale SHA thinking they're documenting reality. `terraform plan` shows zero diffs because of `ignore_changes`, so the mistake is invisible in review. Part B fails at CI-lint time, forcing them back to the floating tag.

### AC-9: `/version` endpoint returns commit SHA + both timestamps
- **Given** the API is deployed with `BUILD_SHA`, `COMMIT_TIME`, `BUILD_TIME` injected
- **When** `curl <api-url>/version` runs
- **Then** response JSON contains: `commit_sha` matching the deployed SHA; `commit_short` (7 chars); `commit_time` (ISO-8601, matches `github.event.head_commit.timestamp` — reproducible across rebuilds); `build_time` (ISO-8601, wall-clock at Docker build — unique per build, distinct from `commit_time` on any rebuild); `commit_url` pointing to GitHub

### AC-10: `/health` also returns commit SHA (non-breaking)
- **Given** the API is deployed
- **When** `curl <api-url>/health` runs
- **Then** response includes the existing `version` and `status` fields PLUS a new `commit_sha` field; no existing fields removed or renamed

### AC-11: UI footer shows version badge linking to GitHub
- **Given** the UI is deployed with `NEXT_PUBLIC_BUILD_SHA` baked in
- **When** any UI page is loaded
- **Then** the footer contains a link with text `v0.1.0 · <7-char SHA>` whose `href` is `https://github.com/djjay0131/agentic-kg/commit/<full SHA>`, opens in a new tab

### AC-12: Ingest Job logs SHA + both timestamps at start
- **Given** the Cloud Run Job runs
- **When** `gcloud run jobs executions logs` (or Cloud Logging query) is inspected
- **Then** the first log line includes `commit_sha=<sha>`, `commit_time=<iso timestamp>`, and `build_time=<iso timestamp>` as structured fields

### AC-13: Docker build ARGs are wired end-to-end
- **Given** a `build-images.yml` run
- **When** the resulting image is inspected (`docker inspect --format '{{.Config.Env}}' <image>`)
- **Then** `BUILD_SHA=<sha>`, `COMMIT_TIME=<iso timestamp>`, and `BUILD_TIME=<iso timestamp>` appear in env vars; SHA matches `${GITHUB_SHA}`; `COMMIT_TIME` matches `github.event.head_commit.timestamp`; `BUILD_TIME` is within 15 minutes of the workflow's `run_started_at`

### AC-14: Post-deploy verification catches SHA drift (unit-tested via extracted script)
- **Given** `scripts/assert_deploy_parity.sh` is invoked with `EXPECTED_SHA` set and the `gcloud`/`curl`/`jq` wrappers stubbed to return a mismatched SHA for one target
- **When** the test suite runs the script in a subprocess
- **Then** it exits non-zero with a message naming the drifted target (`Drift on api-staging: 'oldsha' != 'newsha'`); an all-match run exits 0 with `✓ SHA parity`; the `--check-version` path (PR-3) is covered by a stubbed `/version` mismatch case
- **Note:** the verify logic is a standalone script (not inline YAML) specifically so this AC can be a real unit test with no cloud calls. The workflow step just invokes the script.

### AC-15: `deploy-branch.yml` runs green at least once, against `dev` (not staging)
- **Given** WIF setup complete AND `deploy-branch.yml` has had its `worker`→`job` references renamed (forced by the `Dockerfile.worker` deletion in change #4)
- **When** a `workflow_dispatch` on `deploy-branch.yml` is triggered with `environment: dev`
- **Then** the workflow completes green, deploying to `agentic-kg-api-dev` / `-ui-dev` (isolated from staging — no clobber of master's deploy); it does NOT reference any `worker` image or the deleted `Dockerfile.worker`; NOT `startup_failure`; any real failures fixed in this same PR

### AC-16: `deploy-tag.yml` is worker→job-renamed and YAML-valid (live run deferred)
`deploy-tag.yml` is a **production** deploy — `environment: production`, deploys unsuffixed `agentic-kg-api`/`agentic-kg-ui`, and triggers ONLY on a `v*.*.*` tag push (no `workflow_dispatch`). Running it green means a real production release, which contradicts this spec's staging-only Non-Goal. Decision (2026-07-12): **do NOT trigger a live prod run in PR-1.**
- **Given** the `worker`→`job` rename (forced by the `Dockerfile.worker` deletion) is applied to `deploy-tag.yml`
- **When** the file is parsed (`yaml.safe_load`) and grepped for `worker` / deleted-Dockerfile references
- **Then** it parses cleanly, references `Dockerfile.job` (not `.worker`), uses `job_image` outputs, and deploys the UI on `--port=3000`
- **Deferred:** an actual green `deploy-tag.yml` run is deferred to production bring-up (backlog **P-1**), where a real `v0.x.x` tag release will exercise it end-to-end. Not a PR-1 gate.

### AC-17: `deployment-manifest.yaml` updates on green deploy
- **Given** the workflow's existing `update-manifest` job runs after a green deploy
- **When** the commit lands on master
- **Then** `deploy/deployment-manifest.yaml` exists and its `commit` field matches `${GITHUB_SHA}`; `deployed_at` is within 10 minutes of `now`

### AC-18: `dev` fallback for local builds
- **Given** a developer runs the API locally without `BUILD_SHA`, `COMMIT_TIME`, or `BUILD_TIME` set
- **When** `curl localhost:8000/version` runs
- **Then** response returns `commit_sha: "dev"`, `commit_short: "dev"`, `commit_time: "dev"`, `build_time: "dev"`, `commit_url: ""` — no crash, no error, clearly identifies non-deployed state

## Technical Notes

- **Affected components**:
  - `.github/workflows/deploy-master.yml` — add job deploy step + verify step (invokes `scripts/assert_deploy_parity.sh`), add `job` output to changes filter
  - `scripts/assert_deploy_parity.sh` — new; post-deploy SHA-parity check, `gcloud`/`curl` behind stubable wrappers
  - `tests/test_assert_deploy_parity.py` — new; subprocess-invokes the script with stubbed cloud calls (AC-14 coverage)
  - `.github/workflows/build-images.yml` — rename `worker` → `job`, add `build-args`, add `job_image` output
  - `.github/workflows/deploy-branch.yml` — `worker`→`job` rename (REQUIRED by `Dockerfile.worker` deletion); smoke-tested against `dev` env. NOT brought to full deploy-master parity (no ingest-Job deploy, no version ARGs) — minimal change only
  - `.github/workflows/deploy-tag.yml` — verify runs; rename `worker`→`job` if it references the deleted Dockerfile
  - `infra/main.tf` — add `lifecycle` block on `google_cloud_run_v2_job.ingest`
  - `docker/Dockerfile.api`, `docker/Dockerfile.ui`, `docker/Dockerfile.job` — add `ARG BUILD_SHA` + `ARG BUILD_TIME` + `ENV`
  - `docker/Dockerfile.worker` — deleted
  - `packages/api/src/agentic_kg_api/version.py` — new module (holds `BUILD_SHA`, `COMMIT_TIME`, `BUILD_TIME`, `commit_url()`)
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
- **Config**: four new env vars (`BUILD_SHA`, `COMMIT_TIME`, `BUILD_TIME`, `NEXT_PUBLIC_BUILD_SHA`). All optional at runtime with `"dev"` fallback.

## Delivery — PR Sequence

This spec ships as **three PRs** in strict order to minimize blast radius. Each PR is independently mergeable and deployable; each unblocks the next.

### PR-1 — Recovery: unblock automated deploys (changes #1, #2, #3, #4)

**Goal:** first green `Deploy Master` run since 2026-05-19; API + UI + ingest Job all updated to the pushed SHA.

**Scope:**
- GCP: WIF pool + `gh-deploy` SA + IAM (change #1)
- GitHub: `staging` environment + secrets + variable (change #2)
- `deploy-master.yml`: add ingest-Job deploy step + verify step invoking `scripts/assert_deploy_parity.sh` (label-only, no `--check-version` yet); ship the script + its unit test in PR-1 (change #3)
- `build-images.yml` + `docker/Dockerfile.worker`: `worker`→`job` rename + delete orphan (change #4, folded in because #3 needs `job_image` output)
- `deploy-branch.yml`: `worker`→`job` rename (choice option, parse output, deploy step, image ref) — REQUIRED because deleting `Dockerfile.worker` breaks its worker path; smoke-tested via `environment: dev` (AC-15)
- UI port cleanup: `--port=8501` → `--port=3000` in `deploy-master.yml` + `deploy-branch.yml` (Streamlit-leftover port; works today but contradicts `Dockerfile.ui`). Low-risk; droppable if it complicates the recovery.

**ACs delivered:** AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-14 (partial — SHA drift detected via `gcloud describe` label parity, not `/version`), AC-15, AC-16 (YAML-validation only; live prod run deferred to P-1), AC-17

**Explicitly NOT in PR-1:** Terraform lifecycle changes, `/version` endpoint, UI badge, Job SHA logging, Docker ARGs, AC-8 lint. The recovery ships as pure workflow/GCP work — no application-code changes, no Next.js risk.

**Risk hedge:** verify step in PR-1 uses `gcloud run services describe --format='value(...labels.commit)'` (existing label set by current deploy step). It does not depend on any code the app doesn't already have.

### PR-2 — Terraform safety (change #5 + AC-8 lint)

**Goal:** `terraform apply` on unrelated infra changes cannot silently un-deploy the Job.

**Scope:**
- `infra/main.tf`: `lifecycle { ignore_changes = [image] }` on `google_cloud_run_v2_job.ingest`
- Flip the HCL image string to `:latest` (or `:managed-by-gha` sentinel — decision at implementation time)
- CI lint: reject any SHA-shaped image string in `main.tf` on the Job resource (AC-8 Part B)

**ACs delivered:** AC-8 (Parts A + B)

**Depends on:** PR-1 merged and one successful deploy on master (proves the workflow-driven pin actually reaches the Job, so `ignore_changes` isn't silencing a broken workflow).

### PR-3 — Version pinning (change #6)

**Goal:** operator can answer "what's live?" without gcloud.

**Scope:**
- Docker: `ARG BUILD_SHA` + `ARG BUILD_TIME` + `ENV` in all three Dockerfiles
- `build-images.yml`: `build-args` passing `github.sha` + `head_commit.timestamp`
- API: `version.py` module, `/version` endpoint, enriched `/health` with `commit_sha`
- UI: `VersionBadge` component + footer mount + `NEXT_PUBLIC_BUILD_SHA` in `build-args`
- Job: SHA logging at `main()` start
- `deploy-master.yml` verify step: extend to also `curl /version | jq` (upgrade AC-14 from label-parity to full parity)

**ACs delivered:** AC-9, AC-10, AC-11, AC-12, AC-13, AC-14 (upgraded), AC-18

**Depends on:** PR-1 merged (needs the working deploy pipeline to actually observe the badge in staging).

### Cross-PR invariants

- Each PR merges to master, deploys via the (now-working) pipeline, and is validated in staging before the next PR opens.
- If PR-2 or PR-3 uncovers a workflow bug, fix it in that PR (do not reopen PR-1).
- Total elapsed time expectation: PR-1 the day of the reboot; PR-2 within a day (small, mostly `.tf`); PR-3 the largest, ~2–3 days including UI testing.

## Dependencies

- **Operator access** to the `vt-gcp-00042` GCP project (Owner or IAM Admin) — required for WIF setup (steps 1-2)
- **Operator access** to the GitHub repo Settings (Admin) — required to create the `staging` environment and set secrets/variables
- **Existing Cloud Run infra** (Terraform-managed) — assumed live and healthy; this spec modifies the Job resource but doesn't re-provision it
- **Existing `cloudbuild.yaml`** — assumed intact; this spec does NOT modify it (it remains the manual-deploy escape hatch)

## Open Questions

1. **Trivy scan tightening** — should the workflow gate deploys on `--exit-code 1` for CRITICAL CVEs, or leave as warn-only (`--exit-code 0`)? Needs its own research turn on false-positive rates and CVE lifecycle. **Deferred to a follow-up spec.**
2. **Cloud Run Services lifecycle guardrail** — this spec adds `ignore_changes` + the HCL-invariant lint on the Job only. Should Services get the same treatment (both the `ignore_changes` and the paired lint from AC-8 Part B) now or wait until they hit the same footgun? Recommend deferring; the Services haven't been an issue yet.
3. **UI e2e test for the version badge** — a Playwright/browser test would prove the badge renders and the link is well-formed. Adds a full browser-test dependency this codebase doesn't currently have. Recommend a component-level React Testing Library test (already available), not a browser test.
4. **Rollback tooling** — not in this spec. If a bad SHA lands, operators use `cloudbuild.yaml` manually. Follow-up feature: `deploy-master.yml` with a `--rollback-to <sha>` input.
5. **`deploy-branch.yml` / `deploy-tag.yml` full parity** — this spec only renames `worker`→`job` in them (forced by the Dockerfile deletion). They do NOT get the ingest-Job deploy step, version-pinning ARGs, or the verify step that `deploy-master.yml` gains. They remain minimal manual-testing tools. If branch/tag deploys ever need to exercise the ingest Job or show version badges, file a parity follow-up. **Deferred — not needed for the recovery.**

## Review Log

Dual-persona adversarial review, 2026-07-12. Eight questions (4 Skeptical Tech Lead, 4 Quality/Ops), each resolved with the user and folded into the spec above.

| # | Persona | Question | Resolution |
|---|---------|----------|------------|
| TL-1 | Tech Lead | AC-8's `terraform plan` zero-diff only proves `ignore_changes` is configured, not that HCL matches reality — a hardcoded stale SHA in HCL would never surface as drift. | **Stronger AC.** AC-8 split into Part A (`terraform plan` zero-diff) + Part B (CI lint rejecting any SHA-shaped image string in `main.tf`; HCL must use `:latest`/`:managed-by-gha`). Design §5 gained the "HCL image invariant" note. |
| TL-2 | Tech Lead | Six changes bundled into one PR — a version-pinning bug would gate the 2-month-overdue deploy recovery. | **Three PRs.** PR-1 Recovery (no app code) → PR-2 Terraform safety → PR-3 Version pinning. New "Delivery — PR Sequence" section; each PR's ACs enumerated. |
| TL-3 | Tech Lead | `roles/run.admin` on the deploy SA is broad (create/delete any Cloud Run resource). | **Ship broad, track tightening.** AC-2 flags it as knowingly-over-privileged; **P-8** filed in BACKLOG to tighten to `run.developer` + resource-level bindings after PR-3. |
| TL-4 | Tech Lead | `BUILD_TIME` conflates commit-authored time with image-build time. | **Both.** Split into `COMMIT_TIME` (reproducible, from commit) + `BUILD_TIME` (wall-clock at Docker build). `/version` returns both; AC-9/12/13/18 updated. 4 env vars now. |
| QA-1 | Quality/Ops | AC-14 (verify catches SHA drift) is untestable as inline YAML. | **Extract to script.** `scripts/assert_deploy_parity.sh` with `gcloud`/`curl`/`jq` behind stubable wrappers; `tests/test_assert_deploy_parity.py` unit-tests it with no cloud calls. Both ship in PR-1. |
| QA-2 | Quality/Ops | `deploy-branch.yml`'s `staging` env deploys to the SAME services `deploy-master` owns — AC-15's smoke-test would clobber master's staging deploy. | **`dev` env + minimal rename.** AC-15 runs against `environment: dev` (isolated `-dev` services). Surfaced latent bug: deleting `Dockerfile.worker` breaks `deploy-branch`'s worker path → PR-1 must rename `worker`→`job` there too. Full parity deferred (Open Q5). |
| QA-3 | Quality/Ops | UI badge `NEXT_PUBLIC_BUILD_SHA` baked at build time — stale browser cache after deploy? | **Rely on Next.js defaults.** Content-hashed bundle filenames mean a SHA change → new filename → refetch; no CDN in front, so no HTML-caching risk. Verified UI is **Next.js** (not Streamlit); caught `--port=8501` Streamlit-leftover in both workflows → PR-1 cleanup to `3000`. |
| QA-4 | Quality/Ops | Job changes-filter (`packages/core/**`) over-triggers — core is shared with API, so API-only changes rebuild the Job. | **Keep broad.** Over-triggering (~2 min) is efficiency noise; under-triggering ships a stale Job — the exact bug this spec kills. Design §3 documents the intent; digest-compare skip noted as the future optimization if rebuild time ever matters. |

**Net changes from review:** AC count 18 → 19 (AC-8 became A+B); 2 new files in PR-1 (`assert_deploy_parity.sh` + its test); 1 new env var (`COMMIT_TIME`); 1 new backlog item (P-8); 2 latent bugs surfaced and scoped into PR-1 (`deploy-branch` worker reference, UI `--port=8501`); 1 new Open Question (Q5, branch/tag parity). No change to the core six-part design.
