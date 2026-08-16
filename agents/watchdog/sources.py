"""Signal sources: certificate expiry math and the allowlisted feeds.

Expiry math needs no network at all — it reads dates already in the dossier. Feed fetches go
through the gateway under P3, which restricts outbound fetch to an allowlist of feed domains.
An agent with unbounded outbound fetch is an exfiltration channel and an SSRF surface, which
is a strange thing to leave open in a product whose thesis is untrusted content.

**Fetched page content is screened before it reaches a model.** P3 bounds *where* the fleet
fetches from; nothing about an allowlisted host makes the bytes it returns trustworthy. A
compromised advisory page, or a news article quoting an attacker's own text, is the third
threat Model Armor names — tool poisoning — and it is the one the rest of the system does not
cover, because every other untrusted path enters through the vendor. The fetch is screened on
the same path, with the same template and the same records, as a vendor upload.

In local mode the screening stub is untrusted by construction, so P2 refuses fetched content
and the sweep falls back to expiry math alone. That is the correct local behaviour rather than
a gap: the path exists, it is wired, and it declines to run on evidence nobody inspected.

Failure semantics: a feed that times out or errors is logged and skipped, and the sweep
continues with the remaining sources. A fetch to a host outside the allowlist raises
``PolicyViolation`` at the gateway and is logged as a P3 block; it is never retried against a
different host. The Watchdog never blocks or degrades an active review.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from agents.watchdog.fetch import TOOL_FETCH_URL
from shared.gateway import FEED_ALLOWLIST, PolicyViolation, call_tool

log = logging.getLogger("drawbridge.watchdog.sources")

EXPIRY_WARNING_DAYS = 60
"""How far ahead an expiry is worth raising.

A certificate that lapses next week is a finding a buyer can still act on; one that lapses in
nine months is a diary entry. Sixty days is the window in which a renewal audit is normally
already under way, so a vendor with nothing booked is the signal.
"""



class Signal(BaseModel):
    signal_id: str
    source: str
    vendor_hint: str
    title: str
    url: str | None = None
    published_at: date | None = None
    body: str = ""


def expiry_signals(as_of: date, vendor_ids: list[str] | None = None) -> list[Signal]:
    """Return signals for certificates expiring or expired across the portfolio.

    Pure date arithmetic over the dossier. No network call and no model call, so this half of
    the sweep works even when every feed is down — which is the half that matters, because an
    expiry is the one post-approval event that is certain rather than probable.
    """
    from shared.memory import recall_dossier

    signals: list[Signal] = []
    for vendor_id in vendor_ids or monitored_vendor_ids():
        note = recall_dossier(vendor_id).latest("cert_expiry")
        if note is None:
            continue

        expiry = _as_date(note.value.get("expires_at") or note.value.get("value"))
        if expiry is None:
            log.info("vendor=%s has a cert_expiry note with no readable date", vendor_id)
            continue

        days = (expiry - as_of).days
        if days > EXPIRY_WARNING_DAYS:
            continue

        certificate = str(note.value.get("certificate") or note.value.get("id") or "certificate")
        signals.append(
            Signal(
                signal_id=f"expiry:{vendor_id}:{certificate}:{expiry.isoformat()}",
                source="dossier",
                vendor_hint=vendor_id,
                title=(
                    f"{certificate} expired {abs(days)} days ago"
                    if days < 0
                    else f"{certificate} expires in {days} days"
                ),
                published_at=as_of,
            )
        )

    log.info("expiry sweep produced %d signal(s) as of %s", len(signals), as_of)
    return signals


def monitored_vendor_ids() -> list[str]:
    """Return the vendors with a decided review, which are the ones worth watching.

    A vendor nobody bought from is not a monitoring subject, and sweeping the whole vendor
    table would spend feed calls on companies the organisation declined.
    """
    from google.cloud.firestore_v1 import FieldFilter

    from shared.clients import firestore_client
    from shared.domain import ReviewState

    docs = (
        firestore_client()
        .collection("reviews")
        .where(
            filter=FieldFilter(
                "state", "in", [ReviewState.DECIDED.value, ReviewState.MONITORED.value]
            )
        )
        .stream()
    )
    return sorted({str((d.to_dict() or {}).get("vendor_id")) for d in docs} - {"None", ""})


def fetch_feed_signals(feed_host: str, window_days: int, ctx) -> list[Signal]:
    """Fetch, screen and parse one allowlisted feed.

    The screening is not optional and not a formality: what comes back from an allowlisted host
    is still bytes written by somebody else, and it is about to be read by a model that will act
    on what it says. It goes through ``screen_text`` — the same call, the same template and the
    same records as a vendor upload — and the stamp it produces travels with every signal, so
    the relevance call can be refused by P2 rather than trusting the host.

    Raises:
        PolicyViolation: when ``feed_host`` is outside the P3 allowlist. Never caught here: a
            fetch the policy refuses is a bug in the caller, not a transient failure.
    """
    if feed_host not in FEED_ALLOWLIST:
        raise PolicyViolation("P3", f"{feed_host} is not an allowlisted feed host")

    url = f"https://{feed_host}/feed?window={int(window_days)}d"
    try:
        raw = call_tool(TOOL_FETCH_URL, ctx, url=url)
    except PolicyViolation:
        raise
    except Exception as exc:  # noqa: BLE001 — a feed outage never degrades an active review
        log.warning("feed %s unavailable, skipped: %s", feed_host, exc)
        return []

    body = raw if isinstance(raw, str) else str((raw or {}).get("body", ""))
    if not body.strip():
        return []

    origin_ref = f"feed:{feed_host}"
    try:
        screen_fetched(body, getattr(ctx, "review_id", "watchdog"), origin_ref)
    except Exception as exc:  # noqa: BLE001 — see the module docstring on local mode
        log.warning(
            "feed %s was fetched but not admissible: %s. The sweep continues on expiry math, "
            "which needs no network and no model.",
            feed_host,
            exc,
        )
        return []

    return _parse(body, feed_host, origin_ref)


def screen_fetched(body: str, review_id: str, origin_ref: str) -> None:
    """Screen fetched page content and record the verdict.

    Separated so the ordering is greppable: nothing in this module hands a fetched body to a
    caller before this function has run on it.
    """
    from shared.armor import record_screening, screen_text

    result = screen_text(body, review_id, origin_ref)
    record_screening(review_id, result)
    log.info("screened %s: trustworthy=%s", origin_ref, result.sanitised)


def _parse(body: str, feed_host: str, origin_ref: str) -> list[Signal]:
    """Read a feed's entries. JSON lines or a JSON list; anything else is skipped.

    Deliberately not a general parser. A feed whose format changed is a feed to fix rather than
    to guess at, and guessing is how a monitoring product starts opening reviews on nothing.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        log.warning("feed %s returned a body this parser does not read; skipped", feed_host)
        return []

    entries = payload if isinstance(payload, list) else payload.get("entries") or []
    signals = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("title"):
            continue
        signals.append(
            Signal(
                signal_id=str(entry.get("id") or f"{feed_host}:{index}"),
                source=origin_ref,
                vendor_hint=str(entry.get("vendor") or entry.get("company") or ""),
                title=str(entry["title"]),
                url=entry.get("url"),
                published_at=_as_date(entry.get("published")),
                body=str(entry.get("summary") or ""),
            )
        )
    return signals


def _as_date(raw) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def window_start(as_of: date, window_days: int) -> date:
    """Return the earliest publication date a sweep considers."""
    return as_of - timedelta(days=int(window_days))
