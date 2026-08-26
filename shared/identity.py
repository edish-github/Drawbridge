"""Who a person is, and which organisations they may act in.

Two halves, and they are kept apart on purpose. **Authentication** — proving you are the holder
of an account — is delegated to Google Identity Platform, because a security product that rolls
its own password hashing has already lost the argument it is trying to make. **Authorisation** —
which organisation you may act in, and in what role — is ours, because it is the thing the
product is about.

So this module never sees a password. It receives an identity token that somebody else issued,
verifies its signature, and turns the subject claim into a membership lookup.

**The subject, never the email.** A token's ``sub`` claim is a stable, provider-issued
identifier; an email address is a mutable label that can be reassigned to a different person by
whoever runs the mail domain. An audit record that named an approver by an address someone else
now owns is worse than one that named nobody, so ``uid`` is what memberships are keyed on and
what an approval token is bound to. The email is carried for display and for invitations.

**Local mode.** There is no Identity Platform on a laptop, so local mode accepts a development
token that is signed with a project-local secret and carries the same claims. It is refused
outright in cloud mode — not "discouraged", refused, in ``verify_token`` itself, because an
auth backdoor that is merely off by default is an auth backdoor. ``tests/test_identity.py``
asserts that from the source.

Failure semantics: every verification failure returns ``None`` rather than raising. A caller
that cannot tell "expired" from "forged" from "malformed" cannot leak which one it was, and the
only thing any caller does with the answer is refuse.
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
from datetime import UTC, datetime

from shared import tenancy
from shared.config import settings
from shared.tenancy import Membership, Role, User

log = logging.getLogger("drawbridge.identity")

SESSION_TTL_SECONDS = 60 * 60 * 12
"""How long a session lasts before the holder signs in again.

