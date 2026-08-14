#!/usr/bin/env bash
#
# Delete every Drawbridge resource except the dashboard service.
#
# The dashboard survives because the submission requires a hosted URL that loads while judging
# is in progress. Everything else goes: Agent Engine deployments, the other Cloud Run
# services, topics, subscriptions, buckets and their contents, Model Armor templates, service
# accounts.
#
# This never deletes the project and never touches billing. Deleting the project is a manual
# decision made after judging closes.
#
# Required environment: PROJECT_ID. Optional: REGION (defaults to us-central1).

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID must be set}"
REGION="${REGION:-us-central1}"

KEEP_SERVICE="drawbridge-dashboard"

log() { printf '[teardown] %s\n' "$*"; }
gc()  { gcloud --project="${PROJECT_ID}" "$@"; }

cat <<EOF

This deletes Drawbridge resources in project ${PROJECT_ID} (${REGION}):

  - all Agent Engine deployments
  - all Cloud Run services except ${KEEP_SERVICE}
  - all Pub/Sub topics and subscriptions
  - the quarantine, clean and binder buckets AND THEIR CONTENTS
  - both Model Armor templates
  - the fleet service accounts

It does not delete the project and does not touch billing.

EOF

read -r -p "Type the project id to confirm: " confirm
if [[ "${confirm}" != "${PROJECT_ID}" ]]; then
  log "confirmation did not match; nothing was deleted"
  exit 1
fi

# --- Agent Engine -------------------------------------------------------------------------
log "deleting Agent Engine deployments"
PROJECT_ID="${PROJECT_ID}" REGION="${REGION}" python - <<'PY' || log "  agent engine cleanup reported an error; check by hand"
import os
import vertexai
from vertexai import agent_engines

vertexai.init(project=os.environ["PROJECT_ID"], location=os.environ["REGION"])
for a in agent_engines.list():
    print(f"  deleting {a.resource_name}")
    agent_engines.delete(a.resource_name, force=True)
PY

# --- Cloud Run ----------------------------------------------------------------------------
log "deleting Cloud Run services (keeping ${KEEP_SERVICE})"
for svc in $(gc run services list --region="${REGION}" --format='value(metadata.name)'); do
  if [[ "${svc}" == "${KEEP_SERVICE}" ]]; then
    log "  keeping ${svc}"
    continue
  fi
  log "  deleting ${svc}"
  gc run services delete "${svc}" --region="${REGION}" --quiet
done

# --- Pub/Sub ------------------------------------------------------------------------------
log "deleting subscriptions"
for sub in $(gc pubsub subscriptions list --format='value(name)'); do
  gc pubsub subscriptions delete "${sub}" --quiet
done

log "deleting topics"
for topic in $(gc pubsub topics list --format='value(name)'); do
  gc pubsub topics delete "${topic}" --quiet
done

# --- Storage ------------------------------------------------------------------------------
log "deleting buckets and their contents"
for b in "${PROJECT_ID}-evidence-quarantine" "${PROJECT_ID}-evidence-clean" "${PROJECT_ID}-binders"; do
  if gc storage buckets describe "gs://${b}" >/dev/null 2>&1; then
    log "  deleting gs://${b}"
    gc storage rm --recursive "gs://${b}" --quiet
  fi
done

# --- Model Armor --------------------------------------------------------------------------
log "deleting Model Armor templates"
for tpl in drawbridge-untrusted drawbridge-output; do
  if gc model-armor templates describe "${tpl}" --location="${REGION}" >/dev/null 2>&1; then
    log "  deleting ${tpl}"
    gc model-armor templates delete "${tpl}" --location="${REGION}" --quiet
  fi
done

# --- Service accounts ---------------------------------------------------------------------
log "deleting service accounts"
for sa in orchestrator questionnaire evidence scorer watchdog armor portal dashboard approvals binder; do
  email="sa-${sa}@${PROJECT_ID}.iam.gserviceaccount.com"
  if gc iam service-accounts describe "${email}" >/dev/null 2>&1; then
    log "  deleting ${email}"
    gc iam service-accounts delete "${email}" --quiet
  fi
done

log "teardown complete"
log "Firestore data and the ${KEEP_SERVICE} service were kept. Confirm spend in the billing console."
