"""Least privilege, asserted rather than described.

Most tests here pass by a denial. Failing as expected is the feature: a permission row that
stops being true shows up here rather than in a demo.

**What is proven and what is not.** The emulator evaluates security rules, and the rules are
generated from the permission matrix, so every collection-level row is checked: given this
identity, may it write this collection? That is the half of least privilege that drifts,
because it drifts every time a new collection is added to the code.

The emulator does not evaluate IAM. It never checks that the caller presenting a token is the
principal it claims to be, so nothing here shows that the Evidence agent genuinely runs as
``sa-evidence``, or that a Storage role, a Secret Manager binding or a Model Armor permission
is what the matrix says. Those tests stay skipped, and their skip reasons say which of the two
things is missing rather than a blanket "needs a project".
"""

from __future__ import annotations

import pytest
from google.api_core.exceptions import PermissionDenied

from tests.conftest import FIRESTORE_HOST, _reachable, emulator_required
from tests.support.identity import as_identity, identities, matrix, rows, rules_are_loaded

rules_required = pytest.mark.skipif(
    not rules_are_loaded(),
    reason=(
        "the Firestore emulator is running without security rules; run `make rules` then "
        "restart it with `make emulators-stop && make emulators`"
    ),
)

DOC = "iam-probe"
"""The document id every probe writes to, in every collection it is allowed to write.

One id rather than a unique one per probe, so the sweep below is a single delete per collection
rather than a set of ids to track.
"""


def write_as(identity: str, collection: str) -> None:
    as_identity(identity).collection(collection).document(DOC).set({"probe": True})


def read_as(identity: str, collection: str) -> None:
    as_identity(identity).collection(collection).document(DOC).get()


@pytest.fixture(autouse=True, scope="module")
def _sweep_probes():
    """Remove every probe document when the module finishes.

    These tests prove a grant by *performing* the write, so a passing run necessarily leaves a
    row in roughly twenty collections. They looked like nothing until the operator console
    started reading those collections and rendered `tasks/iam-probe` as a signal with no vendor
    and no title — a blank row on a monitoring screen, produced by a test.

    Swept rather than made unique: a probe document with a random id is still a probe document in
    the ledger, and the console would still draw it.
    """
    yield

    if not _reachable(FIRESTORE_HOST):
        return

    from shared.clients import firestore_client

    db = firestore_client()
    for _, collection, may_write in rows():
        if may_write:
            try:
                db.collection(collection).document(DOC).delete()
            except Exception:  # noqa: BLE001 — a sweep that fails must not fail the suite
                pass


# --- Every row in the matrix, both directions ------------------------------------------------


@emulator_required
@rules_required
@pytest.mark.parametrize("identity,collection,may_write", rows(), ids=lambda v: str(v))
def test_every_matrix_row_holds(identity, collection, may_write):
    """The whole matrix as a table test. A grant that is too broad fails as a write that
    succeeded; one that is too narrow fails as a write that did not."""
    if may_write:
        write_as(identity, collection)
        return

    with pytest.raises(PermissionDenied):
        write_as(identity, collection)


# --- The rows the security diagram makes a claim about ----------------------------------------


@emulator_required
@rules_required
def test_questionnaire_agent_cannot_write_a_finding():
    """Collection level, not service level. A row saying 'Firestore' cannot express this."""
    with pytest.raises(PermissionDenied):
        write_as("sa-questionnaire", "findings")


@emulator_required
@rules_required
def test_questionnaire_agent_can_write_qa_responses():
    """The other half of the same row: the agent that parses answers must persist them."""
    write_as("sa-questionnaire", "qa_responses")


@emulator_required
@rules_required
@pytest.mark.parametrize(
    "identity", ["sa-orchestrator", "sa-questionnaire", "sa-evidence", "sa-scorer", "sa-watchdog"]
)
def test_no_agent_can_write_an_approval(identity):
    """The approval service is the only writer. An agent that could author one could approve
    itself, and every other control in the system is downstream of that."""
    with pytest.raises(PermissionDenied):
        write_as(identity, "approvals")


@emulator_required
@rules_required
def test_the_screening_pipeline_cannot_write_a_finding():
    """It records the screening and publishes; the consuming agent writes the finding."""
    with pytest.raises(PermissionDenied):
        write_as("sa-armor", "findings")


@emulator_required
@rules_required
def test_only_the_screening_pipeline_reaches_the_inert_excerpts():
    """A blocked payload is stored inert. An agent that could read it could re-prompt it, which
    is the one thing the whole quarantine boundary exists to prevent."""
    write_as("sa-armor", "inert_excerpts")

    for identity in ("sa-orchestrator", "sa-questionnaire", "sa-evidence", "sa-scorer"):
        with pytest.raises(PermissionDenied):
            read_as(identity, "inert_excerpts")


