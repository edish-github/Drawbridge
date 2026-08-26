"""Outbound delivery, registered as a gateway tool.

The send is a plain function that appends one document to ``inbox`` and knows nothing about
approval, policy or idempotency. That is the arrangement the security claim depends on: the
tool cannot decide it is allowed to run, because it is only ever reached through
``gateway.call_tool``, which decides first. A tool that checked its own authorisation would be
a tool that could be persuaded to stop.

``inbox`` is the same collection the crash tests count documents in. One representation of
"the vendor was contacted", counted the same way by the test suite and by the demo — two inbox
concepts would let ``test_idempotency`` pass while the thing on screen sent twice.

The transport is ``shared.mail``, chosen by configuration: a ledger append locally and in the
test suite, real SMTP or a provider API in cloud. Nothing above this function changes when it
switches, which is the point of registering the tool rather than calling it — and the ledger row
is written by every transport, so "the vendor was contacted" is counted the same way by the crash
tests, the audit binder and a production deployment.

Failure semantics: a write that fails raises, the idempotency claim stays ``in_progress``, and
the step is surfaced for human confirmation rather than retried — the conservative direction
for an effect that may or may not have left the building.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from shared import tenancy as tenant
from shared.gateway import register_tool

log = logging.getLogger("drawbridge.delivery")

COLLECTION_INBOX = "inbox"
TOOL_SEND_EMAIL = "send_email"

KINDS = ("questionnaire", "followup", "chase")
"""What a message is, recorded on it.

Three things now write to this collection and they are counted separately everywhere it
matters: "the vendor was emailed once" is a claim about the questionnaire, not about the
correspondence, and a test or a demo beat that counted the whole thread would start failing the
first time the fleet asked a sensible follow-up question.
"""


def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    review_id: str,
    vendor: str = "",
    kind: str = "questionnaire",
    approval_token: str | None = None,
) -> dict:
    """Deliver one message and record it. Reached only through the gateway.

    ``approval_token`` is accepted and deliberately unused: P1 consumed it before dispatch, and
    a tool that read it would be a second place where an approval is interpreted.
    """
    from shared import tenancy
    from shared.mail import Outbound, send

    org_id = tenancy.current_org()
    org = tenancy.load_org(org_id)

    # The transport goes first and the record second, and the order is the failure story. A
    # transport that raises leaves no ledger row, the idempotency claim stays `in_progress`, and
    # the step is surfaced for a human to confirm — which is the conservative direction for an
    # effect that may or may not have left the building. Recording first and sending second would
    # produce the opposite: a review that says the vendor was contacted when they were not.
    message_id = send(
        Outbound(
            to=to,
            subject=subject,
            body=body,
            org_id=org_id,
            review_id=review_id,
            kind=kind if kind in KINDS else "questionnaire",
        ),
        org_name=org.name if org else org_id,
    )

    tenant.collection(COLLECTION_INBOX).add(
        tenancy.stamp(
            {
                "review_id": review_id,
                "vendor": vendor,
                "to": to,
                "subject": subject,
                "body": body,
                "kind": kind if kind in KINDS else "questionnaire",
                "message_id": message_id,
                "sent_at": datetime.now(UTC).isoformat(),
            }
        )
    )
    log.info("sent review=%s kind=%s to=%s subject=%r", review_id, kind, to, subject)
    return {"sent": True, "to": to, "message_id": message_id}


register_tool(TOOL_SEND_EMAIL, send_email)
