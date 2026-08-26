"""The vendor inbox: where an inbound reply first touches the system.

The component that receives external bytes is the component that screens them. That is the same
arrangement as the screening service and the quarantine bucket, applied to the other inbound
channel — and it matters more here, not less: a reply body is the likelier injection vector in
reality than a PDF, because it costs the sender nothing.

So this screens, records the verdict, and publishes ``vendor.reply_received`` carrying the
screened body. The Questionnaire agent consumes what was published and refuses to parse a
message with no screening record, which is what makes the boundary checkable rather than
conventional.

Failure semantics: Model Armor is mandatory and fails closed. An unavailable service or a
skipped critical filter means the reply is not published, the review parks, and the message is
left for a human — a reply that cannot be screened is not a reply that can be read.
"""

from __future__ import annotations

import logging

from shared import tenancy
from shared.armor import screen_text
from shared.context import AgentContext
from shared.events import TOPIC_VENDOR_REPLY_RECEIVED, publish

log = logging.getLogger("drawbridge.vendor_inbox")

SERVICE_ACCOUNT = "sa-portal"


def receive(review_id: str, body: str, message_id: str) -> str:
    """Screen an inbound reply and publish it. Returns the origin reference recorded for it.

    Raises:
        ArmorUnavailable, ArmorSkipped: the reply is not published and the review parks. In
            local mode the stub is untrustworthy by construction, so this always raises — which
            is why the demo runner seeds replies through ``scenarios.seed`` rather than routing
            them through here.
    """
    origin_ref = f"reply:{message_id}"
    result = screen_text(body, review_id, origin_ref)

    ctx = AgentContext(
        org_id=tenancy.current_org(),
        review_id=review_id,
        agent="vendor_inbox",
        trace_id="",
    )
    publish(
        TOPIC_VENDOR_REPLY_RECEIVED,
        review_id,
        {"body": body, "message_id": message_id, "verdict": result.summary()},
        ctx=ctx,
    )
    log.info("received reply %s for review=%s under %s", message_id, review_id, result.summary())
    return origin_ref
