"""Exactly-once side effects across restarts.

The organisers dedicated a webinar to why a resumable agent might order two laptops. These are
the answer, and the first one is the assertion the demo makes on camera.

Every crash here is a real SIGKILL to a real subprocess running the real guard against the
emulator. Nothing is mocked: a mocked crash proves the mock works.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared.idempotency import (
    ReconciliationRequired,
    key_for,
    key_for_review,
    once,
    reconcile,
    record_status,
)
from tests.conftest import emulator_required

REPO = Path(__file__).resolve().parent.parent
WORKER = "tests.support.worker"


def run_worker(review_id: str, kill_at: str = "none", vendor: str = "nimbuswrite"):
    """Run the worker in its own process so a SIGKILL is a real process death."""
    return subprocess.run(
        [sys.executable, "-m", WORKER, "--review-id", review_id,
         "--kill-at", kill_at, "--vendor", vendor],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def inbox_count(db, review_id: str) -> int:
    from google.cloud.firestore_v1 import FieldFilter

    query = db.collection("inbox").where(filter=FieldFilter("review_id", "==", review_id))
    return len(list(query.stream()))


def ctx(review_id: str = "r", agent: str = "test"):
    return SimpleNamespace(review_id=review_id, agent=agent, trace_id="t")


# --- Key derivation, pure ------------------------------------------------------------------


def test_key_is_derived_from_workflow_position():
    assert key_for("r1", 1, "questionnaire_send:v1") == "r1:plan_v1:questionnaire_send:v1"


def test_key_includes_plan_version():
    """A key derived from the step name alone is correct only while the plan is immutable."""
    assert key_for("r1", 2, "questionnaire_send:v1") == "r1:plan_v2:questionnaire_send:v1"
    assert key_for("r1", 1, "s") != key_for("r1", 2, "s")


def test_key_is_reproducible():
    """If the key is not identical across calls, the guard is worthless after a restart."""
    assert key_for("r1", 1, "chase:round2") == key_for("r1", 1, "chase:round2")


def test_carried_step_inherits_its_previous_key():
    """A step carried unchanged into a new plan must still be recognised as done."""
    review = SimpleNamespace(review_id="r1", plan_version=2)
    inherited = {"questionnaire_send:v1": "r1:plan_v1:questionnaire_send:v1"}

    assert key_for_review(review, "questionnaire_send:v1", inherited=inherited) == (
        "r1:plan_v1:questionnaire_send:v1"
    )
    # A genuinely new step in plan v2 gets a v2 key and cannot collide with completed work.
    assert key_for_review(review, "followup:q14:v1", inherited=inherited) == (
        "r1:plan_v2:followup:q14:v1"
    )


# --- The guard, against the emulator -------------------------------------------------------


@emulator_required
def test_replay_returns_the_recorded_result_without_re_executing(review_id):
    calls: list[int] = []
    key = key_for(review_id, 1, "score_write:v1")

    first = once(key, ctx(review_id), lambda: (calls.append(1), "written")[1])
    second = once(key, ctx(review_id), lambda: (calls.append(1), "written")[1])

    assert first == "written"
    assert second == "written"
    assert len(calls) == 1
    assert record_status(key)["status"] == "done"


@emulator_required
def test_email_sent_exactly_once_across_restart(review_id, db):
    """The headline claim: kill mid-review, restart, and the vendor is emailed once."""
    crashed = run_worker(review_id, kill_at="before_send")
    assert crashed.returncode == -9, f"expected SIGKILL, got {crashed.returncode}"
    assert inbox_count(db, review_id) == 0

    finished = run_worker(review_id)
    assert finished.returncode == 0, finished.stderr

    assert inbox_count(db, review_id) == 1
    assert record_status(key_for(review_id, 1, "questionnaire_send:v1"))["status"] == "done"


@emulator_required
def test_a_second_full_run_sends_nothing_more(review_id, db):
    """Redelivery of the whole workflow is Tuesday, not an error condition."""
    assert run_worker(review_id).returncode == 0
    assert run_worker(review_id).returncode == 0
    assert run_worker(review_id).returncode == 0

    assert inbox_count(db, review_id) == 1


@emulator_required
def test_crash_between_claim_and_effect_is_not_silently_rerun(review_id, db):
    """The conservative choice: an interrupted email is confirmed by a human, never resent."""
    crashed = run_worker(review_id, kill_at="after_claim")
    assert crashed.returncode == -9

    assert record_status(key_for(review_id, 1, "questionnaire_send:v1"))["status"] == "in_progress"
    assert inbox_count(db, review_id) == 0

    resumed = run_worker(review_id)

    assert resumed.returncode == 3, "the restart must refuse to re-run the claimed step"
    assert "RECONCILE" in resumed.stdout
    assert inbox_count(db, review_id) == 0, "nothing may be sent without human confirmation"


@emulator_required
def test_crash_after_the_effect_does_not_resend(review_id, db):
    """The effect happened and the record says otherwise. Resending is the failure to avoid."""
    crashed = run_worker(review_id, kill_at="after_send")
    assert crashed.returncode == -9
    assert inbox_count(db, review_id) == 1

    resumed = run_worker(review_id)

    assert resumed.returncode == 3
    assert inbox_count(db, review_id) == 1, "the vendor must not be emailed twice"


@emulator_required
def test_stale_claims_are_surfaced_for_reconciliation(review_id):
    run_worker(review_id, kill_at="after_claim")

    with pytest.raises(ReconciliationRequired) as exc:
        once(key_for(review_id, 1, "questionnaire_send:v1"), ctx(review_id), lambda: "resent")

    assert "will not be re-run automatically" in str(exc.value)


@emulator_required
def test_reconcile_lists_only_incomplete_keys(review_id):
    """A resume path passes 0: after a crash there is no other worker to wait for."""
    once(key_for(review_id, 1, "done_step:v1"), ctx(review_id), lambda: "ok")
    run_worker(review_id, kill_at="after_claim")

    stale = reconcile(review_id, older_than_seconds=0)

    assert key_for(review_id, 1, "questionnaire_send:v1") in stale
    assert key_for(review_id, 1, "done_step:v1") not in stale


@emulator_required
def test_reconcile_waits_out_the_default_window_for_a_fresh_claim(review_id):
    """The default assumes a fresh claim may belong to a worker that is still running."""
    run_worker(review_id, kill_at="after_claim")

    assert reconcile(review_id) == []
    assert reconcile(review_id, older_than_seconds=0) != []


# --- Confirmation: the other half of "flag for confirmation" ---------------------------------


@emulator_required
def test_a_claim_records_what_the_step_was_about_to_do(review_id):
    """An operator reconciling a crashed step is looking at a marker. "About to email these
    questions to this address" is the difference between a decision and a guess."""
    key = key_for(review_id, 1, "questionnaire_send:v1")

    with pytest.raises(RuntimeError):
        once(key, ctx(review_id), _fail, claim={"to": "v@example.test", "questions": ["DP01"]})

    assert record_status(key)["claim"] == {"to": "v@example.test", "questions": ["DP01"]}


