"""Targeted follow-ups for answers that arrive but are useless.

Chasing handles *missing* answers. It does nothing about *present but vague* ones, and "we
follow industry best practice" is the exact pain that justifies this project existing. The
confidence score the parser already computes is the trigger: below threshold, one targeted
follow-up quotes the vendor's own answer back, names the specific evidence required, and asks
once.

    Your answer to DP01 states: "We follow industry best practice for encryption both at rest
    and in transit."

    The review needs the specific encryption standards and key lengths used for customer data
    at rest and in transit, and your key-management policy as an attachment.

Capped at ``FOLLOWUP_CAP`` per question so politeness cannot become an infinite loop. This is
the difference between an agent that collects and an agent that interrogates.

**The re-ask is composed from the question bank, not by a model.** The bank already states what
evidence each question requires, in the words the review was designed around, so a model here
would paraphrase something exact into something approximate — and it would do it by putting the
vendor's own text into a prompt and mailing the result to a human. That is the tool-poisoning
shape, spent for no gain. The vendor's answer is quoted verbatim rather than summarised, which
is what makes the re-ask specific and also what keeps it inert. Recorded as amendment A12.

Failure semantics: the cap is checked before anything else, so a redelivered reply cannot spend
a send on a question already re-asked twice. The send goes through the gateway under the same
P1 authorisation as any other outbound message on this review, and carries its own idempotency
key (``followup:dp01:v1``). A follow-up that cannot be sent leaves the answer marked
``needs_human`` and the review unchanged — an outstanding re-ask never blocks the coverage
check, because one ambiguous answer must not stall a review that is otherwise complete.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from shared.clients import firestore_client
from shared.context import AgentContext
from shared.gateway import PolicyViolation, call_tool
from shared.idempotency import key_for
from shared.idempotency import once as run_once

log = logging.getLogger("drawbridge.followup")

COLLECTION_FOLLOWUPS = "followups"

BODY = """\
{greeting}

We are completing the security review of {vendor} and one answer needs more detail before we
can assess it.

Your answer to {question_id} states:

    "{quoted}"

The question asked: {question}

To assess this we need {required}. Please reply to this thread.

This is the {ordinal} time we have asked about {question_id}. If the information is not
available, say so and we will record it as a gap rather than keep asking.

