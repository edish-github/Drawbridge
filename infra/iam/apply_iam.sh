#!/usr/bin/env bash
#
# Apply the permission matrix.
#
# Bucket, secret and project-level roles are applied with gcloud. Collection-level Firestore
# access is not expressible as a project IAM role — a project-wide datastore.user is exactly
# the blanket grant this matrix exists to avoid — so it is enforced by the Firestore security
# rules generated from the same matrix file. Both halves read permission-matrix.yaml, so
# there is one source of truth and the table in the README cannot drift from the project.
#
# Idempotent: IAM binding adds are additive and repeat safely; rules are deployed as a whole
# ruleset, so re-running replaces rather than appends.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="iam"
# shellcheck source=infra/lib.sh
. "${REPO_ROOT}/infra/lib.sh"
require_project
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MATRIX="${REPO_ROOT}/infra/iam/permission-matrix.yaml"
BINDINGS="${REPO_ROOT}/infra/iam/matrix_to_bindings.py"

sa_email() { printf '%s@%s.iam.gserviceaccount.com' "$1" "${PROJECT_ID}"; }

# --- Project-level roles ------------------------------------------------------------------
log "applying project-level roles"
python "${BINDINGS}" --matrix "${MATRIX}" --kind project > /tmp/drawbridge-iam-project.tsv

while IFS=$'\t' read -r name role; do
  [[ -z "${name}" ]] && continue
  log "  ${name} -> ${role}"
  gc projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:$(sa_email "${name}")" \
    --role="${role}" \
    --condition=None >/dev/null
done < /tmp/drawbridge-iam-project.tsv

# --- Bucket-level roles -------------------------------------------------------------------
# The screening pipeline is the only identity with any role on the quarantine bucket at all.
log "applying bucket-level roles"
python "${BINDINGS}" --matrix "${MATRIX}" --kind storage > /tmp/drawbridge-iam-storage.tsv

while IFS=$'\t' read -r name bucket role; do
  [[ -z "${name}" ]] && continue
  log "  ${name} -> gs://${PROJECT_ID}-${bucket} (${role})"
  gc storage buckets add-iam-policy-binding \
    "gs://${PROJECT_ID}-${bucket}" \
    --member="serviceAccount:$(sa_email "${name}")" \
    --role="${role}" >/dev/null
done < /tmp/drawbridge-iam-storage.tsv

# --- Secret Manager -----------------------------------------------------------------------
# The private approval signing key is accessible to exactly one identity. If this loop ever
# grants it to a second, the strongest security sentence in the project stops being true.
log "applying secret access"
python "${BINDINGS}" --matrix "${MATRIX}" --kind secrets > /tmp/drawbridge-iam-secrets.tsv

while IFS=$'\t' read -r name secret; do
  [[ -z "${name}" ]] && continue
  if ! probe secrets describe "${secret}"; then
    log "  secret ${secret} does not exist yet; create it before granting access"
    continue
  fi
  log "  ${name} -> secret ${secret}"
  gc secrets add-iam-policy-binding "${secret}" \
    --member="serviceAccount:$(sa_email "${name}")" \
    --role="roles/secretmanager.secretAccessor" >/dev/null
done < /tmp/drawbridge-iam-secrets.tsv

rm -f /tmp/drawbridge-iam-project.tsv /tmp/drawbridge-iam-storage.tsv /tmp/drawbridge-iam-secrets.tsv

# --- Firestore security rules -------------------------------------------------------------
log "generating collection-level Firestore rules"
if [ "${DRY_RUN}" = "1" ]; then
  # A rehearsal must not rewrite a tracked file. Found by rehearsing: the generated ruleset
  # carries the project id, so a dry run against a placeholder project overwrote the committed
  # rules with rules naming a project that does not exist.
  log "  [dry-run] would regenerate infra/firestore/firestore.rules for ${PROJECT_ID}"
else
python "${REPO_ROOT}/infra/firestore/generate_rules.py" \
  --matrix "${MATRIX}" \
  --project "${PROJECT_ID}" \
  --out "${REPO_ROOT}/infra/firestore/firestore.rules"
log "  written to infra/firestore/firestore.rules"
fi
log "  deploy with: firebase deploy --only firestore:rules --project ${PROJECT_ID}"

log "IAM done"
