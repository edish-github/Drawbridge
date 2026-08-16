"""Who may change a review's state, and in which direction.

**Anything can stop a review; only the Orchestrator can advance one.**

Two functions, and the asymmetry between them is the whole design:

``park`` moves a review backwards or sideways — into ``NEEDS_HUMAN`` or ``GATED`` — and is
available to every component. The cost ceiling parks, screening parks, the contact gate parks,
a failed plan parks. Requiring a park to travel through the Orchestrator would mean a lost
message leaves a review claiming to be in flight when it has already stopped, which is a worse
inconsistency than the one it would fix: a stopped review that says so is safe, and a stopped
review that says it is running is how a vendor never gets contacted and nobody notices.

``advance`` moves a review forward, and only ``agents/orchestrator`` calls it. Forward
transitions are where the plan lives — the tier, the plan version, what happens next — and one
owner is what keeps that coherent. ``tests/test_state_ownership.py`` asserts the rule against
the source tree rather than trusting it, because the second agent that "just needed to set
``REPLIES_IN``" is how single ownership quietly ends.

Both validate against ``domain.ALLOWED`` before writing. Both append to the ledger, so the
binder's timeline is complete whichever direction a review moved. Both raise a dashboard card
on a park, because a review that stopped and produced no card is a review nobody is coming
back to.

Failure semantics: an illegal transition raises ``InvalidTransition`` and nothing is written.
``park`` into ``NEEDS_HUMAN`` is legal from every state by construction, so the failure path
itself cannot fail for a transition reason — which is the property that makes it safe to call
from an exception handler.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from shared.clients import firestore_client
from shared.domain import GateScope, Review, ReviewState, validate_transition

log = logging.getLogger("drawbridge.state")

COLLECTION_REVIEWS = "reviews"
COLLECTION_EVENTS = "events"
COLLECTION_DASHBOARD = "dashboard_events"


def park(
    review_id: str,
    *,
    reason: str,
    target: ReviewState = ReviewState.NEEDS_HUMAN,
    gate_scope: GateScope | None = None,
) -> None:
    """Stop a review, with a stated reason, from anywhere in the fleet.

    Args:
        review_id: the review to stop.
        reason: why, in a form an operator can act on. It goes on the record, the ledger event
            and the dashboard card.
        target: ``NEEDS_HUMAN`` for a failure, ``GATED`` for a wait on a human decision.
        gate_scope: required when ``target`` is ``GATED``. ``contact`` is the wait before first
            outbound contact, ``decision`` the wait on risk acceptance; without it the two
            parks are indistinguishable and the wrong release would resume the wrong state.

    Raises:
        InvalidTransition: when the review's current state cannot reach ``target``. Parking
            into ``NEEDS_HUMAN`` is legal everywhere, so this only fires on a gate park from a
            state that has no gate.
    """
    db = firestore_client()
    ref = db.collection(COLLECTION_REVIEWS).document(review_id)
    current = _current_state(ref)

    if current is not None:
        validate_transition(current, target, gate_scope=gate_scope)

    fields: dict = {"state": target.value, "park_reason": reason}
    if target is ReviewState.GATED:
        fields["gate_scope"] = gate_scope
        fields["gate_reason"] = reason
    ref.set(fields, merge=True)

    _record(review_id, "park", from_state=current, to_state=target, reason=reason)
    _dashboard_card(review_id, target, reason, gate_scope)

    log.warning(
        "PARKED review=%s -> %s%s reason=%s",
        review_id,
        target.value,
        f"/{gate_scope}" if gate_scope else "",
        reason,
    )


def advance(
    review: Review,
    target: ReviewState,
    *,
    reason: str = "",
    gate_scope: GateScope | None = None,
    **fields,
) -> None:
    """Move a review forward. Only ``agents/orchestrator`` calls this.

    Args:
        review: the review as loaded, so the transition is validated against the state that
            was actually read rather than against one assumed.
        target: the state to move to.
        reason: recorded on the ledger event. The audit question is always "why did this
            move", and a forward transition with no answer is the one that gets asked about.
        gate_scope: the scope a ``GATED`` review was parked at, when releasing one.
        **fields: additional review fields written in the same operation — tier, plan version,
            score, band. Writing them with the transition rather than beside it means there is
            no window where the state says one thing and the plan says another.

    Raises:
        InvalidTransition: when ``review.state -> target`` is not in the transition table, or
            when a gate release does not match the scope the review was parked at.
    """
    validate_transition(review.state, target, gate_scope=gate_scope or review.gate_scope)

    payload: dict = {"state": target.value, **fields}
    # A review that has moved forward is no longer parked or gated. Leaving either behind
    # would show a released review as still waiting on the dashboard.
    payload["gate_scope"] = None
    payload["park_reason"] = None

    firestore_client().collection(COLLECTION_REVIEWS).document(review.review_id).set(
        payload, merge=True
    )

    _record(
        review.review_id,
        "advance",
        from_state=review.state,
        to_state=target,
        reason=reason,
    )
    log.info(
        "review=%s %s -> %s%s",
        review.review_id,
        review.state.value,
        target.value,
        f" ({reason})" if reason else "",
    )


def _current_state(ref) -> ReviewState | None:
    """Return the review's state as stored, or ``None`` when the review does not exist yet."""
    data = ref.get().to_dict() or {}
    raw = data.get("state")
    if not raw:
        return None
    try:
        return ReviewState(raw)
    except ValueError:
        log.error("review %s holds an unknown state %r", ref.id, raw)
        return None


def _record(
    review_id: str,
    kind: str,
    *,
    from_state: ReviewState | None,
    to_state: ReviewState,
    reason: str,
) -> None:
    """Append a transition to the immutable ledger. The binder's timeline reads this."""
    event_id = uuid.uuid4().hex
    firestore_client().collection(COLLECTION_EVENTS).document(event_id).set(
        {
            "event_id": event_id,
            "type": f"review.{kind}",
            "review_id": review_id,
            "from_state": from_state.value if from_state else None,
            "to_state": to_state.value,
            "reason": reason,
            "ts": datetime.now(UTC).isoformat(),
        }
    )


def _dashboard_card(
    review_id: str, target: ReviewState, reason: str, gate_scope: GateScope | None
) -> None:
    """Raise the card an operator acts on. Never raises: a failed card must not swallow a park."""
    try:
        firestore_client().collection(COLLECTION_DASHBOARD).add(
            {
                "kind": "gate" if target is ReviewState.GATED else "parked",
                "review_id": review_id,
                "state": target.value,
                "gate_scope": gate_scope,
                "reason": reason,
                "at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception as exc:  # noqa: BLE001 — the card is the notification, not the park
        log.error("failed to raise a dashboard card for %s: %s", review_id, exc)
