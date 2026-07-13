#!/usr/bin/env bash
# One-time operator bootstrap for the GitHub Actions -> GCP deploy path.
#
# Creates the Workload Identity Federation (WIF) pool + provider scoped to THIS
# repo, a `gh-deploy` service account with the roles the deploy workflows need,
# binds the WIF principal to impersonate it, then wires the three GitHub
# secrets/vars the workflows read. Satisfies AC-1..AC-4 of deploy-pipeline-fix.
#
# Requires: gcloud (authed as an Owner/IAM-Admin on the project) and gh (authed
# with repo admin). Safe to re-run — every create is guarded by an existence
# check, and role bindings are idempotent.
#
# Usage:
#   ./scripts/setup_wif_deploy.sh            # do it
#   DRY_RUN=1 ./scripts/setup_wif_deploy.sh  # print what it would do
set -euo pipefail

# ---- Config (edit if your project/repo differ) -----------------------------
PROJECT_ID="${PROJECT_ID:-vt-gcp-00042}"
REPO="${REPO:-djjay0131/agentic-kg}"          # owner/name
POOL="${POOL:-github}"
PROVIDER="${PROVIDER:-github-master}"
SA_NAME="${SA_NAME:-gh-deploy}"
GITHUB_ISSUER="https://token.actions.githubusercontent.com"

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DRY_RUN="${DRY_RUN:-}"

run() {
  if [ -n "$DRY_RUN" ]; then
    printf '  [dry-run] %s\n' "$*"
  else
    echo "  + $*"
    "$@"
  fi
}

echo "==> Project: ${PROJECT_ID}   Repo: ${REPO}"

# ---- 0. Enable required APIs ------------------------------------------------
echo "==> Enabling required APIs"
run gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  --project="${PROJECT_ID}"

# ---- 1. Workload Identity Pool ---------------------------------------------
echo "==> Workload Identity Pool: ${POOL}"
if gcloud iam workload-identity-pools describe "${POOL}" \
    --project="${PROJECT_ID}" --location=global >/dev/null 2>&1; then
  echo "  = pool exists, skipping create"
else
  run gcloud iam workload-identity-pools create "${POOL}" \
    --project="${PROJECT_ID}" --location=global \
    --display-name="GitHub Actions"
fi

# ---- 2. OIDC Provider (repo-scoped) ----------------------------------------
echo "==> WIF Provider: ${PROVIDER} (scoped to repo ${REPO})"
if gcloud iam workload-identity-pools providers describe "${PROVIDER}" \
    --project="${PROJECT_ID}" --location=global \
    --workload-identity-pool="${POOL}" >/dev/null 2>&1; then
  echo "  = provider exists, skipping create"
else
  run gcloud iam workload-identity-pools providers create-oidc "${PROVIDER}" \
    --project="${PROJECT_ID}" --location=global \
    --workload-identity-pool="${POOL}" \
    --display-name="GitHub master" \
    --issuer-uri="${GITHUB_ISSUER}" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition="assertion.repository == '${REPO}'"
fi

# ---- 3. Service Account -----------------------------------------------------
echo "==> Service Account: ${SA_EMAIL}"
if gcloud iam service-accounts describe "${SA_EMAIL}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "  = service account exists, skipping create"
else
  run gcloud iam service-accounts create "${SA_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="GitHub Actions deployer"
fi

# ---- 4. Project roles on the SA (idempotent) --------------------------------
# NOTE: roles/run.admin is broader than strictly needed — see backlog P-8
# (tighten to run.developer + resource-level bindings after PR-3).
echo "==> Granting roles to ${SA_EMAIL}"
for ROLE in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  run gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None >/dev/null
  echo "  = ${ROLE}"
done

# ---- 5. Let the WIF principal (this repo) impersonate the SA -----------------
if [ -n "$DRY_RUN" ]; then
  PROJECT_NUMBER="PROJECT_NUMBER"   # placeholder; real run resolves it
else
  PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
fi
PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}"
echo "==> Binding workloadIdentityUser for ${REPO}"
run gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="${PRINCIPAL}" >/dev/null

# ---- 6. Values for GitHub ---------------------------------------------------
WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"

echo
echo "============================================================"
echo "GCP side complete. GitHub secret values:"
echo "  GCP_WORKLOAD_IDENTITY_PROVIDER = ${WIF_PROVIDER}"
echo "  GCP_SERVICE_ACCOUNT            = ${SA_EMAIL}"
echo "  GCP_PROJECT_ID (variable)      = ${PROJECT_ID}"
echo "============================================================"
echo

# ---- 7. GitHub environment + secrets + variable -----------------------------
echo "==> GitHub: staging environment + secrets + variable on ${REPO}"
run gh api "repos/${REPO}/environments/staging" -X PUT >/dev/null
run gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER --repo "${REPO}" --body "${WIF_PROVIDER}"
run gh secret set GCP_SERVICE_ACCOUNT --repo "${REPO}" --body "${SA_EMAIL}"
run gh variable set GCP_PROJECT_ID --repo "${REPO}" --body "${PROJECT_ID}"

echo
echo "==> Done. Verify with:"
echo "    gh api repos/${REPO}/environments        # expect github-pages + staging"
echo "    gh secret list --repo ${REPO}            # expect the two GCP_* secrets"
echo "    gh variable list --repo ${REPO}          # expect GCP_PROJECT_ID"
