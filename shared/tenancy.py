"""Who owns a row, enforced by where the row lives.

Every tenant's data hangs off ``orgs/{org_id}/``. A review is
``orgs/acme/reviews/rev-1``, its findings are ``orgs/acme/findings/...``, and there is no
document anywhere in the product that belongs to no organisation.

**Path-based rather than a filter field, and the difference is the whole point.** The obvious
design is an ``org_id`` column and a ``where("org_id", "==", ...)`` on every query. It is also
the design where one forgotten ``where`` clause silently returns another customer's evidence:
the query succeeds, the page renders, and nothing anywhere goes red. Under a path the same
mistake cannot be made — there is no path to a collection without naming the org that owns it,
so a missing scope is a ``TypeError`` at the call site rather than a data leak in production.

The cost is that cross-tenant queries need ``collection_group``, which is exactly the right
friction: reading across customers should be a deliberate act with its own name.

``org_id`` is *also* stamped on each document, and that is not redundancy. It travels with a
document into an export, a binder and a backup, so a row that is ever moved or restored still
says whose it is. The path is the enforcement; the field is the provenance.

**What is global.** Three collections sit outside any org because they are how orgs come to
exist: ``orgs`` itself, ``users``, and ``memberships``. Everything else — all twenty-three
collections in the permission matrix — is per-tenant.

Failure semantics: every function here requires an org and none of them defaults one. A helper
that fell back to a "default" organisation would reintroduce exactly the failure the path design
exists to prevent, on the day somebody called it from a context that had lost its scope.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from shared.clients import firestore_client

COLLECTION_ORGS = "orgs"
COLLECTION_USERS = "users"
COLLECTION_MEMBERSHIPS = "memberships"

GLOBAL_COLLECTIONS = frozenset({COLLECTION_ORGS, COLLECTION_USERS, COLLECTION_MEMBERSHIPS})
"""Collections that sit outside any organisation, because they are how organisations exist.

Enumerated rather than inferred. A fourth entry here is a decision about what is platform-wide
rather than tenant-owned, and it should be a diff somebody reviews.
"""

ORG_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
"""What an org id may be.

Lowercase, hyphenated, 3–40 characters. It appears in a Firestore path, in a subdomain and in an
audit binder, so it is constrained to the intersection of what all three accept rather than to
what Firestore alone tolerates.
"""


class InvalidOrgId(ValueError):
    """An org id that cannot be used as a path segment, a subdomain or an audit reference."""


class TenancyError(RuntimeError):
    """A tenant-scoped operation was attempted without a tenant."""


# --- Roles ------------------------------------------------------------------------------------

Role = Literal["viewer", "analyst", "approver", "admin"]

ROLES: tuple[Role, ...] = ("viewer", "analyst", "approver", "admin")
"""Four roles, ordered by what they add rather than by seniority.

``viewer``    reads everything in the org and changes nothing.
``analyst``   opens reviews, marks a reply thread complete, resolves a parked review. Everything
              that moves a review through the workflow, and nothing that closes one.
``approver``  everything an analyst may do, plus releasing a human gate. This is the role a
              signed approval token is minted against, so it is the one that carries legal
              weight in an audit — an approval is a named person accepting risk, and this is
              where "named person" is defined.
``admin``     everything, plus membership, billing and org settings.

Deliberately not a numeric level. ``approver`` and ``admin`` differ in kind rather than degree:
an org administrator who manages billing has no business accepting security risk by default, and
a hierarchy would grant it silently.
"""

CAN_APPROVE: frozenset[str] = frozenset({"approver", "admin"})
CAN_ACT: frozenset[str] = frozenset({"analyst", "approver", "admin"})
CAN_ADMINISTER: frozenset[str] = frozenset({"admin"})


# --- Records ----------------------------------------------------------------------------------


class Org(BaseModel):
    """A customer. Everything else in the product hangs off one of these."""

    org_id: str
    name: str
    created_at: datetime
    created_by: str
    plan: str = "trial"
    status: Literal["active", "suspended", "closed"] = "active"

    # Per-tenant limits. A global ceiling would let one customer's runaway review exhaust the
    # budget another customer is paying for.
    review_ceiling_usd: float = 1.00
    monthly_ceiling_usd: float = 500.00

    settings: dict = Field(default_factory=dict)


class User(BaseModel):
    """A person. Global, because one person may belong to several organisations.

    ``uid`` is the identity provider's subject claim, never an email. Emails change hands; a
    subject does not, and an audit record that identified an approver by an address somebody
    else now owns would be worse than one that identified nobody.
    """

    uid: str
    email: str
    name: str = ""
    created_at: datetime
    last_seen_at: datetime | None = None
    email_verified: bool = False


class Membership(BaseModel):
    """One person's role in one organisation.

    Keyed ``{org_id}:{uid}`` so a lookup is a document read rather than a query — this is on the
    path of every authenticated request, and a query there is a page-load cost paid forever.
    """

    org_id: str
    uid: str
    role: Role
    invited_by: str = ""
    joined_at: datetime | None = None
    invited_at: datetime | None = None
    status: Literal["invited", "active", "removed"] = "invited"

    @property
    def membership_id(self) -> str:
        return membership_id(self.org_id, self.uid)


def membership_id(org_id: str, uid: str) -> str:
    return f"{org_id}:{uid}"


# --- Paths ------------------------------------------------------------------------------------


def validate_org_id(org_id: str) -> str:
    """Return ``org_id`` if it is usable, and raise otherwise.

    Raises:
        InvalidOrgId: on anything that is not a valid path segment, subdomain and audit
            reference at once. Never normalises: an id that had to be corrected is one that two
            systems will disagree about later.
    """
    if not org_id or not ORG_ID.match(org_id):
        raise InvalidOrgId(
            f"{org_id!r} is not a usable organisation id. Lowercase letters, digits and hyphens, "
            "3 to 40 characters, not starting or ending with a hyphen."
        )
    if org_id in GLOBAL_COLLECTIONS:
        raise InvalidOrgId(f"{org_id!r} collides with a platform collection name")
    return org_id


def org_ref(org_id: str):
    """The document every one of a tenant's collections hangs off."""
    return firestore_client().collection(COLLECTION_ORGS).document(validate_org_id(org_id))


