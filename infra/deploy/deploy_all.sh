#!/usr/bin/env bash
#
# Deploy the fleet: services to Cloud Run, the worker as a Cloud Run job, the console in front.
#
# Every service runs as its own service account, which is what makes the permission matrix
# something other than a document. A revision deployed with the default compute identity would
# hold project-wide datastore access and every collection-level row in that matrix would become
# a description of an intention.
#
# Cost posture is identical everywhere and not negotiable per service: min-instances 0,
# max-instances 4, CPU throttled outside a request. A public URL that cannot be made to spend
# money on tokens is the difference between a hosted product and an open wallet.
#
# Idempotent: re-running deploys new revisions and changes nothing else.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID must be set}"
REGION="${REGION:-us-central1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/drawbridge"
TAG="${TAG:-$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo latest)}"

log() { printf '[deploy] %s\n' "$*"; }
run() { if [ -n "${DRY_RUN:-}" ]; then printf '  %s\n' "$*"; else "$@"; fi; }

# --- Secrets these services must have, checked before anything is built --------------------
#
# Checked first because a half-deployed fleet is worse than an undeployed one, and because every
# one of these fails at runtime in a way that looks like a different problem.
REQUIRED_SECRETS=(
  drawbridge-session-secret     # signs console session cookies
  drawbridge-portal-secret      # signs vendor links
  drawbridge-approval-key       # the private half; sa-approvals only
)

log "project=${PROJECT_ID} region=${REGION} tag=${TAG}"

if [ -z "${DRY_RUN:-}" ]; then
  for secret in "${REQUIRED_SECRETS[@]}"; do
    if ! gcloud secrets describe "${secret}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
      echo "error: secret ${secret} does not exist. Create it before deploying:" >&2
      echo "  gcloud secrets create ${secret} --replication-policy=automatic" >&2
      exit 1
    fi
  done
fi

# --- Build ---------------------------------------------------------------------------------
#
# Two images. One Python image for every service and the worker, because they share `shared/`
# and must not drift onto different copies of it; one Node image for the console.

log "building the python image"
run gcloud builds submit "${REPO_ROOT}" \
  --project "${PROJECT_ID}" \
  --tag "${REGISTRY}/fleet:${TAG}" \
  --gcs-log-dir "gs://${PROJECT_ID}_cloudbuild/logs"

log "building the console image"
run gcloud builds submit "${REPO_ROOT}/services/dashboard" \
  --project "${PROJECT_ID}" \
  --tag "${REGISTRY}/console:${TAG}" \
  --gcs-log-dir "gs://${PROJECT_ID}_cloudbuild/logs"

# --- Python services -------------------------------------------------------------------------
#
# name | service-account | entrypoint | public?
#
# "Public" here means unauthenticated at the Cloud Run level. Two are: the portal, because a
# vendor has no account and authenticates a signed link instead; and the console, because it has
# to serve a sign-in page. Everything else requires an invoker token, so the approval service is
# unreachable from the internet even if its URL leaks.
SERVICES=(
  "portal|sa-portal|portal|public"
  "approvals|sa-approvals|approvals|private"
  "binder|sa-binder|binder|private"
  "screening|sa-armor|screening|private"
)

for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name identity module access <<< "${entry}"
  log "deploying ${name} as ${identity}"

  auth_flag="--no-allow-unauthenticated"
  [ "${access}" = "public" ] && auth_flag="--allow-unauthenticated"

  run gcloud run deploy "drawbridge-${name}" \
    --project "${PROJECT_ID}" \
    --region "${REGION}" \
    --image "${REGISTRY}/fleet:${TAG}" \
    --service-account "${identity}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --set-env-vars "RUNTIME_MODE=cloud,SERVICE=${module},PROJECT_ID=${PROJECT_ID},REGION=${REGION}" \
    --set-secrets "DRAWBRIDGE_PORTAL_SECRET=drawbridge-portal-secret:latest" \
    --min-instances 0 --max-instances 4 --cpu-throttling \
    --memory 1Gi --timeout 300 \
    "${auth_flag}"
done

# --- The console -------------------------------------------------------------------------------
log "deploying the console as sa-dashboard"
run gcloud run deploy drawbridge-console \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${REGISTRY}/console:${TAG}" \
  --service-account "sa-dashboard@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars "RUNTIME_MODE=cloud,PROJECT_ID=${PROJECT_ID},GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --set-secrets "DRAWBRIDGE_SESSION_SECRET=drawbridge-session-secret:latest" \
  --min-instances 0 --max-instances 8 --cpu-throttling \
  --memory 1Gi --timeout 60 \
  --allow-unauthenticated

