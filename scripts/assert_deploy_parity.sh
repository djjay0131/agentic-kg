#!/usr/bin/env bash
# Post-deploy verification: assert Cloud Run reports the SHA we just deployed.
#
# Only targets flagged via CHECK_* are inspected, because deploy-master.yml
# deploys changed services only — an unchanged target legitimately keeps an
# older commit label and must NOT fail the check.
#
# Usage:
#   EXPECTED_SHA=<sha> REGION=<region> \
#     CHECK_API=true CHECK_UI=true CHECK_JOB=true \
#     ./scripts/assert_deploy_parity.sh [--check-version]
#
# Exit 0 if every checked target's commit label == EXPECTED_SHA.
# Exit 1 naming the first drifted target.
#
# gcloud/curl/jq are invoked as plain commands so tests can shadow them via
# PATH. Keep them that way — do not inline auth or hardcode paths.
set -euo pipefail

: "${EXPECTED_SHA:?EXPECTED_SHA required}"
: "${REGION:?REGION required}"

CHECK_API="${CHECK_API:-false}"
CHECK_UI="${CHECK_UI:-false}"
CHECK_JOB="${CHECK_JOB:-false}"

CHECK_VERSION=false
[ "${1:-}" = "--check-version" ] && CHECK_VERSION=true

fail() { echo "Drift on $1: '$2' != '$EXPECTED_SHA'" >&2; exit 1; }

service_commit() {
  gcloud run services describe "agentic-kg-$1" --region="$REGION" \
    --format='value(spec.template.metadata.labels.commit)'
}

job_commit() {
  gcloud run jobs describe "agentic-kg-$1" --region="$REGION" \
    --format='value(metadata.labels.commit)'
}

service_url() {
  gcloud run services describe "agentic-kg-$1" --region="$REGION" \
    --format='value(status.url)'
}

checked=0

if [ "$CHECK_API" = "true" ]; then
  d=$(service_commit api-staging)
  [ "$d" = "$EXPECTED_SHA" ] || fail "api-staging" "$d"
  checked=$((checked + 1))
fi

if [ "$CHECK_UI" = "true" ]; then
  d=$(service_commit ui-staging)
  [ "$d" = "$EXPECTED_SHA" ] || fail "ui-staging" "$d"
  checked=$((checked + 1))
fi

if [ "$CHECK_JOB" = "true" ]; then
  d=$(job_commit ingest-staging)
  [ "$d" = "$EXPECTED_SHA" ] || fail "ingest-staging" "$d"
  checked=$((checked + 1))
fi

# PR-3 enables this by passing --check-version once /version exists.
if [ "$CHECK_VERSION" = "true" ] && [ "$CHECK_API" = "true" ]; then
  url=$(service_url api-staging)
  got=$(curl -sf "$url/version" | jq -r '.commit_sha')
  [ "$got" = "$EXPECTED_SHA" ] || fail "api-staging/version" "$got"
fi

if [ "$checked" -eq 0 ]; then
  echo "No targets selected for verification (nothing changed?)."
  exit 0
fi

echo "✓ SHA parity: $checked target(s) match $EXPECTED_SHA"
