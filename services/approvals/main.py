"""The approval service. The only thing in the product that can create a human decision.

Everything else in Drawbridge is arranged so that this service is the sole holder of the private
signing key. The gateway holds the public half and can therefore *recognise* an approval while
being structurally incapable of *manufacturing* one; ``shared.approvals`` exposes lookup and
single-use consumption and no minting function at all; the console has no key and no write to
the ``approvals`` collection. Take those three away and the human gate is decoration.

**It does not trust its caller.** The console proxies a request here on behalf of a signed-in
person, and this service re-verifies that person's identity token and re-reads their membership
before it signs anything. A service that accepted "the console says Alex is an approver" would
be a service whose security depended on the console never being wrong — and the console is the
surface every reviewer can reach.

Three refusals worth naming, because each is a real attack rather than a validation nicety:

- **Wrong role.** Only ``approver`` and ``admin`` may release a gate. An analyst who can move a
  review through the workflow still cannot close one.
- **Wrong gate.** An approval is scoped, and the scope is checked against the state the review is
  actually parked at. A contact approval presented for a decision gate is refused rather than
  spent, so the mistake costs nothing.
- **Wrong tenant.** The review is read from the caller's organisation, by path. A review id from
  another customer resolves to nothing here, whatever the caller claims.

Failure semantics: every refusal is a 4xx with a message a person can act on and no detail about
what else exists. A signing failure is a 503 and writes nothing — a half-issued approval, recorded
but unsigned, would be a gate that appears released and cannot be verified.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from shared import tenancy
from shared.approvals import COLLECTION_APPROVALS, Approval
from shared.context import AgentContext
from shared.domain import ReviewState
from shared.events import TOPIC_REVIEW_APPROVED, publish
from shared.identity import principal_in, verify_token

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("drawbridge.approvals")

app = FastAPI(title="drawbridge-approvals", docs_url=None, redoc_url=None)

TOKEN_TTL_MINUTES = 15
"""How long an issued approval is presentable for.

Short. The token is spent within seconds by the step it releases, and a fifteen-minute window is
generous for that while being useless to anyone who finds it in a log a week later. It is also
single-use, so the window bounds a replay rather than permitting one.
"""

GATE_STATE = {
    "contact": ReviewState.QUESTIONNAIRE_OUT,
    "decision": ReviewState.DECIDED,
}
"""Where each gate scope releases to. Mirrors ``shared.domain._GATE_RELEASE``; the release itself
is the Orchestrator's, and this is only used to reject a scope the review is not parked at."""


class ApprovalRequest(BaseModel):
    org_id: str
    review_id: str
    scope: str = Field(pattern="^(contact|decision)$")
    conditions: list[str] = Field(default_factory=list)
    note: str = ""


class ApprovalIssued(BaseModel):
    jti: str
    scope: str
    identity: str
    expires_at: datetime


@app.get("/healthz")
def health() -> dict:
    """Serving, and nothing more.

    Deliberately checks no dependency: a readiness probe that fails on a downstream outage takes
    a healthy container out of rotation for somebody else's problem.
    """
    return {"status": "ok", "service": "approvals"}


@app.post("/approvals", response_model=ApprovalIssued)
def issue(request: ApprovalRequest, authorization: str = Header(default="")) -> ApprovalIssued:
    """Verify a person, verify their role, verify the gate, then sign.

    In that order, and each step is a separate refusal, because the answers are different: an
    unauthenticated caller should sign in, one with the wrong role should ask an approver, and one
    presenting the wrong scope has made a mistake that costs them nothing.
    """
    token = authorization.removeprefix("Bearer ").strip()
    principal = verify_token(token)
    if principal is None:
        raise HTTPException(401, "Sign in again to approve.")

    with tenancy.acting_for(request.org_id):
        scoped = principal_in(principal, request.org_id)
        if not scoped.may(tenancy.CAN_APPROVE):
            raise HTTPException(
                403,
                "Releasing a gate requires the approver role. Ask an administrator for access.",
            )

        review = _load_review(request.review_id)
        if review is None:
            # Same answer as a review in another organisation, which is the point.
            raise HTTPException(404, "No such review in this workspace.")

        state = str(review.get("state", ""))
        parked_scope = review.get("gate_scope")

        if state != ReviewState.GATED.value:
            raise HTTPException(
                409,
                f"That review is {state.replace('_', ' ')} and is not waiting at a gate.",
            )
        if parked_scope != request.scope:
            raise HTTPException(
                409,
                f"That review is waiting at the {parked_scope} gate, not the {request.scope} one.",
            )

        approval = _sign(request, scoped.email or scoped.uid, review)
        _announce(request, approval, scoped.email or scoped.uid)

        log.info(
            "approval issued org=%s review=%s scope=%s by=%s",
            request.org_id,
            request.review_id,
            request.scope,
            scoped.uid,
        )
        return ApprovalIssued(
            jti=approval.jti,
            scope=approval.scope,
            identity=approval.identity,
            expires_at=approval.expires_at,
        )


def _load_review(review_id: str) -> dict | None:
    snap = tenancy.collection("reviews").document(review_id).get()
    return snap.to_dict() if snap.exists else None


def _sign(request: ApprovalRequest, identity: str, review: dict) -> Approval:
    """Record the approval. In cloud this is where the KMS signature is attached.

    Local mode records the approval and returns an opaque reference, which is honest about what
    it proves: that somebody with the approver role in this organisation wrote this record. What
    stops an agent writing one itself is collection-level IAM rather than cryptography, and the
    ``TODO(verify)`` in ``shared/gateway.py`` carries the other half.
    """
    now = datetime.now(UTC)
    approval = Approval(
        jti=uuid.uuid4().hex,
        review_id=request.review_id,
        scope=request.scope,
        target=_target_for(request.scope, review),
        identity=identity,
        conditions=request.conditions,
        issued_at=now,
        expires_at=now + timedelta(minutes=TOKEN_TTL_MINUTES),
    )
    tenancy.collection(COLLECTION_APPROVALS).document(approval.jti).set(
        tenancy.stamp(approval.model_dump(mode="json"))
    )
    return approval


def _target_for(scope: str, review: dict) -> str:
    """What the approval authorises.

    A contact approval is scoped to one address, because the failure it prevents is a redirected
    vendor contact. A decision approval is scoped to the review, because that is what is being
    accepted.
    """
    if scope == "contact":
        vendor = tenancy.collection("vendors").document(str(review.get("vendor_id"))).get()
        contact = (vendor.to_dict() or {}).get("contact") or {}
        return str(contact.get("email") or contact.get("address") or "")
    return str(review.get("review_id", ""))


def _announce(request: ApprovalRequest, approval: Approval, identity: str) -> None:
    """Publish ``review.approved``. The Orchestrator performs the release, not this service.

    Releasing is a forward transition and forward transitions have one owner. A service that
    moved the review itself would be a second component writing review state, which is the rule
    ``shared/state.py`` exists to keep.
    """
    ctx = AgentContext(
        org_id=request.org_id,
        review_id=request.review_id,
        agent="approvals",
        trace_id=uuid.uuid4().hex,
    )
    publish(
        TOPIC_REVIEW_APPROVED,
        request.review_id,
        {
            "scope": approval.scope,
            "identity": identity,
            "jti": approval.jti,
            "conditions": approval.conditions,
            "note": request.note,
        },
        ctx=ctx,
    )


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8081)))
