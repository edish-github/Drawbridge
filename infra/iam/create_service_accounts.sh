#!/usr/bin/env bash
#
# Create the nine fleet service accounts. Idempotent: an existing account is left alone.
#
# Bindings are applied separately by apply_iam.sh, which reads permission-matrix.yaml, so the
# matrix in the docs and the bindings in the project cannot drift.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?PROJECT_ID must be set}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

log() { printf '[iam] %s\n' "$*"; }

MATRIX="${REPO_ROOT}/infra/iam/permission-matrix.yaml"

# Names come from the matrix so a new identity is added in one place.
NAMES=$(python - "${MATRIX}" <<'PY'
import sys, yaml
with open(sys.argv[1]) as fh:
    matrix = yaml.safe_load(fh)
for identity in matrix["identities"]:
    print(f'{identity["name"]}\t{identity["display"]}')
PY
)

while IFS=$'\t' read -r name display; do
  [[ -z "${name}" ]] && continue
  email="${name}@${PROJECT_ID}.iam.gserviceaccount.com"
  if gcloud --project="${PROJECT_ID}" iam service-accounts describe "${email}" >/dev/null 2>&1; then
    log "  exists, skipping: ${name}"
  else
    gcloud --project="${PROJECT_ID}" iam service-accounts create "${name}" \
      --display-name="${display}" >/dev/null
    log "  created: ${name}"
  fi
done <<< "${NAMES}"

log "service accounts done"
