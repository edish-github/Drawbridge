"""The per-event context every kernel call takes.

``routing.generate``, ``gateway.call_tool``, ``idempotency.once``, ``checkpoint.step`` and
``telemetry.span`` all read the same four fields off whatever object they are handed. Naming
that object is what stops each call site inventing its own shape and one of them forgetting the
field that ties a span to its review.

It is deliberately a plain frozen dataclass rather than a Pydantic model: it is constructed once
per message on the hot path, it holds no external content, and there is nothing here to
validate that the envelope has not already validated.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class AgentContext:
    """What a handler carries through one event.

    Attributes:
        org_id: the tenant this work belongs to. Required, and deliberately first: every
            collection the handler touches hangs off ``orgs/{org_id}/``, so a context without one
            cannot reach any product data at all. There is no default — a fallback organisation
            is how one customer's evidence ends up in another's review.
        review_id: the review being worked on. Every span, cost record and idempotency claim is
            attributed to it.
        agent: who is acting. Appears in policy blocks and idempotency skip lines, because the
            operator's next question after "what was skipped" is "by whom".
        trace_id: ties this work to the span tree of the event that caused it.
        idem_key: the key for the step in flight, when one is in flight. ``events.publish``
            stamps it onto the envelope so a downstream consumer can trace a duplicate back to
            the step that produced it.
    """

    org_id: str
    review_id: str
    agent: str
    trace_id: str = ""
    idem_key: str | None = None

    def collection(self, name: str):
        """This tenant's copy of a collection. The only path from a handler to its data."""
        from shared.tenancy import collection

        return collection(name, self.org_id)

    def document(self, name: str, doc_id: str):
        from shared.tenancy import document

        return document(name, doc_id, self.org_id)

    def for_step(self, idem_key: str) -> AgentContext:
        """Return a copy carrying the idempotency key of the step about to run."""
        return replace(self, idem_key=idem_key)


def context_for(event, agent: str) -> AgentContext:
    """Build the context for handling ``event`` as ``agent``.

    The org comes off the envelope rather than being looked up. An event carries its tenant for
    the same reason it carries its trace: the consumer must not have to ask a second system who
    this work is for, and a lookup that failed would leave a handler holding an event it could
    not scope.
    """
    return AgentContext(
        org_id=event.org_id,
        review_id=event.review_id,
        agent=agent,
        trace_id=event.trace_id,
    )
