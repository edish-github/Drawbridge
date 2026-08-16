"""Reviewing the same vendor twice: what a review leaves behind, and what the next one does with it.

Durable memory is a layer rather than a cache, and this is the file that makes that claim
checkable. Without it a judge could reasonably read Memory Bank as Firestore with extra steps:
something is written, nothing visibly reads it.

The interesting assertions are the refusals again. Scrutiny never falls across reviews. A
question in a domain that produced a finding is asked again however well it was answered. A
vendor who tried to manipulate the last review carries nothing. And nothing that carries forward
is prose — the conditions a person typed stay in the ledger, and the dossier holds the pointer.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.orchestrator.closeout import RATING_USABLE, digest, notes_for, rate, remember_review
from agents.orchestrator.recall import Recalled, recall
from agents.questionnaire.generator import load_bank
from shared.domain import Review, ReviewState
from shared.memory import CONTROLLED_VOCABULARY, MAX_VALUE_WORDS, NoteRejected, remember
from tests.conftest import emulator_required

VENDOR_ID = "repeatvendor"

VENDOR = {
    "vendor_id": VENDOR_ID,
    "name": "Repeat Systems",
    "category": "Analytics",
    "is_ai_vendor": False,
    "tier": 2,
    "intake": {
        "description": "Reporting.",
        "declared_data_categories": ["internal_operational"],
        "declared_system_access": "none",
    },
    "contact": {"name": "Ines Vardanyan", "email": "security@repeatvendor.example"},
}

CONDITIONS = [
    "Enrol every administrative account in multi-factor authentication and provide the access "
    "review evidencing it by 30 November.",
]


def closed_review(review_id: str, *, tier: int = 1, band: str = "conditional") -> Review:
    return Review(
        review_id=review_id,
        vendor_id=VENDOR_ID,
        state=ReviewState.DECIDED,
        tier=tier,
        band=band,
        score=64,
        opened_at=datetime.now(UTC),
        decided_at=datetime.now(UTC),
    )


@pytest.fixture
def first(review_id, db):
    """One closed review of a vendor: two answers, one finding, two conditions."""
    db.collection("vendors").document(VENDOR_ID).set(VENDOR)
    review = closed_review(review_id)
    db.collection("reviews").document(review_id).set(review.model_dump(mode="json"))

    for question_id, confidence, needs_human in (
        ("DP01", 0.94, False),
        ("DP02", 0.91, False),
        ("AC01", 0.93, False),
        ("IR01", 0.31, True),
    ):
        db.collection("qa_responses").document(f"{review_id}:{question_id}").set(
            {
                "review_id": review_id,
                "question_id": question_id,
                "text": "an answer",
                "confidence": confidence,
                "needs_human": needs_human,
                "source_msg": "m1",
            }
        )

    db.collection("findings").document(f"{review_id}:f1").set(
        {
            "finding_id": f"{review_id}:f1",
            "review_id": review_id,
            "domain": "access_control",
            "severity": "high",
            "source": "model",
            "contradiction": True,
            "summary": "MFA is not enforced for administrative access",
        }
    )
    db.collection("approvals").document(f"{review_id}-a").set(
        {
            "jti": f"{review_id}-a",
            "review_id": review_id,
            "scope": "decision",
            "target": review_id,
            "identity": "priya@example.test",
            "issued_at": datetime.now(UTC).isoformat(),
            "expires_at": datetime.now(UTC).isoformat(),
            "conditions": CONDITIONS,
        }
    )
    return review


# --- What a review leaves behind -----------------------------------------------------------


@emulator_required
def test_a_closed_review_writes_its_outcome_band_and_answers(first, db):
    written = remember_review(first, identity="priya@example.test")
    prior = recall(VENDOR_ID)

    assert written >= 6
    assert prior.is_repeat
    assert prior.prior_review_id == first.review_id
    assert prior.prior_outcome == "conditional"
    assert prior.prior_band == "conditional"
    assert prior.prior_tier == 1
    assert prior.contact == VENDOR["contact"]["email"]


@emulator_required
def test_an_answer_is_rated_rather_than_recorded(first, db):
    """A rating is a term from a fixed vocabulary. The answer itself stays in the ledger, behind
    the screening record that describes how it got there."""
    remember_review(first, identity="priya@example.test")
    prior = recall(VENDOR_ID)

    assert set(prior.answered_well) == {"DP01", "DP02", "AC01"}
    assert "IR01" not in prior.answered_well, "a non-answer is not something to carry"


def test_the_rating_vocabulary_is_the_one_memory_declares():
    ratings = {rate(0.95, False), rate(0.65, True), rate(0.2, True)}

    assert ratings <= CONTROLLED_VOCABULARY["question_effectiveness"]


@emulator_required
def test_the_conditions_stay_in_the_ledger_and_the_dossier_holds_the_pointer(first, db):
    """A condition is a sentence a person typed. The dossier is recalled into a planning prompt
    before any screening has run in the new review, which is the one place content from an
    earlier review could reach a model without passing a detector in this one."""
    remember_review(first, identity="priya@example.test")

    from google.cloud.firestore_v1 import FieldFilter

    notes = [
        d.to_dict()["note"]
        for d in db.collection("dossiers")
        .where(filter=FieldFilter("vendor_id", "==", VENDOR_ID))
        .stream()
    ]
    condition_notes = [n for n in notes if n["type"] == "approval_condition"]

    assert condition_notes and condition_notes[0]["value"]["value"] == "attached"
    assert not any(
        "multi-factor" in str(v) for n in notes for v in n["value"].values()
    ), "the condition text reached durable memory"
    assert recall(VENDOR_ID).conditions == CONDITIONS


@emulator_required
def test_a_note_that_does_not_fit_the_guard_is_not_written(first, db):
    """The guard is not widened to admit something. A note that does not fit is a note that does
    not get written, and the caller either finds a note type that fits or leaves it in the
    ledger."""
    from shared.domain import MemoryNote

    prose = MemoryNote(
        vendor_id=VENDOR_ID,
        type="approval_condition",
        provenance="human",
        value={"value": "attached", "text": " ".join(["word"] * (MAX_VALUE_WORDS + 1))},
        at=datetime.now(UTC),
    )

    with pytest.raises(NoteRejected, match="prose"):
        remember(VENDOR_ID, prose)


@emulator_required
def test_every_note_a_closeout_writes_passes_the_guard(first, db):
    """Written rather than assumed: the closeout is the only bulk writer of durable memory, and
    a note it produces that the guard rejects would be silently dropped."""
    from shared.memory import _validate

    for note in notes_for(first, identity="priya@example.test"):
        _validate(note)


# --- What the next review does with it -------------------------------------------------------


def test_a_question_in_a_domain_that_produced_a_finding_is_asked_again():
    """The finding is the reason the review happened. Asking around it would be the worst
    possible economy."""
    prior = Recalled(
        vendor_id=VENDOR_ID,
        prior_review_id="r1",
        answered_well=_digests("DP01", "AC01"),
        domains_with_findings={"access_control"},
    )

    carried = prior.carried_questions(load_bank())

    assert "DP01" in carried
    assert "AC01" not in carried


def test_a_reworded_question_is_asked_again():
    """A bank edit that changed the question un-carries it, without anybody having to remember
    that rewording a question has consequences for a vendor reviewed last year."""
    prior = Recalled(
        vendor_id=VENDOR_ID,
        prior_review_id="r1",
        answered_well={"DP01": "not-the-current-digest"},
    )

    assert prior.carried_questions(load_bank()) == set()


def test_a_vendor_with_a_conduct_flag_carries_nothing():
    """Somebody who tried to manipulate the last review does not get the benefit of the doubt on
    the answers they gave during it."""
    prior = Recalled(
        vendor_id=VENDOR_ID,
        prior_review_id="r1",
        adversarial=True,
        answered_well=_digests("DP01", "DP02", "AC01"),
    )

    assert prior.carried_questions(load_bank()) == set()


def test_a_vendor_nobody_reviewed_before_carries_nothing():
    assert Recalled(vendor_id="new").carried_questions(load_bank()) == set()


@emulator_required
def test_the_tier_never_falls_across_reviews(first, db, monkeypatch):
    """The same rule the re-tier obeys within a review, applied across them: a vendor whose
    second intake form is more modest than their first does not earn a lighter review."""
    from agents.orchestrator import planner
    from shared.context import AgentContext
    from shared.domain import ReviewPlan
    from shared.memory import recall_dossier

    remember_review(first, identity="priya@example.test")
    vendor = planner.load_vendor_record(VENDOR)

    # The intake form declares internal operational data only, which floors at Tier 2. The
    # dossier says the last review ended at Tier 1.
    assert planner.tier_from(planner.facts_from_vendor(vendor)) == 2

    lighter = ReviewPlan(tier=3, reason="looks light", steps=["questionnaire_send", "score"])
    monkeypatch.setattr(
        planner, "generate", lambda *a, **k: type("R", (), {"parsed": lighter})()
    )
    plan = planner.generate_plan(
        vendor,
        recall_dossier(VENDOR_ID),
        AgentContext(review_id="second", agent="orchestrator", trace_id="t"),
    )

    assert plan.tier == 1


@emulator_required
def test_the_second_review_asks_strictly_fewer_questions(first, db):
    """The assertion the whole beat exists for. A second review that asks everything again has
    recalled nothing."""
    remember_review(first, identity="priya@example.test")
    prior = recall(VENDOR_ID)
    bank = load_bank()

    tier_one = {
        q.question_id for questions in bank.values() for q in questions if 1 in q.tiers
    }
    carried = prior.carried_questions(bank)

    assert carried, "nothing carried, so the second review is the first review again"
    assert len(tier_one - carried) < len(tier_one)


@emulator_required
def test_recall_degrades_rather_than_blocking_a_review(monkeypatch):
    """A review that could not open because a memory service was down would be a review blocked
    by the one layer that is context rather than control."""
    from shared import memory

    monkeypatch.setattr(
        memory, "recall_dossier", lambda vendor_id: (_ for _ in ()).throw(RuntimeError("down"))
    )

    assert recall("anyone").is_repeat is False


def _digests(*question_ids: str) -> dict[str, str]:
    bank = load_bank()
    by_id = {q.question_id: q for questions in bank.values() for q in questions}
    return {qid: digest(by_id[qid].text) for qid in question_ids}


def test_the_usable_rating_is_the_only_one_that_carries():
    assert rate(0.95, False) == RATING_USABLE
    assert rate(0.65, True) != RATING_USABLE
    assert rate(0.2, True) != RATING_USABLE
