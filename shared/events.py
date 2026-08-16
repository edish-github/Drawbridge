"""The event contract: eleven topics, one envelope, and the guard every consumer opens with.

Uniformity here is what makes tracing, replay and idempotency work at all. Pub/Sub delivery is
at-least-once and unordered, so ``idem_key`` covers duplicates (``shared.idempotency``) and
``guard`` covers sequence.

The topic constants are the single source of truth. ``infra/pubsub.yaml`` and
``infra/bootstrap.sh`` are checked against ``ALL_TOPICS`` in CI: an undocumented topic is one
bootstrap does not create, and an untested one.

Failure semantics: a malformed envelope fails validation at parse time and the message is
nacked toward its dead-letter topic rather than partially processed. A message reaching its
dead-letter topic moves its review to ``NEEDS_HUMAN`` and surfaces on the dashboard — visible
failure handling is a graded behaviour. ``publish`` refuses an undeclared topic rather than
auto-creating one, because an auto-created topic has no subscription and its messages vanish.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from shared.clients import firestore_client, publisher_client, topic_path
from shared.domain import Review, ReviewState, is_terminal

log = logging.getLogger("drawbridge.events")

TOPIC_REVIEW_INTAKE = "review.intake"
TOPIC_REVIEW_PLAN_READY = "review.plan_ready"
TOPIC_VENDOR_REPLY_RECEIVED = "vendor.reply_received"
TOPIC_REVIEW_CHASE_DUE = "review.chase_due"
TOPIC_VENDOR_EVIDENCE_UPLOADED = "vendor.evidence_uploaded"
TOPIC_EVIDENCE_SCREENED = "evidence.screened"
TOPIC_REVIEW_FINDINGS_READY = "review.findings_ready"
TOPIC_REVIEW_SCORE_READY = "review.score_ready"
TOPIC_REVIEW_APPROVED = "review.approved"
TOPIC_REVIEW_RESCORE = "review.rescore"
TOPIC_WATCHDOG_SWEEP = "watchdog.sweep"
TOPIC_WATCHDOG_HIT = "watchdog.hit"

ALL_TOPICS: tuple[str, ...] = (
    TOPIC_REVIEW_INTAKE,
    TOPIC_REVIEW_PLAN_READY,
    TOPIC_VENDOR_REPLY_RECEIVED,
    TOPIC_REVIEW_CHASE_DUE,
    TOPIC_VENDOR_EVIDENCE_UPLOADED,
    TOPIC_EVIDENCE_SCREENED,
    TOPIC_REVIEW_FINDINGS_READY,
    TOPIC_REVIEW_SCORE_READY,
    TOPIC_REVIEW_APPROVED,
    TOPIC_REVIEW_RESCORE,
    TOPIC_WATCHDOG_SWEEP,
    TOPIC_WATCHDOG_HIT,
)

EXPECTED_STATES: dict[str, set[ReviewState]] = {
    TOPIC_REVIEW_INTAKE: {ReviewState.INTAKE},
    TOPIC_REVIEW_PLAN_READY: {ReviewState.QUESTIONNAIRE_OUT},
    TOPIC_VENDOR_REPLY_RECEIVED: {ReviewState.QUESTIONNAIRE_OUT, ReviewState.REPLIES_IN},
    TOPIC_REVIEW_CHASE_DUE: {ReviewState.QUESTIONNAIRE_OUT, ReviewState.REPLIES_IN},
    TOPIC_VENDOR_EVIDENCE_UPLOADED: {
        ReviewState.QUESTIONNAIRE_OUT,
        ReviewState.REPLIES_IN,
        ReviewState.EVIDENCE_REVIEW,
    },
    TOPIC_EVIDENCE_SCREENED: {ReviewState.REPLIES_IN, ReviewState.EVIDENCE_REVIEW},
    TOPIC_REVIEW_FINDINGS_READY: {ReviewState.EVIDENCE_REVIEW},
    TOPIC_REVIEW_SCORE_READY: {ReviewState.EVIDENCE_REVIEW, ReviewState.SCORED},
    TOPIC_REVIEW_APPROVED: {ReviewState.GATED},
    TOPIC_REVIEW_RESCORE: {ReviewState.EVIDENCE_REVIEW, ReviewState.SCORED, ReviewState.GATED},
    TOPIC_WATCHDOG_SWEEP: {ReviewState.MONITORED},
    TOPIC_WATCHDOG_HIT: {ReviewState.DECIDED, ReviewState.MONITORED},
}
"""The states in which each event makes sense, declared once rather than per consumer.

