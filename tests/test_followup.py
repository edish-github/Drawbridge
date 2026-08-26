"""Chasing and re-asking: the difference between an agent that collects and one that asks again.

Two failure modes, two mechanisms, and the distinction is the point. The chaser handles a
vendor who said *nothing*. The follow-up handles a vendor who said *"we follow industry best
practice"* — an answer that is present, polite and worth nothing, and the one that would
otherwise land on an analyst's desk as manual chasing.

The assertions that matter most are the ones about what does not happen: a cap that terminates,
a re-ask that never stalls a review otherwise complete, and a reminder that is never sent twice
for the same round.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.questionnaire.chaser import (
    MAX_CHASE_ROUNDS,
    chase_round,
    outstanding_questions,
    send_chase,
)
from agents.questionnaire.followup import (
    COLLECTION_FOLLOWUPS,
    compose,
    followups_sent,
    maybe_followup,
    outstanding,
)
from agents.questionnaire.parser import ParsedAnswer
from shared import tenancy
from shared.context import AgentContext
from shared.domain import Review, ReviewState
from shared.events import load_review
from tests.conftest import emulator_required

VENDOR = {
    "vendor_id": "vaguevendor",
    "name": "Vague Systems",
    "category": "Analytics",
    "is_ai_vendor": False,
    "tier": 2,
    "intake": {
        "description": "Reporting.",
        "declared_data_categories": ["internal_operational"],
        "declared_system_access": "none",
    },
    "contact": {"name": "Rowan Alderidge", "email": "security@vaguevendor.example"},
}

PLAN = {
    "tier": 2,
    "plan_version": 1,
    "steps": [{"name": "questionnaire_send", "params": {}}],
    "domains": ["data_protection"],
    "reason": "Internal operational data only.",
}

WEAK = ParsedAnswer(
    question_id="DP01",
    text="We follow industry best practice for encryption both at rest and in transit.",
    confidence=0.31,
    source_msg="m1",
    needs_human=True,
)

STRONG = ParsedAnswer(
    question_id="DP02",
    text="Operational records are retained 24 months; deletion runs nightly including backups.",
    confidence=0.93,
    source_msg="m1",
    needs_human=False,
)


def ctx(review_id: str) -> AgentContext:
    return AgentContext(
        org_id=tenancy.current_org(),
        review_id=review_id,
        agent="questionnaire",
        trace_id="t",
    )


@pytest.fixture
def opened(review_id, db):
    """A review mid-correspondence, with a plan and an authorised contact."""
    db.collection("vendors").document(VENDOR["vendor_id"]).set(VENDOR)
    review = Review(
        review_id=review_id,
        vendor_id=VENDOR["vendor_id"],
        state=ReviewState.REPLIES_IN,
        tier=2,
        opened_at=datetime.now(UTC),
    )
    db.collection("reviews").document(review_id).set(
        {
            **review.model_dump(mode="json"),
            "completed_steps": ["plan", "questionnaire_send"],
            "step_results": {"plan": PLAN},
        }
    )
    # First contact is already authorised on this review, which is what P1 gates. The chase and
    # the follow-up are the same authorised conversation continuing.
    db.collection("approval_tokens_spent").add(
        {
            "review_id": review_id,
            "target": VENDOR["contact"]["email"],
            "spent_at": datetime.now(UTC).isoformat(),
        }
    )
    return review


def inbox(db, review_id: str) -> list[dict]:
    from google.cloud.firestore_v1 import FieldFilter

    # Ordered here rather than by the query: the collection has no index in the emulator, and a
    # test that read an arbitrary "last" message would pass or fail on document id ordering.
    return sorted(
        (
            d.to_dict()
            for d in db.collection("inbox")
            .where(filter=FieldFilter("review_id", "==", review_id))
            .stream()
        ),
        key=lambda m: m["sent_at"],
    )


# --- The re-ask ------------------------------------------------------------------------------


def test_the_re_ask_quotes_the_vendors_own_words_and_names_the_evidence():
    """A follow-up that restated the original question would be the vague letter this replaces."""
    body = compose("DP01", WEAK, vendor="Vague Systems", greeting="Dear Rowan,", round_number=1)

    assert "industry best practice" in body
    assert "key management policy" in body
    assert "DP01" in body


def test_a_question_the_bank_does_not_hold_is_not_re_asked():
    """Nothing specific to ask for means nothing worth sending."""
    assert compose("ZZ99", WEAK, vendor="v", greeting="Hello,", round_number=1) is None


@emulator_required
def test_a_weak_answer_draws_exactly_one_follow_up(opened, db):
    assert maybe_followup(ctx(opened.review_id), opened.review_id, "DP01", WEAK)

    messages = inbox(db, opened.review_id)

    assert len(messages) == 1
    assert "DP01" in messages[0]["subject"]
    assert followups_sent(opened.review_id, "DP01") == 1


@emulator_required
def test_a_good_answer_draws_none(opened, db):
    assert maybe_followup(ctx(opened.review_id), opened.review_id, "DP02", STRONG) is None
    assert inbox(db, opened.review_id) == []


@emulator_required
def test_the_cap_terminates_the_asking(opened, db):
    """Politeness that cannot terminate is a loop. Two asks, then it is recorded as a gap."""
    from shared.config import settings

    cap = settings().followup_cap
    for _ in range(cap + 2):
        db.collection(COLLECTION_FOLLOWUPS).document(f"{opened.review_id}:DP01").set(
            {
                "review_id": opened.review_id,
                "question_id": "DP01",
                "count": followups_sent(opened.review_id, "DP01"),
            }
        )
        maybe_followup(ctx(opened.review_id), opened.review_id, "DP01", WEAK)

    assert followups_sent(opened.review_id, "DP01") <= cap
    assert len(inbox(db, opened.review_id)) <= cap


@emulator_required
def test_the_cap_is_checked_before_anything_is_composed(opened, db, monkeypatch):
    """Checked first, so a retry storm cannot spend sends on a question already dropped."""
    from agents.questionnaire import followup

    db.collection(COLLECTION_FOLLOWUPS).document(f"{opened.review_id}:DP01").set(
        {"review_id": opened.review_id, "question_id": "DP01", "count": 99}
    )
    monkeypatch.setattr(
        followup, "compose", lambda *a, **k: pytest.fail("composed past the cap")
    )

    assert maybe_followup(ctx(opened.review_id), opened.review_id, "DP01", WEAK) is None


@emulator_required
def test_an_outstanding_re_ask_does_not_block_coverage(opened, db):
    """One ambiguous answer must not stall a review that is otherwise complete. The follow-up
    is fire-and-continue by construction: nothing about it is on the coverage path."""
    from agents.questionnaire.parser import coverage

    before = coverage(opened.review_id)
    maybe_followup(ctx(opened.review_id), opened.review_id, "DP01", WEAK)

    assert coverage(opened.review_id) == before
    assert load_review(opened.review_id).state is ReviewState.REPLIES_IN


@emulator_required
def test_the_record_of_what_was_re_asked_reaches_the_binder(opened):
    """An analyst reading the binder needs to see that the fleet asked and the vendor did not
    improve the answer, which is a finding about the vendor rather than about the review."""
    maybe_followup(ctx(opened.review_id), opened.review_id, "DP01", WEAK)

    assert outstanding(opened.review_id) == {"DP01": 1}


# --- The chase -------------------------------------------------------------------------------


@emulator_required
def test_a_chase_names_only_the_questions_with_no_answer_at_all(opened, db):
    """A question answered badly is the follow-up's problem. Chasing a vendor for something
    they already sent is how an automated reminder becomes an ignored one."""
    db.collection("qa_responses").document(f"{opened.review_id}:DP01").set(
        {
            "review_id": opened.review_id,
            "question_id": "DP01",
            "text": WEAK.text,
            "confidence": WEAK.confidence,
            "needs_human": True,
            "source_msg": "m1",
        }
    )

    ids = {q.question_id for q in outstanding_questions(opened.review_id)}

    assert "DP01" not in ids
    assert ids, "the rest of the plan is still outstanding"


@emulator_required
def test_three_rounds_escalate_and_then_stop(opened, db):
    """Reminder, deadline notice, escalation — then a person owns it. A fourth is a loop."""
    for _ in range(MAX_CHASE_ROUNDS):
        send_chase(ctx(opened.review_id), opened.review_id)

    subjects = [m["subject"] for m in inbox(db, opened.review_id)]

    assert chase_round(opened.review_id) == MAX_CHASE_ROUNDS
    assert len(set(subjects)) == MAX_CHASE_ROUNDS, subjects
    assert "escalating" in subjects[-1]

    send_chase(ctx(opened.review_id), opened.review_id)

    assert len(inbox(db, opened.review_id)) == MAX_CHASE_ROUNDS
    assert load_review(opened.review_id).state is ReviewState.NEEDS_HUMAN


@emulator_required
def test_a_round_is_never_sent_twice(opened, db):
    """Each round carries its own idempotency key, so a restart mid-round does not repeat it."""
    key = send_chase(ctx(opened.review_id), opened.review_id)

    from shared.idempotency import once as run_once

    run_once(key, ctx(opened.review_id).for_step(key), lambda: pytest.fail("sent twice"))

    assert len(inbox(db, opened.review_id)) == 1


@emulator_required
def test_a_vendor_who_answered_everything_is_never_chased(opened, db):
    from agents.questionnaire.parser import planned_questions

    for question_id in planned_questions(opened.review_id):
        db.collection("qa_responses").document(f"{opened.review_id}:{question_id}").set(
            {
                "review_id": opened.review_id,
                "question_id": question_id,
                "text": "answered",
                "confidence": 0.9,
                "needs_human": False,
                "source_msg": "m1",
            }
        )

    assert send_chase(ctx(opened.review_id), opened.review_id) is None
    assert inbox(db, opened.review_id) == []
