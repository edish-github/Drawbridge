"""Kill and resume. Recoverability is structural, so these tests need no recovery code.

A review's plan is a list of named steps checkpointed before execution, so resume is "replay
the list, skipping completed ones". If these pass, the live kill in the demo is safe — and they
pass against the emulator before any cloud resource exists.

As in ``test_idempotency``, the crash is a real SIGKILL to a real subprocess.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared.checkpoint import completed_steps, current_step, step, step_result
from tests.conftest import emulator_required
from tests.test_idempotency import inbox_count, run_worker

STEP_ORDER = ["plan", "questionnaire_send", "score"]


def ctx(review_id: str):
    return SimpleNamespace(review_id=review_id, agent="orchestrator", trace_id="t")


@emulator_required
def test_resume_from_checkpoint_preserves_state(review_id):
    run_worker(review_id, kill_at="before_send")
    before = completed_steps(review_id)

    run_worker(review_id)
    after = completed_steps(review_id)

    assert before == ["plan"]
    assert after[: len(before)] == before, "completed steps must never be reordered or lost"
    assert after == STEP_ORDER


@emulator_required
def test_plan_step_is_not_repeated(review_id):
    """Killed after the plan step. On restart the plan is skipped, not recomputed."""
    run_worker(review_id, kill_at="before_send")
    run_worker(review_id)

    assert completed_steps(review_id).count("plan") == 1


@emulator_required
def test_a_completed_step_returns_its_recorded_result_without_executing(review_id):
    calls: list[int] = []

    first = step("plan", ctx(review_id), lambda: (calls.append(1), {"tier": 2})[1])
    second = step("plan", ctx(review_id), lambda: (calls.append(1), {"tier": 99})[1])

    assert first == {"tier": 2}
    assert second == {"tier": 2}, "the recorded result wins; the function is not re-run"
    assert len(calls) == 1


@emulator_required
def test_step_results_survive_the_restart(review_id):
    run_worker(review_id, kill_at="before_send")
    run_worker(review_id)

    assert step_result(review_id, "plan") == {"tier": 1}
    assert step_result(review_id, "score") == {"score": 82}


@emulator_required
def test_an_interrupted_step_is_recorded_as_in_flight(review_id):
    """The dashboard shows what it was doing when it died."""
    run_worker(review_id, kill_at="after_claim")

    assert current_step(review_id) == "questionnaire_send"
    assert "questionnaire_send" not in completed_steps(review_id)


@emulator_required
def test_a_failing_step_stays_incomplete_and_reruns(review_id):
    """A crash between checkpoint and completion re-runs; side effects stay guarded."""
    attempts: list[int] = []

    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return {"ok": True}

    with pytest.raises(RuntimeError):
        step("evidence_review", ctx(review_id), flaky)

    assert "evidence_review" not in completed_steps(review_id)

    assert step("evidence_review", ctx(review_id), flaky) == {"ok": True}
    assert "evidence_review" in completed_steps(review_id)
    assert len(attempts) == 2


@emulator_required
def test_resume_completes_the_review_with_one_email(review_id, db):
    """The whole claim in one test: killed mid-flight, finishes, and contacts once."""
    run_worker(review_id, kill_at="before_send")
    run_worker(review_id)

    assert completed_steps(review_id) == STEP_ORDER
    assert inbox_count(db, review_id) == 1
