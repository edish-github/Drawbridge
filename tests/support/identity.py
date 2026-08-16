"""Talk to the emulator as a named service account, so the permission matrix can be tested.

The Firestore emulator evaluates security rules. What it cannot evaluate is IAM identity: it
never checks that the caller presenting a token is the principal it claims to be, and nothing
here proves that ``sa-evidence`` is the identity the Evidence agent actually runs as in a real
project. That half of the matrix stays unproven until a project exists, and the tests that
would prove it stay skipped with that reason written on them.

What this does prove is the other half, which is the half that drifts: given the identity, may
it write this collection? Every ``firestore.read``/``firestore.write`` row in the matrix
becomes an assertion instead of a claim, and a row that stops being true fails here rather than
in a demo.

**How the impersonation works.** The Firestore client library sends ``Bearer owner`` to the
emulator, which the emulator treats as an administrative caller and exempts from rules — which
is why the fleet, the demo and the rest of the suite are unaffected by loading rules at all.
Presenting an unsigned JWT instead makes the call a client call, and the emulator populates
``request.auth.token`` from its claims. The generated rules read ``request.auth.token.email``,
so the claim set below is the whole of the impersonation.

Failure semantics: a denied operation raises ``google.api_core.exceptions.PermissionDenied``.
A test that expects a denial and gets a write is a permission row that has stopped being true.
"""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
MATRIX_PATH = REPO / "infra" / "iam" / "permission-matrix.yaml"


@lru_cache(maxsize=1)
def matrix() -> dict:
    """Return the permission matrix, the single source both the rules and these tests read."""
    with open(MATRIX_PATH) as fh:
        return yaml.safe_load(fh)


def identities() -> list[dict]:
    return matrix()["identities"]


def rows() -> list[tuple[str, str, bool]]:
    """Return every (identity, collection, may_write) the matrix declares, plus the denials.

    A collection an identity does not name is a denial the matrix implies rather than states,
    and those are the interesting ones — an over-broad grant shows up as a write that should
    have failed.
    """
    every = sorted({c for i in identities() for c in _declared(i)})
    out: list[tuple[str, str, bool]] = []
    for identity in identities():
        writable = set((identity.get("firestore") or {}).get("write") or [])
        for collection in every:
            out.append((identity["name"], collection, collection in writable))
    return out


def _declared(identity: dict) -> set[str]:
    fs = identity.get("firestore") or {}
    return set(fs.get("read") or []) | set(fs.get("write") or [])


def service_account_email(name: str, project: str) -> str:
    return f"{name}@{project}.iam.gserviceaccount.com"


def unsigned_token(email: str, project: str) -> str:
    """Build the unsigned JWT the emulator reads identity claims from.

    Unsigned on purpose and only ever pointed at an emulator: a real Firestore rejects it, so
    this cannot become a way of reaching a real project.
    """

    def segment(payload: dict) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    header = segment({"alg": "none", "typ": "JWT"})
    claims = segment(
        {
            "email": email,
            "email_verified": True,
            "sub": email,
            "user_id": email,
            "aud": project,
            "iss": f"https://securetoken.google.com/{project}",
        }
    )
    return f"{header}.{claims}."


def as_identity(name: str):
    """Return a Firestore client that the emulator sees as this service account."""
    from google.auth.credentials import AnonymousCredentials
    from google.cloud import firestore

    from shared.config import settings

    project = settings().emulator_project()
    token = unsigned_token(service_account_email(name, project), project)

    class _AsIdentity(firestore.Client):
        """A client whose every RPC carries this identity rather than the admin token."""

        @property
        def _rpc_metadata(self):
            return [
                ("google-cloud-resource-prefix", self._database_string),
                ("x-goog-request-params", self._database_string),
                ("authorization", f"Bearer {token}"),
            ]

    return _AsIdentity(project=project, credentials=AnonymousCredentials())


def rules_are_loaded() -> bool:
    """Return whether the running emulator is evaluating rules.

    Probed rather than assumed: an emulator started before ``make rules`` existed enforces
    nothing, and a suite that silently passed against it would be reporting the opposite of the
    truth. One write that the matrix denies decides it.
    """
    from google.api_core.exceptions import PermissionDenied

    try:
        as_identity("sa-questionnaire").collection("findings").document("_rules_probe").set(
            {"probe": True}
        )
    except PermissionDenied:
        return True
    except Exception:  # noqa: BLE001 — no emulator, no rules; the caller skips either way
        return False
    return False
