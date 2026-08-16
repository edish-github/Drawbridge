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

import logging

from google.adk import Agent

from shared.checkpoint import step
from shared.clients import firestore_client
from shared.config import settings
from shared.context import context_for
from shared.domain import Finding, Review
from shared.events import (
    TOPIC_REVIEW_FINDINGS_READY,
    TOPIC_REVIEW_RESCORE,
    TOPIC_REVIEW_SCORE_READY,
    EventEnvelope,
    publish,
)
from shared.memory import recall_dossier
from shared.state import park
from shared.telemetry import record_decision, span

log = logging.getLogger("drawbridge.risk_scorer")

SERVICE_ACCOUNT = "sa-scorer"

COLLECTION_SCORES = "scores"
STEP_SCORE = "score"
STEP_MEMO = "memo"

TOOLS: list = []

agent = Agent(
    name="risk_scorer",
    model=settings().model_deep,
    description=(
        "Writes the one-page risk memo a CISO reads before deciding. The Trust Score itself "
        "is computed in code, not by this agent."
    ),
    instruction=(
        "You write a one-page risk memo for a CISO from findings that have already been\n"
        "scored. Roughly 300 words. The reader is accountable for the decision and will\n"
        "read nothing else.\n"
        "\n"
        "STRUCTURE, fixed: the recommendation; the three things that drove it; the\n"
        "mitigations required for conditional approval; what to re-check in 90 days. If\n"
        "fewer than three findings drove the outcome, give the ones that did and say so —\n"
        "do not pad to three.\n"
        "\n"
        "THE RECOMMENDATION MUST MATCH THE BAND you are given. Approve, conditional or\n"
        "escalate is already decided by the score; your job is to explain it, not revisit\n"
        "it. If the findings seem to you to contradict the band, write the memo to the band\n"
        "and state the tension in one sentence at the end.\n"
        "\n"
        "BOUNDARIES. You do not compute or adjust the Trust Score and you do not change any\n"
        "severity. Where you quote a vendor document, mark it as their claim rather than as\n"
        "fact. You report what the findings say."
    ),
    tools=TOOLS,
)
# No output_schema: the memo is prose for a human, and its fixed structure is a writing
# instruction rather than a parseable shape. Constraining it to a schema would turn the one
# artefact a CISO reads into a form.


def handle_event(event: EventEnvelope, review: Review) -> None:
    """Pub/Sub entrypoint. Routes by ``event.type``.

    The state guard runs in ``shared.subscriber`` before dispatch, so ``review`` arrives loaded
    and in phase.

    Raises:
        UnhandledEvent: on an event type this agent has no branch for.
    """
    if event.type in (TOPIC_REVIEW_FINDINGS_READY, TOPIC_REVIEW_RESCORE):
        on_findings_ready(event, review)
        return
    raise UnhandledEvent(f"risk_scorer has no branch for {event.type!r}")


class UnhandledEvent(Exception):
    """An event reached an agent with no branch for it."""


def on_findings_ready(event: EventEnvelope, review: Review) -> None:
    """Score the review, write the memo, and publish the result.

    Scoring is arithmetic and runs first; the memo is one deep-model call and runs second,
    against a band it is given rather than one it decides. If the memo fails, the score stays
    written and the review parks — a score with no reasoning behind it is not something to
    approve against, and losing the score as well would mean re-running the expensive passes
    that produced it.

    ``review.rescore`` routes here too. Rescoring is the same arithmetic over a finding set
    that has changed, which is why raising Adversarial Conduct can simply add a finding and
    republish rather than reaching into a score.

    Raises:
        RubricError: on a finding the rubric cannot map. The review parks; the number is never
            computed from a partial finding set.
    """
    from agents.risk_scorer.memo import write_memo
    from agents.risk_scorer.scoring import Flags, compute_score, explain, load_rubric

    ctx = context_for(event, agent="risk_scorer")
    db = firestore_client()

    with span("risk_scorer.score", ctx) as s:
        findings = load_findings(review.review_id)
        rubric = load_rubric()
        flags = Flags(adversarial_conduct=adversarial_flag(review.review_id))

        try:
            result = compute_score(findings, rubric, flags, tier=review.tier)
        except Exception as exc:
            park(review.review_id, reason="scoring_failed")
            log.error("scoring failed for review=%s: %s", review.review_id, exc)
            raise

        breakdown = explain(result, rubric, tier=review.tier)
        step(
            STEP_SCORE,
            ctx,
            lambda: save_score(review.review_id, result, breakdown),
        )

        vendor = db.collection("vendors").document(review.vendor_id).get().to_dict() or {}
        try:
            step(
                STEP_MEMO,
                ctx,
                lambda: write_memo(
                    ctx,
                    review.review_id,
                    findings=findings,
                    score=result,
                    vendor=vendor,
                    dossier=recall_dossier(review.vendor_id),
                ),
            )
        except Exception as exc:
            park(review.review_id, reason="memo_failed")
            log.error("memo failed for review=%s: %s", review.review_id, exc)
            raise

        record_decision(
            s,
            goal=f"score {vendor.get('name', review.vendor_id)} from {len(findings)} finding(s)",
            decision=f"Trust Score {result.score}, band {result.band}",
        )

        publish(
            TOPIC_REVIEW_SCORE_READY,
            review.review_id,
            {
                "score": result.score,
                "band": result.band,
                "breakdown": result.breakdown,
                "adversarial_applied": result.adversarial_applied,
            },
            ctx=ctx,
        )

    log.info(
        "review=%s scored %d (%s)\n%s",
        review.review_id,
        result.score,
        result.band,
        "\n".join(f"    {line}" for line in breakdown),
    )


def load_findings(review_id: str) -> list[Finding]:
    """Return every finding recorded for this review, ordered by id.

    Ordered so the arithmetic is reproducible: the score is order-independent by construction,
    but the breakdown printed in the binder should not shuffle between runs.
    """
    from google.cloud.firestore_v1 import FieldFilter

    docs = (
        firestore_client()
        .collection("findings")
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    )
    return sorted(
        (Finding.model_validate(d.to_dict()) for d in docs), key=lambda f: f.finding_id
    )


def adversarial_flag(review_id: str) -> bool:
    """Return whether Adversarial Conduct has been raised on this review."""
    snap = firestore_client().collection("reviews").document(review_id).get()
    return bool((snap.to_dict() or {}).get("adversarial_conduct", False))


def save_score(review_id, result, breakdown: list[str]) -> dict:
    """Persist the score and the arithmetic that produced it.

    The breakdown is stored, not recomputed on demand. It is binder section 5, and a binder
    that recomputed it would be showing today's rubric against a decision taken under an
    earlier one.
    """
    doc = {
        "review_id": review_id,
        "score": result.score,
        "band": result.band,
        "breakdown": result.breakdown,
        "adversarial_applied": result.adversarial_applied,
        "arithmetic": breakdown,
    }
    firestore_client().collection(COLLECTION_SCORES).document(review_id).set(doc)
    firestore_client().collection("reviews").document(review_id).set(
        {"score": result.score, "band": result.band}, merge=True
    )
    return doc
