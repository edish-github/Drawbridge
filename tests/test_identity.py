"""Who a person is, and what stops them being someone else.

Authentication is delegated — a security product that rolls its own password hashing has already
lost the argument it exists to make — so these tests are about the parts that are ours: that a
forged token is refused, that the development path cannot survive a deploy, that a role is read
fresh rather than carried in a cookie, and that an organisation can never be left with nobody who
can administer it.

The local development token is the sharpest edge here. It exists because there is no Identity
Platform on a laptop, and an auth bypass that is merely *off by default* is an auth bypass. So
the tests below assert the guard from two directions: that the function refuses in cloud mode, and
that the refusal is in the code rather than in a configuration file somebody could get wrong.
"""

from __future__ import annotations

import ast
import re
import time
import uuid
from pathlib import Path

import pytest

from shared import identity, tenancy
from shared.identity import (
    AuthUnavailable,
    InviteError,
    Principal,
    accept_invite,
    invite,
    issue_dev_token,
    principal_in,
    remove_member,
    set_role,
    verify_token,
)

REPO = Path(__file__).resolve().parent.parent


# --- Token verification -------------------------------------------------------------------


def test_a_valid_token_verifies():
    token = issue_dev_token("uid-1", "ada@example.com", "Ada Lovelace")
    principal = verify_token(token)

    assert principal is not None
    assert principal.uid == "uid-1"
    assert principal.email == "ada@example.com"


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "not-a-token",
        "no-dot-separator",
        "eyJhbGciOiJub25lIn0.",  # a JWT-shaped thing, which this scheme is deliberately not
    ],
)
def test_a_malformed_token_is_refused(token):
    assert verify_token(token) is None


def test_a_tampered_payload_is_refused():
    """The signature covers the claims, so editing the subject invalidates it."""
    token = issue_dev_token("uid-1", "ada@example.com")
    body, signature = token.rsplit(".", 1)

    forged = f"{body[:-4]}AAAA.{signature}"

    assert verify_token(forged) is None


def test_a_tampered_signature_is_refused():
    token = issue_dev_token("uid-1", "ada@example.com")
    body, signature = token.rsplit(".", 1)

    assert verify_token(f"{body}.{signature[:-4]}0000") is None


def test_an_expired_token_is_refused():
    assert verify_token(issue_dev_token("uid-1", "a@b.com", ttl=-1)) is None


def test_a_token_from_another_issuer_is_refused():
    """A token signed with our secret but issued by something else is still not ours."""
    import base64
    import hashlib
    import hmac
    import json

    claims = {"iss": "somebody-else", "sub": "uid-1", "email": "a@b.com", "exp": time.time() + 60}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = hmac.new(identity._dev_secret(), body.encode(), hashlib.sha256).hexdigest()

    assert verify_token(f"{body}.{signature}") is None


def test_every_failure_returns_the_same_answer():
    """A caller that could tell expired from forged from malformed would be an oracle.

    Nothing anywhere does something different with the distinction, so nothing is told it.
    """
    for token in (None, "", "rubbish", issue_dev_token("u", "e", ttl=-1)):
        assert verify_token(token) is None


# --- The development path cannot survive a deploy ---------------------------------------------


def test_the_development_token_is_refused_in_cloud(monkeypatch):
    """Asserted by calling it, not by reading configuration."""
    from shared.config import settings

    settings.cache_clear() if hasattr(settings, "cache_clear") else None
    monkeypatch.setattr("shared.identity.settings", lambda: _CloudSettings())

    with pytest.raises(AuthUnavailable):
        issue_dev_token("uid-1", "a@b.com")


class _CloudSettings:
    is_cloud = True
    project_id = "drawbridge-prod"


def test_the_cloud_guard_is_in_the_code_rather_than_in_configuration():
    """A bypass that is disabled by a setting is a bypass one environment variable away.

    This reads the source and requires the guard to be a branch inside the function, so it cannot
    be turned off without a diff somebody reviews.
    """
    source = (REPO / "shared" / "identity.py").read_text()
    tree = ast.parse(source)

    issuer = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "issue_dev_token"
    )
    body = ast.get_source_segment(source, issuer) or ""

    assert "is_cloud" in body
    assert "raise AuthUnavailable" in body


def test_the_console_refuses_development_credentials_in_cloud_too():
    """Both halves of the product, because either one alone is a bypass."""
    verify = (REPO / "services" / "dashboard" / "lib" / "verify.ts").read_text()
    signup = (REPO / "services" / "dashboard" / "app" / "api" / "signup" / "route.ts").read_text()

    assert "refused in cloud mode" in verify
    assert "assertLocalCredentialsAllowed" in signup
    assert 'RUNTIME_MODE === "cloud"' in signup


def test_the_console_never_stores_a_password_in_cloud():
    """The claim a security product has to be able to make: we never receive it."""
    signup = (REPO / "services" / "dashboard" / "app" / "api" / "signup" / "route.ts").read_text()

    assert "scryptSync" in signup, "the local store must hash rather than keep plaintext"
    assert "plaintext" not in signup.lower().replace("keep plaintext", "")
    # The cloud path must be unreachable, not merely unused: every verb guards.
    assert signup.count("assertLocalCredentialsAllowed()") >= 2


# --- Authorisation ------------------------------------------------------------------------------


@pytest.fixture
def org():
    org_id = f"idn-{uuid.uuid4().hex[:8]}"
    tenancy.create_org(org_id=org_id, name="Identity Test", owner_uid="owner-uid")
    return org_id