Drawbridge, on behalf of the security review team
"""

_ORDINALS = {1: "first", 2: "second", 3: "third"}


def followups_sent(review_id: str, question_id: str) -> int:
    """Return how many follow-ups have already gone out for this question."""
    snap = (
        firestore_client()
        .collection(COLLECTION_FOLLOWUPS)
        .document(f"{review_id}:{question_id}")
        .get()
    )
    return int((snap.to_dict() or {}).get("count", 0))


def outstanding(review_id: str) -> dict[str, int]:
    """Return every question this review has re-asked, and how many times.

    Binder section 2 prints it: an analyst reading the record needs to see that the fleet asked
    twice and the vendor did not improve the answer, which is a finding about the vendor rather
    than about the review.
    """
    from google.cloud.firestore_v1 import FieldFilter

    docs = (
        firestore_client()
        .collection(COLLECTION_FOLLOWUPS)
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    )
    return {d.to_dict()["question_id"]: int(d.to_dict().get("count", 0)) for d in docs}


def maybe_followup(ctx: AgentContext, review_id: str, question_id: str, answer) -> str | None:
    """Send one targeted follow-up if the answer is weak and the cap allows it.

    Returns:
        The idempotency key the send was claimed under, or ``None`` when the answer is above
        threshold, the cap is reached, or the send was refused.
    """
    from agents.questionnaire.delivery import TOOL_SEND_EMAIL
    from shared.config import settings

    cap = settings().followup_cap
    if not getattr(answer, "needs_human", False):
        return None

    already = followups_sent(review_id, question_id)
    if already >= cap:
        # Checked before anything else is done, so a retry storm cannot spend sends on a
        # question the fleet has already stopped asking about.
        log.info(
            "review=%s %s: %d follow-up(s) already sent, cap is %d — recorded as a gap",
            review_id,
            question_id,
            already,
            cap,
        )
        return None

    vendor, recipient, greeting = _contact(review_id)
    if not recipient:
        log.warning("review=%s has no contact address; %s not re-asked", review_id, question_id)
        return None

    round_number = already + 1
    body = compose(
        question_id,
        answer,
        vendor=vendor,
        greeting=greeting,
        round_number=round_number,
    )
    if body is None:
        return None

    idem_key = key_for(review_id, _plan_version(review_id), f"followup:{question_id.lower()}:v1")
    send_ctx = ctx.for_step(idem_key)

    def deliver():
        result = call_tool(
            TOOL_SEND_EMAIL,
            send_ctx,
            to=recipient,
            subject=f"Security review — {vendor}: follow-up on {question_id}",
            body=body,
            review_id=review_id,
            vendor=vendor,
            kind="followup",
            approval_token=None,
        )
        _record(review_id, question_id, round_number)
        return result

    try:
        run_once(idem_key, send_ctx, deliver)
    except PolicyViolation as exc:
        # A follow-up is never worth parking a review for. The answer stays marked needs_human,
        # the analyst sees it on the gate card, and the review carries on.
        log.warning("review=%s follow-up on %s refused by %s", review_id, question_id, exc.policy)
        return None

    log.info(
        "review=%s follow-up %d/%d sent on %s", review_id, round_number, cap, question_id
    )
    return idem_key


def compose(
    question_id: str, answer, *, vendor: str, greeting: str, round_number: int
) -> str | None:
    """Build the re-ask from the question bank and the vendor's own words.

    Returns ``None`` for a question id the bank does not hold — a follow-up that could not name
    what it wanted would be the vague letter this feature exists to replace.
    """
    question = _from_bank(question_id)
    if question is None:
        log.warning("no bank entry for %s; nothing specific to ask for", question_id)
        return None

    return BODY.format(
        greeting=greeting,
        vendor=vendor,
        question_id=question_id,
        quoted=str(getattr(answer, "text", "")).strip(),
        question=" ".join(question.text.split()),
        required=_required_phrase(question),
        ordinal=_ORDINALS.get(round_number + 1, f"{round_number + 1}th"),
    )


def _required_phrase(question) -> str:
    """Name the evidence the bank says this question requires, in plain words.

    The bank stores an identifier, not a sentence, so it is read out rather than pasted in: a
    letter to a vendor asking for a ``key_management_policy`` is a letter that was written by
    nobody.
    """
    asked_for = "the specific values, standards, scopes or dates the question asks for"
    if not question.evidence_required:
        return asked_for
    documents = _join(f"your {_readable(name)}" for name in question.evidence_required)
    return f"{asked_for}, and {documents} attached"


def _readable(name: str) -> str:
    return name.replace("_", " ")


def _join(parts) -> str:
    parts = list(parts)
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _from_bank(question_id: str):
    from agents.questionnaire.generator import load_bank

    for questions in load_bank().values():
        for question in questions:
            if question.question_id == question_id:
                return question
    return None


def _contact(review_id: str) -> tuple[str, str, str]:
    """Return the vendor name, contact address and greeting for this review."""
    from shared.events import load_review

    review = load_review(review_id)
    raw = firestore_client().collection("vendors").document(review.vendor_id).get().to_dict()
    raw = raw or {}
    contact = raw.get("contact") or {}
    name = contact.get("name", "")
    return (
        raw.get("name", review.vendor_id),
        contact.get("email", ""),
        f"Dear {name}," if name else "Hello,",
    )


def _plan_version(review_id: str) -> int:
    from shared.events import load_review

    return int(load_review(review_id).plan_version)


def _record(review_id: str, question_id: str, count: int) -> None:
    """Record the send inside the guarded call, so a crash cannot lose it and re-ask again."""
    firestore_client().collection(COLLECTION_FOLLOWUPS).document(
        f"{review_id}:{question_id}"
    ).set(
        {
            "review_id": review_id,
            "question_id": question_id,
            "count": count,
            "last_sent_at": datetime.now(UTC).isoformat(),
        }
    )
