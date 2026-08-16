"""Evidence-corrected re-tiering: the fleet overrules the intake form.

The intake form is filled in by the party with the strongest incentive to understate scope.
These tests are what make "the fleet trusts it only until evidence arrives" true.

The two that matter most are the ones about what does *not* happen: a tier that never moves
down, and a vendor who is never asked the same question twice across a re-plan. The first is an
attack surface and the second is the difference between a re-tier and a mess.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.orchestrator.planner import tier_from
from agents.orchestrator.retier import (
    ScopeClassification,
    TierChangeNotRecorded,
    all_classifications,
    facts_from_classifications,
    reassess_tier,
    record_tier_change,
    replan,
)
from shared.checkpoint import step_result
from shared.context import AgentContext
from shared.domain import Review, ReviewState, TierChange
from shared.events import load_review
from tests.conftest import emulator_required

VENDOR = {
    "vendor_id": "retiervendor",
    "name": "Retier Logistics",
    "category": "Freight routing",
    "is_ai_vendor": False,
    "tier": 2,
    "intake": {
        "description": "Internal analytics only.",
        "declared_data_categories": ["internal_operational"],
        "declared_system_access": "none",
    },
    "contact": {"email": "compliance@retiervendor.example"},
}

PLAN_V1 = {
    "tier": 2,
    "plan_version": 1,
    "steps": [{"name": "questionnaire_send", "params": {}}, {"name": "score", "params": {}}],
    "domains": ["data_protection", "access_control"],
    "reason": "Internal operational data only.",
}


def ctx(review_id: str) -> AgentContext:
    return AgentContext(review_id=review_id, agent="orchestrator", trace_id="t")


@pytest.fixture
def opened(review_id, db):
    """A Tier 2 review at REPLIES_IN with a checkpointed plan v1."""
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
            "step_results": {"plan": PLAN_V1},
        }
    )
    return review


def answer(db, review_id: str, question_id: str, text: str, msg: str = "m1") -> None:
    db.collection("qa_responses").document(f"{review_id}:{question_id}").set(
        {
            "review_id": review_id,
            "question_id": question_id,
            "text": text,
            "confidence": 0.9,
            "needs_human": False,
            "source_msg": msg,
        }
    )


@pytest.fixture
def classifying(monkeypatch):
    """Replace the classification call with a fixed answer, keeping the rules real."""

    def install(*classifications: ScopeClassification):
        from agents.orchestrator import retier

        monkeypatch.setattr(retier, "classify", lambda *a, **k: list(classifications))

    return install


# --- The rules decide, not the model -------------------------------------------------------


def test_the_model_classifies_and_the_rules_decide():
    """The model maps prose onto a category; a written policy maps categories onto a tier."""
    customer = ScopeClassification(question_id="DP03", categories=["customer_pii"])
    internal = ScopeClassification(question_id="DP03", categories=["internal_operational"])

    assert tier_from(facts_from_classifications([customer])) == 1
    assert tier_from(facts_from_classifications([internal])) == 2


def test_a_category_nobody_wrote_a_rule_for_moves_the_tier_nowhere():
    """Anonymised counters are not customer data, however the answer is phrased."""
    aggregate = ScopeClassification(question_id="DP03", categories=["aggregate_anonymised"])

    assert facts_from_classifications([aggregate]) == set()
    assert tier_from(facts_from_classifications([aggregate])) == 3


def test_declared_system_access_raises_the_tier_on_its_own():
    read_only = ScopeClassification(question_id="AC04", categories=[], system_access="read_only")

    assert tier_from(facts_from_classifications([read_only])) == 1


# --- Upward, and only upward ----------------------------------------------------------------


@emulator_required
def test_evidence_retiers_upward_and_records_the_new_plan(opened, db, classifying):
    classifying(
        ScopeClassification(
            question_id="DP03",
            categories=["customer_pii"],
            quote="Customer records are processed in our EU environment",
        )
    )
    answer(db, opened.review_id, "DP03", "customer records are processed in our EU environment")

    result = reassess_tier(ctx(opened.review_id), opened)

    assert result.tier == 1
    assert result.plan_version == 2
    assert load_review(opened.review_id).tier == 1
    assert step_result(opened.review_id, "plan")["tier"] == 1


@emulator_required
def test_tier_never_moves_down(opened, db, classifying):
    """A downward re-tier would let a vendor's own answers reduce the scrutiny applied to them."""
    classifying(
        ScopeClassification(question_id="DP03", categories=["aggregate_anonymised"], quote="x")
    )
    answer(db, opened.review_id, "DP03", "we only process anonymised aggregate counters")

    result = reassess_tier(ctx(opened.review_id), opened)

    assert result.tier == 2
    assert result.plan_version == 1
    assert result.tier_history == []


@emulator_required
def test_replan_refuses_a_downward_move_outright(opened):
    with pytest.raises(ValueError, match="not upward"):
        replan(opened, 3)


def test_a_tier_change_that_is_not_upward_is_not_recordable(review_id):
    change = TierChange(
        from_tier=2, to_tier=3, reason="r", source_ref="DP03", at=datetime.now(UTC)
    )
    review = Review(review_id=review_id, vendor_id="v", opened_at=datetime.now(UTC))

    with pytest.raises(ValueError, match="not upward"):
        record_tier_change(review, change)


