"""The screening service: the only component that reads quarantine.

A service rather than an agent, and it runs as its own identity. It handles the rawest, most
hostile bytes in the system and is the one component structurally incapable of prompting
anything — no Vertex AI role, no email, no ``findings`` write. It reads a quarantined upload,
extracts its text locally, screens that text, and either promotes the result to the clean
bucket or leaves it where it is.

It writes no finding. The pipeline records the screening and publishes; the consuming agent
records the consequence. That boundary is what keeps the component handling the most hostile
content incapable of writing into the score, and it is why Adversarial Conduct is raised in the
Risk Scorer rather than here.

Failure semantics: Model Armor is a mandatory control and fails closed. If it is unavailable,
or if any critical filter did not execute, nothing is promoted, the review parks, and the
object stays in quarantine until its lifecycle rule deletes it — while the inert excerpt in the
ledger survives, so the binder is complete after the payload is gone. In local mode the stub is
untrustworthy by construction, so this path parks every time, which is the control working
rather than a bug to route around.
"""

from __future__ import annotations

import logging

from shared.armor import ArmorSkipped, ArmorUnavailable, screen_and_promote
from shared.context import context_for
from shared.domain import Review
from shared.events import (
    TOPIC_EVIDENCE_SCREENED,
    TOPIC_VENDOR_EVIDENCE_UPLOADED,
    EventEnvelope,
    publish,
)
from shared.telemetry import record_decision, span

log = logging.getLogger("drawbridge.screening")

SERVICE_ACCOUNT = "sa-armor"


class UnhandledEvent(Exception):
    """An event reached the screening service with no branch for it."""


def handle_event(event: EventEnvelope, review: Review) -> None:
    """Pub/Sub entrypoint. Routes by ``event.type``.

    Raises:
        UnhandledEvent: on an event type this service has no branch for.
    """
    if event.type == TOPIC_VENDOR_EVIDENCE_UPLOADED:
        on_evidence_uploaded(event, review)
        return
    raise UnhandledEvent(f"screening has no branch for {event.type!r}")


def on_evidence_uploaded(event: EventEnvelope, review: Review) -> None:
    """Screen one quarantined upload and promote it if it earns a clean stamp.

    Raises:
        ArmorUnavailable, ArmorSkipped: nothing is promoted and the review has already been
            parked by the screening path. The exception propagates so the message is nacked and
            redelivered rather than acked into silence.
    """
    ctx = context_for(event, agent="screening")
    quarantine_ref = str(event.payload.get("ref", ""))

    with span("screening.promote", ctx, ref=quarantine_ref) as s:
        if not quarantine_ref:
            raise UnhandledEvent(
                f"upload event for {review.review_id} names no object reference"
            )

        try:
            result = screen_and_promote(quarantine_ref, review.review_id)
        except (ArmorUnavailable, ArmorSkipped) as exc:
            record_decision(
                s,
                goal=f"screen and promote {quarantine_ref}",
                decision=f"failed closed: {type(exc).__name__}; nothing promoted",
            )
            log.warning("failed closed on %s: %s", quarantine_ref, exc)
            raise

        record_decision(
            s,
            goal=f"screen and promote {quarantine_ref}",
            decision=f"promoted under {result.summary()}",
        )
        publish(
            TOPIC_EVIDENCE_SCREENED,
            review.review_id,
            {"ref": quarantine_ref, "verdict": result.summary(), "clean": result.clean},
            ctx=ctx,
        )
