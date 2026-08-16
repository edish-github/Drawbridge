"""Intake to first contact: the gate refuses, a human releases it, the vendor is emailed once.

This is the first half of the demo as a test. The three assertions that matter are the ones a
judge watches happen: the gateway refuses an unapproved send, the review parks at the contact
gate rather than proceeding, and the whole flow replayed afterwards still produces exactly one
email.

The planning model call is stubbed. Everything else is real — real emulator, real gateway, real
idempotency guard, real approval lookup — because the point of the test is the control flow
between them, and a live model call would make the run slow, costly and non-deterministic
without testing anything the routing layer does not already cover.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shared.clients import firestore_client
from shared.context import AgentContext
from shared.domain import Review, ReviewPlan, ReviewState
from shared.events import EventEnvelope, load_review
from shared.gateway import PolicyViolation
from shared.routing import ModelResult
from tests.conftest import emulator_required
from tests.test_idempotency import inbox_count

VENDOR = {
    "vendor_id": "testvendor",
    "name": "TestVendor AI",
    "category": "AI writing assistance",
    "is_ai_vendor": True,
    "tier": 1,
    "intake": {
        "description": "Drafts replies from customer support transcripts.",
        "declared_data_categories": ["customer_content"],
        "declared_system_access": "none",
    },
    "contact": {"email": "trust@testvendor.example"},
}

PLANNED = ReviewPlan(
    tier=1,
    reason="An AI service processing customer text is Tier 1 from the intake form onward.",
    steps=["questionnaire_send", "evidence_extract", "cross_examine", "score", "memo"],
)


@pytest.fixture
def stub_planning(monkeypatch):
    """Return the planning output without a model call. Nothing else is stubbed."""
    def fake_generate(task, prompt, ctx, **kwargs):
        assert task == "plan_review", f"unexpected model task in this flow: {task}"
        return ModelResult(
            text=PLANNED.model_dump_json(),
            model="stub",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            parsed=PLANNED,
        )

    monkeypatch.setattr("agents.orchestrator.planner.generate", fake_generate)


@pytest.fixture
def opened(review_id, stub_planning):
    """A vendor and a review at INTAKE, written the way the portal writes them."""
    db = firestore_client()
    db.collection("vendors").document(VENDOR["vendor_id"]).set(VENDOR)

    review = Review(
        review_id=review_id,
        vendor_id=VENDOR["vendor_id"],
        state=ReviewState.INTAKE,
        tier=1,
        opened_at=datetime.now(UTC),
    )
    db.collection("reviews").document(review_id).set(review.model_dump(mode="json"))
    return review


def envelope(review_id: str, event_type: str, payload: dict | None = None) -> EventEnvelope:
    return EventEnvelope(
        type=event_type,
        review_id=review_id,
        idem_key=f"{review_id}:plan_v1:{event_type}",
        trace_id="t",
        source="test",
        payload=payload or {},
    )


def run_intake(review: Review) -> None:
    from agents.orchestrator.agent import handle_event

    handle_event(envelope(review.review_id, "review.intake"), review)


def run_plan_ready(review_id: str) -> None:
    from agents.questionnaire.agent import handle_event

    review = load_review(review_id)
    handle_event(envelope(review_id, "review.plan_ready"), review)


def approve_contact(review_id: str) -> str:
    """Approve first contact and let the Orchestrator release the gate.

    Two steps because they are two responsibilities: the approvals path records the decision and
    publishes, and the Orchestrator performs the transition. A test that wrote the state itself
    would pass while the release path was broken.
    """
    from agents.orchestrator.agent import handle_event
    from scripts.issue_token import issue

    token = issue(review_id, scope="contact", identity="test-operator", ttl_minutes=10)

    review = load_review(review_id)
    handle_event(
        envelope(review_id, "review.approved", {"scope": "contact", "identity": "test-operator"}),
        review,
    )
    return token


# --- Intake ----------------------------------------------------------------------------------


@emulator_required
def test_intake_tiers_the_review_and_states_why(opened):
    run_intake(opened)

    from shared.checkpoint import step_result

    plan = step_result(opened.review_id, "plan")
    review = load_review(opened.review_id)

    assert plan["tier"] == 1
    assert plan["reason"]
    assert review.tier == 1
    assert review.state is ReviewState.QUESTIONNAIRE_OUT


@emulator_required
def test_the_plan_is_checkpointed_at_version_one(opened):
    """The plan version is in every idempotency key from the first plan, not from the first
    re-tier. Adding it later would be a migration."""
    run_intake(opened)

    from shared.checkpoint import completed_steps, step_result

    assert "plan" in completed_steps(opened.review_id)
    assert step_result(opened.review_id, "plan")["plan_version"] == 1
    assert load_review(opened.review_id).plan_version == 1


@emulator_required
def test_the_plan_names_only_steps_the_fleet_can_execute(opened):
    from agents.orchestrator.planner import STEP_VOCABULARY
    from shared.checkpoint import step_result

    run_intake(opened)

    for step in step_result(opened.review_id, "plan")["steps"]:
        assert step["name"] in STEP_VOCABULARY


@emulator_required
def test_the_tier_floor_is_not_the_models_to_lower(opened, monkeypatch):
    """A persuasive intake description must not reduce the scrutiny applied to the vendor."""
    relaxed = PLANNED.model_copy(update={"tier": 3, "reason": "looks harmless"})
    monkeypatch.setattr(
        "agents.orchestrator.planner.generate",
        lambda *a, **k: ModelResult(
            text="", model="stub", prompt_tokens=0, completion_tokens=0,
            cost_usd=0.0, parsed=relaxed,
        ),
    )

    run_intake(opened)

    assert load_review(opened.review_id).tier == 1


# --- The refusal -------------------------------------------------------------------------------


@emulator_required
def test_the_gateway_refuses_first_contact_without_an_approval(opened, db):
    """The 1:05 demo beat. P1 is the machine's inability to skip the human gate."""
    run_intake(opened)
    run_plan_ready(opened.review_id)

    review = load_review(opened.review_id)

    assert review.state is ReviewState.GATED
    assert review.gate_scope == "contact"
    assert inbox_count(db, opened.review_id) == 0


