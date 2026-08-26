"""Chasing missing answers on a schedule.

Three rounds, capped, escalating in tone: reminder, then deadline notice, then escalation to
the analyst. Each round carries its own idempotency key (``chase:round2``), so a restart
mid-round does not send the same reminder twice.

Like the follow-up, the message is composed from the plan and the question bank rather than by
a model. A chase names the questions still outstanding and a date; both are facts the fleet
already holds exactly, and a model asked to phrase them would be a model call in the outbound
path for no gain.

Failure semantics: three rounds without a response ends in ``NEEDS_HUMAN`` rather than a fourth
round — politeness that cannot terminate is a loop. A send blocked under P1 parks the review at
the contact gate; the chase schedule resumes on release rather than restarting. A chase is
never sent to a vendor who has answered everything, and never for questions already answered.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from shared import tenancy as tenant
from shared.context import AgentContext
from shared.domain import Review, ReviewState
from shared.gateway import PolicyViolation, call_tool
from shared.idempotency import key_for
from shared.idempotency import once as run_once
from shared.state import park

log = logging.getLogger("drawbridge.chaser")

MAX_CHASE_ROUNDS = 3
CHASE_INTERVAL_DAYS = 3

FIELD_ROUNDS = "chase_rounds"
FIELD_NEXT = "chase_due_at"

TONES = {
    1: (
        "Following up on our security review",
        "We sent a security questionnaire for {vendor} on behalf of the purchasing team and "
        "have not yet had answers to the questions below. A reply on any of them moves the "
        "review forward.",
    ),
    2: (
        "Security review — response needed by {due}",
        "This is a second request. The security review of {vendor} cannot be completed while "
        "the questions below are unanswered, and the review is currently held. We need a "
        "response by {due}.",
    ),
    3: (
        "Security review — escalating to the review owner",
        "This is our third and final request. The questions below have been outstanding since "
        "the review opened. We are passing the review to its owner to decide how to proceed, "
        "and will record the outstanding items as unanswered.",
    ),
}

BODY = """\
{greeting}

{opening}

Outstanding:
{questions}

Drawbridge, on behalf of the security review team
"""


def schedule_chase(review_id: str, *, days: int = CHASE_INTERVAL_DAYS) -> datetime:
    """Schedule the next chase and return when it fires.

    During a compressed demo run the time is computed against the injected clock, so a chase
    timer accelerates with everything else.
    """
    from shared.clock import now

    due = now() + timedelta(days=days)
    tenant.collection("reviews").document(review_id).set(
        {FIELD_NEXT: due.isoformat()}, merge=True
    )
    return due


def chase_due(review_id: str) -> bool:
    """Return whether the scheduled chase time has passed."""
    from shared.clock import now

    snap = tenant.collection("reviews").document(review_id).get()
    raw = (snap.to_dict() or {}).get(FIELD_NEXT)
    if not raw:
        return False
    return now() >= datetime.fromisoformat(raw)


def chase_round(review_id: str) -> int:
    """Return how many chases have already been sent for this review."""
    snap = tenant.collection("reviews").document(review_id).get()
    return int((snap.to_dict() or {}).get(FIELD_ROUNDS, 0))


def outstanding_questions(review_id: str) -> list:
    """Return the planned questions with no recorded answer of any confidence.

    A question answered badly is the follow-up's problem, not the chaser's. Chasing a vendor
    for something they already sent is how an automated reminder becomes an ignored one.
    """
    from agents.questionnaire.generator import load_bank
    from agents.questionnaire.parser import answered_ids, planned_questions

    planned = planned_questions(review_id) - answered_ids(review_id)
    bank = load_bank()
    return [q for questions in bank.values() for q in questions if q.question_id in planned]


def send_chase(ctx: AgentContext, review_id: str) -> str | None:
    """Compose and send the next chase, escalating in tone by round.

    Returns:
        The idempotency key the send was claimed under, or ``None`` when there is nothing
        outstanding or the cap is reached — at which point the review moves to ``NEEDS_HUMAN``
        with a card explaining that the vendor stopped responding.
    """
    from agents.questionnaire.delivery import TOOL_SEND_EMAIL
    from shared.events import load_review

    review = load_review(review_id)
    questions = outstanding_questions(review_id)
    if not questions:
        log.info("review=%s has no outstanding questions; nothing to chase", review_id)
        return None

    round_number = chase_round(review_id) + 1
    if round_number > MAX_CHASE_ROUNDS:
        stop_chasing(review, len(questions))
        return None

    vendor, recipient, greeting = _contact(review)
    if not recipient:
        log.warning("review=%s has no contact address; nothing chased", review_id)
        return None

    subject, opening = TONES[round_number]
    due = _due_date()
    body = BODY.format(
        greeting=greeting,
        opening=opening.format(vendor=vendor, due=due),
        questions="\n".join(f"  {q.question_id}. {' '.join(q.text.split())}" for q in questions),
    )

    idem_key = key_for(review_id, review.plan_version, f"chase:round{round_number}")
    send_ctx = ctx.for_step(idem_key)

    def deliver():
        result = call_tool(
            TOOL_SEND_EMAIL,
            send_ctx,
            to=recipient,
            subject=subject.format(vendor=vendor, due=due),
            body=body,
            review_id=review_id,
            vendor=review.vendor_id,
            kind="chase",
            approval_token=None,
        )
        _record_round(review_id, round_number)
        return result

    try:
        run_once(idem_key, send_ctx, deliver)
    except PolicyViolation as exc:
        park(review_id, reason=str(exc), target=ReviewState.GATED, gate_scope="contact")
        log.warning("review=%s chase %d refused by %s", review_id, round_number, exc.policy)
        return None

    schedule_chase(review_id)
    log.info(
        "review=%s chase %d/%d sent, %d question(s) outstanding",
        review_id,
        round_number,
        MAX_CHASE_ROUNDS,
        len(questions),
    )
    return idem_key


def stop_chasing(review: Review, outstanding_count: int) -> None:
    """End the chase schedule and hand the review to a person.

    A fourth reminder is a loop, and a vendor who has ignored three is not going to answer the
    fourth. What a person decides here is whether to proceed on partial answers or to stop
    buying from this vendor, and neither is a decision the fleet gets to make.
    """
    park(
        review.review_id,
        reason=(
            f"the vendor did not respond to {MAX_CHASE_ROUNDS} chases; "
            f"{outstanding_count} question(s) remain unanswered"
        ),
        target=ReviewState.NEEDS_HUMAN,
    )
    log.warning(
        "review=%s stopped chasing after %d rounds with %d question(s) outstanding",
        review.review_id,
        MAX_CHASE_ROUNDS,
        outstanding_count,
    )


def _record_round(review_id: str, round_number: int) -> None:
    """Record the round inside the guarded send, so a crash cannot repeat a reminder."""
    tenant.collection("reviews").document(review_id).set(
        {FIELD_ROUNDS: round_number, "last_chase_at": datetime.now(UTC).isoformat()}, merge=True
    )


def _due_date() -> str:
    from shared.clock import now

    return (now() + timedelta(days=CHASE_INTERVAL_DAYS)).date().isoformat()


def _contact(review: Review) -> tuple[str, str, str]:
    raw = tenant.collection("vendors").document(review.vendor_id).get().to_dict()
    raw = raw or {}
    contact = raw.get("contact") or {}
    name = contact.get("name", "")
    return (
        raw.get("name", review.vendor_id),
        contact.get("email", ""),
        f"Dear {name}," if name else "Hello,",
    )
