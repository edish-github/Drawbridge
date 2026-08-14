"""The event contract: the eleven topic names and the envelope every message carries.

Uniformity here is what makes tracing, replay and idempotency work at all. Pub/Sub delivery
is at-least-once and unordered, so ``idem_key`` covers duplicates (``shared/idempotency``)
and the state guard covers sequence (``guard`` below).

The topic constants are the single source of truth. ``infra/pubsub.yaml`` and
``infra/bootstrap.sh`` are checked against ``ALL_TOPICS`` in CI: an undocumented topic is
one bootstrap does not create, and an untested one.

Failure semantics: a malformed envelope fails validation at parse time and the message is
nacked toward the dead-letter topic rather than being partially processed. A message that
reaches its dead-letter topic moves its review to ``NEEDS_HUMAN`` and surfaces on the
dashboard.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from shared.domain import Review, ReviewState

TOPIC_REVIEW_INTAKE = "review.intake"
TOPIC_REVIEW_PLAN_READY = "review.plan_ready"
TOPIC_VENDOR_REPLY_RECEIVED = "vendor.reply_received"
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
    TOPIC_VENDOR_EVIDENCE_UPLOADED,
    TOPIC_EVIDENCE_SCREENED,
    TOPIC_REVIEW_FINDINGS_READY,
    TOPIC_REVIEW_SCORE_READY,
    TOPIC_REVIEW_APPROVED,
    TOPIC_REVIEW_RESCORE,
    TOPIC_WATCHDOG_SWEEP,
    TOPIC_WATCHDOG_HIT,
)

DLQ_SUFFIX = ".dlq"
MAX_DELIVERY_ATTEMPTS = 5
ACK_DEADLINE_SECONDS = 60


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
        idem_key: ``f"{review_id}:plan_v{n}:{step_id}"`` — derived from workflow position,
            never from a timestamp or uuid, or the exactly-once guard is worthless.
        trace_id: ties the event to its OpenTelemetry span tree.
        source: the emitting agent or service.
        ts: publish time, from the injected clock during a compressed demo run.
        payload: topic-specific body.
    """

    event_id: str
    type: str
    review_id: str
    idem_key: str
    trace_id: str
    source: str
    ts: datetime
    payload: dict


def publish(topic: str, review_id: str, payload: dict, *, ctx) -> str:
    """Publish an envelope to ``topic`` and return the message id.

    Raises:
        ValueError: if ``topic`` is not in ``ALL_TOPICS``. Publishing to an undeclared
            topic is a bug, not a runtime condition, and auto-creation is never attempted.
        GoogleAPICallError: propagated; the caller's retry policy applies. The publish is
            not idempotent by itself, which is why consumers are.
    """
    raise NotImplementedError


def guard(ev: EventEnvelope, expected: set[ReviewState]) -> Review | None:
    """Load the review and confirm it is in a state where ``ev`` makes sense.

    Every consumer opens with this call. Pub/Sub guarantees delivery, not sequence.

    Returns:
        The review when its state is in ``expected``. ``None`` when the event arrived out
        of phase and was routed to ``handle_out_of_phase``; the consumer returns without
        acting, and the event has already been dealt with — never dropped.
    """
    raise NotImplementedError


def handle_out_of_phase(ev: EventEnvelope, review: Review) -> None:
    """Apply the defined behaviour for an event that arrived out of phase.

    Four cases, each defined rather than incidental:

    - A reply arriving after ``SCORED`` attaches to the ledger as an addendum; if it
      changes an answer that produced a finding, ``review.rescore`` is published. It is
      never silently discarded, because the discarded reply is the one the vendor quotes
      back.
    - ``evidence.screened`` arriving before the plan exists parks the message and retries
      with backoff, dead-lettering after five attempts.
    - ``watchdog.hit`` on an already-reopened review deduplicates on signal id.
    - Any event for a ``DECIDED`` review appends to the ledger and never mutates it.

    Raises:
        NotImplementedError: on an event type with no defined out-of-phase behaviour. A
            new topic must declare its behaviour here before it can be consumed.
    """
    raise NotImplementedError
