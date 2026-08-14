#!/usr/bin/env bash
#
# Deploy the hello-world Cloud Run service.
#
# Its only job is to prove the platform works: a container builds, deploys, receives a public
# URL and answers. Nothing in the product depends on it.
#
# Cost posture matches every other service in the project: min-instances 0, max-instances 2,
# CPU throttled outside requests. A public URL that cannot be made to spend money is the
# difference between a hosted demo and an open wallet.

set -euo pipefail

SERVICE="drawbridge-hello"
PROJECT_ID="${PROJECT_ID:?PROJECT_ID must be set}"
REGION="${REGION:-us-central1}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '[hello-run] %s\n' "$*"; }

log "deploying ${SERVICE} to ${PROJECT_ID}/${REGION} from ${SOURCE_DIR}"

gcloud run deploy "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --source="${SOURCE_DIR}" \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=2 \
  --cpu-throttling \
  --set-env-vars="REGION=${REGION}" \
  --quiet

URL="$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"

log "deployed: ${URL}"
log "health check:"
curl -fsS "${URL}/healthz" && printf '\n'

log "record this URL in the capability report"
