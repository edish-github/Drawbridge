"""Isolation between customers, asserted rather than assumed.

This is the most security-critical file in the suite. Everything else in Drawbridge protects one
organisation from a vendor that lies to it; this protects each organisation from every other one,
and a failure here is the kind that ends a company rather than a review.

The design under test: **a tenant's data is reached by path, not by filter.** There is no
``where("org_id", "==", ...)`` anywhere in the product, because that is the design where one
forgotten clause returns another customer's evidence and nothing goes red. Under a path the same
mistake is a ``TenancyError`` at the call site.

So the tests come in three groups. That the path helper works. That nothing bypasses it — asserted
against the source tree, because the bypass that matters is the one somebody adds in a hurry. And
that two organisations genuinely cannot see each other, exercised against the emulator with real
data in both.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from shared import tenancy
from shared.tenancy import (
    GLOBAL_COLLECTIONS,
    InvalidOrgId,
    TenancyError,
    acting_for,
    collection,
    create_org,
    current_org,
)
from tests.conftest import TEST_ORG, emulator_required

REPO = Path(__file__).resolve().parent.parent


# --- Ids ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("org_id", ["acme", "acme-security", "a1b2c3", "x" * 40])
def test_a_usable_id_is_accepted(org_id):
    assert tenancy.validate_org_id(org_id) == org_id


@pytest.mark.parametrize(
    "org_id",
    [
        "",            # nothing
        "a",           # too short to be distinctive
        "x" * 41,      # too long for a subdomain label
        "Acme",        # uppercase does not survive a DNS round trip
        "acme_corp",   # underscore is not valid in a hostname
        "-acme",       # leading hyphen
        "acme-",       # trailing hyphen
        "acme corp",   # whitespace
        "acme/evil",   # a path separator, which is the one that would matter
        "../etc",      # traversal
    ],
)
def test_an_unusable_id_is_rejected(org_id):
    """Never normalised. An id that had to be corrected is one two systems disagree about."""
    with pytest.raises(InvalidOrgId):
        tenancy.validate_org_id(org_id)


@pytest.mark.parametrize("name", sorted(GLOBAL_COLLECTIONS))
def test_an_org_cannot_be_named_after_a_platform_collection(name):
    """``orgs/users`` would be a tenant whose path collides with the user directory."""
    with pytest.raises(InvalidOrgId):
        tenancy.validate_org_id(name)


# --- The scope ------------------------------------------------------------------------------------


def test_no_scope_is_an_error_rather_than_a_default():
    """The single most important assertion in this file.

    A fallback organisation turns a missing scope from a crash into a disclosure. There is no
    default anywhere in the module, and this is what stops one being added.
    """
    token = tenancy._CURRENT.set(None)
    try:
        with pytest.raises(TenancyError, match="no organisation is in scope"):
            current_org()
        with pytest.raises(TenancyError):
            collection("reviews")
    finally:
        tenancy._CURRENT.reset(token)


def test_a_scope_is_restored_after_the_block():
    with acting_for("first-org"):
        assert current_org() == "first-org"
        with acting_for("second-org"):
            assert current_org() == "second-org"
        assert current_org() == "first-org"
    assert current_org() == TEST_ORG


def test_a_scope_is_restored_even_when_the_block_raises():
    """A handler that raises must not leave the next message running as the wrong tenant."""
    with pytest.raises(ValueError):
        with acting_for("transient-org"):
            raise ValueError("boom")
    assert current_org() == TEST_ORG


def test_a_platform_collection_cannot_be_reached_through_the_tenant_helper():
    for name in GLOBAL_COLLECTIONS:
        with pytest.raises(TenancyError, match="platform collection"):
            collection(name)


def test_the_path_names_the_org():
    """The isolation, stated as a path. ``orgs/{org}/reviews`` and nothing shorter."""
    ref = collection("reviews", org_id="some-org")

    assert ref.id == "reviews"
    assert ref.parent.id == "some-org"
    assert ref.parent.parent.id == "orgs"


    # The helper itself, and the module that builds the client it wraps.


PRODUCT_ROOTS = (
    "shared",
    "agents",
    "services/binder",
    "services/screening",
    "services/vendor_inbox",
)

ALLOWED_RAW = {
    # The helper itself, and the module that builds the client it wraps.
    "shared/tenancy.py",
    "shared/clients.py",
}


def product_sources():
    for root in PRODUCT_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if str(path.relative_to(REPO)) in ALLOWED_RAW:
                continue
            yield path


def test_no_product_code_reaches_firestore_without_a_tenant():
    """The bypass that matters is the one somebody adds in a hurry.

    ``firestore_client().collection(...)`` is how every one of these call sites looked before
    tenancy existed, and it still works — it just reaches the wrong place. Asserted against the
    source tree so the next one fails here rather than in a customer's data.
    """
    offenders = []
    pattern = re.compile(r"firestore_client\(\)\s*\.?\s*\n?\s*\.?\s*collection\(")

    for path in product_sources():
        source = path.read_text()
        for match in pattern.finditer(source):
            line = source[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(REPO)}:{line}")

    assert not offenders, (
        "these reach Firestore without naming a tenant: "
        f"{offenders}. Use shared.tenancy.collection() — or tenancy.across_orgs() if the read "
        "genuinely spans customers, which is a deliberate act with its own name."
    )


def test_cross_tenant_reads_are_named_and_countable():
    """``across_orgs`` is the one sanctioned way to read every customer at once.

    It exists — billing rollups and platform incident work need it — and every caller should be
    findable by grepping for it. This test is the grep, and it fails if the count grows without
    somebody updating the expectation.
    """
    callers = [
        str(p.relative_to(REPO))
        for p in product_sources()
        if "across_orgs(" in p.read_text()
    ]
    assert callers == [], (
        f"cross-tenant reads appeared in product code: {callers}. That may be correct — update "
        "this expectation deliberately, having checked each one is a platform operation rather "
        "than a missing scope."
    )


def test_the_generated_rules_agree_with_the_module_on_what_is_global():
    """A collection treated as global on one side and tenanted on the other is reachable at a
    path no rule covers."""
    from infra.firestore.generate_rules import GLOBAL_COLLECTIONS as RULE_GLOBALS

    assert set(RULE_GLOBALS) == set(GLOBAL_COLLECTIONS)


def test_every_context_carries_an_org():
    """``AgentContext`` has no default org, so a handler cannot be built without one."""
    from shared.context import AgentContext

    with pytest.raises(TypeError):
        AgentContext(review_id="r", agent="a")  # type: ignore[call-arg]


def test_every_event_carries_its_org():
    """One topic carries every tenant's traffic, so the envelope has to say whose."""
    from pydantic import ValidationError

    from shared.events import EventEnvelope

    with pytest.raises(ValidationError, match="org_id"):
        EventEnvelope(type="review.intake", review_id="r", idem_key="k", trace_id="t", source="s")


