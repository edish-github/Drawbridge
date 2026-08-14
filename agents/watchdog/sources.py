"""Signal sources: certificate expiry math and the allowlisted feeds.

Expiry math needs no network at all — it reads dates already in the dossier. Feed fetches go
through the gateway under P3, which restricts outbound fetch to an allowlist of feed domains.
An agent with unbounded outbound fetch is an exfiltration channel and an SSRF surface, which
is a strange thing to leave open in a product whose thesis is untrusted content.

Failure semantics: a feed that times out or errors is logged and skipped, and the sweep
continues with the remaining sources. A fetch to a host outside the allowlist raises
``PolicyViolation`` at the gateway and is logged as a P3 block; it is never retried against a
different host. Feed content is untrusted input and is screened before it reaches the
relevance model.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class Signal(BaseModel):
    signal_id: str
    source: str
    vendor_hint: str
    title: str
    url: str | None = None
    published_at: date | None = None


def expiry_signals(as_of: date) -> list[Signal]:
    """Return signals for certificates expiring or expired across the portfolio.

    Pure date arithmetic over the dossier. No network call, so this half of the sweep works
    even when every feed is down.
    """
    raise NotImplementedError


def fetch_feed_signals(feed_host: str, window_days: int, ctx) -> list[Signal]:
    """Fetch and parse one allowlisted feed.

    Raises:
        PolicyViolation: when ``feed_host`` is outside the P3 allowlist.
    """
    raise NotImplementedError
