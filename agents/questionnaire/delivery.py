"""Outbound delivery, registered as a gateway tool.

The send is a plain function that appends one document to ``inbox`` and knows nothing about
approval, policy or idempotency. That is the arrangement the security claim depends on: the
tool cannot decide it is allowed to run, because it is only ever reached through
``gateway.call_tool``, which decides first. A tool that checked its own authorisation would be
a tool that could be persuaded to stop.

``inbox`` is the same collection the crash tests count documents in. One representation of
"the vendor was contacted", counted the same way by the test suite and by the demo — two inbox
concepts would let ``test_idempotency`` pass while the thing on screen sent twice.

Cloud mode substitutes a real mail transport behind the same registration. Nothing above this
function changes when it does, which is the point of registering it rather than calling it.

Failure semantics: a write that fails raises, the idempotency claim stays ``in_progress``, and
the step is surfaced for human confirmation rather than retried — the conservative direction
for an effect that may or may not have left the building.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from shared.clients import firestore_client
from shared.gateway import register_tool

log = logging.getLogger("drawbridge.delivery")

COLLECTION_INBOX = "inbox"
TOOL_SEND_EMAIL = "send_email"


def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    review_id: str,
    vendor: str = "",
    approval_token: str | None = None,
) -> dict:
    """Deliver one message and record it. Reached only through the gateway.

    ``approval_token`` is accepted and deliberately unused: P1 consumed it before dispatch, and
    a tool that read it would be a second place where an approval is interpreted.
    """
    firestore_client().collection(COLLECTION_INBOX).add(
        {
            "review_id": review_id,
            "vendor": vendor,
            "to": to,
            "subject": subject,
            "body": body,
            "sent_at": datetime.now(UTC).isoformat(),
        }
    )
    log.info("sent review=%s to=%s subject=%r", review_id, to, subject)
    return {"sent": True, "to": to}


register_tool(TOOL_SEND_EMAIL, send_email)
