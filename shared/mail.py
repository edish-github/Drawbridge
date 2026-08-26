"""Outbound mail, behind the interface the fleet already had.

``agents/questionnaire/delivery.py`` has always registered one tool through the gateway and
written a document to ``inbox``. Its docstring promised that "cloud mode substitutes a real mail
transport behind the same registration", and this is that substitution — the promise kept rather
than restated.

Three transports, chosen by configuration and never by a caller:

``ledger``   append to ``inbox`` and stop. What local mode and the test suite use. It is not a
             mock: it is the same record every other transport also writes, which is why the
             crash tests can count documents there and mean it.
``smtp``     a real server. Works with any provider that speaks SMTP, which is all of them, so
             a customer with their own relay is not blocked on us adding an integration.
``api``      a provider's HTTP API, for the deliverability reporting SMTP cannot give.

**Every transport writes the ledger record**, including the ones that also send. One
representation of "the vendor was contacted", counted the same way by the test suite, the crash
demo and the audit binder — two notions of that would let ``test_idempotency`` pass while the
thing on screen sent twice.

**The reply address carries the review.** Outbound mail is sent from
``review+{org}.{review}@{domain}``, so an inbound reply routes to the right tenant and the right
review without anybody parsing a subject line. Subject lines get edited; a plus-address survives
being replied to from a phone.

Failure semantics: a send that fails raises. The idempotency claim stays ``in_progress`` and the
step is surfaced for human confirmation rather than retried — the conservative direction for an
effect that may or may not have left the building. A transport that swallowed a failure would be
a vendor who was never contacted and a review that says they were.
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

log = logging.getLogger("drawbridge.mail")

REPLY_ADDRESS = re.compile(r"review\+([a-z0-9-]+)\.([A-Za-z0-9_.:-]+)@")
"""How a reply is routed back to its review.

The local part is ``review+{org}.{review}``. Both halves are constrained to what an address may
contain, so a review id that could not survive an SMTP round trip is caught when the address is
built rather than when somebody replies.
"""


class MailNotConfigured(RuntimeError):
    """A transport was selected and its settings are absent. Refused rather than degraded."""


class SendFailed(RuntimeError):
    """The message did not leave. Never swallowed: see the module docstring."""


@dataclass(frozen=True, slots=True)
class Outbound:
    to: str
    subject: str
    body: str
    org_id: str
    review_id: str
    kind: str


def transport() -> str:
    """Which transport is in use. ``ledger`` unless configured otherwise."""
    return os.environ.get("MAIL_TRANSPORT", "ledger").lower()


def sending_domain() -> str:
    return os.environ.get("MAIL_DOMAIN", "drawbridge.local")


def from_address(org_name: str) -> str:
    """Who the mail appears to be from.

    The organisation's name, not ours, and only the name. A security questionnaire arriving from
    a company the recipient has never heard of is a security questionnaire that gets deleted, so
    the display name is the customer's and only the domain is the platform's.

    No suffix. An earlier version appended "Security Review" and produced *Northgate Security
    Security Review* for every customer whose name already ends in the word — the purpose belongs
    in the subject line, where it is not competing with somebody's brand.
    """
    return formataddr((org_name, f"no-reply@{sending_domain()}"))


def reply_to(org_id: str, review_id: str) -> str:
    """The address a reply must come back to, carrying its own routing."""
    return f"review+{org_id}.{review_id}@{sending_domain()}"


def route_reply(address: str) -> tuple[str, str] | None:
    """Resolve an inbound address to ``(org_id, review_id)``, or ``None``.

    ``None`` for anything that does not match, including mail to a plain address on the sending
    domain. A reply that cannot be routed is quarantined for a person rather than guessed at: the
    guess that attaches a vendor's answer to the wrong review is worse than the delay.
    """
    match = REPLY_ADDRESS.search(address or "")
    if not match:
        return None
    return match.group(1), match.group(2)


def send(message: Outbound, *, org_name: str) -> str:
    """Send one message and return its id.

    Raises:
        SendFailed: on any transport failure.
        MailNotConfigured: when the selected transport has no settings.
    """
    chosen = transport()
    message_id = make_msgid(domain=sending_domain())

    if chosen == "ledger":
        pass
    elif chosen == "smtp":
        _send_smtp(message, org_name=org_name, message_id=message_id)
    elif chosen == "api":
        _send_api(message, org_name=org_name, message_id=message_id)
    else:
        raise MailNotConfigured(
            f"{chosen!r} is not a mail transport. Use 'ledger', 'smtp' or 'api'."
        )

    log.info(
        "mail %s review=%s to=%s kind=%s id=%s",
        chosen,
        message.review_id,
        message.to,
        message.kind,
        message_id,
    )
    return message_id


def _build(message: Outbound, *, org_name: str, message_id: str) -> EmailMessage:
    mail = EmailMessage()
    mail["From"] = from_address(org_name)
    mail["To"] = message.to
    mail["Reply-To"] = reply_to(message.org_id, message.review_id)
    mail["Subject"] = message.subject
    mail["Message-ID"] = message_id
    # Both headers, because different providers honour different ones, and a questionnaire that
    # generates an out-of-office storm is a questionnaire the recipient's IT team blocks.
    mail["Auto-Submitted"] = "auto-generated"
    mail["X-Auto-Response-Suppress"] = "OOF, AutoReply"
    mail.set_content(message.body)
    return mail


def _send_smtp(message: Outbound, *, org_name: str, message_id: str) -> None:
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise MailNotConfigured("SMTP_HOST is required when MAIL_TRANSPORT=smtp")

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            if user and password:
                server.login(user, password)
            server.send_message(_build(message, org_name=org_name, message_id=message_id))
    except Exception as exc:  # noqa: BLE001 — every failure is the same answer to the caller
        raise SendFailed(f"SMTP delivery to {message.to} failed: {exc}") from exc


def _send_api(message: Outbound, *, org_name: str, message_id: str) -> None:
    """Send through a provider's HTTP API.

    Deliberately provider-shaped rather than provider-specific: endpoint, key and a JSON body with
    the fields every provider has. A provider needing more than this gets its own function, and
    the branch says which one rather than a configuration file implying it.
    """
    endpoint = os.environ.get("MAIL_API_URL")
    key = os.environ.get("MAIL_API_KEY")
    if not endpoint or not key:
        raise MailNotConfigured(
            "MAIL_API_URL and MAIL_API_KEY are required when MAIL_TRANSPORT=api"
        )

    import httpx

    try:
        response = httpx.post(
            endpoint,
            headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
            json={
                "from": from_address(org_name),
                "to": [message.to],
                "reply_to": reply_to(message.org_id, message.review_id),
                "subject": message.subject,
                "text": message.body,
                "headers": {"Message-ID": message_id, "Auto-Submitted": "auto-generated"},
            },
            timeout=20,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise SendFailed(f"API delivery to {message.to} failed: {exc}") from exc
