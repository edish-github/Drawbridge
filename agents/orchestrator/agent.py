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

from google.adk import Agent

from shared.events import EventEnvelope

SERVICE_ACCOUNT = "sa-orchestrator"

TOOLS: list = []
"""Registered through ``shared.gateway``. Empty until the tools exist; an agent with no
registered tools can take no action, which is the correct posture for a stub.
"""

agent = Agent(
    name="orchestrator",
    model="${MODEL_FAST}",
    description=(
        "Plans a tiered vendor security review, dispatches its steps, re-tiers upward when "
        "evidence contradicts the intake form, and owns review state."
    ),
    instruction=(
        "You plan vendor security reviews. You receive vendor facts and a tiering policy and "
        "return a JSON plan: a list of step names with parameters. Tier 1 if the vendor "
        "processes customer data, has production system access, or is an AI service handling "
        "company text. Tier 2 if it handles internal non-customer data. Tier 3 otherwise. "
        "When evidence is ambiguous, tier up and say why. You never approve a vendor, never "
        "invent an answer the vendor did not give, and never lower a tier."
    ),
    tools=TOOLS,
)


def handle_event(event: EventEnvelope) -> None:
    """Pub/Sub entrypoint. Routes by ``event.type`` after the state guard.

    Raises:
        NotImplementedError: contract only.
    """
    raise NotImplementedError
