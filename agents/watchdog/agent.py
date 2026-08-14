"""Watchdog — proves the review does not end at signature.

Mission
    Sweep the approved-vendor portfolio on a schedule for certificate expiries and breach or
    news signals, and open a re-review on a confirmed hit. Fewer than half of organisations
    continuously monitor their vendors, and the reason they stop is noise — so signal quality
    is the design, not a tuning exercise.

Trigger
    ``watchdog.sweep``, published by Cloud Scheduler.

Tools
    Pub/Sub publish, outbound fetch through the gateway under policy P3 (an allowlist of feed
    domains), and re-review task write. Never approvals, never email, never vendor data write.

Model
    The fast model, for relevance scoring only.

Failure behaviour
    A feed outage is logged and skipped; the Watchdog never blocks or degrades an active
    review. A fetch outside the allowlist is a P3 block, logged like any other policy
    decision. A signal below the confidence threshold becomes a triage card for the analyst
    rather than a new review, and a signal that does not match the vendor's registered domain
    and legal entity is discarded before the model is called at all — matching on a bare name
    is how continuous monitoring earns its reputation for noise. A confirmed hit opens a
    *new* linked review; it never mutates the closed one, because a decided review is
    immutable.
"""

from __future__ import annotations

from google.adk import Agent

from shared.events import EventEnvelope

SERVICE_ACCOUNT = "sa-watchdog"

TOOLS: list = []

agent = Agent(
    name="watchdog",
    model="${MODEL_FAST}",
    description=(
        "Sweeps approved vendors for expiring certificates and breach signals, and opens a "
        "re-review only on a high-confidence, materially relevant hit."
    ),
    instruction=(
        "You assess whether a news or breach signal is materially relevant to a specific "
        "vendor. You are given the vendor's registered domain and legal entity name. A "
        "similar company name is not a match. Return a relevance judgement with an explicit "
        "confidence score; when you are unsure, return low confidence rather than a "
        "confident guess — an unnecessary re-review costs an analyst an hour and destroys "
        "trust in the feature."
    ),
    tools=TOOLS,
)


def handle_event(event: EventEnvelope) -> None:
    """Pub/Sub entrypoint. Routes by ``event.type`` after the state guard.

    Raises:
        NotImplementedError: contract only.
    """
    raise NotImplementedError
