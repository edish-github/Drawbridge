"""The vendor portal, and the link that stands in for an account.

A vendor has no account. They are somebody at another company who was emailed a questionnaire and
would rather be doing something else, and asking them to register is how a three-day review
becomes a six-week one. So the portal authenticates a **link**, and the link is the whole control.

That makes it a bearer credential — anyone holding it is the vendor as far as this system is
concerned — which is the same trust model as every "click here to continue" email ever sent. The
tests below are about keeping that model honest: the signature covers every field, the scope is
one review, expiry is enforced, and every refusal renders the same page so nobody can enumerate
review ids by trying them.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared.portal_links import issue, verify
from tests.conftest import TEST_ORG, emulator_required

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    from services.portal.main import app

    return TestClient(app)


@pytest.fixture
def open_review(db):
    """A review a vendor may still contribute to."""
    review_id = f"portal-{uuid.uuid4().hex[:10]}"
    db.collection("vendors").document("portalvendor").set(
        {"vendor_id": "portalvendor", "name": "Portal Test Vendor", "category": "testing"}
    )
    db.collection("reviews").document(review_id).set(
        {
            "review_id": review_id,
            "vendor_id": "portalvendor",
            "state": "questionnaire_out",
            "tier": 2,
            "plan_version": 1,
            "opened_at": "2026-01-01T00:00:00+00:00",
            "sent_questions": ["DP01", "AC01"],
        }
    )
    return review_id


# --- The link -------------------------------------------------------------------------------


def test_a_link_carries_its_org_and_review():
    link = verify(issue("acme-security", "rev-1"))

    assert link is not None
    assert link.org_id == "acme-security"
    assert link.review_id == "rev-1"


def test_a_link_is_good_for_one_review():
    """A vendor with two reviews open has two links, and neither reaches the other's evidence."""
    first = verify(issue("acme-security", "rev-1"))
    second = verify(issue("acme-security", "rev-2"))

    assert first.review_id != second.review_id


@pytest.mark.parametrize("token", [None, "", "nonsense", "no-dot", "a.b.c.d"])
def test_a_malformed_link_is_refused(token):
    assert verify(token) is None


def test_a_tampered_link_is_refused():
    """The signature covers every claim, so editing the org invalidates it — which is the failure
    that would otherwise let one vendor's link read another customer's review."""
    token = issue("acme-security", "rev-1")
    body, signature = token.rsplit(".", 1)

    assert verify(f"{body[:-6]}AAAAAA.{signature}") is None


def test_a_forged_signature_is_refused():
    token = issue("acme-security", "rev-1")
    body, _ = token.rsplit(".", 1)

    assert verify(f"{body}.{'0' * 32}") is None


def test_an_expired_link_is_refused():
    assert verify(issue("acme-security", "rev-1", ttl_days=-1)) is None


def test_a_link_carries_no_capability():
    """Nothing on the link decides what may be done. Its permissions are the service account's,
    and a link that could widen them would be a link worth stealing."""
    import base64
    import json

    body, _ = issue("acme-security", "rev-1").rsplit(".", 1)
    claims = json.loads(base64.urlsafe_b64decode(body.encode() + b"=="))

    assert set(claims) == {"org", "review", "exp"}


def test_the_portal_secret_is_required_in_cloud(monkeypatch):
    """Deriving it from a project id in production would make every link forgeable by anybody who
    knows the project id, which is not a secret."""
    from shared.portal_links import PortalSecretMissing, _secret

    monkeypatch.delenv("DRAWBRIDGE_PORTAL_SECRET", raising=False)
    monkeypatch.setattr("shared.portal_links.settings", lambda: _Cloud())

    with pytest.raises(PortalSecretMissing):
        _secret()


class _Cloud:
    is_cloud = True
    project_id = "drawbridge-prod"


# --- The pages -------------------------------------------------------------------------------


@emulator_required
def test_a_valid_link_renders_the_questionnaire(client, open_review):
    response = client.get(f"/r/{issue(TEST_ORG, open_review)}")

    assert response.status_code == 200
    assert "Portal Test Vendor" in response.text
    assert "DP01" in response.text