# --- The ambient tenant ---------------------------------------------------------------------
#
# The org travels in a context variable, set once per message by ``shared.subscriber`` and once
# per request by the console and the portal. Two properties make that safe here rather than
# merely convenient:
#
#   It is **required**. ``current_org()`` raises rather than defaulting, so a code path that
#   lost its scope fails loudly at the first read instead of quietly returning the wrong
#   customer's rows. There is no "default org" anywhere in this module, on purpose.
#
#   The worker is **single-threaded by design**. ``shared/subscriber.py`` uses a synchronous
#   pull loop with no callbacks and no thread pool — a decision taken for the kill-and-resume
#   demo, and one that also means a context variable cannot leak between messages the way it
#   could under the streaming client's background threads. ``contextvars`` additionally scope
#   per-task under asyncio, so the property survives if that ever changes.
#
# Every call still accepts an explicit ``org_id=`` override, which is what scripts, tests and
# cross-tenant platform work use. Explicit beats ambient wherever the caller knows.

_CURRENT: ContextVar[str | None] = ContextVar("drawbridge_org", default=None)


def current_org() -> str:
    """The tenant this code path is acting for.

    Raises:
        TenancyError: when nothing has set one. Never defaults — a fallback organisation is the
            single most dangerous line of code a multi-tenant system can contain, because it
            turns a missing scope from a crash into a disclosure.
    """
    org_id = _CURRENT.get()
    if not org_id:
        raise TenancyError(
            "no organisation is in scope. Tenant-scoped data cannot be reached without one: "
            "wrap the call in `with tenancy.acting_for(org_id):`, or pass org_id= explicitly."
        )
    return org_id


DEV_ORG = "local-dev"
"""The organisation local tooling acts as when nothing names one.

**Only ever reached from a command-line entry point**, never from library code, and never in
cloud mode. The distinction matters: a default buried in ``collection()`` would turn a lost
scope into a silent read of the wrong tenant, which is the failure this whole module is shaped
around. A default on ``make seed`` is a convenience for one developer on one laptop.

``cli_org`` raises in cloud mode rather than falling back, so the same script that is ergonomic
locally is explicit in production.
"""


def cli_org(explicit: str | None = None) -> str:
    """Resolve the organisation a command-line entry point should act as.

    Order: an explicit ``--org``, then ``DRAWBRIDGE_ORG``, then the local development org — and
    that last step only in local mode.

    Raises:
        TenancyError: in cloud mode with no org named. A production script that guessed its
            tenant would be a production script that wrote to the wrong customer.
    """
    import os

    from shared.config import settings

    if explicit:
        return validate_org_id(explicit)

    from_env = os.environ.get("DRAWBRIDGE_ORG")
    if from_env:
        return validate_org_id(from_env)

    if settings().is_cloud:
        raise TenancyError(
            "no organisation named. In cloud mode an entry point must be given one: pass "
            "--org, or set DRAWBRIDGE_ORG."
        )
    return DEV_ORG


def current_org_or_none() -> str | None:
    """The tenant, or ``None``. For code that must behave differently outside a request."""
    return _CURRENT.get()


@contextmanager
def acting_for(org_id: str) -> Iterator[str]:
    """Run a block on behalf of one organisation.

    Restores the previous scope on exit, including on an exception, so a handler that raises
    cannot leave the next message running as the wrong tenant.
    """
    token = _CURRENT.set(validate_org_id(org_id))
    try:
        yield org_id
    finally:
        _CURRENT.reset(token)


