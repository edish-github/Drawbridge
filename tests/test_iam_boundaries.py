"""Least privilege, asserted rather than described.

Every test here passes by a denial. Failing as expected is the feature: these are the
collection-level cases that catch drift, and a permission row that stops being true shows up
here rather than in a demo.
"""

import pytest


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_evidence_agent_cannot_send_email():
    with pytest.raises(PolicyViolation):
        as_agent("evidence").gateway_call("send_email", to="contact@vendor.example")


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_questionnaire_agent_cannot_write_a_finding():
    """Collection level, not service level. A row saying 'Firestore' cannot express this."""
    with pytest.raises(PermissionDenied):
        as_agent("questionnaire").firestore_write("findings", {"domain": "access_control"})


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_questionnaire_agent_can_write_qa_responses():
    """The other half of the same row: the agent that parses answers must persist them."""
    ref = as_agent("questionnaire").firestore_write("qa_responses", {"question_id": "AC01"})

    assert ref is not None


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_evidence_agent_cannot_write_an_approval():
    with pytest.raises(PermissionDenied):
        as_agent("evidence").firestore_write("approvals", {"decision": "approve"})


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_evidence_agent_cannot_read_quarantine():
    """No agent holds any role on the quarantine bucket. Only the screening pipeline does."""
    with pytest.raises(PermissionDenied):
        as_agent("evidence").storage_read("evidence-quarantine", "nimbuswrite/security-overview.md")


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_screening_pipeline_cannot_call_a_generative_model():
    """The component handling the most hostile bytes is the one that cannot prompt anything."""
    with pytest.raises(PermissionDenied):
        as_agent("armor").generate("parse_reply", "anything")


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_screening_pipeline_cannot_write_a_finding():
    """It records the screening and publishes; the consuming agent writes the finding."""
    with pytest.raises(PermissionDenied):
        as_agent("armor").firestore_write("findings", {"domain": "conduct"})


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_scorer_cannot_write_an_approval():
    with pytest.raises(PermissionDenied):
        as_agent("scorer").firestore_write("approvals", {"decision": "approve"})


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_watchdog_cannot_fetch_outside_the_allowlist():
    with pytest.raises(PolicyViolation):
        as_agent("watchdog").gateway_call("fetch_url", url="https://not-a-feed.example/data")


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_only_the_approval_service_can_read_the_signing_key():
    for identity in ("orchestrator", "questionnaire", "evidence", "scorer", "watchdog", "armor"):
        with pytest.raises(PermissionDenied):
            as_agent(identity).access_secret("drawbridge-approval-key")


@pytest.mark.skip(reason="requires a provisioned project; unskip after bootstrap runs")
def test_generated_rules_match_the_permission_matrix():
    """The README table and the rules applied to the project come from one file."""
    matrix = load_matrix("infra/iam/permission-matrix.yaml")
    rules = deployed_firestore_rules()

    for identity in matrix["identities"]:
        for collection in identity["firestore"].get("write", []):
            assert rules.permits_write(identity["name"], collection)
