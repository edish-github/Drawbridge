#!/usr/bin/env bash
#
# Create the Firestore composite and KNN vector indexes declared in indexes.yaml.
#
# Idempotent: gcloud reports an already-existing index as an error, which this script treats
# as success rather than aborting the bootstrap. Index creation is asynchronous — the command
# returns before the index is queryable, which is why the seed step waits for readiness.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="indexes"
# shellcheck source=infra/lib.sh
. "${REPO_ROOT}/infra/lib.sh"
require_project
DATABASE="${FIRESTORE_DATABASE:-(default)}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INDEXES="${REPO_ROOT}/infra/firestore/indexes.yaml"


python "${REPO_ROOT}/infra/firestore/indexes_to_args.py" --indexes "${INDEXES}" \
  > /tmp/drawbridge-indexes.tsv

while IFS=$'\t' read -r collection args; do
  [[ -z "${collection}" ]] && continue
  log "  index on ${collection}: ${args}"
  # shellcheck disable=SC2086
  if gc firestore indexes composite create \
      --database="${DATABASE}" \
      --collection-group="${collection}" \
      --query-scope=collection \
      ${args} \
      --async >/dev/null 2>&1; then
    log "    created"
  else
    log "    already exists or creation deferred; continuing"
  fi
done < /tmp/drawbridge-indexes.tsv

rm -f /tmp/drawbridge-indexes.tsv

log "indexes submitted; they build asynchronously"
log "check readiness with: gcloud firestore indexes composite list --project=${PROJECT_ID}"
