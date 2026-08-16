#!/usr/bin/env bash
#
# The one place a provisioning script calls gcloud.
#
# Five scripts provision this project and each of them used to call gcloud directly, which meant
# a rehearsal would have had to be five separate changes and a command added later would not be
# rehearsed at all. Now every mutating call goes through gc(), so DRY_RUN is a property of one
# function.
#
# DRY_RUN=1 prints what would run and returns success. That is the point: the first real run
# should not also be the first run, and a rehearsal catches a mistyped flag, a resource named two
# different ways in two places, and an ordering mistake for the price of reading the output.
#
# Sourced, never executed. It defines functions and reads two variables; it creates nothing.

PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
DRY_RUN="${DRY_RUN:-0}"

TAG="${TAG:-infra}"

log()  { printf '[%s] %s\n' "${TAG}" "$*"; }
skip() { printf '[%s]   exists, skipping: %s\n' "${TAG}" "$*"; }
made() { printf '[%s]   created: %s\n' "${TAG}" "$*"; }

# Every mutating gcloud call in infra/ goes through here.
gc() {
  if [ "${DRY_RUN}" = "1" ]; then
    printf '[dry-run]   gcloud --project=%s %s\n' "${PROJECT_ID}" "$*"
    return 0
  fi
  gcloud --project="${PROJECT_ID}" "$@"
}

# Read-only probes. Separated from gc() because a rehearsal must not pretend a resource exists:
# under DRY_RUN this returns failure, so the script takes the create branch and prints the
# command that would run. A probe that returned success in a rehearsal would print nothing but
# "exists, skipping" and rehearse none of the work.
probe() {
  if [ "${DRY_RUN}" = "1" ]; then
    return 1
  fi
  gcloud --project="${PROJECT_ID}" "$@" >/dev/null 2>&1
}

require_project() {
  if [ -z "${PROJECT_ID}" ]; then
    log "ERROR: PROJECT_ID must be set; these scripts do not create projects"
    exit 1
  fi
}
