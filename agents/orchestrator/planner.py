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

from pydantic import BaseModel

from shared.domain import Vendor
from shared.memory import Dossier


class PlanStep(BaseModel):
    step_id: str
    kind: str
    params: dict = {}


class Plan(BaseModel):
    tier: int
    plan_version: int
    steps: list[PlanStep]
    inherited_keys: dict[str, str] = {}
    domains: list[str] = []


def generate_plan(vendor: Vendor, dossier: Dossier, ctx) -> Plan:
    """Produce the initial plan for a review.

    Raises:
        ValueError: when the model returns a plan that does not validate, or one whose tier
            disagrees with the deterministic tiering rules. Deterministic rules win.
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
