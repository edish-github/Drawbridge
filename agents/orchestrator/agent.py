"""Orchestrator — owns the review's state and the plan it executes.

Mission
    Turn an intake request into a tiered review plan, dispatch its steps, re-evaluate the
    tier as evidence arrives, enforce the human gates, and own the review's state. It is the
    only agent that writes review state.

Trigger
    ``review.intake``, plus the follow-ons it coordinates: ``review.plan_ready``,
    ``vendor.reply_received`` batches, ``review.findings_ready``, ``review.score_ready``,
    ``review.approved`` and ``watchdog.hit``.

Tools
    Firestore review-state read/write, durable memory recall at intake, and dispatch through
    the gateway. No email, no Storage, no ``approvals`` write.

Model
    The fast model, for planning and for classifying free-text answers into data-scope
    categories. No deep model anywhere in this agent: planning is structured selection over
    a tiering policy we wrote, and the deep model is spent in exactly two places, neither of
    them here.

Failure behaviour
    Malformed intake parks the review in ``NEEDS_HUMAN`` rather than guessing a tier. A
    model call failure on planning retries under the plan step's checkpoint and, past the
    dead-letter threshold, parks with a dashboard card naming what stalled. A dependency
    being unavailable never causes a partial plan to be written: the plan step is atomic
    under ``shared.checkpoint``. The Orchestrator never invents a missing answer and never
    approves anything.
"""

from __future__ import annotations

import logging

from google.adk import Agent

from shared.checkpoint import step
from shared.clients import firestore_client
from shared.config import settings
from shared.context import context_for
from shared.domain import Review, ReviewPlan, ReviewState, validate_transition
from shared.events import TOPIC_REVIEW_INTAKE, TOPIC_REVIEW_PLAN_READY, EventEnvelope, publish
from shared.memory import recall_dossier
from shared.routing import park
from shared.telemetry import record_decision, span

log = logging.getLogger("drawbridge.orchestrator")

SERVICE_ACCOUNT = "sa-orchestrator"

STEP_PLAN = "plan"
"""The checkpoint name the plan is recorded under. Read by the Questionnaire agent, so it is a
constant rather than a string literal in two files.
"""

TOOLS: list = []
"""Registered through ``shared.gateway``. Empty until the tools exist; an agent with no
registered tools can take no action, which is the correct posture for a stub.
"""

agent = Agent(
    name="orchestrator",
    model=settings().model_fast,
    description=(
        "Plans a tiered vendor security review, dispatches its steps, re-tiers upward when "
        "evidence contradicts the intake form, and owns review state."
    ),
    instruction=(
        "You plan vendor security reviews and decide their tier. You never execute steps\n"
        "yourself, never approve a vendor, never invent an answer the vendor did not give,\n"
        "and never lower a tier that has already been set.\n"
        "\n"
        "TIERING. Tier 1 if the vendor processes customer data, has production system\n"
        "access, or is an AI service handling company text. Tier 2 if it handles internal\n"
        "non-customer data. Tier 3 otherwise. Intake descriptions are written by the person\n"
        "who wants the contract signed, so treat them as a claim, not as fact: when the\n"
        "vendor's own answers or evidence indicate broader access than intake declared,\n"
        "raise the tier and state which answer caused it. When evidence is ambiguous, tier\n"
        "up and say why.\n"
        "\n"
        "Every step name must come from the STEP_VOCABULARY supplied in this prompt. If the\n"
        "work you think is needed has no name in that vocabulary, do not invent one — return\n"
        "needs_human with a reason instead."
    ),
    output_schema=ReviewPlan,
    tools=TOOLS,
)


def handle_event(event: EventEnvelope, review: Review) -> None:
    """Pub/Sub entrypoint. Routes by ``event.type``.

    The state guard has already run in ``shared.subscriber``, so ``review`` is loaded and in a
    state where this event makes sense. Handlers are therefore about the work rather than about
    whether the work applies.

    Raises:
        UnhandledEvent: on an event type this agent has no branch for. The subscriber nacks and
            the message redelivers rather than being acked into silence.
    """
    if event.type == TOPIC_REVIEW_INTAKE:
        on_intake(event, review)
        return
    raise UnhandledEvent(f"orchestrator has no branch for {event.type!r}")


class UnhandledEvent(Exception):
    """An event reached an agent with no branch for it."""


def on_intake(event: EventEnvelope, review: Review) -> None:
    """Tier the review, checkpoint its plan, and hand off to the Questionnaire agent.

    Intake to first contact in one step: recall what is already known about the vendor, plan
    against the tiering policy, record the plan under a checkpoint, move the review to
    ``QUESTIONNAIRE_OUT`` and publish ``review.plan_ready``.

    The plan is checkpointed **before** the state moves, so a crash between the two re-runs a
    transition that is already legal rather than a plan that is already spent.

    Raises:
        Exception: a planning failure propagates after parking the review in ``NEEDS_HUMAN``.
            The Orchestrator never guesses a tier: an unplanned review is a dashboard card, not
            a default.
    """
    from agents.orchestrator.planner import Plan, generate_plan, load_vendor_record

    ctx = context_for(event, agent="orchestrator")
    db = firestore_client()

    with span("orchestrator.intake", ctx) as s:
        raw = db.collection("vendors").document(review.vendor_id).get().to_dict()
        if not raw:
            park(review.review_id, reason="vendor_record_missing")
            raise UnhandledEvent(
                f"no vendor record for {review.vendor_id!r}; a review cannot be tiered from an "
                "intake event alone"
            )

        vendor = load_vendor_record(raw)
        dossier = recall_dossier(vendor.vendor_id)

        try:
            recorded = step(
                STEP_PLAN,
                ctx,
                lambda: generate_plan(vendor, dossier, ctx).model_dump(mode="json"),
            )
        except Exception as exc:
            park(review.review_id, reason="planning_failed")
            log.error("planning failed for review=%s: %s", review.review_id, exc)
            raise

        plan = Plan.model_validate(recorded)

        validate_transition(review.state, ReviewState.QUESTIONNAIRE_OUT)
        db.collection("reviews").document(review.review_id).set(
            {
                "state": ReviewState.QUESTIONNAIRE_OUT.value,
                "tier": plan.tier,
                "plan_version": plan.plan_version,
                "gate_scope": None,
            },
            merge=True,
        )

        record_decision(
            s,
            goal=f"tier and plan the review of {vendor.name}",
            decision=f"Tier {plan.tier}: {plan.reason}",
        )

        publish(
            TOPIC_REVIEW_PLAN_READY,
            review.review_id,
            {
                "tier": plan.tier,
                "plan_version": plan.plan_version,
                "domains": plan.domains,
                "vendor_id": vendor.vendor_id,
            },
            ctx=ctx,
        )

    log.info(
        "review=%s tiered %d and planned; %s",
        review.review_id,
        plan.tier,
        plan.reason,
    )
