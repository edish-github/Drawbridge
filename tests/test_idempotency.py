"""Exactly-once side effects across restarts.

The organisers dedicated a webinar to why a resumable agent might order two laptops. These are
the answer, and the first one is the assertion the demo makes on camera.
"""

import pytest


@pytest.mark.skip(reason="shared/idempotency.py is a contract; unskip when it executes")
def test_email_sent_exactly_once_across_restart():
    run_until("questionnaire_send", then="SIGKILL")
    restart_worker()
    run_to_completion()

    assert inbox_count(vendor="nimbuswrite") == 1
    assert idem_record("nimbuswrite:plan_v1:questionnaire_send:v1")["status"] == "done"


@pytest.mark.skip(reason="shared/idempotency.py is a contract; unskip when it executes")
def test_key_includes_plan_version():
    """A key derived from the step name alone is correct only while the plan is immutable."""
    review = review_at_plan_version(2)

    key = key_for(review, "questionnaire_send:v2")

    assert key == f"{review.review_id}:plan_v2:questionnaire_send:v2"


@pytest.mark.skip(reason="shared/idempotency.py is a contract; unskip when it executes")
def test_carried_step_inherits_its_previous_key():
    """A step carried unchanged into a new plan must still be recognised as done."""
    review = review_at_plan_version(1)
    once(key_for(review, "questionnaire_send:v1"), ctx(), send_questionnaire)

    retiered = replan(review, new_tier=1)

    expected = f"{review.review_id}:plan_v1:questionnaire_send:v1"
    assert key_for(retiered, "questionnaire_send:v1") == expected
    assert inbox_count(vendor="datadynamo") == 1


@pytest.mark.skip(reason="shared/idempotency.py is a contract; unskip when it executes")
def test_crash_between_claim_and_effect_is_not_silently_rerun():
    """The conservative choice: an interrupted email is confirmed by a human, never resent."""
    key = "datadynamo:plan_v1:questionnaire_send:v1"
    claim_without_completing(key)

    with pytest.raises(ReconciliationRequired):
        once(key, ctx(), send_questionnaire)

    assert key in reconcile("datadynamo")
    assert inbox_count(vendor="datadynamo") == 0


@pytest.mark.skip(reason="shared/idempotency.py is a contract; unskip when it executes")
def test_replay_returns_the_recorded_result_without_re_executing():
    calls = []
    key = "cleancloud:plan_v1:score_write:v1"

    once(key, ctx(), lambda: calls.append(1) or "written")
    second = once(key, ctx(), lambda: calls.append(1) or "written")

    assert second == "written"
    assert len(calls) == 1
