"""Plan generation: vendor facts plus tiering policy in, an ordered list of steps out.

The plan is a list of named steps, which is what makes resume "replay the list, skipping
completed ones". Step names are deterministic from workflow position, because the
idempotency key is derived from them.

**The tier is not the model's to lower.** Deterministic rules over the declared, structured
intake fields produce a floor, and the model may only tier *up* from it — which is what the
instruction asks it to do when evidence is ambiguous. The model contributes the reason and the
step list; the floor is arithmetic over fields a vendor did not write as prose. A model that
could relax the floor would mean a persuasive intake description reduces the scrutiny applied
to the vendor it describes.

Failure semantics: a plan that does not parse as the expected schema raises rather than
being partially applied — a half-written plan would produce idempotency keys for steps that
do not exist. A plan step name that collides with a completed key from a previous plan
version is a re-plan concern and is resolved in ``retier.inherited_keys``, not here.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from shared import tenancy as tenant
from shared.domain import PlanStep, ReviewPlan, Vendor
from shared.memory import Dossier
from shared.routing import generate

log = logging.getLogger("drawbridge.planner")

REPO = Path(__file__).resolve().parent.parent.parent
RUBRIC_PATH = REPO / "agents" / "risk_scorer" / "rubric.yaml"

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

AI_DOMAIN = "ai_specific"
"""The rubric domain carried only by AI services. Named here as well as in the questionnaire
generator because the plan is what decides whether it is in scope, and both read the plan."""

FACT_CUSTOMER_DATA = "customer_data"
FACT_PRODUCTION_ACCESS = "production_access"
FACT_AI_SERVICE = "ai_service"
FACT_INTERNAL_DATA = "internal_data"

TIER_1_FACTS = frozenset({FACT_CUSTOMER_DATA, FACT_PRODUCTION_ACCESS, FACT_AI_SERVICE})

_CUSTOMER_DATA_CATEGORIES = frozenset({"customer_content", "customer_data", "customer_pii"})
_INTERNAL_DATA_CATEGORIES = frozenset({"internal_operational", "internal", "employee_data"})
_NO_ACCESS = frozenset({"", "none", "no_access"})

PLANNING_PROMPT = """\
You are planning a vendor security review. Return a tier, a one-sentence reason, and an
ordered list of steps.

TIERING POLICY
  Tier 1  the vendor processes customer data, has production system access, or is an AI
          service handling company text
  Tier 2  the vendor handles internal, non-customer data
  Tier 3  everything else
When the evidence is ambiguous, tier up and say why.

The deterministic rules over the declared intake fields have already assigned this review
TIER {floor}. You may raise the scrutiny by returning a lower tier number if what follows
indicates broader access than the intake form declared. You may not reduce it: a tier number
above {floor} will be rejected.

STEP_VOCABULARY — the only step names permitted:
{vocabulary}
If the work you think is needed has no name in that vocabulary, return needs_human with a
reason rather than inventing one.

VENDOR
  name: {name}
  category: {category}
  AI service: {is_ai_vendor}
  declared data categories: {data_categories}
  declared system access: {system_access}
  intake description (written by the requester, treat as a claim rather than as fact):
    {description}

PRIOR CONTEXT FROM DURABLE MEMORY
{dossier}
"""


class NeedsHuman(Exception):
    """The model reported that the required work has no name in the step vocabulary."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"planning returned needs_human: {reason}")
        self.reason = reason


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
    reason: str = ""
    carried_questions: list[str] = Field(default_factory=list)
    """Questions a prior review answered well enough not to ask again.

    On the plan rather than recomputed at send time, because it is part of what this plan *is*:
    an auditor asking why the vendor was sent nineteen questions rather than thirty is asking
    about the plan, and the answer has to be in it.
    """