# --- Two organisations, against the emulator ------------------------------------------------


@pytest.fixture
def two_orgs():
    """Two organisations with the same-shaped data in each."""
    a = f"iso-a-{uuid.uuid4().hex[:8]}"
    b = f"iso-b-{uuid.uuid4().hex[:8]}"
    return a, b


@emulator_required
def test_one_org_cannot_see_another_orgs_reviews(two_orgs):
    a, b = two_orgs

    with acting_for(a):
        collection("reviews").document("shared-id").set({"review_id": "shared-id", "secret": "A"})
    with acting_for(b):
        collection("reviews").document("shared-id").set({"review_id": "shared-id", "secret": "B"})

    with acting_for(a):
        assert collection("reviews").document("shared-id").get().to_dict()["secret"] == "A"
    with acting_for(b):
        assert collection("reviews").document("shared-id").get().to_dict()["secret"] == "B"


@emulator_required
def test_the_same_review_id_in_two_orgs_is_two_reviews(two_orgs):
    """Ids are unique per tenant, not globally.

    A customer choosing 'REV-001' must not collide with another who chose the same.
    """
    a, b = two_orgs

    with acting_for(a):
        collection("findings").document("f1").set({"summary": "A's finding"})
    with acting_for(b):
        assert not collection("findings").document("f1").get().exists