@emulator_required
def test_the_refusal_is_logged_as_a_named_policy(opened, db):
    """"P1 REJECTED" reads as a product; "blocked" reads as a mock."""
    run_intake(opened)
    run_plan_ready(opened.review_id)

    from google.cloud.firestore_v1 import FieldFilter

    blocks = [
        d.to_dict()
        for d in db.collection("dashboard_events")
        .where(filter=FieldFilter("review_id", "==", opened.review_id))
        .stream()
    ]

    # Two cards land for one refusal and they are different things: the policy block naming
    # what refused, and the gate card an operator acts on. Both belong on the dashboard.
    policy_blocks = [b for b in blocks if b.get("kind") == "policy_block"]
    gate_cards = [b for b in blocks if b.get("kind") == "gate"]

    assert policy_blocks, "a policy refusal must leave a dashboard event"
    assert policy_blocks[0]["policy"] == "P1"
    assert policy_blocks[0]["line"].startswith("P1 REJECTED")
    assert gate_cards and gate_cards[0]["gate_scope"] == "contact"


@emulator_required
def test_the_refusal_releases_its_idempotency_claim(opened):
    """A refusal is not a crash: the effect provably did not happen, so the step stays runnable.

    A claim left behind here would meet the gate release as an unreconciled step and refuse to
    send at all — the gate would be releasable in the state machine and stuck in the guard.
    """
    from shared.idempotency import key_for, record_status

    run_intake(opened)
    run_plan_ready(opened.review_id)

    assert record_status(key_for(opened.review_id, 1, "questionnaire_send:v1")) is None


@emulator_required
def test_a_refused_send_raises_rather_than_returning_quietly(opened):
    from shared.gateway import call_tool

    run_intake(opened)
    ctx = AgentContext(review_id=opened.review_id, agent="questionnaire", trace_id="t")

    with pytest.raises(PolicyViolation) as exc:
        call_tool(
            "send_email",
            ctx,
            to="trust@testvendor.example",
            subject="s",
            body="b",
            review_id=opened.review_id,
        )

    assert exc.value.policy == "P1"


# --- The release ---------------------------------------------------------------------------------


@emulator_required
def test_approval_releases_the_gate_and_the_vendor_is_emailed_once(opened, db):
    """The whole first half of J1 in one test."""
    run_intake(opened)
    run_plan_ready(opened.review_id)
    assert inbox_count(db, opened.review_id) == 0

    approve_contact(opened.review_id)
    assert load_review(opened.review_id).state is ReviewState.QUESTIONNAIRE_OUT

    run_plan_ready(opened.review_id)

    assert inbox_count(db, opened.review_id) == 1


@emulator_required
def test_replaying_the_whole_flow_sends_nothing_more(opened, db):
    """Redelivery is Tuesday. Three more deliveries must not produce three more emails."""
    run_intake(opened)
    run_plan_ready(opened.review_id)
    approve_contact(opened.review_id)
    run_plan_ready(opened.review_id)

    for _ in range(3):
        run_plan_ready(opened.review_id)

    assert inbox_count(db, opened.review_id) == 1


@emulator_required
def test_the_delivered_questionnaire_is_tier_appropriate_and_evidence_demanding(opened, db):
    from google.cloud.firestore_v1 import FieldFilter

    run_intake(opened)
    run_plan_ready(opened.review_id)
    approve_contact(opened.review_id)
    run_plan_ready(opened.review_id)

    sent = next(
        d.to_dict()
        for d in db.collection("inbox")
        .where(filter=FieldFilter("review_id", "==", opened.review_id))
        .stream()
    )

    assert sent["to"] == "trust@testvendor.example"
    assert "Tier 1" in sent["subject"]
    # An AI vendor gets the AI-specific domain, and every question names its evidence.
    assert "AI SPECIFIC" in sent["body"]
    assert "Evidence required:" in sent["body"]


@emulator_required
def test_a_gate_that_is_not_open_cannot_be_released(opened):
    """Releasing a gate a review is not parked at is how an approval is spent on the wrong
    decision."""
    from scripts.issue_token import GateNotOpen

    run_intake(opened)

    with pytest.raises(GateNotOpen):
        approve_contact(opened.review_id)