# --- The audit record -------------------------------------------------------------------------


@emulator_required
def test_the_tier_change_names_the_answer_that_caused_it(opened, db, classifying):
    """The audit question is "why did this become a Tier 1?", answered in the vendor's words."""
    classifying(
        ScopeClassification(
            question_id="DP03",
            categories=["customer_pii"],
            quote="Customer records are processed in our EU environment",
        )
    )
    answer(db, opened.review_id, "DP03", "customer records are processed in our EU environment")

    change = reassess_tier(ctx(opened.review_id), opened).tier_history[-1]

    assert change.from_tier == 2 and change.to_tier == 1
    assert change.source_ref == "DP03"
    assert "customer records" in change.reason.lower()


@emulator_required
def test_the_tier_change_reaches_the_dashboard(opened, db, classifying):
    from google.cloud.firestore_v1 import FieldFilter

    classifying(ScopeClassification(question_id="DP03", categories=["customer_pii"], quote="q"))
    answer(db, opened.review_id, "DP03", "customer records in the EU")

    reassess_tier(ctx(opened.review_id), opened)

    cards = [
        d.to_dict()
        for d in db.collection("dashboard_events")
        .where(filter=FieldFilter("review_id", "==", opened.review_id))
        .stream()
    ]
    tier_cards = [c for c in cards if c.get("kind") == "tier_change"]

    assert tier_cards and tier_cards[0]["to_tier"] == 1


@emulator_required
def test_a_retier_that_cannot_record_its_reason_is_aborted(opened, db, classifying, monkeypatch):
    """An audit question the binder cannot answer is worse than a stale tier."""
    from agents.orchestrator import retier

    classifying(ScopeClassification(question_id="DP03", categories=["customer_pii"], quote="q"))
    answer(db, opened.review_id, "DP03", "customer records in the EU")

    def refuse(*_args, **_kwargs):
        raise TierChangeNotRecorded("the ledger is unavailable")

    monkeypatch.setattr(retier, "record_tier_change", refuse)

    with pytest.raises(TierChangeNotRecorded):
        reassess_tier(ctx(opened.review_id), opened)

    assert load_review(opened.review_id).tier == 2
    assert step_result(opened.review_id, "plan")["plan_version"] == 1


# --- Classification failure -------------------------------------------------------------------


@emulator_required
def test_a_classification_failure_leaves_the_tier_unchanged(opened, db, monkeypatch):
    """A re-tier on a bad classification sends a vendor thirty questions they do not owe."""
    from agents.orchestrator import retier

    answer(db, opened.review_id, "DP03", "customer records are processed in our EU environment")
    monkeypatch.setattr(retier, "classify", lambda *a, **k: None)

    assert reassess_tier(ctx(opened.review_id), opened).tier == 2


@emulator_required
def test_an_answer_is_never_classified_twice(opened, db, classifying):
    """A redelivered reply batch must not spend a model call per delivery."""
    classifying(ScopeClassification(question_id="DP03", categories=["internal_operational"]))
    answer(db, opened.review_id, "DP03", "internal shipment volumes only")

    reassess_tier(ctx(opened.review_id), opened)
    before = len(all_classifications(opened.review_id))

    reassess_tier(ctx(opened.review_id), opened)

    assert len(all_classifications(opened.review_id)) == before


@emulator_required
def test_the_tier_is_recomputed_against_every_batch_not_the_newest(opened, db, classifying):
    """A review that reached Tier 1 on day 6 must not fall back when day 9's answers are dull."""
    classifying(ScopeClassification(question_id="DP03", categories=["customer_pii"], quote="q"))
    answer(db, opened.review_id, "DP03", "customer records in the EU")
    retiered = reassess_tier(ctx(opened.review_id), opened)

    classifying(ScopeClassification(question_id="BC01", categories=["none_stated"]))
    answer(db, opened.review_id, "BC01", "RTO is 8 hours", msg="m2")

    assert reassess_tier(ctx(opened.review_id), retiered).tier == 1


# --- Carried work is not repeated --------------------------------------------------------------


@emulator_required
def test_carried_steps_inherit_their_old_idempotency_keys(opened):
    """Work completed under plan v1 stays done under plan v2, so nothing is emailed twice."""
    plan = replan(opened, 1)

    assert plan.inherited_keys["questionnaire_send:v1"] == (
        f"{opened.review_id}:plan_v1:questionnaire_send:v1"
    )
    assert "subprocessor_extract:v1" not in plan.inherited_keys, (
        "a step that is new at Tier 1 must not inherit a key from a plan that never ran it"
    )


@emulator_required
def test_the_replan_widens_the_domain_set(opened):
    plan = replan(opened, 1)

    assert set(plan.domains) > {"data_protection", "access_control"}
    assert "ai_specific" not in plan.domains, "the vendor is not an AI service"


@emulator_required
def test_the_new_plan_keeps_its_version_and_replaces_the_old_one(opened):
    replan(opened, 1)

    recorded = step_result(opened.review_id, "plan")

    assert recorded["plan_version"] == 2
    assert recorded["tier"] == 1
