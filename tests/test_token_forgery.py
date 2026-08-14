"""Approval tokens are asymmetric: the gateway verifies, and cannot sign.

If both sides shared one secret, anything that can verify could also forge — and the gateway is
reachable by every agent in the fleet. These tests are what turn "the gateway checks for a
token" into "the gateway can recognise a human decision but is structurally incapable of
manufacturing one".
"""

import pytest


@pytest.mark.skip(reason="shared/gateway.py is a contract; unskip when it executes")
def test_gateway_cannot_mint_an_approval():
    """The gateway process holds the public key only."""
    with pytest.raises(SigningKeyUnavailable):
        as_service("gateway").issue_approval_token(review_id(), scope="decision", identity="x")

    claims = {"review_id": review_id(), "scope": "decision"}
    forged = jwt_sign(claims, key=public_key(APPROVAL_PUBLIC_KEY))
    assert verify_approval_token(review_id(), "contact@vendor.example", token=forged) is False


@pytest.mark.skip(reason="shared/gateway.py is a contract; unskip when it executes")
def test_an_agent_holding_the_gateway_config_cannot_forge_a_token():
    """The assume-breach case: even with everything the gateway holds, forgery is impossible."""
    config = full_gateway_configuration()

    forged = attempt_to_mint_token(config, review_id=review_id(), scope="decision")

    assert verify_approval_token(review_id(), "contact@vendor.example", token=forged) is False


@pytest.mark.skip(reason="shared/gateway.py is a contract; unskip when it executes")
def test_a_replayed_token_fails():
    """Single use, recorded by jti."""
    token = issue_valid_token(review_id(), scope="email:contact@vendor.example")

    assert verify_approval_token(review_id(), "contact@vendor.example", token=token) is True
    assert verify_approval_token(review_id(), "contact@vendor.example", token=token) is False


@pytest.mark.skip(reason="shared/gateway.py is a contract; unskip when it executes")
def test_a_token_for_another_review_fails():
    token = issue_valid_token("some-other-review", scope="decision")

    assert verify_approval_token(review_id(), "contact@vendor.example", token=token) is False


@pytest.mark.skip(reason="shared/gateway.py is a contract; unskip when it executes")
def test_a_token_scoped_to_another_address_fails():
    token = issue_valid_token(review_id(), scope="email:someone@else.example")

    assert verify_approval_token(review_id(), "contact@vendor.example", token=token) is False


@pytest.mark.skip(reason="shared/gateway.py is a contract; unskip when it executes")
def test_an_expired_token_fails():
    token = issue_expired_token(review_id(), scope="decision")

    assert verify_approval_token(review_id(), "contact@vendor.example", token=token) is False


@pytest.mark.skip(reason="shared/gateway.py is a contract; unskip when it executes")
def test_no_code_path_reaches_decided_without_a_token():
    r = run_until("scored", vendor="cleancloud")

    with pytest.raises(PolicyViolation):
        force_state(r.review_id, ReviewState.DECIDED)

    assert load_review(r.review_id).state == ReviewState.GATED
    assert load_review(r.review_id).gate_scope == "decision"


@pytest.mark.skip(reason="shared/gateway.py is a contract; unskip when it executes")
def test_first_outbound_contact_without_a_token_parks_at_the_contact_gate():
    """P1 is the machine's inability to skip G2."""
    r = run_until("plan_ready", vendor="cleancloud")

    with pytest.raises(PolicyViolation):
        send_questionnaire(ctx(), r.review_id, "contact@vendor.example", questions=[])

    assert load_review(r.review_id).state == ReviewState.GATED
    assert load_review(r.review_id).gate_scope == "contact"
    assert inbox_count("cleancloud") == 0