Twelve hours: one working day. Long enough that an analyst working a queue is not interrupted,
short enough that a laptop left on a train stops being useful before the next morning.
"""

DEV_SECRET_ENV = "DRAWBRIDGE_DEV_AUTH_SECRET"
DEV_ISSUER = "drawbridge-local"


class AuthUnavailable(RuntimeError):
    """Identity verification is not configured. Never degraded into accepting anything."""


@dataclass(frozen=True, slots=True)
class Principal:
    """A verified person, and what they may do in one organisation.

    ``role`` is ``None`` when the person is authenticated but not a member of the organisation
    they asked for — which is a different answer from "not signed in", and the surfaces above
    render it differently: one is a login page, the other is *you do not have access to this
    workspace*.
    """

    uid: str
    email: str
    name: str
    org_id: str | None = None
    role: Role | None = None

    @property
    def is_member(self) -> bool:
        return self.role is not None

    def may(self, capability) -> bool:
        return tenancy.may(self.role, capability)


# --- Verification --------------------------------------------------------------------------


def verify_token(token: str | None) -> Principal | None:
    """Verify an identity token and return the person it names, or ``None``.

    Returns ``None`` for every failure — expired, forged, malformed, unknown issuer — because a
    caller that could tell them apart would be an oracle, and no caller does anything different
    with the distinction.
    """
    if not token:
        return None

    cfg = settings()
    if cfg.is_cloud:
        return _verify_platform_token(token)
    return _verify_dev_token(token)


def _verify_platform_token(token: str) -> Principal | None:
    """Verify against Google Identity Platform.

    Raises:
        AuthUnavailable: when the Admin SDK is not installed or not configured. Deliberately a
            raise rather than a ``None``: a misconfigured deployment that quietly refused every
            login would look exactly like a site full of people typing the wrong password.
    """
    try:
        from firebase_admin import auth as fb_auth  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — exercised only in a cloud deployment
        raise AuthUnavailable(
            "firebase-admin is not installed, so identity tokens cannot be verified. Install it "
            "in the deployed image; local mode does not need it."
        ) from exc

    try:
        claims = fb_auth.verify_id_token(token, check_revoked=True)
    except Exception as exc:  # noqa: BLE001 — every failure is the same answer
        log.info("identity token rejected: %s", type(exc).__name__)
        return None

    return Principal(
        uid=str(claims["sub"]),
        email=str(claims.get("email", "")),
        name=str(claims.get("name", "")),
    )


def _verify_dev_token(token: str) -> Principal | None:
    """Verify a development token. Local mode only, and structurally unreachable in cloud.

    The token is ``base64(claims).hmac`` under a project-local secret. It is not a JWT and does
    not pretend to be one — a format that looked like a JWT would eventually be pasted somewhere
    that treats it as one.
    """
    if settings().is_cloud:  # pragma: no cover — the guard above already returned
        raise AuthUnavailable("development tokens are refused in cloud mode")

    try:
        body, signature = token.rsplit(".", 1)
        expected = hmac.new(_dev_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        claims = json.loads(base64.urlsafe_b64decode(body.encode() + b"=="))
    except Exception:  # noqa: BLE001 — malformed is the same answer as forged
        return None

    if claims.get("iss") != DEV_ISSUER or float(claims.get("exp", 0)) < time.time():
        return None

    return Principal(
        uid=str(claims["sub"]),
        email=str(claims.get("email", "")),
        name=str(claims.get("name", "")),
    )


def issue_dev_token(uid: str, email: str, name: str = "", ttl: int = SESSION_TTL_SECONDS) -> str:
    """Mint a development token. Local mode only.

    Raises:
        AuthUnavailable: in cloud mode. The one function in this module that can create an
            identity, and it refuses to exist in production.
    """
    if settings().is_cloud:
        raise AuthUnavailable(
            "development tokens cannot be issued in cloud mode. Sign in through Identity "
            "Platform."
        )

    claims = {
        "iss": DEV_ISSUER,
        "sub": uid,
        "email": email,
        "name": name,
        "exp": time.time() + ttl,
    }
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = hmac.new(_dev_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _dev_secret() -> bytes:
    """The local signing secret.

    Read from the environment where one is set, and derived from the project id otherwise. The
    derivation is deliberate: a laptop with no configuration should still be able to log in, and
    a secret that is guessable is harmless in a mode that cannot reach a customer.
    """
    configured = os.environ.get(DEV_SECRET_ENV)
    if configured:
        return configured.encode()
    return hashlib.sha256(f"drawbridge-dev:{settings().project_id}".encode()).digest()


# --- Authorisation ---------------------------------------------------------------------------


def principal_in(principal: Principal, org_id: str) -> Principal:
    """Resolve what a verified person may do in one organisation.

    A membership that is invited but not accepted, or removed, carries no role. Neither does a
    membership in a suspended organisation — a customer whose account is closed should not be
    able to keep working through a session that was already open.
    """
    membership = tenancy.load_membership(org_id, principal.uid)
    if membership is None or membership.status != "active":
        return Principal(principal.uid, principal.email, principal.name, org_id, None)

    org = tenancy.load_org(org_id)
    if org is None or org.status != "active":
        return Principal(principal.uid, principal.email, principal.name, org_id, None)

    return Principal(
        principal.uid, principal.email, principal.name, org_id, membership.role
    )


def memberships_of(principal: Principal) -> list[Membership]:
    return tenancy.orgs_for(principal.uid)


# --- The user record ---------------------------------------------------------------------------


def upsert_user(principal: Principal) -> User:
    """Record a person on first sight, and touch them on every later one.

    The identity provider owns the account; this is the product's own copy, for the things it
    needs that a token does not carry — when they first appeared, when they were last seen, and
    the display name shown beside an approval in an audit binder.
    """
    now = datetime.now(UTC)
    ref = tenancy.users().document(principal.uid)
    existing = ref.get()

    if existing.exists:
        ref.set({"last_seen_at": now.isoformat(), "email": principal.email}, merge=True)
        return User.model_validate({**existing.to_dict(), "last_seen_at": now})

    user = User(
        uid=principal.uid,
        email=principal.email,
        name=principal.name,
        created_at=now,
        last_seen_at=now,
        email_verified=True,
    )
    ref.set(user.model_dump(mode="json"))
    log.info("first sight of user %s", principal.uid)
    return user


# --- Invitations ---------------------------------------------------------------------------------


class InviteError(RuntimeError):
    """An invitation could not be created or accepted."""


def invite(org_id: str, *, email: str, role: Role, invited_by: str) -> Membership:
    """Invite somebody to an organisation.

    Keyed on the invited **email** until it is accepted, because the invitee may have no account
    yet and therefore no uid. Acceptance re-keys the membership onto the uid, which is what every
    later authorisation reads.

    Raises:
        InviteError: on an unknown role. Never defaults to a role — an invitation that silently
            granted less than intended is a support ticket, and one that granted more is an
            incident.
    """
    if role not in tenancy.ROLES:
        raise InviteError(f"{role!r} is not a role; expected one of {list(tenancy.ROLES)}")

    now = datetime.now(UTC)
    pending = Membership(
        org_id=org_id,
        uid=f"invite:{email.lower()}",
        role=role,
        invited_by=invited_by,
        invited_at=now,
        status="invited",
    )
    tenancy.memberships().document(pending.membership_id).set(pending.model_dump(mode="json"))
    log.info("invited %s to %s as %s", email, org_id, role)
    return pending


def accept_invite(org_id: str, *, email: str, uid: str) -> Membership:
    """Turn an invitation into a membership, re-keyed onto the accepting person's uid.

    Raises:
        InviteError: when there is no pending invitation for that address in that organisation.
            Never creates a membership from nothing: an accept endpoint that provisioned access
            for an unrecognised address is an open door.
    """
    pending_id = tenancy.membership_id(org_id, f"invite:{email.lower()}")
    snap = tenancy.memberships().document(pending_id).get()
    if not snap.exists:
        raise InviteError(f"no pending invitation for {email} in {org_id}")

    pending = Membership.model_validate(snap.to_dict())
    if pending.status != "invited":
        raise InviteError(f"the invitation for {email} in {org_id} is {pending.status}")

    accepted = Membership(
        org_id=org_id,
        uid=uid,
        role=pending.role,
        invited_by=pending.invited_by,
        invited_at=pending.invited_at,
        joined_at=datetime.now(UTC),
        status="active",
    )
    tenancy.memberships().document(accepted.membership_id).set(accepted.model_dump(mode="json"))
    tenancy.memberships().document(pending_id).delete()
    log.info("%s accepted the invitation to %s as %s", uid, org_id, pending.role)
    return accepted


def set_role(org_id: str, uid: str, role: Role) -> Membership:
    """Change somebody's role.

    Raises:
        InviteError: on an unknown role, or when removing the last administrator. An
            organisation with no administrator is one nobody can ever get back into, and it is
            reachable by one careless dropdown.
    """
    if role not in tenancy.ROLES:
        raise InviteError(f"{role!r} is not a role")

    members = tenancy.members_of(org_id)
    admins = [m for m in members if m.role == "admin" and m.status == "active"]
    if role != "admin" and len(admins) == 1 and admins[0].uid == uid:
        raise InviteError(
            "this is the organisation's only administrator; promote somebody else first"
        )

    ref = tenancy.memberships().document(tenancy.membership_id(org_id, uid))
    snap = ref.get()
    if not snap.exists:
        raise InviteError(f"{uid} is not a member of {org_id}")

    ref.set({"role": role}, merge=True)
    return Membership.model_validate({**snap.to_dict(), "role": role})


def remove_member(org_id: str, uid: str) -> None:
    """Remove somebody from an organisation.

    Marked ``removed`` rather than deleted. Their approvals are in the audit ledger for years,
    and a binder that referenced a membership that no longer exists would be a binder with a
    hole where the approver's role used to be.

    Raises:
        InviteError: when removing the last administrator.
    """
    members = tenancy.members_of(org_id)
    admins = [m for m in members if m.role == "admin" and m.status == "active"]
    if len(admins) == 1 and admins[0].uid == uid:
        raise InviteError("this is the organisation's only administrator")

    tenancy.memberships().document(tenancy.membership_id(org_id, uid)).set(
        {"status": "removed"}, merge=True
    )
