"""Approval tokens are asymmetric: the gateway verifies, and cannot sign.

If both sides shared one secret, anything that can verify could also forge — and the gateway is
reachable by every agent in the fleet. These tests are what turn "the gateway checks for a
token" into "the gateway can recognise a human decision but is structurally incapable of
manufacturing one".

The tests below run against local verification, which is a record lookup rather than a
signature check. That is honest about what it proves: scope, target, expiry and single use are
enforced today, and what stops an agent writing its own approval is collection-level IAM. The
tests that require asymmetric key material stay skipped and say so, because a test that asserted
forgery is impossible while the mechanism preventing it does not exist is worse than no test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from shared import approvals
from shared.approvals import COLLECTION_APPROVALS, Approval, token_for
from shared.clients import firestore_client
from shared.gateway import SigningKeyUnavailable, issue_approval_token, verify_approval_token
from tests.conftest import emulator_required

TARGET = "contact@vendor.example"


def write_approval(
    review_id: str,
    *,
    scope: str = "contact",
    target: str = TARGET,
    expires_in_minutes: int = 10,
) -> str:
    """Record an approval the way the approval service does, and return its token.

    The test stands in for the approval service on purpose: there is no minting function inside
    the shared package to call, which is the property under test in the first case below.
    """
    now = datetime.now(UTC)
    approval = Approval(
        jti=uuid.uuid4().hex,
        review_id=review_id,
        scope=scope,
        target=target,
        identity="test-operator",
        issued_at=now,
        expires_at=now + timedelta(minutes=expires_in_minutes),
    )
    firestore_client().collection(COLLECTION_APPROVALS).document(approval.jti).set(
        approval.model_dump(mode="json")
    )
    return token_for(approval.jti)


# --- The gateway cannot sign ------------------------------------------------------------------


def test_the_gateway_cannot_mint_an_approval():
    """The gateway process holds the public key only, so asking it to sign fails loudly."""
    with pytest.raises(SigningKeyUnavailable):
        issue_approval_token("r1", scope="decision", identity="x")


def test_the_shared_package_exposes_no_way_to_issue_one():
    """Structural, not conventional: an agent that imports everything still cannot mint.

    Minting lives in the approval service and, locally, in ``scripts/issue_token.py``. If an
    issue function ever appears in ``shared.approvals``, every agent in the fleet gains the
    ability to approve its own outbound contact.
    """
    exposed = [name for name in dir(approvals) if not name.startswith("_")]
    forbidden = {"issue", "mint", "sign", "create_approval", "issue_approval_token"}

    assert not forbidden.intersection(exposed), f"minting reached the shared package: {exposed}"


def test_an_unsigned_invented_token_fails():
    """The assume-breach case at the level the local mechanism operates on."""
    forged = token_for(uuid.uuid4().hex)

    assert verify_approval_token("r1", TARGET, token=forged) is False


def test_an_absent_or_malformed_token_fails():
    assert verify_approval_token("r1", TARGET, token=None) is False
    assert verify_approval_token("r1", TARGET, token="") is False
    assert verify_approval_token("r1", TARGET, token="not-one-of-ours") is False
    assert verify_approval_token("r1", TARGET, token="drawbridge-approval:") is False


# --- Scope, target, expiry, single use --------------------------------------------------------


@emulator_required
def test_a_valid_token_verifies_once(review_id):
    token = write_approval(review_id)

    assert verify_approval_token(review_id, TARGET, token=token) is True


@emulator_required
def test_a_token_is_spent_the_first_time_it_verifies(review_id):
    """Single use, recorded by jti. The spend is what retires the human decision."""
    token = write_approval(review_id)

    assert verify_approval_token(review_id, TARGET, token=token) is True
    assert approvals.already_spent(approvals.jti_of(token)) is True


@emulator_required
def test_a_spent_token_cannot_be_carried_to_another_address(review_id):
    """The replay that matters. Once contact with one address is authorised the fleet may
    write to *that* address again, so a spent token is only dangerous if it can reach a
    different recipient — and it cannot."""
    token = write_approval(review_id)

    assert verify_approval_token(review_id, TARGET, token=token) is True
    assert verify_approval_token(review_id, "attacker@elsewhere.example", token=token) is False


@emulator_required
def test_first_contact_is_the_gate_and_later_messages_are_not(review_id):
    """P1 gates G2 — *first* outbound contact. Once a named human has approved writing to an
    address, the chases, the follow-up and the questions a re-tier adds are the same authorised
    conversation. Re-approving each message would make the gate noise rather than a control."""
    token = write_approval(review_id)

    assert verify_approval_token(review_id, TARGET, token=token) is True
    assert verify_approval_token(review_id, TARGET, token=None) is True


@emulator_required
def test_a_contact_address_that_changed_mid_review_is_unauthorised_again(review_id):
    """The case the narrowness is for: a redirected vendor contact is a new decision."""
    verify_approval_token(review_id, TARGET, token=write_approval(review_id))

    assert approvals.contact_established(review_id, TARGET) is True
    assert approvals.contact_established(review_id, "new-contact@vendor.example") is False


@emulator_required
def test_a_token_for_another_review_fails(review_id):
    token = write_approval("some-other-review")

    assert verify_approval_token(review_id, TARGET, token=token) is False


@emulator_required
def test_a_token_scoped_to_another_address_fails(review_id):
    token = write_approval(review_id, target="someone@else.example")

    assert verify_approval_token(review_id, TARGET, token=token) is False


@emulator_required
def test_a_decision_token_does_not_authorise_contact(review_id):
    """A contact approval must never double as a vendor approval, in either direction."""
    token = write_approval(review_id, scope="decision")

    assert verify_approval_token(review_id, TARGET, token=token, scope="contact") is False


@emulator_required
def test_an_expired_token_fails(review_id):
    token = write_approval(review_id, expires_in_minutes=-1)

    assert verify_approval_token(review_id, TARGET, token=token) is False


@emulator_required
def test_a_rejected_token_is_not_consumed(review_id):
    """A mis-scoped presentation must not burn the approval it was not entitled to."""
    token = write_approval(review_id, target="someone@else.example")

    assert verify_approval_token(review_id, TARGET, token=token) is False
    assert verify_approval_token(review_id, "someone@else.example", token=token) is True


@emulator_required
def test_verification_never_writes_to_the_approvals_collection(review_id, db):
    """Single use is recorded where the gateway may write, not where approvals are authored."""
    token = write_approval(review_id)
    jti = approvals.jti_of(token)

    verify_approval_token(review_id, TARGET, token=token)

    assert db.collection(COLLECTION_APPROVALS).document(jti).get().to_dict()["jti"] == jti
    assert db.collection(approvals.COLLECTION_SPENT).document(jti).get().exists


# --- Still contracts ----------------------------------------------------------------------------


@pytest.mark.skip(reason="asymmetric verification lands with the approval service")
def test_a_token_signed_with_the_public_key_fails():
    """Verification with the public half must not accept a signature made with it."""
    claims = {"review_id": review_id(), "scope": "decision"}
    forged = jwt_sign(claims, key=public_key(APPROVAL_PUBLIC_KEY))

    assert verify_approval_token(review_id(), TARGET, token=forged) is False


@pytest.mark.skip(reason="asymmetric verification lands with the approval service")
def test_an_agent_holding_the_gateway_config_cannot_forge_a_token():
    """The assume-breach case: even with everything the gateway holds, forgery is impossible."""
    config = full_gateway_configuration()

    forged = attempt_to_mint_token(config, review_id=review_id(), scope="decision")

    assert verify_approval_token(review_id(), TARGET, token=forged) is False


@pytest.mark.skip(reason="the decision gate lands with the Risk Scorer")
def test_no_code_path_reaches_decided_without_a_token():
    r = run_until("scored", vendor="cleancloud")

    with pytest.raises(PolicyViolation):
        force_state(r.review_id, ReviewState.DECIDED)

    assert load_review(r.review_id).state == ReviewState.GATED
    assert load_review(r.review_id).gate_scope == "decision"
