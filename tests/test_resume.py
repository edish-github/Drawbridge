"""Kill and resume. Recoverability is structural, so these tests need no recovery code.

A review's plan is a list of named steps checkpointed before execution, so resume is "replay
the list, skipping completed ones". If these pass, the live kill in the demo is safe.
"""

import pytest


@pytest.mark.skip(reason="shared/checkpoint.py is a contract; unskip when it executes")
def test_resume_from_checkpoint_preserves_state():
    s1 = run_until("evidence_review")
    kill()

    s2 = restart_and_finish()

    assert s2.completed_steps[: len(s1.completed_steps)] == s1.completed_steps
    assert s2.state == ReviewState.GATED


@pytest.mark.skip(reason="shared/checkpoint.py is a contract; unskip when it executes")
def test_plan_step_is_not_repeated_and_publishes_once():
    """Killed after the plan step, before its publish. Exactly one plan_ready must exist."""
    run_until("plan", then="SIGKILL")

    restart_worker()
    run_to_completion()

    assert published_count("review.plan_ready", review_id()) == 1
    assert completed_steps(review_id()).count("plan") == 1


@pytest.mark.skip(reason="shared/checkpoint.py is a contract; unskip when it executes")
def test_incomplete_step_reruns_and_its_effects_stay_guarded():
    """A step interrupted mid-execution reruns; the idempotency guard covers its side effects."""
    run_until("questionnaire_send", then="SIGKILL_MID_STEP")

    restart_worker()
    run_to_completion()

    assert "questionnaire_send" in completed_steps(review_id())
    assert inbox_count(vendor="cleancloud") == 1


@pytest.mark.skip(reason="shared/checkpoint.py is a contract; unskip when it executes")
def test_dossier_survives_the_restart():
    s1 = run_until("evidence_review")
    dossier_before = recall_dossier(s1.vendor_id)
    kill()

    restart_worker()

    assert recall_dossier(s1.vendor_id).notes == dossier_before.notes
