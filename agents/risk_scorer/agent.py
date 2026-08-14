"""Risk Scorer — converts findings into a Trust Score, a band and a memo.

Mission
    Compute the Trust Score (0–100, higher is safer) as deterministic arithmetic over
    model-assigned finding severities, apply the Adversarial Conduct modifier, and write the
    one-page risk memo a human reads before deciding.

Trigger
    ``review.findings_ready`` and ``review.rescore``.

Tools
    Firestore read, ``scores`` write, the rubric config. Never external calls, never
    ``approvals`` write.

Model
    **None for scoring.** Severity is assigned by the Evidence agent where the model is
    already reading the passage; scoring is arithmetic and there is no routing entry for it.
    The deep model is used once, for the memo. This is the reason the binder can show its
    working and the reason no agent in this fleet holds the pen on its own metric.

Failure behaviour
    A malformed finding — an unmapped domain, an unrecognised severity — fails loudly rather
    than quietly skewing a score. The memo is screened through the output template before a
    human reads it, and a threat found there parks the review in ``NEEDS_HUMAN`` with reason
    ``output_screening`` rather than publishing anything. A memo generation failure leaves
    the score written and the memo absent, and the review parks: a score without the
    reasoning that produced it is not something to approve against. Under P2 a sanitised
    document is inadmissible to the memo call, because a sanitised document is by definition
    one that tried something.
"""

from __future__ import annotations

from google.adk import Agent

from shared.events import EventEnvelope

SERVICE_ACCOUNT = "sa-scorer"

TOOLS: list = []

agent = Agent(
    name="risk_scorer",
    model="${MODEL_DEEP}",
    description=(
        "Writes the one-page risk memo a CISO reads before deciding. The Trust Score itself "
        "is computed in code, not by this agent."
    ),
    instruction=(
        "You write a one-page risk memo for a CISO from findings that have already been "
        "scored. The structure is fixed: the recommendation, the three things that drove it, "
        "the mitigations required for conditional approval, and what to re-check in 90 days. "
        "You do not compute or adjust the Trust Score, and you do not change any severity. "
        "You report what the findings say."
    ),
    tools=TOOLS,
)


def handle_event(event: EventEnvelope) -> None:
    """Pub/Sub entrypoint. Routes by ``event.type`` after the state guard.

    Raises:
        NotImplementedError: contract only.
    """
    raise NotImplementedError
