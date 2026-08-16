"""Orchestrator — owns the review's state and the plan it executes.

Mission
    Turn an intake request into a tiered review plan, dispatch its steps, re-evaluate the
    tier as evidence arrives, enforce the human gates, and own the review's forward progress.

State ownership
    The Orchestrator owns the review's plan and every **forward** transition. Any component
    may **park** a review — into ``GATED`` or ``NEEDS_HUMAN`` — through the shared ``park()``
    helper, which validates the transition, writes the event and the dashboard card. Nothing
    else writes review state.

    In one line: anything can stop a review, only the Orchestrator can advance one. Requiring
    a park to travel through here would mean a lost message leaves a review claiming to be in
    flight after it has already stopped, which is the worse of the two inconsistencies.
    ``tests/test_state_ownership.py`` asserts the rule against the source tree.

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
from datetime import UTC, datetime

from google.adk import Agent

from shared.checkpoint import step
from shared.clients import firestore_client
from shared.config import settings
from shared.context import context_for
from shared.domain import Review, ReviewPlan, ReviewState
from shared.events import (
    TOPIC_REVIEW_APPROVED,
    TOPIC_REVIEW_INTAKE,
    TOPIC_REVIEW_PLAN_READY,
    TOPIC_REVIEW_SCORE_READY,
    TOPIC_VENDOR_REPLY_RECEIVED,
    EventEnvelope,
    publish,
)
from shared.memory import recall_dossier
from shared.state import advance, park
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
    handler = {
        TOPIC_REVIEW_INTAKE: on_intake,
        TOPIC_VENDOR_REPLY_RECEIVED: on_reply_received,
        TOPIC_REVIEW_SCORE_READY: on_score_ready,
        TOPIC_REVIEW_APPROVED: on_approved,
    }.get(event.type)

    if handler is None:
        raise UnhandledEvent(f"orchestrator has no branch for {event.type!r}")
    handler(event, review)


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

        advance(
            review,
            ReviewState.QUESTIONNAIRE_OUT,
            reason=f"Tier {plan.tier}: {plan.reason}",
            tier=plan.tier,
            plan_version=plan.plan_version,
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


def on_reply_received(event: EventEnvelope, review: Review) -> None:
    """Move the review forward as replies arrive, and open evidence review when there are enough.

    Runs after the Questionnaire agent has parsed and merged the same event, so the coverage it
    reads is current. Two transitions live here and nowhere else:

    ``QUESTIONNAIRE_OUT -> REPLIES_IN`` on the first reply, and ``REPLIES_IN ->
    EVIDENCE_REVIEW`` once the answered proportion crosses the threshold — or once an analyst
    has marked the thread complete, which is what happens when a vendor simply stops replying
    and the chase rounds are spent. Reconciling below the threshold produces gaps that describe
    the process rather than the vendor, which is why it waits.
    """
    from agents.questionnaire.parser import COVERAGE_TO_PROCEED, coverage

    ctx = context_for(event, agent="orchestrator")

    with span("orchestrator.replies", ctx) as s:
        if review.state is ReviewState.QUESTIONNAIRE_OUT:
            advance(review, ReviewState.REPLIES_IN, reason="first reply received")
            review = review.model_copy(update={"state": ReviewState.REPLIES_IN})

        reached = coverage(review.review_id)
        forced = replies_marked_complete(review.review_id)

        if reached < COVERAGE_TO_PROCEED and not forced:
            record_decision(
                s,
                goal="decide whether the review has enough answers to reconcile",
                decision=f"coverage {reached:.0%}, below {COVERAGE_TO_PROCEED:.0%}; still waiting",
            )
            return

        advance(
            review,
            ReviewState.EVIDENCE_REVIEW,
            reason=(
                f"coverage {reached:.0%}"
                if not forced
                else f"analyst proceeded at {reached:.0%} coverage"
            ),
        )
        record_decision(
            s,
            goal="decide whether the review has enough answers to reconcile",
            decision=f"opened evidence review at {reached:.0%} coverage",
        )
        log.info(
            "review=%s opened evidence review at %.0f%% coverage%s",
            review.review_id,
            reached * 100,
            " (analyst proceeded)" if forced else "",
        )


def replies_marked_complete(review_id: str) -> bool:
    """Return whether an analyst has declared the reply thread finished.

    The escape hatch for the ordinary case where a vendor answers most of what was asked and
    stops. Without it a review waits on a threshold no real correspondence reaches, which looks
    like the fleet hanging rather than like the fleet waiting.
    """
    snap = firestore_client().collection("reviews").document(review_id).get()
    return bool((snap.to_dict() or {}).get("replies_complete", False))


def on_score_ready(event: EventEnvelope, review: Review) -> None:
    """Park the scored review at the decision gate.

    Two movements, and they are separate on purpose. ``EVIDENCE_REVIEW -> SCORED`` is forward
    progress: the review now has a number, and that is a fact about the review rather than a
    wait. ``SCORED -> GATED`` is the wait — a named human accepting the risk — which is a park,
    carrying ``gate_scope="decision"`` so a release can only reach ``DECIDED`` and never resume
    the questionnaire.

    Collapsing them would put a review into a gate without ever recording that it was scored,
    and the binder's timeline would show a decision gate opening for a review with no scoring
    event behind it.
    """
    ctx = context_for(event, agent="orchestrator")
    score = event.payload.get("score")
    band = event.payload.get("band")

    with span("orchestrator.decision_gate", ctx) as s:
        if review.state is not ReviewState.SCORED:
            advance(
                review,
                ReviewState.SCORED,
                reason=f"Trust Score {score} ({band})",
                score=score,
                band=band,
            )
            review = review.model_copy(update={"state": ReviewState.SCORED})

        park(
            review.review_id,
            reason=f"awaiting risk acceptance · score {score} · band {band}",
            target=ReviewState.GATED,
            gate_scope="decision",
        )
        record_decision(
            s,
            goal="decide whether the review can close",
            decision=f"scored {score} ({band}); parked for a named human to accept the risk",
        )


def on_approved(event: EventEnvelope, review: Review) -> None:
    """Release a gate a human has approved, and resume the path it was blocking.

    The approval service publishes ``review.approved``; the release itself happens here,
    because it is a forward transition. A contact gate resumes the questionnaire by
    republishing ``review.plan_ready``; a decision gate closes the review.

    Raises:
        UnhandledEvent: on an approval whose scope does not match the gate the review is
            parked at. An approval spent on the wrong gate is the failure the scope exists to
            prevent, so a mismatch stops rather than guessing.
    """
    ctx = context_for(event, agent="orchestrator")
    scope = event.payload.get("scope")
    identity = event.payload.get("identity", "unknown")

    if scope != review.gate_scope:
        raise UnhandledEvent(
            f"approval scoped {scope!r} presented for a review parked at "
            f"{review.gate_scope!r}; an approval is never portable between gates"
        )

    with span("orchestrator.gate_release", ctx, gate_scope=scope) as s:
        if scope == "contact":
            advance(
                review,
                ReviewState.QUESTIONNAIRE_OUT,
                reason=f"contact approved by {identity}",
                gate_scope="contact",
                gate_released_by=identity,
            )
            publish(
                TOPIC_REVIEW_PLAN_READY,
                review.review_id,
                {
                    "tier": review.tier,
                    "plan_version": review.plan_version,
                    "released_by": identity,
                },
                ctx=ctx,
            )
        else:
            advance(
                review,
                ReviewState.DECIDED,
                reason=f"risk accepted by {identity}",
                gate_scope="decision",
                gate_released_by=identity,
                decided_at=datetime.now(UTC).isoformat(),
            )

        record_decision(
            s,
            goal=f"release the {scope} gate",
            decision=f"released by {identity}",
        )

    log.info("review=%s %s gate released by %s", review.review_id, scope, identity)