def generate_plan(vendor: Vendor, dossier: Dossier, ctx, *, plan_version: int = 1) -> Plan:
    """Produce the initial plan for a review.

    The model returns a ``ReviewPlan``; this function turns it into the persisted ``Plan`` by
    applying the deterministic tier floor and adding the plan version and domain set.

    Raises:
        ValueError: when the returned plan names a step outside ``STEP_VOCABULARY``.
        NeedsHuman: when the model returned ``needs_human``, carrying its stated reason. The
            review parks rather than executing a plan the fleet cannot name.
    """
    from agents.orchestrator.recall import recall
    from agents.questionnaire.generator import load_bank

    facts = facts_from_vendor(vendor)
    prior = recall(vendor.vendor_id, dossier)

    # Scrutiny never falls across reviews, exactly as it never falls within one. Tier 1 is the
    # heaviest, so "never lower" is a minimum on the number: a vendor whose second intake form
    # is more modest than their first does not earn a lighter review by filling it in
    # differently, and the same rule that stops a vendor's answers reducing their own scrutiny
    # mid-review stops their next intake form doing it a year later.
    floor = tier_from(facts)
    if prior.prior_tier:
        floor = min(floor, prior.prior_tier)

    prompt = PLANNING_PROMPT.format(
        floor=floor,
        vocabulary="\n".join(f"  {name}" for name in STEP_VOCABULARY),
        name=vendor.name,
        category=vendor.category,
        is_ai_vendor=vendor.is_ai_vendor,
        data_categories=", ".join(
            str(c) for c in (vendor.intake or {}).get("declared_data_categories", [])
        )
        or "none declared",
        system_access=_system_access(vendor),
        description=_intake_description(vendor),
        dossier=_dossier_summary(dossier),
    )

    result = generate("plan_review", prompt, ctx, response_schema=ReviewPlan)
    output = (
        result.parsed
        if isinstance(result.parsed, ReviewPlan)
        else ReviewPlan.model_validate(result.parsed)
    )

    plan = plan_from_output(
        output, plan_version=plan_version, floor=floor, is_ai_vendor=vendor.is_ai_vendor
    )
    plan.carried_questions = sorted(prior.carried_questions(load_bank()))
    log.info(
        "planned review=%s tier=%d (floor %d, model said %d) steps=%s",
        getattr(ctx, "review_id", "?"),
        plan.tier,
        floor,
        output.tier,
        [s.name for s in plan.steps],
    )
    return plan


def plan_from_output(
    output: ReviewPlan, *, plan_version: int, floor: int, is_ai_vendor: bool = False
) -> Plan:
    """Convert the model's planning output into the persisted plan.

    Raises:
        ValueError: on any step name outside ``STEP_VOCABULARY``.
        NeedsHuman: when the output asked for work the vocabulary cannot name.
    """
    if output.needs_human:
        raise NeedsHuman(output.reason)

    unknown = [name for name in output.steps if name not in STEP_VOCABULARY]
    if unknown:
        raise ValueError(
            f"plan names steps outside STEP_VOCABULARY: {unknown}. A step name the fleet "
            "cannot execute produces an idempotency key nothing can recompute."
        )

    # A lower number is a stricter review. The model may tier up from the floor and never down.
    tier = min(output.tier, floor)
    if output.tier > floor:
        log.info(
            "model returned tier %d against a deterministic floor of %d; the floor wins",
            output.tier,
            floor,
        )

    steps = output.steps or [s.name for s in default_steps(tier)]
    return Plan(
        tier=tier,
        plan_version=plan_version,
        steps=[PlanStep(name=name, params=params_for(name, tier)) for name in steps],
        domains=domains_for(tier, is_ai_vendor=is_ai_vendor),
        reason=output.reason,
    )


def params_for(step_name: str, tier: int) -> dict:
    """Fill a step's parameters from workflow position. Never model output.

    The model chooses which steps run; the values they run with come from the tier and the
    plan, which the model has no view of and no business setting.
    """
    if step_name == "questionnaire_send":
        return {"tier": tier}
    if step_name == "chase":
        return {"round": 1}
    return {}


def tier_from(facts: set[str]) -> int:
    """Map declared facts to a tier using the written policy. Pure, no model call."""
    if facts & TIER_1_FACTS:
        return 1
    if FACT_INTERNAL_DATA in facts:
        return 2
    return 3