# --- The worker -----------------------------------------------------------------------------------
#
# A job rather than a service. It serves nothing, and a Cloud Run service with no requests is
# either scaled to zero — where it consumes no messages — or held resident at a cost that buys
# nothing. Scheduler triggers it; the loop drains what is waiting and exits.
#
# Recovery needs no special handling here, which is the point of the whole checkpoint design: the
# review's position is a Firestore row and an unacknowledged message, so a job killed mid-step is
# replaced by the next execution reading the same two things.
log "deploying the worker job"
run gcloud run jobs deploy drawbridge-worker \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --image "${REGISTRY}/fleet:${TAG}" \
  --service-account "sa-orchestrator@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars "RUNTIME_MODE=cloud,PROJECT_ID=${PROJECT_ID},REGION=${REGION}" \
  --command python --args "-m,scripts.run_local" \
  --max-retries 1 --task-timeout 900 --memory 2Gi

# --- Wiring -----------------------------------------------------------------------------------------
#
# The console reaches the approval and binder services by URL, and neither is public, so it also
# needs the invoker role on them. Granted here rather than in the IAM bootstrap because it is a
# fact about the deployment topology rather than about the permission matrix.
if [ -z "${DRY_RUN:-}" ]; then
  APPROVALS_URL=$(gcloud run services describe drawbridge-approvals \
    --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')
  BINDER_URL=$(gcloud run services describe drawbridge-binder \
    --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')
  PORTAL_URL=$(gcloud run services describe drawbridge-portal \
    --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')

  log "granting the console invoker on the private services"
  for target in drawbridge-approvals drawbridge-binder; do
    run gcloud run services add-iam-policy-binding "${target}" \
      --project "${PROJECT_ID}" --region "${REGION}" \
      --member "serviceAccount:sa-dashboard@${PROJECT_ID}.iam.gserviceaccount.com" \
      --role roles/run.invoker
  done

  log "pointing the console and the fleet at each other"
  run gcloud run services update drawbridge-console \
    --project "${PROJECT_ID}" --region "${REGION}" \
    --update-env-vars "APPROVALS_URL=${APPROVALS_URL},BINDER_URL=${BINDER_URL},PORTAL_URL=${PORTAL_URL}"

  run gcloud run jobs update drawbridge-worker \
    --project "${PROJECT_ID}" --region "${REGION}" \
    --update-env-vars "PORTAL_URL=${PORTAL_URL}"
fi

# --- Schedules -----------------------------------------------------------------------------------------
#
# Two topics have consumers and no publisher because a timer fires them. Without these the fleet
# never chases a silent vendor and never sweeps the approved portfolio — both failures are quiet,
# which is why they are here rather than in a runbook.
log "creating the schedules"
run gcloud scheduler jobs create pubsub drawbridge-chase \
  --project "${PROJECT_ID}" --location "${REGION}" \
  --schedule "0 9 * * 1-5" --time-zone UTC \
  --topic review.chase_due --message-body '{"scheduled":true}' \
  2>/dev/null || log "  chase schedule already exists"

run gcloud scheduler jobs create pubsub drawbridge-sweep \
  --project "${PROJECT_ID}" --location "${REGION}" \
  --schedule "0 6 * * *" --time-zone UTC \
  --topic watchdog.sweep --message-body '{"scheduled":true}' \
  2>/dev/null || log "  sweep schedule already exists"

run gcloud scheduler jobs create http drawbridge-worker-tick \
  --project "${PROJECT_ID}" --location "${REGION}" \
  --schedule "*/2 * * * *" --time-zone UTC \
  --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/drawbridge-worker:run" \
  --http-method POST \
  --oauth-service-account-email "sa-orchestrator@${PROJECT_ID}.iam.gserviceaccount.com" \
  2>/dev/null || log "  worker tick already exists"

log "deployed"
if [ -z "${DRY_RUN:-}" ]; then
  log "  console : $(gcloud run services describe drawbridge-console \
    --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"
  log "  portal  : $(gcloud run services describe drawbridge-portal \
    --project "${PROJECT_ID}" --region "${REGION}" --format='value(status.url)')"
  log ""
  log "Before anyone can sign up, point Identity Platform at the console's URL and add it to the"
  log "authorised domains. Nothing else is required — the first person to sign up becomes the"
  log "administrator of the workspace they create."
fi
