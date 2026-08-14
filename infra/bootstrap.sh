#!/usr/bin/env bash
#
# Provision every Drawbridge resource in an existing, billing-enabled project.
#
# This script does NOT create the project and does NOT touch billing. Both are done by hand,
# once, before this runs — see the checklist in the README.
#
# Idempotent throughout: every step checks for the resource before creating it and logs what
# it did, so re-running after a partial failure is safe and produces a readable diff of what
# was already there.
#
# Required environment: PROJECT_ID. Optional: REGION (defaults to us-central1).

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID must be set; this script does not create projects}"
REGION="${REGION:-us-central1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log()  { printf '[bootstrap] %s\n' "$*"; }
skip() { printf '[bootstrap]   exists, skipping: %s\n' "$*"; }
made() { printf '[bootstrap]   created: %s\n' "$*"; }

gc() { gcloud --project="${PROJECT_ID}" "$@"; }

log "project=${PROJECT_ID} region=${REGION}"

# --- Preflight ----------------------------------------------------------------------------
# Refuse to run against a project with no billing rather than failing halfway through with a
# confusing API error.
if ! gcloud beta billing projects describe "${PROJECT_ID}" --format='value(billingEnabled)' 2>/dev/null | grep -qi true; then
  log "ERROR: billing is not enabled on ${PROJECT_ID}. Enable it and re-run."
  exit 1
fi

# --- APIs ---------------------------------------------------------------------------------
log "enabling APIs"
gc services enable \
  aiplatform.googleapis.com \
  run.googleapis.com \
  pubsub.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudtrace.googleapis.com \
  logging.googleapis.com \
  cloudscheduler.googleapis.com \
  modelarmor.googleapis.com
made "APIs enabled"

# --- Pub/Sub ------------------------------------------------------------------------------
# Eleven topics. The list here, the list in shared/events.py and the list in infra/pubsub.yaml
# must agree; CI diffs them, because an undocumented topic is one this script does not create.
TOPICS=(
  review.intake
  review.plan_ready
  vendor.reply_received
  vendor.evidence_uploaded
  evidence.screened
  review.findings_ready
  review.score_ready
  review.approved
  review.rescore
  watchdog.sweep
  watchdog.hit
)

log "creating ${#TOPICS[@]} topics, their dead-letter counterparts and subscriptions"
for t in "${TOPICS[@]}"; do
  for name in "${t}" "${t}.dlq"; do
    if gc pubsub topics describe "${name}" >/dev/null 2>&1; then
      skip "topic ${name}"
    else
      gc pubsub topics create "${name}" >/dev/null
      made "topic ${name}"
    fi
  done

  # The dead-letter subscription exists so a dead-lettered message is retained and visible;
  # without a subscription on the DLQ topic, a failed message is silently discarded.
  if gc pubsub subscriptions describe "${t}.dlq.sub" >/dev/null 2>&1; then
    skip "subscription ${t}.dlq.sub"
  else
    gc pubsub subscriptions create "${t}.dlq.sub" --topic="${t}.dlq" >/dev/null
    made "subscription ${t}.dlq.sub"
  fi

  if gc pubsub subscriptions describe "${t}.sub" >/dev/null 2>&1; then
    skip "subscription ${t}.sub"
  else
    gc pubsub subscriptions create "${t}.sub" \
      --topic="${t}" \
      --ack-deadline=60 \
      --dead-letter-topic="${t}.dlq" \
      --max-delivery-attempts=5 >/dev/null
    made "subscription ${t}.sub (ack 60s, DLQ after 5 attempts)"
  fi
done

# --- Storage ------------------------------------------------------------------------------
BUCKET_QUARANTINE="${PROJECT_ID}-evidence-quarantine"
BUCKET_CLEAN="${PROJECT_ID}-evidence-clean"
BUCKET_BINDERS="${PROJECT_ID}-binders"

log "creating buckets"
for b in "${BUCKET_QUARANTINE}" "${BUCKET_CLEAN}" "${BUCKET_BINDERS}"; do
  if gc storage buckets describe "gs://${b}" >/dev/null 2>&1; then
    skip "bucket gs://${b}"
  else
    gc storage buckets create "gs://${b}" \
      --location="${REGION}" \
      --uniform-bucket-level-access \
      --public-access-prevention >/dev/null
    made "bucket gs://${b}"
  fi
done

# Hostile payloads are not retained longer than they are needed. The blocked excerpt already
# lives in the ledger as inert text, so the audit binder survives this deletion — which makes
# the rule a security story as much as a cost one.
log "applying the 7-day lifecycle rule to the quarantine bucket"
gc storage buckets update "gs://${BUCKET_QUARANTINE}" \
  --lifecycle-file="${REPO_ROOT}/infra/quarantine-lifecycle.json" >/dev/null
made "lifecycle rule on gs://${BUCKET_QUARANTINE} (delete after 7 days)"

# --- Firestore ----------------------------------------------------------------------------
log "creating Firestore in native mode"
if gc firestore databases describe --database='(default)' >/dev/null 2>&1; then
  skip "Firestore (default)"
else
  gc firestore databases create --location="${REGION}" --type=firestore-native >/dev/null
  made "Firestore (default), native mode, ${REGION}"
fi

log "creating Firestore indexes"
"${REPO_ROOT}/infra/firestore/create_indexes.sh"

# --- Service accounts ---------------------------------------------------------------------
# Nine identities: five agents, the screening pipeline, and three user-facing services.
# The permission matrix lives in infra/iam/permission-matrix.yaml as data; apply_iam.sh reads
# it and applies it, so the matrix in the README and the bindings in the project cannot drift.
log "creating service accounts"
"${REPO_ROOT}/infra/iam/create_service_accounts.sh"

log "applying collection-level IAM"
"${REPO_ROOT}/infra/iam/apply_iam.sh"

# --- Model Armor --------------------------------------------------------------------------
# Both templates are created here, never by hand in the console. A template that exists only
# in a console is not reproducible, and the spin-up path is a graded item. Record the ids and
# versions: they go into the clean-stamp and the binder, so a reviewer six months later knows
# which policy screened a document.
log "creating Model Armor templates"
"${REPO_ROOT}/infra/model_armor/create_templates.sh"

# --- Artifact Registry --------------------------------------------------------------------
log "creating the container repository"
if gc artifacts repositories describe drawbridge --location="${REGION}" >/dev/null 2>&1; then
  skip "artifact repository drawbridge"
else
  gc artifacts repositories create drawbridge \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Drawbridge container images" >/dev/null
  made "artifact repository drawbridge"
fi

log "bootstrap complete"
log "next: set budget alerts by hand at \$50 / \$100 / \$130, then run 'make seed'"
