#!/usr/bin/env bash
#
# Start the Firestore and Pub/Sub emulators for local mode.
#
# Idempotent: an emulator already listening on its port is left alone, so this is safe to call
# from other make targets. Logs go to .emulators/ so a failed start is diagnosable rather than
# silent.
#
# The emulators need no billing account, no project and no credentials. That is the whole
# point: the kernel is built and its tests pass before any cloud resource exists.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${REPO_ROOT}/.emulators"
PROJECT="${PROJECT_ID:-drawbridge-local}"

FIRESTORE_HOST="${FIRESTORE_EMULATOR_HOST:-localhost:8080}"
PUBSUB_HOST="${PUBSUB_EMULATOR_HOST:-localhost:8085}"

log() { printf '[emulators] %s\n' "$*"; }

mkdir -p "${LOG_DIR}"

port_of() { printf '%s' "${1##*:}"; }

listening() {
  nc -z localhost "$(port_of "$1")" >/dev/null 2>&1
}

start() {
  local name="$1" host="$2" cmd="$3"
  if listening "${host}"; then
    log "${name} already listening on ${host}"
    return 0
  fi
  log "starting ${name} on ${host}"
  # shellcheck disable=SC2086
  nohup ${cmd} > "${LOG_DIR}/${name}.log" 2>&1 &
  echo $! > "${LOG_DIR}/${name}.pid"

  for _ in $(seq 1 40); do
    if listening "${host}"; then
      log "${name} ready"
      return 0
    fi
    sleep 0.5
  done

  log "ERROR: ${name} did not come up. Last lines of ${LOG_DIR}/${name}.log:"
  tail -20 "${LOG_DIR}/${name}.log" || true
  return 1
}

if ! gcloud components list --filter="id:cloud-firestore-emulator" \
     --format="value(state.name)" 2>/dev/null | grep -qi installed; then
  log "installing the Firestore emulator"
  gcloud components install cloud-firestore-emulator --quiet
fi

if ! gcloud components list --filter="id:pubsub-emulator" \
     --format="value(state.name)" 2>/dev/null | grep -qi installed; then
  log "installing the Pub/Sub emulator"
  gcloud components install pubsub-emulator --quiet
fi

start firestore "${FIRESTORE_HOST}" \
  "gcloud emulators firestore start --host-port=${FIRESTORE_HOST} --project=${PROJECT}"

# The Pub/Sub emulator lives under `gcloud beta`, unlike the Firestore one. Verified against
# gcloud 580.0.0: `gcloud emulators pubsub` is not a valid command group.
start pubsub "${PUBSUB_HOST}" \
  "gcloud beta emulators pubsub start --host-port=${PUBSUB_HOST} --project=${PROJECT}"

log "both emulators running"
log "  FIRESTORE_EMULATOR_HOST=${FIRESTORE_HOST}"
log "  PUBSUB_EMULATOR_HOST=${PUBSUB_HOST}"

# Same topology as infra/bootstrap.sh, read from the same topic list, so local mode has the
# real event backbone rather than an approximation of it.
log "creating topics and subscriptions in the emulator"
FIRESTORE_EMULATOR_HOST="${FIRESTORE_HOST}" PUBSUB_EMULATOR_HOST="${PUBSUB_HOST}" \
  "${REPO_ROOT}/.venv/bin/python" -m scripts.local_topics

log "stop them with: make emulators-stop"