@emulator_required
def test_a_listing_never_crosses_a_tenant(two_orgs):
    """The failure a filter-based design produces: a query that forgot its clause."""
    a, b = two_orgs

    with acting_for(a):
        for i in range(3):
            collection("vendors").document(f"v{i}").set({"name": f"A vendor {i}"})
    with acting_for(b):
        collection("vendors").document("v0").set({"name": "B vendor"})

        names = [d.to_dict()["name"] for d in collection("vendors").stream()]
        assert names == ["B vendor"]


@emulator_required
def test_a_document_says_which_org_it_belongs_to(two_orgs):
    """The path is the enforcement; the field is the provenance. A row lifted into an export or a
    restored backup still says whose it is."""
    a, _ = two_orgs

    with acting_for(a):
        collection("reviews").document("r").set(tenancy.stamp({"review_id": "r"}))
        assert collection("reviews").document("r").get().to_dict()["org_id"] == a


# --- Provisioning ---------------------------------------------------------------------------


@emulator_required
def test_creating_an_org_makes_its_creator_an_admin():
    """An organisation with no administrator is one nobody can ever get into."""
    org_id = f"prov-{uuid.uuid4().hex[:8]}"

    result = create_org(org_id=org_id, name="Provisioned", owner_uid="uid-1")

    assert result.org.org_id == org_id
    assert result.membership.role == "admin"
    assert result.membership.status == "active"
    assert tenancy.load_membership(org_id, "uid-1").role == "admin"


@emulator_required
def test_signing_up_into_an_existing_org_is_refused():
    """A signup that silently joined an existing org would hand a stranger a customer's data."""
    org_id = f"taken-{uuid.uuid4().hex[:8]}"
    create_org(org_id=org_id, name="First", owner_uid="uid-1")

    with pytest.raises(TenancyError, match="already exists"):
        create_org(org_id=org_id, name="Impostor", owner_uid="uid-2")


@emulator_required
def test_a_person_can_belong_to_several_orgs():
    """Consultants and managed providers are the ordinary case, not an edge one."""
    first = f"multi-a-{uuid.uuid4().hex[:6]}"
    second = f"multi-b-{uuid.uuid4().hex[:6]}"
    create_org(org_id=first, name="First", owner_uid="shared-uid")
    create_org(org_id=second, name="Second", owner_uid="shared-uid")

    mine = {m.org_id for m in tenancy.orgs_for("shared-uid")}

    assert {first, second} <= mine


# --- Roles ----------------------------------------------------------------------------------


def test_only_an_approver_or_an_admin_may_approve():
    """The role that carries legal weight: an approval is a named person accepting risk."""
    assert tenancy.may("approver", tenancy.CAN_APPROVE)
    assert tenancy.may("admin", tenancy.CAN_APPROVE)
    assert not tenancy.may("analyst", tenancy.CAN_APPROVE)
    assert not tenancy.may("viewer", tenancy.CAN_APPROVE)


def test_no_membership_carries_no_capability():
    for capability in (tenancy.CAN_APPROVE, tenancy.CAN_ACT, tenancy.CAN_ADMINISTER):
        assert not tenancy.may(None, capability)


def test_a_viewer_changes_nothing():
    assert not tenancy.may("viewer", tenancy.CAN_ACT)
    assert not tenancy.may("viewer", tenancy.CAN_ADMINISTER)


def test_roles_are_not_a_hierarchy():
    """``approver`` and ``admin`` differ in kind, not degree.

    An org administrator who manages billing has no business accepting security risk by default,
    and a numeric level would grant it silently.
    """
    assert "admin" in tenancy.CAN_APPROVE
    assert tenancy.CAN_ADMINISTER == frozenset({"admin"})
    assert "approver" not in tenancy.CAN_ADMINISTER
