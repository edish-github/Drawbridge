"""Domain types and the review state machine.

Every document, event payload and tool argument in Drawbridge is one of these types.
The transition table at the bottom of this module is the single authority on which state
changes are legal; an illegal transition raises rather than silently correcting, and the
review lands in ``NEEDS_HUMAN``.

Failure semantics: validation errors surface as ``pydantic.ValidationError`` at the point
of construction, never as a partially-built object. ``validate_transition`` raises
``InvalidTransition`` and never returns a corrected state.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ReviewState(StrEnum):
    """States a review moves through. Progress is monotonic apart from the two documented
    backward transitions: a re-tier sending new questions mid-flight, and the Watchdog
    reopening a closed review (which creates a new linked review, never mutating history).
    """

    INTAKE = "intake"
    QUESTIONNAIRE_OUT = "questionnaire_out"
    REPLIES_IN = "replies_in"
    EVIDENCE_REVIEW = "evidence_review"
    SCORED = "scored"
    GATED = "gated"
    DECIDED = "decided"
    MONITORED = "monitored"
    NEEDS_HUMAN = "needs_human"


GateScope = Literal["contact", "decision"]
"""Which human gate a ``GATED`` review is parked at.

``contact`` is the park before first outbound contact (human gate G2, made unskippable by
gateway policy P1). ``decision`` is the park after scoring (human gate G1, risk acceptance).
Without the scope the state machine cannot tell the two waits apart, and a contact-gate
release would resume into the wrong state.
"""

Severity = Literal["low", "medium", "high"]
FindingSource = Literal["rule", "model"]
Provenance = Literal["human", "rule", "model_structured"]

RUBRIC_DOMAINS = (
    "data_protection",
    "access_control",
    "incident_response",
    "compliance_posture",
    "business_continuity",
    "subprocessors",
    "ai_specific",
    "conduct",
)
"""Domain keys a finding may carry. The first seven are the weighted scoring domains in
``agents/risk_scorer/rubric.yaml``; ``conduct`` carries the Adversarial Conduct finding,
which is scored through the modifier rather than through a domain weight.
"""

ALLOWED_NOTE_TYPES = frozenset(
    {
        "outcome",
        "band",
        "negotiated_exception",
        "conduct_flag",
        "contact_change",
        "cert_expiry",
        "subprocessor",
        "question_effectiveness",
    }
)
"""The only note types durable memory accepts. Memory is written from material derived from
vendor-supplied content and recalled at the start of every future review, before any
screening runs in that review, so it accepts enumerated structure and never free prose.
"""


class Vendor(BaseModel):
    vendor_id: str
    name: str
    category: str
    legal_entity_name: str | None = None
    primary_domain: str | None = None
    is_ai_vendor: bool = False
    tier: int = 2
    status: str = "active"
    adversarial_flag: bool = False


class TierChange(BaseModel):
    """One re-tier, with the evidence that caused it.

    Tier only ever moves up. A downward re-tier is an attack surface: it would let a
    vendor's own answers reduce the scrutiny applied to them.
    """

    from_tier: int
    to_tier: int
    reason: str
    source_ref: str
    at: datetime


class Review(BaseModel):
    review_id: str
    vendor_id: str
    state: ReviewState = ReviewState.INTAKE
    gate_scope: GateScope | None = None
    tier: int = 2
    plan_version: int = 1
    tier_history: list[TierChange] = Field(default_factory=list)
    score: int | None = None
    band: str | None = None
    opened_at: datetime
    decided_at: datetime | None = None
    cost_usd: float = 0.0
    reopened_from: str | None = None


class Finding(BaseModel):
    finding_id: str
    review_id: str
    domain: str
    severity: Severity
    source: FindingSource
    contradiction: bool = False
    summary: str
    evidence_ref: str | None = None
    claim_ref: str | None = None
    trace_ref: str | None = None


class EvidenceChunk(BaseModel):
    """A chunk of a screened document, embedded and indexed for retrieval.

    ``review_id`` is not decoration: the KNN index is pre-filtered on it, so retrieval can
    never surface one review's evidence inside another.
    """

    chunk_id: str
    review_id: str
    doc_ref: str
    page: int
    text: str
    embedding: list[float]


class Subprocessor(BaseModel):
    subprocessor_id: str
    vendor_id: str
    name: str
    purpose: str
    processes_customer_data: bool
    known_to_org: bool = False
    prior_review_id: str | None = None


class MemoryNote(BaseModel):
    vendor_id: str
    type: str
    provenance: Provenance
    value: dict
    supersedes: str | None = None
    at: datetime


class ScoreResult(BaseModel):
    score: int
    band: str
    breakdown: dict[str, float]
    adversarial_applied: bool = False


ALLOWED: dict[ReviewState, set[ReviewState]] = {
    # GATED here carries gate_scope="contact": the park before first outbound contact.
    ReviewState.INTAKE: {ReviewState.QUESTIONNAIRE_OUT, ReviewState.NEEDS_HUMAN},
    ReviewState.QUESTIONNAIRE_OUT: {
        ReviewState.REPLIES_IN,
        ReviewState.GATED,
        ReviewState.NEEDS_HUMAN,
    },
    # REPLIES_IN -> QUESTIONNAIRE_OUT and EVIDENCE_REVIEW -> QUESTIONNAIRE_OUT are the
    # re-tier transitions: the only legitimate backward move inside a single review.
    ReviewState.REPLIES_IN: {
        ReviewState.EVIDENCE_REVIEW,
        ReviewState.QUESTIONNAIRE_OUT,
        ReviewState.NEEDS_HUMAN,
    },
    ReviewState.EVIDENCE_REVIEW: {
        ReviewState.SCORED,
        ReviewState.QUESTIONNAIRE_OUT,
        ReviewState.NEEDS_HUMAN,
    },
    ReviewState.SCORED: {ReviewState.GATED, ReviewState.NEEDS_HUMAN},
    # GATED -> QUESTIONNAIRE_OUT is a contact-gate release resuming the thread;
    # GATED -> DECIDED is a decision-gate release.
    ReviewState.GATED: {
        ReviewState.DECIDED,
        ReviewState.QUESTIONNAIRE_OUT,
        ReviewState.NEEDS_HUMAN,
    },
    ReviewState.DECIDED: {ReviewState.MONITORED, ReviewState.NEEDS_HUMAN},
    ReviewState.MONITORED: {ReviewState.MONITORED, ReviewState.NEEDS_HUMAN},
    ReviewState.NEEDS_HUMAN: {
        ReviewState.QUESTIONNAIRE_OUT,
        ReviewState.EVIDENCE_REVIEW,
        ReviewState.SCORED,
        ReviewState.GATED,
        ReviewState.DECIDED,
    },
}
"""Legal state transitions. ``NEEDS_HUMAN`` is reachable from every state — a failure path
that is documented but cannot be executed is worse than one never written down.
"""


class InvalidTransition(Exception):
    """Raised when a write attempts a transition the table forbids."""


def validate_transition(
    current: ReviewState,
    target: ReviewState,
    *,
    gate_scope: GateScope | None = None,
) -> None:
    """Assert that ``current -> target`` is legal, checking gate scope where it applies.

    Args:
        current: the review's state as loaded from the ledger.
        target: the state the caller intends to write.
        gate_scope: required when ``target`` is ``GATED``; the scope a ``GATED`` review is
            released from determines which target is legal, so a contact-gate release may
            only resume ``QUESTIONNAIRE_OUT`` and a decision-gate release may only reach
            ``DECIDED``.

    Raises:
        InvalidTransition: on any illegal pair, on a ``GATED`` target with no scope, or on
            a release that does not match the scope the review was parked at. The caller
            parks the review in ``NEEDS_HUMAN``; this function never corrects the target.
    """
    raise NotImplementedError


def is_terminal(state: ReviewState) -> bool:
    """Return whether a review in ``state`` is immutable.

    A ``DECIDED`` review is immutable: later events append to the ledger and a new signal
    opens a new linked review rather than editing a closed one.
    """
    raise NotImplementedError
