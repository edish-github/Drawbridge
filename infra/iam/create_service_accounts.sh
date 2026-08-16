#!/usr/bin/env bash
#
# Create the nine fleet service accounts. Idempotent: an existing account is left alone.
#
# Bindings are applied separately by apply_iam.sh, which reads permission-matrix.yaml, so the
# matrix in the docs and the bindings in the project cannot drift.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="iam"
# shellcheck source=infra/lib.sh
. "${REPO_ROOT}/infra/lib.sh"
require_project
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"


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
  if probe iam service-accounts describe "${email}"; then
    log "  exists, skipping: ${name}"
  else
    gc iam service-accounts create "${name}" \
      --display-name="${display}" >/dev/null
    log "  created: ${name}"
  fi
done <<< "${NAMES}"

log "service accounts done"
