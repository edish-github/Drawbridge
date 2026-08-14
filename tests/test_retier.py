"""Evidence-corrected re-tiering: the fleet overrules the intake form.

The intake form is filled in by the party with the strongest incentive to understate scope.
These tests are what make "the fleet trusts it only until evidence arrives" true.
"""

import pytest


@pytest.mark.skip(reason="agents/orchestrator/retier.py is a contract; unskip when it executes")
def test_evidence_retiers_upward_and_does_not_resend():
    r = run_review("datadynamo", intake_scope="internal analytics only")

    reply(r, q="DP03", a="customer records are processed in our EU environment")

    assert r.tier == 1 and r.plan_version == 2
    assert r.tier_history[-1].reason
    assert inbox_count("datadynamo") == 2
    assert never_downward(r.tier_history)


@pytest.mark.skip(reason="agents/orchestrator/retier.py is a contract; unskip when it executes")
def test_tier_never_moves_down():
    """A downward re-tier would let a vendor's own answers reduce the scrutiny applied to them."""
    r = run_review("nimbuswrite")

    reply(r, q="DP03", a="we only process anonymised aggregate counters")

    assert r.tier == 1
    assert r.plan_version == 1
    assert r.tier_history == []


@pytest.mark.skip(reason="agents/orchestrator/retier.py is a contract; unskip when it executes")
def test_tier_change_names_the_answer_that_caused_it():
    """The audit question is 'why did this become a Tier 1?', answered in the vendor's words."""
    r = run_review("datadynamo", intake_scope="internal analytics only")
    reply(r, q="DP03", a="customer records are processed in our EU environment")

    change = r.tier_history[-1]

    assert change.from_tier == 2 and change.to_tier == 1
    assert change.source_ref == "DP03"
    assert "customer records" in change.reason.lower()


@pytest.mark.skip(reason="agents/orchestrator/retier.py is a contract; unskip when it executes")
def test_retier_adds_the_new_domains_questions_only():
    r = run_review("datadynamo", intake_scope="internal analytics only")
    before = set(sent_question_ids(r.review_id))

    reply(r, q="DP03", a="customer records are processed in our EU environment")

    after = set(sent_question_ids(r.review_id))
    assert before.issubset(after)
    assert after - before


@pytest.mark.skip(reason="agents/orchestrator/retier.py is a contract; unskip when it executes")
def test_deterministic_rules_decide_the_tier_not_the_model():
    """The model classifies free text into categories; the rules map categories to a tier."""
    with model_returning_categories(["customer_data"]):
        assert tier_from({"customer_data"}) == 1

    with model_returning_categories([]):
        assert tier_from({"internal_operational"}) == 2


@pytest.mark.skip(reason="agents/orchestrator/retier.py is a contract; unskip when it executes")
def test_classification_failure_leaves_the_tier_unchanged():
    """A re-tier on a bad classification sends a vendor questions they do not owe."""
    r = run_review("datadynamo", intake_scope="internal analytics only")

    with model_call_failing("classify_data_scope"):
        reply(r, q="DP03", a="customer records are processed in our EU environment")

    assert r.tier == 2
    assert degraded_mode_logged(r.review_id, control="retier")


@pytest.mark.skip(reason="agents/orchestrator/retier.py is a contract; unskip when it executes")
def test_retier_that_cannot_record_its_reason_is_aborted():
    """An audit question the binder cannot answer is worse than a stale tier."""
    r = run_review("datadynamo", intake_scope="internal analytics only")

    with tier_history_write_failing():
        reply(r, q="DP03", a="customer records are processed in our EU environment")

    assert r.tier == 2
    assert r.plan_version == 1
