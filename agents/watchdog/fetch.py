"""Outbound fetch, registered as a gateway tool.

The same arrangement as the outbound email tool, for the same reason: the fetch is a plain
function that knows nothing about the allowlist, because it is only ever reached through
``gateway.call_tool``, which applies P3 first. A tool that checked its own allowlist would be a
tool that could be persuaded to stop.

There is one outbound fetch in this system and it is here. Anything else reaching the network
from an agent is a bug rather than a feature with a different implementation, which is what
makes "the Watchdog fetches only from the feed allowlist" a claim about the code rather than
about intent.

Failure semantics: a timeout or an error status raises, the caller logs it and skips that feed,
and the sweep continues on the sources that answered. The Watchdog never blocks or degrades an
active review, so no fetch failure is ever fatal to anything.
"""

from __future__ import annotations

import logging

from shared.gateway import register_tool

log = logging.getLogger("drawbridge.watchdog.fetch")

TOOL_FETCH_URL = "fetch_url"
TIMEOUT_SECONDS = 10


def fetch_url(*, url: str, review_id: str = "", **_: object) -> dict:
    """Fetch one allowlisted URL and return its body. Reached only through the gateway.

    Local mode has no feed to fetch, so this returns an empty body rather than reaching the
    network from a developer's machine — and it says so, because a silent empty result is
    indistinguishable from a feed with no news.
    """
    from shared.config import settings

    if settings().is_local:
        log.info("local mode: %s not fetched; the sweep runs on expiry math alone", url)
        return {"url": url, "body": "", "status": 0, "note": "local mode makes no outbound fetch"}

    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Drawbridge/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        body = response.read().decode("utf-8", errors="replace")

    log.info("fetched %s: %d byte(s)", url, len(body))
    return {"url": url, "body": body, "status": response.status}


register_tool(TOOL_FETCH_URL, fetch_url)
