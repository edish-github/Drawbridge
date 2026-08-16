"""Three synthetic vendors, three distinct outcomes — checked as arithmetic, not as a hope.

The pack exists to produce one vendor in each band. If two of them collapse into the same band
the calibration argument goes with them, and so do the gate card, the memo's "mitigations
required for conditional approval" structure and the 2:50 demo beat.

This runs the real rubric over each vendor's declared findings rather than over a run, for two
reasons. CleanCloud and NimbusWrite cannot complete a local run — NimbusWrite is deliberately
unseeded because its evidence must go through real screening — so a test that needed a run
would only ever cover one of the three. And a band that depends on a model's judgement is not
something a test can pin; a band that depends on declared severities is.

No emulator and no model. The severities come from ``expected.json``; the arithmetic over them
is the thing under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.orchestrator.planner import domains_for
from agents.risk_scorer.scoring import Flags, compute_score, explain, load_rubric
from shared.domain import Finding

REPO = Path(__file__).resolve().parent.parent
PACK = REPO / "synthetic-vendors"

MARGIN = 4
"""Points a score must sit inside its band at every boundary the band has.

A vendor sitting on 60.00 is in the conditional band and is one medium finding away from not
being. Asserting the band alone would let a later change to a finding set reclassify a vendor
silently, and the reclassification is the thing that would break the demo rather than the
arithmetic. This is the assertion that fails first.
"""


def band_bounds(band: str) -> tuple[float, float]:
    """Return the inclusive score range a band covers."""
    bands = load_rubric().bands
    return {
        "approve": (bands["approve"], 100.0),
        "conditional": (bands["conditional"], bands["approve"] - 1),
        "escalate": (0.0, bands["conditional"] - 1),
    }[band]


def assert_inside_band(score: int, band: str) -> None:
    low, high = band_bounds(band)
    assert score - low >= MARGIN, f"{score} is within {MARGIN} of the bottom of {band} ({low})"
    assert high - score >= MARGIN, f"{score} is within {MARGIN} of the top of {band} ({high})"


def pack(slug: str) -> tuple[dict, dict]:
    """Return one vendor's profile and expectations."""
    return (
        json.loads((PACK / slug / "profile.json").read_text()),
        json.loads((PACK / slug / "expected.json").read_text()),
    )


def declared_findings(slug: str) -> list[Finding]:
    """Build findings from the vendor's declared expectations, severities and all."""
    _, expected = pack(slug)
    return [
        Finding(
            finding_id=f"{slug}:{spec['id']}",
            review_id=slug,
            domain=spec["domain"],
            severity=spec["severity"],
            source=spec["source"],
            contradiction=bool(spec.get("contradiction")),
            summary=spec.get("note", spec["id"]),
        )
        for spec in expected.get("required_findings", [])
    ]


def score_for(slug: str, *, adversarial: bool | None = None):
    """Score one vendor against the domain set its final tier and its nature would ask about."""
    profile, expected = pack(slug)
    tier = int(expected["tier"]["final"])
    domains = domains_for(tier, is_ai_vendor=bool(profile.get("is_ai_vendor")))
    flags = Flags(
        adversarial_conduct=(
            expected["flags"]["adversarial_conduct"] if adversarial is None else adversarial
        )
    )
    return compute_score(
        declared_findings(slug), load_rubric(), flags, tier=tier, domains=domains
    )


# --- The three bands ----------------------------------------------------------------------


def test_cleancloud_approves():
    """The control vendor. Three minor gaps and nothing worse, which is what a good vendor looks
    like — a hundred out of a hundred reads as a fixture built to pass rather than as a pass."""
    result = score_for("cleancloud")
    _, expected = pack("cleancloud")

    assert expected["score"]["min"] <= result.score <= expected["score"]["max"]
    assert result.band == "approve"
    assert_inside_band(result.score, "approve")


