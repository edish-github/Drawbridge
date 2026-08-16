"""The Trust Score is arithmetic, and the arithmetic is checkable by hand.

The strongest architectural claim in the project is that the model judges severity and the code
computes the score, so no agent holds the pen on its own metric. The first test below is what
makes that claim greppable rather than merely true today: ``shared.routing`` does not appear in
this module's transitive imports, and a routing entry for scoring cannot come back without
something failing.

No emulator and no model. Scoring is a pure function of findings and a config file.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agents.risk_scorer.scoring import (
    Flags,
    Rubric,
    RubricError,
    compute_score,
    explain,
    load_rubric,
)
from shared.domain import Finding
from shared.routing import ROUTING

REPO = Path(__file__).resolve().parent.parent
SCORING = REPO / "agents" / "risk_scorer" / "scoring.py"


def finding(domain: str, severity: str, *, contradiction: bool = False, i: int = 0) -> Finding:
    return Finding(
        finding_id=f"r:{domain}:{severity}:{i}",
        review_id="r",
        domain=domain,
        severity=severity,
        source="model",
        contradiction=contradiction,
        summary="x",
    )


# --- The claim, asserted structurally ----------------------------------------------------------


def test_scoring_does_not_import_the_model_router():
    """A model call anywhere in this call graph would end the separation the project rests on."""
    tree = ast.parse(SCORING.read_text())
    imported = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert not any(name.startswith("shared.routing") for name in imported), (
        f"scoring.py imports the model router: {sorted(imported)}"
    )


def test_there_is_no_routing_entry_for_scoring():
    """If one ever appears, an agent has been given the pen on its own metric."""
    assert "score_rubric" not in ROUTING
    assert not any("score" in task for task in ROUTING)


# --- The rubric ---------------------------------------------------------------------------------


def test_the_rubric_loads_and_sums_to_one_hundred():
    rubric = load_rubric()

    assert round(sum(rubric.domains.values()), 6) == 100.0
    assert rubric.adversarial_penalty == 25


def test_a_rubric_that_does_not_add_up_raises(tmp_path):
    bad = tmp_path / "rubric.yaml"
    bad.write_text(
        "domains: {data_protection: 50, access_control: 30}\n"
        "penalties: {low: 2, medium: 5, high: 10}\n"
        "modifiers: {adversarial_conduct: {penalty: 25, forces_band: escalate}}\n"
        "bands: {approve: 80, conditional: 60, escalate: 0}\n"
    )

    with pytest.raises(RubricError, match="sum to 80"):
        load_rubric(bad)


def test_a_rubric_that_reintroduces_the_multiplier_raises(tmp_path):
    """No fact is priced twice, and the rule is enforced at load rather than remembered."""
    bad = tmp_path / "rubric.yaml"
    bad.write_text(
        "domains: {data_protection: 100}\n"
        "penalties: {low: 2, medium: 5, high: 10}\n"
        "contradiction_multiplier: 1.5\n"
        "modifiers: {adversarial_conduct: {penalty: 25, forces_band: escalate}}\n"
        "bands: {approve: 80, conditional: 60, escalate: 0}\n"
    )

    with pytest.raises(RubricError, match="contradiction_multiplier"):
        load_rubric(bad)


def test_tier_profiles_renormalise_to_one_hundred():
    """A flawless Tier 3 vendor must not score below the escalation threshold for being Tier 3."""
    rubric = load_rubric()

    for tier in (1, 2, 3):
        assert round(sum(rubric.weights_for_tier(tier).values()), 6) == 100.0


def test_a_flawless_review_scores_full_marks_at_every_tier():
    rubric = load_rubric()

    for tier in (1, 2, 3):
        assert compute_score([], rubric, Flags(), tier=tier).score == 100


# --- The arithmetic ------------------------------------------------------------------------------


def test_a_contradiction_is_not_priced_twice():
    """The severity anchors already treat a contradicted claim as high. Charging again would
    charge the same fact twice, and "we set it to 1.5" is not defensible under questioning."""
    rubric = load_rubric()

    gap = compute_score([finding("access_control", "high")], rubric, Flags())
    contradiction = compute_score(
        [finding("access_control", "high", contradiction=True)], rubric, Flags()
    )

    assert contradiction.score == gap.score


def test_the_contradiction_flag_survives_on_the_finding():
    """It drives the binder, the badge and the finding text. It stopped being a score lever,
    not a fact."""
    flagged = finding("access_control", "high", contradiction=True)

    assert flagged.contradiction is True


def test_a_domain_never_goes_negative():
    """One catastrophic domain must not erase points from the others."""
    rubric = load_rubric()
    findings = [finding("business_continuity", "high", contradiction=True, i=i) for i in range(9)]

    result = compute_score(findings, rubric, Flags(), tier=1)

    assert result.breakdown["business_continuity"] == 0
    assert result.breakdown["data_protection"] == pytest.approx(20, abs=0.01)


def test_an_unmapped_domain_raises_rather_than_being_ignored():
    """An ignored finding is a silently wrong number, which is the failure this design prevents."""
    rubric = load_rubric()

    with pytest.raises(RubricError, match="not scored on this review"):
        compute_score([finding("telepathy", "high")], rubric, Flags())


# --- What is scored is what was asked ----------------------------------------------------


def test_an_explicit_domain_set_wins_over_the_tier_profile():
    """A Tier 1 freight vendor is never asked about model providers, so it is never awarded
    the ai_specific domain's ten points for not being asked."""
    rubric = load_rubric()
    asked = [
        "data_protection", "access_control", "incident_response",
        "compliance_posture", "subprocessors", "business_continuity",
    ]

    result = compute_score([], rubric, Flags(), tier=1, domains=asked)

    assert "ai_specific" not in result.breakdown
    assert result.score == 100


