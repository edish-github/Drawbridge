"""Reading human approvals. Nothing here can create one.

The asymmetry is the whole point, so it is structural rather than documented: this module
exposes lookup and single-use consumption and holds no minting function at all. Issuing lives
outside the shared package entirely — in the approval service, and locally in
``scripts/issue_token.py`` — so an agent that imports everything it can reach still cannot
manufacture a human decision. ``gateway.issue_approval_token`` raises for the same reason.

An approval is scoped. ``contact`` authorises first outbound contact for one review (human gate
G2, made unskippable by policy P1); ``decision`` authorises the risk acceptance (G1). A token
that verified for either would let a contact approval double as a vendor approval, which is the
failure the gate scope exists to prevent.

**Single use is enforced by recording the spend, not by editing the approval.** The gateway
writes to ``approval_tokens_spent`` and never to ``approvals``, which keeps the collection-level
IAM claim true: only ``sa-approvals`` writes approvals, and the gateway can recognise and retire
a decision without being able to author one.

Local verification is a record lookup rather than a signature check. That is honest about what
it proves: it proves the token corresponds to an approval someone wrote into the ``approvals``
collection, and the thing stopping an agent writing one itself is collection-level IAM, not
cryptography. Cloud verification is asymmetric and lands with the approval service —
``gateway.verify_approval_token`` carries the ``TODO(verify)``.

Failure semantics: every failure path returns falsy rather than raising. A bad token is a policy
outcome, and P1 is the single place that outcome is logged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from google.cloud import firestore
from pydantic import BaseModel, Field

from shared.clients import firestore_client
from shared.domain import GateScope

log = logging.getLogger("drawbridge.approvals")

COLLECTION_APPROVALS = "approvals"
COLLECTION_SPENT = "approval_tokens_spent"

TOKEN_PREFIX = "drawbridge-approval:"
"""Prefix on every token string. A token that does not carry it is not one of ours."""


class Approval(BaseModel):
    """One human decision, recorded by the approval service.

    Attributes:
        jti: the token id. Unique per approval, and what single-use is tracked against.
        review_id: the review this decision belongs to. A token is never portable between
            reviews.
        scope: which gate was released.
        target: what the decision authorises — the recipient address for a contact approval.
        identity: the named human who decided. An approval by "the system" is not an approval.
        issued_at, expires_at: the validity window.
    """

    jti: str
    review_id: str
    scope: GateScope
    target: str
    identity: str
    issued_at: datetime
    expires_at: datetime
    conditions: list[str] = Field(default_factory=list)
    """What the approver required, in their own words.

    Kept here rather than in durable memory on purpose. Memory holds enumerated terms and
    identifiers; a condition is a sentence a person wrote, and the ledger is where sentences
    live. A later review recalls that conditions were attached and reads them from here.
    """


def token_for(jti: str) -> str:
    """Render the token string a holder presents to the gateway."""
    return f"{TOKEN_PREFIX}{jti}"


def jti_of(token: str) -> str | None:
    """Return the token id carried by ``token``, or ``None`` if it is not one of ours."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    return token[len(TOKEN_PREFIX) :].strip() or None


def pending_token(review_id: str, scope: GateScope) -> str | None:
    """Return an unspent approval token for this review and scope, or ``None``.

    Read-only, and the only thing an agent needs: an agent presents whatever approval exists
    and the gateway decides whether it is good. An agent that could tell a valid token from an
    invalid one before presenting it would be duplicating the check it is supposed to be
    subject to.
    """
    from google.cloud.firestore_v1 import FieldFilter

    try:
        docs = (
            firestore_client()
            .collection(COLLECTION_APPROVALS)
            .where(filter=FieldFilter("review_id", "==", review_id))
            .where(filter=FieldFilter("scope", "==", scope))
            .stream()
        )
    except Exception as exc:  # noqa: BLE001 — an unreadable approvals collection is a closed gate
        log.warning("could not read approvals for review=%s: %s", review_id, exc)
        return None

    for doc in docs:
        data = doc.to_dict() or {}
        if not already_spent(data.get("jti", "")):
            return token_for(data["jti"])
    return None