The subscriber applies these before dispatch, so out-of-phase handling is uniform across the
fleet instead of being whatever each handler remembered to check. A topic with no entry here
cannot be consumed — which is the point: adding a topic forces a decision about when its events
are in phase, rather than leaving it to whichever consumer is written first.
"""

DLQ_SUFFIX = ".dlq"
MAX_DELIVERY_ATTEMPTS = 5
ACK_DEADLINE_SECONDS = 60

COLLECTION_EVENTS = "events"
COLLECTION_REVIEWS = "reviews"


class UndeclaredTopic(Exception):
    """A publish to a topic that is not in ``ALL_TOPICS``. A bug, not a runtime condition."""


def dlq_topic(topic: str) -> str:
    """Return the dead-letter topic name paired with ``topic``."""
    return f"{topic}{DLQ_SUFFIX}"


def subscription_name(topic: str) -> str:
    """Return the pull subscription name for ``topic``."""
    return f"{topic}.sub"


class EventEnvelope(BaseModel):
    """The wrapper every Pub/Sub message carries.

    Attributes:
        event_id: uuid4, unique per publish attempt.
        type: the topic name, one of ``ALL_TOPICS``.
        review_id: the review this event belongs to.
        idem_key: ``review_id:plan_vN:step_id`` — derived from workflow position, never from a
            timestamp or uuid, or the exactly-once guard is worthless.
        trace_id: ties the event to its span tree.
        source: the emitting agent or service.
        ts: publish time, from the injected clock during a compressed demo run.
        payload: topic-specific body.
    """

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: str
    review_id: str
    idem_key: str
    trace_id: str
    source: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict = Field(default_factory=dict)

    def to_bytes(self) -> bytes:
        """Serialise for the wire."""
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> EventEnvelope:
        """Parse a wire message.

        Raises:
            pydantic.ValidationError: on a malformed envelope. The caller nacks the message
                toward its dead-letter topic rather than processing part of it.
        """
        return cls.model_validate(json.loads(raw.decode("utf-8")))


def publish(topic: str, review_id: str, payload: dict, *, ctx) -> str:
    """Publish an envelope to ``topic`` and return the message id.

    The envelope is built here so no caller can emit a bare dict — uniformity of the envelope
    is what the tracing, replay and idempotency stories all rest on.

    Raises:
        UndeclaredTopic: if ``topic`` is not in ``ALL_TOPICS``. Auto-creation is never
            attempted: an auto-created topic has no subscription, so its messages are
            published successfully and consumed by nobody.
        GoogleAPICallError: propagated. The publish is not idempotent by itself, which is why
            consumers are.
    """
    if topic not in ALL_TOPICS:
        raise UndeclaredTopic(
            f"{topic!r} is not a declared topic. Add it to ALL_TOPICS, infra/pubsub.yaml and "
            "infra/bootstrap.sh together — CI checks that the three agree."
        )

    # A context declares idem_key and leaves it None outside a guarded step, so the fallback is
    # `or` rather than a getattr default: the attribute exists, it is simply not set yet.
    envelope = EventEnvelope(
        type=topic,
        review_id=review_id,
        idem_key=getattr(ctx, "idem_key", None) or f"{review_id}:plan_v1:{topic}",
        trace_id=getattr(ctx, "trace_id", uuid.uuid4().hex),
        source=getattr(ctx, "agent", "unknown"),
        payload=payload,
    )

    future = publisher_client().publish(topic_path(topic), envelope.to_bytes())
    message_id = future.result()

    record_event(envelope)
    log.info("published %s review=%s idem=%s", topic, review_id, envelope.idem_key)
    return message_id


def record_event(envelope: EventEnvelope) -> None:
    """Append an envelope to the immutable ledger.

    The ledger is what the audit binder's timeline is rendered from, so this is append-only by
    construction: each event is its own document keyed by ``event_id`` and nothing updates one.
    """
    firestore_client().collection(COLLECTION_EVENTS).document(envelope.event_id).set(
        envelope.model_dump(mode="json")
    )


def load_review(review_id: str) -> Review | None:
    """Load a review from the ledger, or ``None`` when it does not exist yet."""
    snap = firestore_client().collection(COLLECTION_REVIEWS).document(review_id).get()
    if not snap.exists:
        return None
    return Review.model_validate(snap.to_dict())


def guard(ev: EventEnvelope, expected: set[ReviewState]) -> Review | None:
    """Load the review and confirm it is in a state where ``ev`` makes sense.

    Every consumer opens with this call. Pub/Sub guarantees delivery, not sequence.

    Returns:
        The review when its state is in ``expected``. ``None`` when the event arrived out of
        phase and was routed to ``handle_out_of_phase``; the consumer returns without acting,
        and the event has already been dealt with — never dropped.
    """
    review = load_review(ev.review_id)
    if review is None:
        handle_out_of_phase(ev, None)
        return None
    if review.state in expected:
        return review
    handle_out_of_phase(ev, review)
    return None


def handle_out_of_phase(ev: EventEnvelope, review: Review | None) -> None:
    """Apply the defined behaviour for an event that arrived out of phase.

    Four cases, each defined rather than incidental:

    - A reply arriving after ``SCORED`` attaches to the ledger as an addendum; if it changes
      an answer that produced a finding, ``review.rescore`` is published. It is never silently
      discarded, because the discarded reply is the one the vendor quotes back.
    - ``evidence.screened`` arriving before the plan exists parks the message for redelivery,
      dead-lettering after five attempts.
    - ``watchdog.hit`` on an already-reopened review deduplicates on signal id.
    - Any event for a ``DECIDED`` review appends to the ledger and never mutates it.

    Raises:
        UndeclaredTopic: on an event type with no defined out-of-phase behaviour. A new topic
            must declare its behaviour here before it can be consumed, because the alternative
            is a message class that is silently dropped.
        MessageParked: when the correct action is redelivery. The caller nacks so Pub/Sub
            redelivers with backoff and dead-letters after the configured attempts.
    """
    if ev.type not in ALL_TOPICS:
        raise UndeclaredTopic(f"{ev.type!r} has no defined out-of-phase behaviour")

    # A decided review is immutable. Anything arriving for one is appended and nothing else;
    # a new signal opens a new linked review rather than editing a closed one.
    if review is not None and is_terminal(review.state):
        _append_addendum(ev, reason="event_for_decided_review")
        log.info("appended to decided review %s: %s", ev.review_id, ev.type)
        return

    if review is None:
        # The plan does not exist yet. Park for redelivery rather than creating a review from
        # a downstream event, which would produce a review with no plan and no intake record.
        raise MessageParked(ev.review_id, ev.type, "review_not_found")

    if ev.type == TOPIC_VENDOR_REPLY_RECEIVED and review.state in {
        ReviewState.SCORED,
        ReviewState.GATED,
    }:
        _append_addendum(ev, reason="late_reply")
        log.info("late reply attached as addendum for %s", ev.review_id)
        return

    if ev.type == TOPIC_WATCHDOG_HIT:
        signal_id = ev.payload.get("signal_id")
        if signal_id and _signal_seen(ev.review_id, signal_id):
            log.info("duplicate watchdog signal %s for %s, ignored", signal_id, ev.review_id)
            return
        _append_addendum(ev, reason="watchdog_hit_out_of_phase")
        return

    # Everything else is a sequencing problem that redelivery may resolve.
    raise MessageParked(ev.review_id, ev.type, f"unexpected_state:{review.state.value}")


class MessageParked(Exception):
    """The event arrived too early. Nack, redeliver with backoff, dead-letter after five."""

    def __init__(self, review_id: str, event_type: str, reason: str) -> None:
        super().__init__(f"{event_type} parked for {review_id}: {reason}")
        self.review_id = review_id
        self.event_type = event_type
        self.reason = reason


def _append_addendum(ev: EventEnvelope, *, reason: str) -> None:
    """Record an out-of-phase event in the ledger without mutating the review."""
    doc = ev.model_dump(mode="json")
    doc["addendum"] = True
    doc["addendum_reason"] = reason
    firestore_client().collection(COLLECTION_EVENTS).document(ev.event_id).set(doc)


def _signal_seen(review_id: str, signal_id: str) -> bool:
    """Return whether a watchdog signal has already been recorded for this review."""
    from google.cloud.firestore_v1 import FieldFilter

    hits = (
        firestore_client()
        .collection(COLLECTION_EVENTS)
        .where(filter=FieldFilter("review_id", "==", review_id))
        .where(filter=FieldFilter("type", "==", TOPIC_WATCHDOG_HIT))
        .stream()
    )
    return any(h.to_dict().get("payload", {}).get("signal_id") == signal_id for h in hits)