@emulator_required
def test_only_the_questions_actually_sent_are_shown(client, open_review):
    """A re-tier adds questions mid-review. A portal that recomputed from the tier would show a
    vendor questions nobody had sent them."""
    body = client.get(f"/r/{issue(TEST_ORG, open_review)}").text

    assert "DP01" in body and "AC01" in body
    assert "2 questions" in body or "of 2 answered" in body


@emulator_required
@pytest.mark.parametrize(
    "token", ["nonsense", "a.b", "eyJvcmciOiJ4In0.0000000000000000000000000000000"]
)
def test_every_refusal_renders_the_same_page(client, token):
    """A vendor cannot act on the difference between expired and forged, and a portal that
    distinguished them would confirm which review ids exist to anybody who tried a few."""
    response = client.get(f"/r/{token}")

    assert response.status_code == 404
    assert "no longer usable" in response.text


@emulator_required
def test_a_link_for_a_closed_review_is_refused(client, db):
    """A decided review's record is immutable. A late submission opens a new review rather than
    editing a closed one."""
    review_id = f"closed-{uuid.uuid4().hex[:8]}"
    db.collection("reviews").document(review_id).set(
        {"review_id": review_id, "vendor_id": "portalvendor", "state": "decided"}
    )

    response = client.get(f"/r/{issue(TEST_ORG, review_id)}")

    assert response.status_code == 404


@emulator_required
def test_a_link_for_another_tenants_review_finds_nothing(client, open_review):
    """The isolation that matters most here: a forged org in a link resolves to a path with no
    review in it."""
    response = client.get(f"/r/{issue('some-other-org', open_review)}")

    assert response.status_code == 404


# --- Answering --------------------------------------------------------------------------------


@emulator_required
def test_an_answer_is_saved_and_reappears(client, open_review, db):
    token = issue(TEST_ORG, open_review)

    saved = client.post(
        f"/r/{token}/answer",
        data={"question_id": "DP01", "text": "AES-256 at rest, TLS 1.3 in transit."},
    )

    assert saved.status_code == 200
    assert "AES-256" in client.get(f"/r/{token}").text


@emulator_required
def test_editing_an_answer_replaces_rather_than_duplicates(client, open_review, db):
    """Saved as somebody types, so the same answer edited five times must be one document."""
    token = issue(TEST_ORG, open_review)

    for text in ("first", "second", "third"):
        client.post(f"/r/{token}/answer", data={"question_id": "DP01", "text": text})

    stored = db.collection("qa_responses").document(f"{open_review}:DP01").get().to_dict()
    assert stored["text"] == "third"


@emulator_required
def test_a_portal_answer_declares_where_it_came_from(client, open_review, db):
    """Provenance, not a judgement. A portal answer was typed into a form; the confidence a
    parsed email answer carries does not apply, and inventing one would be the fleet asserting
    something it did not measure."""
    token = issue(TEST_ORG, open_review)
    client.post(f"/r/{token}/answer", data={"question_id": "DP01", "text": "x"})

    stored = db.collection("qa_responses").document(f"{open_review}:DP01").get().to_dict()

    assert stored["provenance"] == "portal"
    assert "confidence" not in stored


@emulator_required
def test_an_answer_cannot_be_saved_through_a_bad_link(client, open_review):
    response = client.post("/r/forged.0000/answer", data={"question_id": "DP01", "text": "x"})

    assert response.status_code == 404


# --- The portal's own boundaries -----------------------------------------------------------------


def test_the_portal_never_reads_quarantine():
    """It writes uploads and cannot retrieve them, which is what makes "no agent has a code path
    into quarantine" true rather than aspirational."""
    source = (REPO / "services" / "portal" / "main.py").read_text()

    assert "read_object" not in source
    assert "write_object" in source


def test_the_portal_never_reads_a_finding_or_a_score():
    """A vendor must not be able to read the conclusions being drawn about them."""
    source = (REPO / "services" / "portal" / "main.py").read_text()

    for forbidden in ('"findings"', '"scores"', '"memos"', '"decisions"'):
        assert forbidden not in source, f"the portal reaches {forbidden}"


def test_the_portal_works_without_javascript():
    """Its audience did not choose this product, will use it once, and may be on a phone on a
    train. The auto-save is enhancement; the form is a plain POST."""
    source = (REPO / "services" / "portal" / "main.py").read_text()

    assert '<form method="post"' in source
    assert "Progressive enhancement only" in source
