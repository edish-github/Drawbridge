"""Release a human gate by hand: mint a scoped approval and resume the review.

    python -m scripts.issue_token --review-id rev-nimbuswrite-1a2b3c4d

Stands in for the approval service, which is what a human will actually click in the dashboard.
It is a script rather than a library function on purpose: **minting lives outside the shared
package**, so an agent that imports everything it can reach still cannot manufacture an
approval. ``shared.approvals`` reads and retires tokens and has no issue function;
``gateway.issue_approval_token`` raises. This file is the only place in the repository that
writes to the ``approvals`` collection.

Two things happen, in this order:

1. An approval is recorded, scoped to one review, one gate and one target.
2. ``review.approved`` is published.

**This script does not move the review.** Releasing a gate is a forward transition, and the
Orchestrator owns those — it consumes ``review.approved``, validates the release against the
scope the review was parked at, and resumes the path the gate was blocking. An approvals
service that could also transition would be a second component advancing reviews, which is
exactly the ownership this project spent a rule on.

Both gates are supported. ``--scope contact`` releases G2, first outbound contact.
``--scope decision`` releases G1, the risk acceptance, and closes the review.

The token itself is not secret in local mode and is not pretending to be: verification is a
record lookup, and what stops an agent writing its own approval is collection-level IAM.
Asymmetric signing lands with the approval service — see the ``TODO(verify)`` on
``gateway.verify_approval_token``.

Failure semantics: a review that is not parked at the named gate is refused rather than
released. Releasing a gate a review is not parked at is how an approval intended for one
decision is spent on another.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime, timedelta

from shared.approvals import COLLECTION_APPROVALS, Approval, token_for
from shared.clients import firestore_client
from shared.context import AgentContext
from shared.domain import ReviewState
from shared.events import TOPIC_REVIEW_APPROVED, load_review, publish

DEFAULT_TTL_MINUTES = 60
GATE_RELEASE = {
    "contact": ReviewState.QUESTIONNAIRE_OUT,
    "decision": ReviewState.DECIDED,
}


class GateNotOpen(Exception):
    """The review is not parked at the gate this approval would release."""


def issue(
    review_id: str,
    *,
    scope: str,
    identity: str,
    ttl_minutes: int,
    conditions: list[str] | None = None,
) -> str:
    """Record one approval and return the token that presents it.

    ``conditions`` are what the approver required of the vendor before or after signing. They
    are stored on the approval record — the ledger — and never in durable memory, because they
    are sentences a person wrote and memory holds terms. A later review recalls that conditions
    were attached and reads them back from here.
    """
    review = load_review(review_id)
    if review is None:
        raise GateNotOpen(f"no review {review_id!r}")
    if review.state is not ReviewState.GATED or review.gate_scope != scope:
        raise GateNotOpen(
            f"review {review_id} is {review.state.value}"
            f"{f'/{review.gate_scope}' if review.gate_scope else ''}, "
            f"not parked at the {scope!r} gate"
        )

    db = firestore_client()
    vendor = db.collection("vendors").document(review.vendor_id).get().to_dict() or {}
    target = _target_for(scope, review, vendor)

    now = datetime.now(UTC)
    approval = Approval(
        jti=uuid.uuid4().hex,
        review_id=review_id,
        scope=scope,
        target=target,
        identity=identity,
        issued_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
        conditions=list(conditions or []),
    )
    db.collection(COLLECTION_APPROVALS).document(approval.jti).set(
        approval.model_dump(mode="json")
    )

    ctx = AgentContext(review_id=review_id, agent="approvals", trace_id=uuid.uuid4().hex)
    publish(
        TOPIC_REVIEW_APPROVED,
        review_id,
        {"scope": scope, "identity": identity, "jti": approval.jti},
        ctx=ctx,
    )
    return token_for(approval.jti)


def _target_for(scope: str, review, vendor: dict) -> str:
    """Return what this approval authorises.

    A contact approval authorises one recipient address, so P1 can check that the address about
    to be emailed is the one a human agreed to. A decision approval authorises closing one
    review, so the review id is the target — there is no address involved, and inventing one
    would make the scope check compare two unrelated things.

    Raises:
        GateNotOpen: when a contact approval has no address to be scoped to.
    """
    if scope != "contact":
        return review.review_id

    target = (vendor.get("contact") or {}).get("email", "")
    if not target:
        raise GateNotOpen(f"vendor {review.vendor_id!r} has no contact address to authorise")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--scope", default="contact", choices=sorted(GATE_RELEASE))
    parser.add_argument(
        "--identity",
        default="local-operator",
        help="the named human making the decision; an approval by 'the system' is not one",
    )
    parser.add_argument("--ttl-minutes", type=int, default=DEFAULT_TTL_MINUTES)
    args = parser.parse_args()

    try:
        token = issue(
            args.review_id,
            scope=args.scope,
            identity=args.identity,
            ttl_minutes=args.ttl_minutes,
        )
    except GateNotOpen as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    print(f"approval issued for {args.review_id} · scope={args.scope} · by {args.identity}")
    print(f"token {token}")
    print(
        f"review.approved published; the Orchestrator will release the gate to "
        f"{GATE_RELEASE[args.scope].value}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