def collection(name: str, org_id: str | None = None):
    """A tenant-scoped collection reference.

    This is the only way the product reaches its own data. ``firestore_client().collection(...)``
    is reserved for the three global collections, and ``tests/test_tenancy.py`` asserts that
    against the source tree.

    Args:
        name: the collection, one of the twenty-three per-tenant ones.
        org_id: the owner. Defaults to the ambient tenant, which must have been set.

    Raises:
        TenancyError: when ``name`` is a global collection, or when no org is in scope.
        InvalidOrgId: when the org id is unusable.
    """
    if name in GLOBAL_COLLECTIONS:
        raise TenancyError(
            f"{name!r} is a platform collection and does not live under an organisation; "
            "reach it through shared.tenancy.orgs()/users()/memberships()"
        )
    return org_ref(org_id or current_org()).collection(name)


def document(name: str, doc_id: str, org_id: str | None = None):
    """A tenant-scoped document reference."""
    return collection(name, org_id).document(doc_id)


def across_orgs(name: str):
    """Query one collection across every tenant.

    Deliberately named. Reading across customers is a platform operation — billing rollups, the
    watchdog's portfolio sweep, an operator investigating an incident — and it should be
    impossible to do by accident. Every caller is auditable by grepping for this function.

    Raises:
        TenancyError: on a global collection, which has no per-org copies to span.
    """
    if name in GLOBAL_COLLECTIONS:
        raise TenancyError(f"{name!r} is already global; query it directly")
    return firestore_client().collection_group(name)


# --- Global collections -------------------------------------------------------------------------


def orgs():
    return firestore_client().collection(COLLECTION_ORGS)


def users():
    return firestore_client().collection(COLLECTION_USERS)


def memberships():
    return firestore_client().collection(COLLECTION_MEMBERSHIPS)


# --- Stamping -----------------------------------------------------------------------------------


def stamp(payload: dict, org_id: str | None = None) -> dict:
    """Add the owning org to a document about to be written.

    The path already enforces ownership; this makes the document say so on its own. A row lifted
    into an export, a binder or a restored backup carries its owner with it rather than depending
    on where it used to sit.
    """
    return {**payload, "org_id": validate_org_id(org_id or current_org())}


# --- Reading the tenant ---------------------------------------------------------------------------


def load_org(org_id: str) -> Org | None:
    snap = orgs().document(org_id).get()
    return Org.model_validate(snap.to_dict()) if snap.exists else None


def load_user(uid: str) -> User | None:
    snap = users().document(uid).get()
    return User.model_validate(snap.to_dict()) if snap.exists else None


def load_membership(org_id: str, uid: str) -> Membership | None:
    snap = memberships().document(membership_id(org_id, uid)).get()
    return Membership.model_validate(snap.to_dict()) if snap.exists else None


def orgs_for(uid: str) -> list[Membership]:
    """Every organisation a person belongs to, active memberships only."""
    from google.cloud.firestore_v1 import FieldFilter

    rows = (
        memberships()
        .where(filter=FieldFilter("uid", "==", uid))
        .where(filter=FieldFilter("status", "==", "active"))
        .stream()
    )
    return [Membership.model_validate(r.to_dict()) for r in rows]


def members_of(org_id: str) -> list[Membership]:
    from google.cloud.firestore_v1 import FieldFilter

    rows = memberships().where(filter=FieldFilter("org_id", "==", org_id)).stream()
    return [Membership.model_validate(r.to_dict()) for r in rows]


def may(role: str | None, capability: frozenset[str]) -> bool:
    """Whether a role carries a capability. ``None`` — no membership — never does."""
    return role is not None and role in capability


# --- Provisioning ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Provisioned:
    org: Org
    membership: Membership


def create_org(*, org_id: str, name: str, owner_uid: str, plan: str = "trial") -> Provisioned:
    """Create an organisation and make its creator an admin, atomically.

    One transaction, because an org with no administrator is an org nobody can ever get into,
    and a membership pointing at an org that does not exist is a login that half-succeeds.

    Raises:
        InvalidOrgId: on an unusable id.
        TenancyError: when the id is already taken. Never silently joins an existing org — that
            would be a signup form that hands a stranger an existing customer's data.
    """
    validate_org_id(org_id)
    db = firestore_client()
    now = datetime.now(UTC)

    org = Org(org_id=org_id, name=name, created_at=now, created_by=owner_uid, plan=plan)
    member = Membership(
        org_id=org_id,
        uid=owner_uid,
        role="admin",
        joined_at=now,
        invited_at=now,
        status="active",
    )

    from google.cloud import firestore as fs

    @fs.transactional
    def commit(tx):
        org_doc = orgs().document(org_id)
        if org_doc.get(transaction=tx).exists:
            raise TenancyError(f"organisation {org_id!r} already exists")
        tx.set(org_doc, org.model_dump(mode="json"))
        tx.set(memberships().document(member.membership_id), member.model_dump(mode="json"))

    commit(db.transaction())
    return Provisioned(org=org, membership=member)
