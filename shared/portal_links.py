"""Links a vendor can use, and nothing more.

A vendor has no account. They are somebody at another company who was emailed a questionnaire,
and asking them to create one is how a review takes six weeks instead of three days. So the portal
authenticates a **link**, not a person: a signed, scoped, expiring reference to one review.

What a link carries, and why each part is on it:

``org``      whose review this is. Without it a link is a path with no tenant, and the portal
             would have to guess.
``review``   which review. A link is good for one, so a vendor with two reviews open has two
             links and neither reaches the other's evidence.
``exp``      when it stops working. Ninety days by default — long enough for a real
             correspondence, short enough that a link in a forwarded email thread is not a
             permanent door.

What it deliberately does **not** carry: a role, a capability, or anything the portal reads to
decide what may be done. The portal's permissions come from its service account, and a link that
could widen them would be a link worth stealing.

**The signature is the whole control**, so it covers every field and is compared in constant
time. A link is a bearer credential — anyone holding it is the vendor as far as this system is
concerned — which is the same trust model as every "click here to continue" email ever sent, and
it is stated here rather than assumed.

Failure semantics: every verification failure returns ``None``. The portal renders one page for
all of them, because a vendor cannot act on the difference between *expired* and *forged*, and a
response that distinguished them would confirm which review ids exist.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass

from shared.config import settings

log = logging.getLogger("drawbridge.portal")

LINK_TTL_DAYS = 90
LINK_SECRET_ENV = "DRAWBRIDGE_PORTAL_SECRET"


@dataclass(frozen=True, slots=True)
class PortalLink:
    org_id: str
    review_id: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return self.expires_at < time.time()


class PortalSecretMissing(RuntimeError):
    """No signing secret is configured. Refused rather than defaulted in cloud."""


def _secret() -> bytes:
    configured = os.environ.get(LINK_SECRET_ENV)
    if configured:
        return configured.encode()
    if settings().is_cloud:
        # Not a warning. Deriving a portal secret from a project id in production would mean the
        # links are forgeable by anybody who knows the project id, which is not a secret.
        raise PortalSecretMissing(
            f"{LINK_SECRET_ENV} is required in cloud mode. Set it from Secret Manager."
        )
    return hashlib.sha256(f"drawbridge-portal:{settings().project_id}".encode()).digest()


def issue(org_id: str, review_id: str, *, ttl_days: int = LINK_TTL_DAYS) -> str:
    """Mint a link for one review."""
    claims = {
        "org": org_id,
        "review": review_id,
        "exp": time.time() + ttl_days * 86400,
    }
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{signature}"


def verify(token: str | None) -> PortalLink | None:
    """Verify a link, or return ``None`` for every failure."""
    if not token:
        return None
    try:
        body, signature = token.rsplit(".", 1)
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(expected, signature):
            return None
        claims = json.loads(base64.urlsafe_b64decode(body.encode() + b"=="))
    except Exception:  # noqa: BLE001 — malformed is the same answer as forged
        return None

    link = PortalLink(
        org_id=str(claims.get("org", "")),
        review_id=str(claims.get("review", "")),
        expires_at=float(claims.get("exp", 0)),
    )
    if not link.org_id or not link.review_id or link.expired:
        return None
    return link


def url_for(org_id: str, review_id: str, *, base: str | None = None) -> str:
    """The address to put in an email."""
    root = base or os.environ.get("PORTAL_URL") or "http://localhost:8083"
    return f"{root.rstrip('/')}/r/{issue(org_id, review_id)}"
