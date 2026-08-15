"""Plan generation: vendor facts plus tiering policy in, an ordered list of steps out.

The plan is a list of named steps, which is what makes resume "replay the list, skipping
completed ones". Step names are deterministic from workflow position, because the
idempotency key is derived from them.

Failure semantics: a plan that does not parse as the expected schema raises rather than
being partially applied — a half-written plan would produce idempotency keys for steps that
do not exist. A plan step name that collides with a completed key from a previous plan
version is a re-plan concern and is resolved in ``retier.inherited_keys``, not here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from shared.domain import PlanStep, ReviewPlan, Vendor
from shared.memory import Dossier

STEP_VOCABULARY: tuple[str, ...] = (
    "questionnaire_send",
    "chase",
    "followup",
    "evidence_extract",
    "cross_examine",
    "subprocessor_extract",
    "score",
    "memo",
    "gate_decision",
)
"""The only step names a plan may contain, supplied to the planning prompt.

A closed vocabulary is what makes ``idem_key`` derivation reproducible after a restart: a step
name invented by a model is a key nothing can recompute. Work with no name here is returned as
``needs_human`` rather than given one.
"""


class Plan(BaseModel):
    """The persisted plan, as distinct from the model's ``ReviewPlan`` output.

    Carries the plan version and the inherited idempotency keys that a re-plan sets — neither
    of which a model has any business assigning.
    """

    tier: int
    plan_version: int
    steps: list[PlanStep]
    inherited_keys: dict[str, str] = Field(default_factory=dict)
    domains: list[str] = Field(default_factory=list)


def generate_plan(vendor: Vendor, dossier: Dossier, ctx) -> Plan:
    """Produce the initial plan for a review.

    The model returns a ``ReviewPlan``; this function turns it into the persisted ``Plan`` by
    adding the plan version and any inherited keys.

    Raises:
        ValueError: when the returned plan names a step outside ``STEP_VOCABULARY``, or when
            its tier disagrees with the deterministic tiering rules. Deterministic rules win.
        NeedsHuman: when the model returned ``needs_human``, carrying its stated reason. The
            review parks rather than executing a plan the fleet cannot name.
    """
    raise NotImplementedError


def plan_from_output(output: ReviewPlan, *, plan_version: int) -> Plan:
    """Convert the model's planning output into the persisted plan.

    Raises:
        ValueError: on any step name outside ``STEP_VOCABULARY``.
    """
    raise NotImplementedError


def tier_from(facts: set[str]) -> int:
    """Map declared facts to a tier using the written policy. Pure, no model call."""
    raise NotImplementedError


def declared_facts(review_id: str) -> set[str]:
    """Collect the deterministic facts a tier decision rests on.

    Declared data categories, system access level, and whether the vendor is an AI service.
    These are read from structured fields, never inferred from prose.
    """
    raise NotImplementedError
