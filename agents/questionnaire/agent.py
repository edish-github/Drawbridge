"""Questionnaire — the only agent that talks to the outside world.

Mission
    Generate the tier-appropriate question set from the curated bank, deliver it after a
    human authorises first contact, parse replies incrementally as they arrive over days,
    chase politely on a schedule, and issue one targeted follow-up when an answer arrives but
    is unusable.

Trigger
    ``review.plan_ready``, ``vendor.reply_received``, and chase timers.

Tools
    ``qa_responses`` read/write, portal write, email through the gateway, Pub/Sub, and the
    fast model. Never ``findings``, ``scores`` or ``approvals`` write, and no Storage read.
    The agent that talks to the outside world holds nothing that can change the number.

Model
    The fast model, for parsing replies, composing chases and composing follow-ups.

Failure behaviour
    Malformed or unparseable replies are quoted back to the analyst queue with their source
    message reference, never silently dropped — the reply that gets dropped is the one the
    vendor quotes back. An answer parsed below the confidence threshold sets
    ``needs_human`` rather than being recorded as an answer. A model call failure leaves the
    reply unparsed and retries; it never records a partial parse. If no approval token
    exists the gateway raises under P1, the review parks in ``GATED`` with
    ``gate_scope="contact"``, and an approval card appears on the dashboard — that parked
    state is the intended behaviour, not a failure. Three chase rounds without a response
    ends in ``NEEDS_HUMAN``.
"""

from __future__ import annotations

from google.adk import Agent

from shared.events import EventEnvelope

SERVICE_ACCOUNT = "sa-questionnaire"

TOOLS: list = []

agent = Agent(
    name="questionnaire",
    model="${MODEL_FAST}",
    description=(
        "Selects tier-appropriate questions from a curated bank, delivers them once a human "
        "has authorised contact, parses replies incrementally, and chases on a schedule."
    ),
    instruction=(
        "You conduct the vendor side of a security review. You select and tailor questions "
        "from the supplied bank; you never invent a question that is not in it. Every "
        "question demands specific evidence and never accepts a yes or no answer: 'Do you "
        "encrypt data?' becomes 'List encryption standards for data at rest and in transit "
        "and attach your key-management policy.' When you parse a reply you record a "
        "confidence score, and you mark an answer for human review rather than guessing at "
        "what the vendor meant. You do not assign severities, write findings or score "
        "anything."
    ),
    tools=TOOLS,
)


def handle_event(event: EventEnvelope) -> None:
    """Pub/Sub entrypoint. Routes by ``event.type`` after the state guard.

    Raises:
        NotImplementedError: contract only.
    """
    raise NotImplementedError
