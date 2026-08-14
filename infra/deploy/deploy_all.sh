#!/usr/bin/env bash
#
# Deploy the fleet: agents to Agent Engine, services to Cloud Run.
#
# Today this deploys only the two platform checks, which is the whole point of the current
# stage — the platform is proven before any product code depends on it. Each agent and service
# is added to the lists below as it becomes deployable.
#
# Cost posture is identical everywhere and not negotiable per service: min-instances 0,
# max-instances 2, CPU throttled. A public URL that cannot be made to spend money on tokens is
# the difference between a hosted demo and an open wallet.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID must be set}"
REGION="${REGION:-us-central1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

log() { printf '[deploy] %s\n' "$*"; }

# --- Cloud Run services -------------------------------------------------------------------
SERVICES=(
  hello
  # portal
  # dashboard
  # approvals
  # binder
  # vendor_inbox
  # screening
  # pii_scrubber
)

for svc in "${SERVICES[@]}"; do
  script="${REPO_ROOT}/services/${svc}/deploy.sh"
  if [[ ! -x "${script}" ]]; then
    log "no deploy script for services/${svc}; skipping"
    continue
  fi
  log "deploying services/${svc}"
  PROJECT_ID="${PROJECT_ID}" REGION="${REGION}" "${script}"
done

# --- Agent Engine -------------------------------------------------------------------------
AGENTS=(
  hello_agent
  # orchestrator
  # questionnaire
  # evidence
  # risk_scorer
  # watchdog
)

STAGING_BUCKET="${STAGING_BUCKET:-gs://${PROJECT_ID}-binders}"

for a in "${AGENTS[@]}"; do
  script="${REPO_ROOT}/infra/deploy/${a}/deploy.py"
  if [[ ! -f "${script}" ]]; then
    log "no deploy script for ${a}; skipping"
    continue
  fi
  log "deploying agent ${a}"
  PROJECT_ID="${PROJECT_ID}" REGION="${REGION}" STAGING_BUCKET="${STAGING_BUCKET}" \
    python "${script}"
done

log "deploy complete"