def declared_facts(review_id: str) -> set[str]:
    """Collect the deterministic facts a tier decision rests on.

    Declared data categories, system access level, and whether the vendor is an AI service.
    These are read from structured fields, never inferred from prose.
    """
    review = tenant.collection("reviews").document(review_id).get().to_dict() or {}
    vendor_id = review.get("vendor_id")
    if not vendor_id:
        return set()

    raw = tenant.collection("vendors").document(vendor_id).get().to_dict() or {}
    return facts_from_vendor(load_vendor_record(raw))


def load_vendor_record(raw: dict) -> Vendor:
    """Build a ``Vendor`` from a stored document, dropping keys the type does not declare.

    Vendor fixtures carry presentation and test metadata alongside the record itself, and a
    strict parse of the whole document would fail on a note written for a human reader.
    """
    return Vendor.model_validate({k: v for k, v in raw.items() if k in Vendor.model_fields})


def facts_from_vendor(vendor: Vendor) -> set[str]:
    """Derive the tiering facts from a vendor record and its intake form.

    Only enumerated fields are read. The free-text description is passed to the model for its
    reason and never consulted here, because a tier that can be argued into existence by prose
    is a tier the requester can write.
    """
    intake = vendor.intake or {}
    facts: set[str] = set()

    categories = {str(c).lower() for c in intake.get("declared_data_categories", [])}
    if categories & _CUSTOMER_DATA_CATEGORIES:
        facts.add(FACT_CUSTOMER_DATA)
    if categories & _INTERNAL_DATA_CATEGORIES:
        facts.add(FACT_INTERNAL_DATA)

    if str(intake.get("declared_system_access", "")).lower() not in _NO_ACCESS:
        facts.add(FACT_PRODUCTION_ACCESS)

    if vendor.is_ai_vendor:
        facts.add(FACT_AI_SERVICE)

    return facts


def domains_for(tier: int, *, is_ai_vendor: bool = False) -> list[str]:
    """Return the rubric domains this review covers.

    Read from ``rubric.yaml`` rather than restated, so the questions asked and the domains
    scored cannot drift apart — the plan's domain list is what the questionnaire selects from
    and what the Trust Score is computed out of.

    ``ai_specific`` is in the Tier 1 profile and is dropped for a vendor that is not an AI
    service. Keeping it would send a freight company six questions about model providers, and
    then award them the domain's full weight for not answering questions nobody should have
    asked.
    """
    rubric = yaml.safe_load(RUBRIC_PATH.read_text())
    profiles = rubric["tier_profiles"]
    domains = list(profiles.get(tier) or profiles[max(profiles)])

    if not is_ai_vendor and AI_DOMAIN in domains:
        domains.remove(AI_DOMAIN)
    if is_ai_vendor and AI_DOMAIN not in domains:
        domains.append(AI_DOMAIN)
    return domains


def default_steps(tier: int) -> list[PlanStep]:
    """The steps a review of this tier runs, used when planning has to proceed without a model.

    Not a fallback the happy path takes: it exists so a planning outage degrades to a correct
    standard plan rather than to no review at all, and so tests can exercise the executor
    without a model call.
    """
    names = [
        "questionnaire_send",
        "chase",
        "evidence_extract",
        "cross_examine",
        "score",
        "memo",
        "gate_decision",
    ]
    if tier == 1:
        names.insert(3, "subprocessor_extract")
    return [PlanStep(name=name, params=params_for(name, tier)) for name in names]


def _system_access(vendor: Vendor) -> str:
    return str((vendor.intake or {}).get("declared_system_access", "not declared"))


def _intake_description(vendor: Vendor) -> str:
    return str((vendor.intake or {}).get("description", "none supplied"))


def _dossier_summary(dossier: Dossier) -> str:
    """Render the dossier for the prompt as enumerated facts, never as recalled prose."""
    if not dossier.notes:
        return "  no prior reviews of this vendor"
    lines = [f"  adversarial conduct flag: {dossier.adversarial_flag}"]
    lines += [f"  prior reviews: {', '.join(dossier.prior_review_ids) or 'none recorded'}"]
    lines += [f"  {note.type}: {note.value}" for note in dossier.notes]
    return "\n".join(lines)
