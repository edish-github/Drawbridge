"""The pull loop: what turns published events into agent work.

``publish`` and ``guard`` existed before this module and nothing pulled, so the event backbone
was a write-only log. This is the consumer side, and it is deliberately one loop shared by every
agent rather than a subscriber per service: the state guard, the acknowledgement rule and the
out-of-phase behaviour are properties of the *event contract*, not of any one agent, and five
copies of them would drift.

**Synchronous pull, single-threaded, no callbacks.** The streaming-pull client runs handlers on
a background thread pool, which makes a SIGKILL mid-step land somewhere non-deterministic and
makes the resume tests measure the client library rather than the guard. A blocking pull loop
dies exactly where the kill lands, which is what the kill-and-resume demo needs to be a
demonstration rather than an anecdote.

Acknowledgement rule, and it is the whole failure story:

- handled cleanly — ack. The work is durable in the ledger before the ack.
- ``MessageParked`` — nack. The event arrived early; Pub/Sub redelivers with backoff and
  dead-letters after five attempts, at which point the review parks and shows on the dashboard.
- a malformed envelope — ack after recording it. Redelivering a message that cannot be parsed
  produces four more identical failures and a dead letter, and the parse will not start working.
- anything else — nack, so a transient dependency failure is retried rather than swallowed.

The runner subscribes only to topics with a registered handler. An unhandled topic is left
unconsumed on purpose: its messages accumulate on a subscription where they can be seen, rather
than being pulled and dropped by a loop that had nowhere to send them.

Failure semantics: a handler that raises leaves the message unacked and the review untouched
beyond whatever it checkpointed, which is what makes every handler safe to redeliver. Nothing
here catches ``KeyboardInterrupt`` beyond stopping the loop, so an operator's Ctrl-C is not
mistaken for a message failure.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable

from pydantic import ValidationError

from shared.clients import subscriber_client, subscription_path
from shared.context import context_for
from shared.domain import Review
from shared.events import (
    ALL_TOPICS,
    EXPECTED_STATES,
    TOPIC_REVIEW_INTAKE,
    TOPIC_REVIEW_PLAN_READY,
    TOPIC_VENDOR_REPLY_RECEIVED,
    EventEnvelope,
    MessageParked,
    guard,
    subscription_name,
)
from shared.telemetry import init_tracing, span

log = logging.getLogger("drawbridge.subscriber")

PULL_BATCH = 8
IDLE_SLEEP_SECONDS = 0.5
PULL_TIMEOUT_SECONDS = 2.0
"""Deadline on one pull.