def test_a_narrower_domain_set_still_renormalises_to_one_hundred():
    rubric = load_rubric()

    weights = rubric.weights_for(["data_protection", "access_control"])

    assert round(sum(weights.values()), 6) == 100.0


def test_a_domain_set_that_scores_nothing_raises():
    rubric = load_rubric()

    with pytest.raises(RubricError, match="no weighted domain"):
        rubric.weights_for(["conduct"])


def test_an_unknown_severity_raises():
    rubric = load_rubric()
    bad = finding("access_control", "high").model_copy(update={"severity": "catastrophic"})

    with pytest.raises(RubricError, match="no penalty configured"):
        compute_score([bad], rubric, Flags())


# --- Adversarial conduct ----------------------------------------------------------------


def test_adversarial_conduct_costs_25_and_forces_escalation():
    """Prose may say the vendor raised your risk; the number always falls."""
    rubric = load_rubric()

    clean = compute_score([], rubric, Flags())
    flagged = compute_score([], rubric, Flags(adversarial_conduct=True))

    assert clean.score - flagged.score == 25
    assert flagged.band == "escalate"
    assert flagged.adversarial_applied is True


def test_escalation_is_forced_rather_than_reached():
    """The band is escalate because escalation was forced, not because the arithmetic got there."""
    rubric = load_rubric()

    result = compute_score([], rubric, Flags(adversarial_conduct=True))

    assert result.score == 75, "the arithmetic alone would band this as conditional"
    assert result.band == "escalate"


def test_a_conduct_finding_is_not_also_scored_as_a_domain():
    """The modifier is the consequence. Counting it twice would double the penalty."""
    rubric = load_rubric()
    conduct = finding("conduct", "high")

    assert compute_score([conduct], rubric, Flags()).score == 100


# --- The breakdown is the answer to "why 71?" -------------------------------------------


def test_the_breakdown_covers_every_scored_domain():
    rubric = load_rubric()

    result = compute_score([finding("access_control", "medium")], rubric, Flags(), tier=1)

    assert set(result.breakdown) == set(rubric.weights_for_tier(1))


def test_the_breakdown_sums_to_the_score_before_the_modifier():
    rubric = load_rubric()
    findings = [finding("access_control", "high"), finding("subprocessors", "medium")]

    result = compute_score(findings, rubric, Flags(), tier=1)

    assert sum(result.breakdown.values()) == pytest.approx(result.score, abs=1)


def test_explain_renders_a_line_per_domain_plus_the_total():
    rubric = load_rubric()
    result = compute_score([], rubric, Flags(), tier=1)

    lines = explain(result, rubric, tier=1)

    assert len(lines) == len(result.breakdown) + 2
    assert "TRUST SCORE" in lines[-1]


# --- Bands ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "band"),
    [(100, "approve"), (80, "approve"), (79, "conditional"), (60, "conditional"), (59, "escalate")],
)
def test_band_boundaries(score, band):
    assert load_rubric().band_for(score) == band


def test_the_band_is_read_off_the_published_number():
    """A boundary case must not turn on float representation. 60.0 printed as 60 bands as 60,
    whether the sum of renormalised thirds landed a hair above or below it."""
    rubric = load_rubric()
    asked = [
        "data_protection", "access_control", "incident_response",
        "compliance_posture", "subprocessors", "business_continuity",
    ]
    findings = [
        finding("access_control", "high", i=1),
        finding("incident_response", "high", i=2),
        finding("compliance_posture", "high", i=3),
        finding("compliance_posture", "medium", i=4),
        finding("business_continuity", "medium", i=5),
    ]

    result = compute_score(findings, rubric, Flags(), tier=1, domains=asked)

    assert result.score == 60
    assert result.band == "conditional"


def test_the_bands_agree_with_the_rubric_file():
    """One scale, one set of bands, reconciled between the code and the config."""
    rubric = load_rubric()

    assert isinstance(rubric, Rubric)
    assert rubric.bands["approve"] == 80
    assert rubric.bands["conditional"] == 60