def test_a_verified_person_who_is_not_a_member_has_no_role(org):
    """Different from 'not signed in', and the surfaces render it differently: one is a login
    page, the other is *you do not have access to this workspace*."""
    outsider = Principal(uid="stranger", email="s@example.com", name="Stranger")

    resolved = principal_in(outsider, org)

    assert resolved.org_id == org
    assert resolved.role is None
    assert resolved.is_member is False


def test_a_member_carries_their_role(org):
    resolved = principal_in(Principal(uid="owner-uid", email="o@example.com", name="Owner"), org)

    assert resolved.role == "admin"
    assert resolved.may(tenancy.CAN_APPROVE)


def test_an_invited_but_unaccepted_membership_carries_no_role(org):
    invite(org, email="pending@example.com", role="approver", invited_by="owner-uid")

    resolved = principal_in(Principal(uid="pending-uid", email="pending@example.com", name=""), org)

    assert resolved.role is None


def test_a_removed_member_loses_their_role_immediately(org):
    """Roles are read per request, never carried in a session. A revoked approver loses the gate
    on their next page load rather than in twelve hours."""
    invite(org, email="analyst@example.com", role="analyst", invited_by="owner-uid")
    accept_invite(org, email="analyst@example.com", uid="analyst-uid")
    assert principal_in(Principal("analyst-uid", "analyst@example.com", ""), org).role == "analyst"

    remove_member(org, "analyst-uid")

    assert principal_in(Principal("analyst-uid", "analyst@example.com", ""), org).role is None


def test_a_suspended_organisation_carries_no_roles(org):
    """A customer whose account is closed should not keep working through an open session."""
    tenancy.orgs().document(org).set({"status": "suspended"}, merge=True)

    assert principal_in(Principal("owner-uid", "o@example.com", ""), org).role is None


# --- Invitations ----------------------------------------------------------------------------


def test_an_invitation_is_keyed_on_the_email_until_it_is_accepted(org):
    """The invitee may have no account yet, and therefore no uid."""
    pending = invite(org, email="New.Person@Example.com", role="viewer", invited_by="owner-uid")

    assert pending.uid == "invite:new.person@example.com"
    assert pending.status == "invited"


def test_accepting_rekeys_the_membership_onto_the_uid(org):
    invite(org, email="joiner@example.com", role="analyst", invited_by="owner-uid")

    accepted = accept_invite(org, email="joiner@example.com", uid="joiner-uid")

    assert accepted.uid == "joiner-uid"
    assert accepted.status == "active"
    assert tenancy.load_membership(org, "invite:joiner@example.com") is None


def test_accepting_an_invitation_that_does_not_exist_is_refused(org):
    """An accept endpoint that provisioned access for an unrecognised address is an open door."""
    with pytest.raises(InviteError, match="no pending invitation"):
        accept_invite(org, email="uninvited@example.com", uid="uninvited-uid")


def test_an_invitation_cannot_be_accepted_twice(org):
    invite(org, email="once@example.com", role="viewer", invited_by="owner-uid")
    accept_invite(org, email="once@example.com", uid="once-uid")

    with pytest.raises(InviteError):
        accept_invite(org, email="once@example.com", uid="somebody-else")


def test_an_unknown_role_is_refused(org):
    """Never defaults. An invitation that granted less than intended is a support ticket; one
    that granted more is an incident."""
    with pytest.raises(InviteError, match="is not a role"):
        invite(org, email="x@example.com", role="superuser", invited_by="owner-uid")  # type: ignore[arg-type]


# --- The last administrator -----------------------------------------------------------------


def test_the_last_administrator_cannot_be_demoted(org):
    """An organisation with no administrator is one nobody can ever get back into, and it is
    reachable by one careless dropdown."""
    with pytest.raises(InviteError, match="only administrator"):
        set_role(org, "owner-uid", "viewer")


def test_the_last_administrator_cannot_be_removed(org):
    with pytest.raises(InviteError, match="only administrator"):
        remove_member(org, "owner-uid")


def test_an_administrator_can_be_demoted_once_there_is_another(org):
    invite(org, email="second@example.com", role="admin", invited_by="owner-uid")
    accept_invite(org, email="second@example.com", uid="second-uid")

    set_role(org, "owner-uid", "analyst")

    assert tenancy.load_membership(org, "owner-uid").role == "analyst"


def test_removal_marks_rather_than_deletes(org):
    """Their approvals are in the audit ledger for years. A binder referencing a membership that
    no longer exists would have a hole where the approver's role used to be."""
    invite(org, email="leaver@example.com", role="analyst", invited_by="owner-uid")
    accept_invite(org, email="leaver@example.com", uid="leaver-uid")

    remove_member(org, "leaver-uid")

    membership = tenancy.load_membership(org, "leaver-uid")
    assert membership is not None
    assert membership.status == "removed"
    assert membership.role == "analyst"


# --- The user record ------------------------------------------------------------------------


def test_a_person_is_recorded_on_first_sight(org):
    uid = f"first-{uuid.uuid4().hex[:8]}"

    user = identity.upsert_user(Principal(uid=uid, email="new@example.com", name="New Person"))

    assert user.uid == uid
    assert tenancy.load_user(uid) is not None


def test_the_subject_is_the_identity_and_the_email_is_a_label():
    """An audit record naming an approver by an address somebody else now owns is worse than one
    naming nobody. Memberships key on the subject; the email is carried for display."""
    source = (REPO / "shared" / "identity.py").read_text()

    assert "The subject, never the email" in source
    assert re.search(r"uid=str\(claims\[.sub.\]\)", source)