A pull with no deadline blocks server-side until a message arrives, which would make a loop
over several subscriptions starve every topic after the first quiet one — and would make Ctrl-C
land inside a gRPC call rather than between messages.
"""

Handler = Callable[[EventEnvelope, Review], None]


def handlers() -> dict[str, Handler]:
    """Return the topic-to-handler table.

    Built on call rather than at import because importing an agent resolves its model id
    through configuration, and a table built at import would make ``shared.subscriber``
    unimportable in a process that only wanted to publish.

    Topics whose agent is still a contract are absent by design: a handler that raises
    ``NotImplementedError`` inside the loop would nack forever and eventually dead-letter every
    message on that topic, which reads as a failure rather than as unfinished work.
    """
    from agents.orchestrator import agent as orchestrator
    from agents.questionnaire import agent as questionnaire

    return {
        TOPIC_REVIEW_INTAKE: orchestrator.handle_event,
        TOPIC_REVIEW_PLAN_READY: questionnaire.handle_event,
        TOPIC_VENDOR_REPLY_RECEIVED: questionnaire.handle_event,
    }


class Runner:
    """Pulls from a set of subscriptions in turn and dispatches what it finds."""

    def __init__(self, topics: list[str] | None = None) -> None:
        table = handlers()
        selected = topics or sorted(table)

        unknown = [t for t in selected if t not in ALL_TOPICS]
        if unknown:
            raise ValueError(f"not declared topics: {unknown}")
        unhandled = [t for t in selected if t not in table]
        if unhandled:
            raise ValueError(
                f"no handler registered for {unhandled}. Add one to shared.subscriber.handlers "
                "before subscribing, or the loop pulls messages it cannot dispatch."
            )

        self.topics = selected
        self.table = table
        self.running = True

    def stop(self, *_args) -> None:
        """Ask the loop to finish the message in flight and exit."""
        self.running = False

    def run(self, *, max_batches: int | None = None) -> int:
        """Pull and dispatch until stopped. Returns the number of messages handled.

        Args:
            max_batches: stop after this many passes over the subscription list. ``None`` runs
                until interrupted; a finite value is what the tests use to drain a queue without
                needing a second process.
        """
        init_tracing("drawbridge-worker")
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        log.info("subscribed to %s", ", ".join(self.topics))
        handled = 0
        batches = 0

        while self.running:
            idle = True
            for topic in self.topics:
                for message in self._pull(topic):
                    idle = False
                    handled += 1
                    self._dispatch(topic, message)

            batches += 1
            if max_batches is not None and batches >= max_batches:
                break
            if idle:
                time.sleep(IDLE_SLEEP_SECONDS)

        log.info("worker stopped after handling %d message(s)", handled)
        return handled

    def _pull(self, topic: str):
        """Pull a batch from ``topic``'s subscription. An empty batch is the normal case."""
        from google.api_core import exceptions as gexc

        client = subscriber_client()
        try:
            response = client.pull(
                request={
                    "subscription": subscription_path(subscription_name(topic)),
                    "max_messages": PULL_BATCH,
                },
                timeout=PULL_TIMEOUT_SECONDS,
            )
        except gexc.DeadlineExceeded:
            return []
        return response.received_messages

    def _dispatch(self, topic: str, message) -> None:
        """Guard, dispatch and acknowledge one message."""
        client = subscriber_client()
        subscription = subscription_path(subscription_name(topic))
        ack_id = message.ack_id

        try:
            event = EventEnvelope.from_bytes(message.message.data)
        except (ValidationError, ValueError) as exc:
            # Acked, not nacked: four more deliveries of an unparseable message produce four
            # more identical failures. It is recorded here and dead-lettering it would only
            # move where the same nothing sits.
            log.error("malformed envelope on %s, acking and recording: %s", topic, exc)
            client.acknowledge(request={"subscription": subscription, "ack_ids": [ack_id]})
            return

        ctx = context_for(event, agent="worker")
        with span(f"consume.{topic}", ctx, topic=topic, event_id=event.event_id):
            try:
                review = guard(event, EXPECTED_STATES[topic])
                if review is None:
                    # guard() already applied the defined out-of-phase behaviour and the event
                    # is dealt with, so this is an ack rather than a redelivery.
                    log.info("%s for %s handled out of phase", topic, event.review_id)
                    client.acknowledge(
                        request={"subscription": subscription, "ack_ids": [ack_id]}
                    )
                    return

                self.table[topic](event, review)

            except MessageParked as exc:
                log.info("parked: %s", exc)
                self._nack(client, subscription, ack_id)
                return
            except Exception as exc:  # noqa: BLE001 — the loop survives one bad message
                log.exception("handler for %s failed on review=%s: %s", topic, event.review_id, exc)
                self._nack(client, subscription, ack_id)
                return

        client.acknowledge(request={"subscription": subscription, "ack_ids": [ack_id]})

    @staticmethod
    def _nack(client, subscription: str, ack_id: str) -> None:
        """Return a message for redelivery by zeroing its ack deadline."""
        client.modify_ack_deadline(
            request={
                "subscription": subscription,
                "ack_ids": [ack_id],
                "ack_deadline_seconds": 0,
            }
        )