@emulator_required
@rules_required
def test_the_binder_cannot_write_to_the_ledger():
    """It renders the record; it must not be able to become part of it."""
    for collection in ("findings", "scores", "reviews", "decisions"):
        with pytest.raises(PermissionDenied):
            write_as("sa-binder", collection)


@emulator_required
@rules_required
def test_the_dashboard_cannot_write_a_decision_or_a_score():
    """The read-only claim the dashboard makes about itself, checked at the data layer rather
    than by reading its source for an absent write path."""
    for collection in ("approvals", "scores", "findings", "reviews"):
        with pytest.raises(PermissionDenied):
            write_as("sa-dashboard", collection)


@emulator_required
@rules_required
def test_no_agent_can_add_a_vendor_to_the_approved_register():
    """The unknown-fourth-party finding is a set difference against this register. An identity
    that could write it could make an unknown subprocessor known by writing one document."""
    for identity in (i["name"] for i in identities()):
        with pytest.raises(PermissionDenied):
            write_as(identity, "approved_vendors")


@emulator_required
@rules_required
def test_a_collection_nobody_declares_is_reachable_by_nobody():
    """Deny by default, so adding a collection to the code without adding it to the matrix
    fails loudly rather than inheriting somebody else's access."""
    for identity in ("sa-orchestrator", "sa-evidence", "sa-binder"):
        with pytest.raises(PermissionDenied):
            write_as(identity, "collection_nobody_declared")


# --- The matrix and the code cannot drift apart ------------------------------------------------


def test_every_collection_the_code_names_is_in_the_matrix():
    """The generated rules deny by default, so a collection the matrix does not declare is one
    no identity can reach in a real project — a runtime failure that would first appear after
    deployment. This is that failure, moved to the suite."""
    from tests.support.collections import collections_in_code

    declared = {
        c
        for identity in identities()
        for key in ("read", "write")
        for c in (identity.get("firestore") or {}).get(key) or []
    }
    used = collections_in_code()

    assert used <= declared, (
        f"named in code and absent from the permission matrix: {sorted(used - declared)}"
    )


def test_the_committed_rules_match_the_matrix():
    """One source of truth, two enforcement points. A matrix edit that was never regenerated
    would leave the README table and the deployed ruleset describing different systems."""
    from infra.firestore.generate_rules import RULES_PATH, rules_for
    from shared.config import settings

    on_disk = RULES_PATH.read_text()

    assert on_disk == rules_for(project=settings().emulator_project()), (
        "infra/firestore/firestore.rules is stale; run `make rules`"
    )


def test_every_denied_action_probe_names_a_real_identity():
    """The probe list in the matrix is a published claim about what is tested."""
    names = {identity["name"] for identity in identities()}

    for probe in matrix()["denied_action_probes"]:
        assert probe["identity"] in names, probe


# --- Not provable without a project -------------------------------------------------------------


@pytest.mark.skip(
    reason="the emulator evaluates rules but never checks identity; needs real IAM on a project"
)
def test_the_evidence_agent_runs_as_sa_evidence():
    """Every rules test above presents an identity. That the running agent *is* that identity is
    a binding between a Cloud Run revision and a service account, and nothing local can see it."""
    assert running_identity_of("evidence") == "sa-evidence"


@pytest.mark.skip(reason="no Storage emulator; local mode writes to the filesystem")
def test_evidence_agent_cannot_read_quarantine():
    """No agent holds any role on the quarantine bucket. Only the screening pipeline does."""
    with pytest.raises(PermissionDenied):
        as_agent("evidence").storage_read("evidence-quarantine", "nimbuswrite/security-overview.md")


@pytest.mark.skip(reason="no Secret Manager emulator; needs a provisioned project")
def test_only_the_approval_service_can_read_the_signing_key():
    for identity in ("orchestrator", "questionnaire", "evidence", "scorer", "watchdog", "armor"):
        with pytest.raises(PermissionDenied):
            as_agent(identity).access_secret("drawbridge-approval-key")


@pytest.mark.skip(reason="Vertex AI permissions are project IAM; the emulator holds no roles")
def test_screening_pipeline_cannot_call_a_generative_model():
    """The component handling the most hostile bytes is the one that cannot prompt anything.
    Locally this is asserted by an import graph in tests/test_state_ownership.py instead."""
    with pytest.raises(PermissionDenied):
        as_agent("armor").generate("parse_reply", "anything")


@pytest.mark.skip(reason="gateway egress is enforced in-process; see tests/test_kernel.py for P3")
def test_watchdog_cannot_fetch_outside_the_allowlist():
    with pytest.raises(PolicyViolation):
        as_agent("watchdog").gateway_call("fetch_url", url="https://not-a-feed.example/data")