@emulator_required
def test_a_confirmed_claim_is_skipped_rather_than_re_run(review_id, db):
    """The path out of reconciliation. A person establishes the email went out, and the next
    replay skips the effect instead of repeating it."""
    from shared.idempotency import confirm

    run_worker(review_id, kill_at="after_claim")
    key = key_for(review_id, 1, "questionnaire_send:v1")

    confirm(key, confirmed_by="priya@example.test")
    once(key, ctx(review_id), lambda: pytest.fail("the confirmed effect was repeated"))

    assert record_status(key)["confirmed_by"] == "priya@example.test"


@emulator_required
def test_confirmation_hands_back_what_the_claim_recorded(review_id):
    """A step whose process died before recording its result is still replayable, because the
    claim said what it was about to do and confirmation promotes it."""
    from shared.idempotency import confirm

    key = key_for(review_id, 1, "questionnaire_send:v1")
    with pytest.raises(RuntimeError):
        once(key, ctx(review_id), _fail, claim={"questions": ["DP01", "AC01"]})

    confirm(key, confirmed_by="priya@example.test")

    assert once(key, ctx(review_id), lambda: None) == {"questions": ["DP01", "AC01"]}


@emulator_required
def test_an_effect_nobody_claimed_cannot_be_confirmed(review_id):
    """A done marker for work nobody attempted would silently skip real work later."""
    from shared.idempotency import confirm

    with pytest.raises(ReconciliationRequired):
        confirm(key_for(review_id, 1, "never_claimed:v1"), confirmed_by="priya@example.test")


def _fail():
    raise RuntimeError("the worker died here")


@emulator_required
def test_a_result_that_cannot_be_recorded_raises(review_id):
    """Recording a key as done without its result would make the next replay skip lost work."""
    with pytest.raises(TypeError, match="not\n?\\s*serialisable|serialisable"):
        once(key_for(review_id, 1, "bad:v1"), ctx(review_id), lambda: object())
