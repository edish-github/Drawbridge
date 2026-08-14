"""Turn the permission matrix into the flat binding lists the shell scripts apply.

Keeping this in Python rather than in shell is what lets the matrix stay readable as data. It
emits tab-separated lines so the caller can read them with a plain shell loop.

Failure semantics: an identity whose declared grants cannot be mapped to a role raises rather
than being skipped. A silently skipped binding is an agent that cannot do its job, discovered
during a demo, and a silently added one is a least-privilege claim that is no longer true.
"""

from __future__ import annotations

import argparse
import sys

import yaml


class UnmappedGrant(Exception):
    """A grant in the matrix has no role mapping. Add the mapping rather than ignoring it."""


def project_roles(identity: dict) -> list[str]:
    """Return the project-level roles this identity needs.

    Deliberately narrow. Vertex AI is granted only where the matrix marks it, which is what
    makes "the screening pipeline cannot call a generative model" an IAM fact rather than a
    code convention.
    """
    roles: list[str] = []
    if identity.get("vertex_ai"):
        roles.append("roles/aiplatform.user")
    if identity.get("model_armor"):
        roles.append(f'roles/{identity["model_armor"]}')
    pubsub = identity.get("pubsub") or []
    if "publish" in pubsub:
        roles.append("roles/pubsub.publisher")
    if "subscribe" in pubsub:
        roles.append("roles/pubsub.subscriber")
    if identity.get("trace") == "read":
        roles.append("roles/cloudtrace.user")
    # Which collections an identity may touch is enforced by the security rules; this role only
    # opens the API. The matrix is the authority on the collection list.
    if identity.get("firestore"):
        roles.append("roles/datastore.user")
    return roles


def emit(matrix: dict, kind: str) -> None:
    for identity in matrix["identities"]:
        name = identity["name"]
        if kind == "project":
            for role in project_roles(identity):
                print(f"{name}\t{role}")
        elif kind == "storage":
            for grant in identity.get("storage") or []:
                role = grant.get("role")
                if role not in ("objectViewer", "objectCreator", "objectAdmin"):
                    raise UnmappedGrant(f"{name}: unknown storage role {role!r}")
                print(f'{name}\t{grant["bucket"]}\troles/storage.{role}')
        elif kind == "secrets":
            for secret in identity.get("secrets") or []:
                print(f"{name}\t{secret}")
        else:
            raise UnmappedGrant(f"unknown binding kind {kind!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", required=True)
    parser.add_argument("--kind", required=True, choices=("project", "storage", "secrets"))
    args = parser.parse_args()

    with open(args.matrix) as fh:
        matrix = yaml.safe_load(fh)

    try:
        emit(matrix, args.kind)
    except UnmappedGrant as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
