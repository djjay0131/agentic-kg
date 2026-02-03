#!/usr/bin/env bash
# Build and push a Docker image, then update the Cloud Run service.
#
# Usage: ./infra/deploy.sh <env> <service>
#   env: staging | prod
#   service: api
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV="${1:?Usage: $0 <env> <service>}"
SERVICE="${2:?Usage: $0 <env> <service> (api)}"
TFVARS="${SCRIPT_DIR}/envs/${ENV}.tfvars"

if [ ! -f "$TFVARS" ]; then
    echo "ERROR: ${TFVARS} not found"
    exit 1
fi

# Parse project and region from tfvars
PROJECT=$(grep 'project_id' "$TFVARS" | sed 's/.*= *"\(.*\)"/\1/')
REGION=$(grep 'region' "$TFVARS" | head -1 | sed 's/.*= *"\(.*\)"/\1/')
COMMIT=$(git -C "$ROOT_DIR" rev-parse --short HEAD)
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/agentic-kg/${SERVICE}:${COMMIT}"
IMAGE_LATEST="${REGION}-docker.pkg.dev/${PROJECT}/agentic-kg/${SERVICE}:latest"

echo "============================================"
echo "  Build & Deploy: ${SERVICE} → ${ENV}"
echo "  Image:  ${IMAGE}"
echo "  Commit: ${COMMIT}"
echo "============================================"

# Build via Cloud Build (just the image, no deploy step)
cd "$ROOT_DIR"
gcloud builds submit \
    --project="$PROJECT" \
    --tag="$IMAGE" \
    --quiet

# Tag as latest
gcloud artifacts docker tags add "$IMAGE" "$IMAGE_LATEST" --quiet 2>/dev/null || true

# Update Cloud Run service
SERVICE_NAME="agentic-kg-${SERVICE}-${ENV}"
echo ""
echo "==> Updating Cloud Run service ${SERVICE_NAME}..."
gcloud run services update "$SERVICE_NAME" \
    --image="$IMAGE" \
    --region="$REGION" \
    --project="$PROJECT" \
    --quiet

URL=$(gcloud run services describe "$SERVICE_NAME" \
    --region="$REGION" --project="$PROJECT" \
    --format="get(status.url)")

echo ""
echo "============================================"
echo "  Deploy complete"
echo "  URL: ${URL}"
echo "============================================"