def conditions_for(review_id: str) -> list[str]:
    """Return the conditions a decision approval attached to this review, in the approver's words.

    Read from the ledger rather than from durable memory, deliberately. Memory holds enumerated
    terms and identifiers; a condition is a sentence a person typed, and a store that is
    recalled into a prompt at the start of a future review — before any screening has run in
    that review — is not where sentences belong. The dossier records that conditions exist and
    names the review; this is the read that resolves them.

    Raises:
        Nothing. An unreadable approvals collection returns nothing and the caller reports the
        conditions as unavailable rather than as absent.
    """
    from google.cloud.firestore_v1 import FieldFilter

    try:
        docs = (
            firestore_client()
            .collection(COLLECTION_APPROVALS)
            .where(filter=FieldFilter("review_id", "==", review_id))
            .where(filter=FieldFilter("scope", "==", "decision"))
            .stream()
        )
    except Exception as exc:  # noqa: BLE001 — conditions are context, never a control
        log.warning("could not read conditions for review=%s: %s", review_id, exc)
        return []

    out: list[str] = []
    for doc in docs:
        out.extend(str(c) for c in (doc.to_dict() or {}).get("conditions", []) if str(c).strip())
    return out


def load_approval(jti: str) -> Approval | None:
    """Return the approval record for ``jti``, or ``None`` when there is none."""
    snap = firestore_client().collection(COLLECTION_APPROVALS).document(jti).get()
    if not snap.exists:
        return None
    try:
        return Approval.model_validate(snap.to_dict())
    except Exception as exc:  # noqa: BLE001 — a malformed approval is not an approval
        log.warning("approval %s does not validate: %s", jti, exc)
        return None


def already_spent(jti: str) -> bool:
    """Return whether this token has been honoured before. Never raises."""
    if not jti:
        return True
    try:
        return firestore_client().collection(COLLECTION_SPENT).document(jti).get().exists
    except Exception as exc:  # noqa: BLE001 — unreadable means treat as spent, the safe way
        log.warning("could not check whether %s was spent: %s", jti, exc)
        return True


def contact_established(review_id: str, target: str) -> bool:
    """Return whether a human has already authorised contact with ``target`` on this review.

    P1 gates **first** contact, which is what the G2 human gate is. Once a named person has
    approved writing to an address, the fleet may write to that address again on the same
    review: the chase rounds, the targeted follow-up and the additional questions a re-tier
    produces are all the same conversation, and asking a CISO to re-approve each message would
    make the gate noise rather than a control.

    The check is on the spend record rather than on a flag, so it is exactly as narrow as the
    approval was: a **different** address on the same review has not been authorised by
    anything and still needs its own token. That is the case the policy is actually protecting
    against — a vendor contact that changed mid-review is the classic redirection.

    Never raises. An unreadable spend record returns ``False``, which closes the gate rather
    than opening it.
    """
    from google.cloud.firestore_v1 import FieldFilter

    if not target:
        return False

    try:
        spent = (
            firestore_client()
            .collection(COLLECTION_SPENT)
            .where(filter=FieldFilter("review_id", "==", review_id))
            .where(filter=FieldFilter("target", "==", target))
            .limit(1)
            .stream()
        )
        return any(True for _ in spent)
    except Exception as exc:  # noqa: BLE001 — unreadable means not established, the safe way
        log.warning("could not check prior contact for review=%s: %s", review_id, exc)
        return False


def spend(jti: str, *, review_id: str, target: str) -> bool:
    """Retire a token transactionally. Returns ``False`` if it was already spent.

    The transaction is what makes single use true under concurrent delivery: Pub/Sub is
    at-least-once, so two workers can present the same token in the same second, and a
    read-then-write would let both through.
    """
    db = firestore_client()
    ref = db.collection(COLLECTION_SPENT).document(jti)

    @firestore.transactional
    def claim(tx) -> bool:
        if ref.get(transaction=tx).exists:
            return False
        tx.set(
            ref,
            {
                "jti": jti,
                "review_id": review_id,
                "target": target,
                "spent_at": datetime.now(UTC).isoformat(),
            },
        )
        return True

    try:
        return claim(db.transaction())
    except Exception as exc:  # noqa: BLE001 — a spend that cannot be recorded is not honoured
        log.warning("could not retire approval %s: %s", jti, exc)
        return False
