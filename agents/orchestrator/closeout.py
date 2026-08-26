"""What a finished review leaves behind for the next one.

Layer three of the memory hierarchy has a writer here, and this is the whole point of it being
a layer rather than a cache: a review that ends and remembers nothing makes every future review
of the same vendor start from zero, which is exactly how vendor management works today and
exactly what makes it cost six weeks.

Six things are written, and all six are enumerated by construction:

``outcome``     what a named person decided, and the tier the review ended at
``band``        what the arithmetic said, which is not always the same thing
``conduct_flag``        raised once, carried forever — a vendor's second review opens knowing
                        what they tried in the first
``cert_expiry``         written by the Evidence agent during the review, not here
``contact_change``      the address the correspondence actually used
``question_effectiveness``  per question: was the answer usable, weak, or a non-answer
``approval_condition``      that conditions were attached, and how many

**The conditions themselves are not written here.** They are sentences a person typed, and the
structured-write guard exists precisely to keep sentences out of a store that is recalled into
a prompt before any screening has run in the new review. The note records that conditions
exist and names the review they belong to; the text is read from that review's approval record,
which is the ledger, where sentences live. ``recall.py`` does that read.

``question_effectiveness`` carries a digest of the question text as well as the rating. Skipping
a question next time because it was answered well last time is only sound if it is *the same
question* — a bank edit that reworded it must un-skip it, and comparing digests is how that
stays true without anybody remembering to think about it.

Failure semantics: memory is context, never a control. A note that fails the guard is logged
and skipped, and the review still closes — a decision a human made must not be blocked by a
write to a store whose only job is to inform a review that has not started yet.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

from shared import tenancy as tenant
from shared.domain import MemoryNote, Review
from shared.memory import NoteRejected, remember

log = logging.getLogger("drawbridge.closeout")

RATING_USABLE = "usable"
RATING_LOW_CONFIDENCE = "low_confidence"
RATING_NON_ANSWER = "non_answer"

NON_ANSWER_BELOW = 0.4
"""A confidence at or under which an answer is recorded as a non-answer rather than a weak one.

The distinction matters next time: a weak answer is worth asking again in the hope of better
evidence, and a non-answer is worth asking differently.
"""


def remember_review(review: Review, *, identity: str, ctx=None) -> int:
    """Write everything this review leaves for the next one. Returns the note count."""
    written = 0
    for note in notes_for(review, identity=identity):
        try:
            remember(review.vendor_id, note)
            written += 1
        except NoteRejected as exc:
            # The guard did its job. A note that does not fit is a note that does not get
            # written; widening the guard to admit it would be the wrong repair.
            log.warning("dossier note rejected for %s: %s", review.vendor_id, exc)
        except Exception as exc:  # noqa: BLE001 — memory is context, never a control
            log.warning("could not write a %s note for %s: %s", note.type, review.vendor_id, exc)

    log.info(
        "review=%s closed; %d note(s) written to the dossier for %s",
        review.review_id,
        written,
        review.vendor_id,
    )
    return written


def notes_for(review: Review, *, identity: str) -> list[MemoryNote]:
    """Build the notes this review leaves behind. Pure, so the shape is testable without a write."""
    at = datetime.now(UTC)
    notes: list[MemoryNote] = [
        MemoryNote(
            vendor_id=review.vendor_id,
            type="outcome",
            provenance="human",
            value={
                "value": _outcome(review),
                "review_id": review.review_id,
                "tier": review.tier,
                "decided_by": identity,
            },
            at=at,
        ),
        MemoryNote(
            vendor_id=review.vendor_id,
            type="band",
            provenance="rule",
            value={
                "value": str(review.band or "escalate"),
                "review_id": review.review_id,
                "score": review.score,
            },
            at=at,
        ),
    ]

    contact = _contact(review.vendor_id)
    if contact:
        notes.append(
            MemoryNote(
                vendor_id=review.vendor_id,
                type="contact_change",
                provenance="rule",
                value={"value": "confirmed", "email": contact, "review_id": review.review_id},
                at=at,
            )
        )

    if _adversarial(review.review_id):
        notes.append(
            MemoryNote(
                vendor_id=review.vendor_id,
                type="conduct_flag",
                provenance="rule",
                value={"value": "adversarial_conduct", "review_id": review.review_id},
                at=at,
            )
        )

    conditions = _conditions(review.review_id)
    if conditions:
        notes.append(
            MemoryNote(
                vendor_id=review.vendor_id,
                type="approval_condition",
                provenance="human",
                value={
                    "value": "attached",
                    "review_id": review.review_id,
                    "count": len(conditions),
                },
                at=at,
            )
        )

    notes.extend(_question_notes(review, at))
    return notes


def _question_notes(review: Review, at: datetime) -> list[MemoryNote]:
    """One note per answered question, rating how usable the answer was."""
    from agents.questionnaire.generator import load_bank
    from agents.questionnaire.parser import recorded_answers

    by_id = {
        question.question_id: question
        for questions in load_bank().values()
        for question in questions
    }

    notes = []
    for answer in recorded_answers(review.review_id):
        question = by_id.get(answer.question_id)
        if question is None:
            continue
        notes.append(
            MemoryNote(
                vendor_id=review.vendor_id,
                type="question_effectiveness",
                provenance="rule",
                value={
                    "value": rate(answer.confidence, answer.needs_human),
                    "question_id": answer.question_id,
                    "review_id": review.review_id,
                    "text_digest": digest(question.text),
                },
                at=at,
            )
        )
    return notes


def rate(confidence: float, needs_human: bool) -> str:
    """Return how usable an answer was, as one of three terms."""
    if confidence <= NON_ANSWER_BELOW:
        return RATING_NON_ANSWER
    if needs_human:
        return RATING_LOW_CONFIDENCE
    return RATING_USABLE


def digest(text: str) -> str:
    """Return a short digest of a question's text, whitespace-insensitive.

    An identifier rather than a copy: it lets a later review ask "is this still the same
    question" without durable memory holding the question.
    """
    normalised = " ".join(text.split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:12]


def _outcome(review: Review) -> str:
    """Map the band a review ended on to the outcome vocabulary."""
    return {"approve": "approved", "conditional": "conditional"}.get(
        str(review.band or ""), "rejected"
    )


def _contact(vendor_id: str) -> str:

    raw = tenant.collection("vendors").document(vendor_id).get().to_dict() or {}
    return str((raw.get("contact") or {}).get("email", ""))


def _adversarial(review_id: str) -> bool:
    from google.cloud.firestore_v1 import FieldFilter


    docs = (
        tenant.collection("findings")
        .where(filter=FieldFilter("review_id", "==", review_id))
        .where(filter=FieldFilter("domain", "==", "conduct"))
        .limit(1)
        .stream()
    )
    return any(True for _ in docs)


def _conditions(review_id: str) -> list[str]:
    from shared.approvals import conditions_for

    return conditions_for(review_id)
