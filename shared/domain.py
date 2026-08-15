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


# --- Model output schemas ------------------------------------------------------------------
#
# What an agent emits, as distinct from what gets persisted. The types below are attached to
# agents as ``output_schema``, so malformed output is rejected by the framework rather than
# parsed hopefully downstream.
#
# The distinction is deliberate everywhere it appears. A model emits a ``FindingDraft``; the
# persisted ``Finding`` adds ``finding_id``, ``trace_ref`` and — most importantly — ``source``,
# which records whether the conclusion was arithmetic or judgement. A model must not be able to
# claim its own finding was computed by a rule, so the field it would need is not in its schema.
# The same holds for ``ReviewPlan`` against the persisted plan, which carries the plan version
# and inherited idempotency keys the model has no business setting.


class PlanStep(BaseModel):
    """One step of a review plan.

    ``name`` must come from the step vocabulary supplied in the planning prompt. Step names are
    deterministic from workflow position because the idempotency key is derived from them.
    """

    name: str
    params: dict = Field(default_factory=dict)


class ReviewPlan(BaseModel):
    """The Orchestrator's planning output.

    ``needs_human`` is the defined non-compliance path: when the work required has no name in
    the step vocabulary, the model returns ``needs_human=True`` with a reason rather than
    inventing a step name that nothing downstream can execute.
    """

    tier: Literal[1, 2, 3]
    reason: str
    steps: list[PlanStep] = Field(default_factory=list)
    needs_human: bool = False


class FindingDraft(BaseModel):
    """A finding as the Evidence agent emits it, before persistence.

    Carries no ``source`` field: provenance is assigned by the code that persists it, so a
    model cannot label its own judgement as a rule.
    """

    domain: str
    severity: Severity
    contradiction: bool
    summary: str
    evidence_ref: str | None = None
    claim_ref: str | None = None


class RelevanceJudgement(BaseModel):
    """The Watchdog's assessment of one signal against one vendor.

    ``confidence`` is what separates a re-review from a triage card, which is why it is a
    required field rather than an optional annotation on a boolean.
    """

    relevant: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    source_url: str


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
        gate_scope: required when ``target`` is ``GATED``, and required when ``current`` is
            ``GATED``. The scope a review is parked at decides which release is legal: a
            contact gate may only resume ``QUESTIONNAIRE_OUT``, a decision gate may only
            reach ``DECIDED``. Without the scope the two parks are indistinguishable and a
            contact-gate release could approve a vendor nobody scored.

    Raises:
        InvalidTransition: on any illegal pair, on a ``GATED`` state with no scope, or on a
            release that does not match the scope the review was parked at. The caller parks
            the review in ``NEEDS_HUMAN``; this function never corrects the target.
    """
    allowed = ALLOWED.get(current)
    if allowed is None:
        raise InvalidTransition(f"{current} is not a state in the transition table")

    if target not in allowed:
        raise InvalidTransition(
            f"{current} -> {target} is not a legal transition; "
            f"legal targets are {sorted(s.value for s in allowed)}"
        )

    # NEEDS_HUMAN is reachable from everywhere and carries no scope of its own: a parked
    # review's scope is preserved on the record so the release path still knows where it was.
    if target is ReviewState.NEEDS_HUMAN:
        return

    if target is ReviewState.GATED and gate_scope is None:
        raise InvalidTransition(
            "a transition into GATED must state its gate_scope: 'contact' for the park "
            "before first outbound contact, 'decision' for the park after scoring"
        )

    if current is ReviewState.GATED:
        if gate_scope is None:
            raise InvalidTransition(
                f"releasing a GATED review to {target} requires the gate_scope it was "
                "parked at; without it a contact-gate release is indistinguishable from a "
                "decision-gate release"
            )
        expected = _GATE_RELEASE[gate_scope]
        if target is not expected:
            raise InvalidTransition(
                f"a review parked at the {gate_scope!r} gate may only be released to "
                f"{expected.value}, not {target.value}"
            )


_GATE_RELEASE: dict[GateScope, ReviewState] = {
    "contact": ReviewState.QUESTIONNAIRE_OUT,
    "decision": ReviewState.DECIDED,
}
"""Where each gate scope releases to.

A contact gate authorises first outbound contact, so its release resumes the questionnaire. A
decision gate is the risk acceptance, so its release is the decision itself. Nothing else is
reachable from a park, which is what stops a contact approval from doubling as a vendor
approval.
"""

_TERMINAL: frozenset[ReviewState] = frozenset({ReviewState.DECIDED})
"""States whose record is immutable."""


def is_terminal(state: ReviewState) -> bool:
    """Return whether a review in ``state`` is immutable.

    A ``DECIDED`` review is immutable: later events append to the ledger and a new signal
    opens a new linked review rather than editing a closed one.

    ``MONITORED`` is deliberately not terminal. Monitoring is an ongoing state a review sits
    in after a decision, and the Watchdog re-enters it on every sweep; treating it as
    immutable would stop the sweep recording that it ran.
    """
    return state in _TERMINAL