def test_cleancloud_still_approves_at_the_worst_it_is_allowed_to_be():
    """The pack permits at most one finding, medium at worst. That must not cost the band."""
    rubric = load_rubric()
    profile, _ = pack("cleancloud")
    domains = domains_for(2, is_ai_vendor=bool(profile.get("is_ai_vendor")))
    worst = Finding(
        finding_id="cleancloud:worst",
        review_id="cleancloud",
        domain="compliance_posture",
        severity="medium",
        source="rule",
        summary="the worst finding the pack allows against the control vendor",
    )

    result = compute_score([worst], rubric, Flags(), tier=2, domains=domains)

    assert result.score >= 80
    assert result.band == "approve"


def test_datadynamo_is_conditional():
    """The middle band. Without it there is no gate card worth signing and no calibration
    argument, because two of three vendors would escalate."""
    result = score_for("datadynamo")
    _, expected = pack("datadynamo")

    assert expected["score"]["min"] <= result.score <= expected["score"]["max"]
    assert result.band == "conditional"
    assert_inside_band(result.score, "conditional")


def test_nimbuswrite_escalates():
    result = score_for("nimbuswrite")
    _, expected = pack("nimbuswrite")

    assert result.score <= expected["score"]["max"]
    assert result.band == "escalate"
    assert_inside_band(result.score, "escalate")


def test_the_arithmetic_alone_puts_nimbuswrite_in_trouble():
    """A vendor the modifier rescues from an approve band would make the -25 look like the whole
    story. The evidence has to say the same thing the conduct flag says, only more quietly."""
    arithmetic = score_for("nimbuswrite", adversarial=False)
    _, expected = pack("nimbuswrite")
    declared = expected["score"]["arithmetic_before_modifier"]

    assert declared["min"] <= arithmetic.score <= declared["max"]
    assert arithmetic.band == declared["band"]


def test_nimbuswrite_escalates_because_escalation_was_forced():
    """Recorded rather than asserted as a target: the arithmetic alone does not reach escalate,
    and the modifier is what takes it there. That is the pack's stated design and it is what
    makes the -25 visible on camera as a cause rather than as a rounding difference."""
    with_modifier = score_for("nimbuswrite")
    without = score_for("nimbuswrite", adversarial=False)

    assert without.score - with_modifier.score == 25
    assert without.band != "escalate"
    assert with_modifier.band == "escalate"
    assert with_modifier.adversarial_applied is True


# --- The three are distinct -----------------------------------------------------------------


def test_the_three_vendors_land_in_three_different_bands():
    """The single assertion the whole pack exists to satisfy."""
    bands = {slug: score_for(slug).band for slug in ("cleancloud", "datadynamo", "nimbuswrite")}

    assert len(set(bands.values())) == 3, bands


@pytest.mark.parametrize("slug", ["cleancloud", "datadynamo", "nimbuswrite"])
def test_every_declared_finding_reaches_the_score(slug):
    """A finding the rubric cannot map would be a silently wrong number, which is the one
    failure this design exists to prevent."""
    profile, expected = pack(slug)
    rubric = load_rubric()
    tier = int(expected["tier"]["final"])
    scored = set(
        rubric.weights_for(domains_for(tier, is_ai_vendor=bool(profile.get("is_ai_vendor"))))
    )

    for spec in expected.get("required_findings", []):
        assert spec["domain"] in scored | {"conduct"}, spec["id"]


@pytest.mark.parametrize("slug", ["cleancloud", "datadynamo", "nimbuswrite"])
def test_the_arithmetic_is_printable(slug):
    """Binder section 5 renders for every vendor in the pack, not only the one that runs."""
    profile, expected = pack(slug)
    tier = int(expected["tier"]["final"])
    domains = domains_for(tier, is_ai_vendor=bool(profile.get("is_ai_vendor")))
    result = score_for(slug)

    lines = explain(result, load_rubric(), tier=tier, domains=domains)

    assert lines[-1].startswith("TRUST SCORE")
    assert len(lines) == len(domains) + (3 if result.adversarial_applied else 2)
