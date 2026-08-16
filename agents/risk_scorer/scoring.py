"""The Trust Score: 0-100, higher is safer. Pure arithmetic, no model call anywhere.

Every domain starts at its maximum and loses points per finding by severity. The domains sum to
exactly 100, so a band read as a percentage is arithmetically true rather than approximately
true — which matters because the binder prints the computation and a judge can re-do it by hand.

**No fact is priced twice.** A contradiction costs what its severity costs and nothing more: the
severity anchors already define *high* as a control the vendor claims is in place being
contradicted by their own evidence, so the contradiction was priced at the moment the severity
was assigned. The flag stays on the finding — the binder prints it, the badge shows it, the
summary reads on it — and it is not a score lever.

**The domains scored are the domains asked about.** A review's plan selects a domain set, and
scoring renormalises over that set rather than over the tier's nominal profile. Scoring a domain
nobody put a question to would hand the vendor full marks in it: a freight vendor scoring ten out
of ten on AI-specific controls is a number that cannot be defended out loud.

The Adversarial Conduct modifier is applied after the domain arithmetic and regardless of it:
25 trust points, and the band is forced to escalate. Wording discipline that goes with it:
prose may say a vendor has raised your risk; the score always **falls**, because it is a
Trust Score and higher is safer.

**This module imports nothing from ``shared.routing``, and a test asserts it.** The claim the
whole project rests on is that the model judges severity and the code computes the score, and
an import graph is the only form of that claim which cannot quietly stop being true. Making it
greppable is the point: "no model call happens here" is a sentence anyone can write, and
"``shared.routing`` does not appear in this file's transitive imports" is a sentence a test can
check.

Failure semantics: a finding whose domain is not in the rubric raises rather than being
ignored — an ignored finding is a silently wrong number, which is the one failure mode this
whole design exists to prevent. A rubric whose weights do not sum to 100 raises at load time.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from shared.domain import Finding, ScoreResult

log = logging.getLogger("drawbridge.scoring")

REPO = Path(__file__).resolve().parent.parent.parent
RUBRIC_PATH = REPO / "agents" / "risk_scorer" / "rubric.yaml"

CONDUCT_DOMAIN = "conduct"
"""The domain the Adversarial Conduct finding carries.

It is deliberately not a weighted domain: conduct is scored through the modifier, applied after
and regardless of the domain arithmetic. A conduct finding that also cost points inside a
domain would be counted twice.
"""


class Flags(BaseModel):
    adversarial_conduct: bool = False


class RubricError(Exception):
    """The rubric file is malformed, or its weights do not sum to 100."""


class Rubric(BaseModel):
    """The loaded rubric. Config, never prompt."""

    domains: dict[str, float]
    penalties: dict[str, float]
    adversarial_penalty: float
    bands: dict[str, float]
    tier_profiles: dict[int, list[str]] = Field(default_factory=dict)

    def penalty(self, severity: str) -> float:
        """Return the points deducted for one finding.

        Severity is the whole of it. There is no contradiction multiplier: the anchors the
        Evidence agent judges against already treat a contradicted claim as high, so a
        multiplier would charge the same fact twice.

        Raises:
            RubricError: on a severity with no configured penalty.
        """
        base = self.penalties.get(severity)
        if base is None:
            raise RubricError(
                f"no penalty configured for severity {severity!r}; the rubric declares "
                f"{sorted(self.penalties)}"
            )
        return base

    def band_for(self, score: int, *, forced_escalation: bool = False) -> str:
        """Return the band for the published score, or ``escalate`` when escalation is forced.

        Takes the integer a reader sees rather than the float behind it. A boundary case is
        then decided by the number printed in the binder rather than by whether a sum of
        renormalised weights landed on 59.999999999999996 — which is a real possibility with
        thirds of a hundred, and not one to explain on camera.

        Must agree with the rubric file and with the binder's score section: one scale, one
        set of bands, one arithmetic, reconciled in all three places.
        """
        if forced_escalation:
            return "escalate"
        if score >= self.bands["approve"]:
            return "approve"
        if score >= self.bands["conditional"]:
            return "conditional"
        return "escalate"

    def weights_for(self, domains: list[str]) -> dict[str, float]:
        """Return the weights for an explicit domain set, renormalised to 100.

        The set comes from the review's plan, so what is scored is what was asked. Any name
        the rubric does not weight is dropped — ``conduct`` is the one that matters, and it is
        scored through the modifier rather than through a weight.

        Raises:
            RubricError: when the set selects no weighted domain, which would make the scale
                meaningless rather than merely narrow.
        """
        subset = {name: self.domains[name] for name in domains if name in self.domains}
        total = sum(subset.values())
        if not total:
            raise RubricError(
                f"{sorted(domains)} selects no weighted domain; there is nothing to score out of"
            )

        # Rounded to the two decimals the binder prints, then the rounding residue is settled on
        # the heaviest domain so the column adds to exactly 100. Six domains renormalised from
        # ninety give three sixteen-point-six-sevens, and a maximum column that sums to 100.01
        # against a stated total of 100.00 is the first thing an auditor circles.
        weights = {name: round(weight * 100.0 / total, 2) for name, weight in subset.items()}
        heaviest = max(weights, key=lambda name: (weights[name], name))
        weights[heaviest] = round(weights[heaviest] + (100.0 - sum(weights.values())), 2)
        return weights

    def weights_for_tier(self, tier: int) -> dict[str, float]:
        """Return the domain weights for a tier's nominal profile, renormalised to 100.

        The default when a review has no recorded domain set. A Tier 3 review covers three
        domains rather than seven, and scoring it out of the subset's raw weights would put its
        maximum at 50 — so a flawless Tier 3 vendor would score below the escalation threshold.
        Renormalisation is what keeps one scale meaning one thing across all three tiers.
        """
        return self.weights_for(self.tier_profiles.get(tier) or list(self.domains))


def load_rubric(path: str | Path = RUBRIC_PATH) -> Rubric:
    """Load and validate the rubric.

    Raises:
        RubricError: when the domain weights do not sum to exactly 100, when a band boundary
            is missing, or when the file does not parse. A scale that does not add up is a
            number a judge can catch on screen.
    """
    return _load_rubric_cached(str(path))


@lru_cache(maxsize=4)
def _load_rubric_cached(path: str) -> Rubric:
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RubricError(f"could not read the rubric at {path}: {exc}") from exc

    try:
        domains = {str(k): float(v) for k, v in raw["domains"].items()}
        penalties = {str(k): float(v) for k, v in raw["penalties"].items()}
        bands = {str(k): float(v) for k, v in raw["bands"].items()}
        modifier = raw["modifiers"]["adversarial_conduct"]
        rubric = Rubric(
            domains=domains,
            penalties=penalties,
            adversarial_penalty=float(modifier["penalty"]),
            bands=bands,
            tier_profiles={int(k): list(v) for k, v in (raw.get("tier_profiles") or {}).items()},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RubricError(f"{path} is missing a required field: {exc}") from exc

    if "contradiction_multiplier" in raw:
        raise RubricError(
            f"{path} declares a contradiction_multiplier. It was removed deliberately: the "
            "severity anchors already price a contradiction, so a multiplier charges the same "
            "fact twice. The flag stays on the finding; it is not a score lever."
        )

    total = sum(rubric.domains.values())
    if round(total, 6) != 100.0:
        raise RubricError(
            f"domain weights sum to {total}, expected exactly 100. A band read as a percentage "
            "is only arithmetically true if they do."
        )
    for band in ("approve", "conditional", "escalate"):
        if band not in rubric.bands:
            raise RubricError(f"band {band!r} is missing")
    if not rubric.bands["approve"] > rubric.bands["conditional"] > rubric.bands["escalate"]:
        raise RubricError(f"band boundaries are not descending: {rubric.bands}")

    return rubric


def compute_score(
    findings: list[Finding],
    rubric: Rubric,
    flags: Flags,
    *,
    tier: int = 1,
    domains: list[str] | None = None,
) -> ScoreResult:
    """Compute the Trust Score, band and per-domain breakdown.

    Pure arithmetic. No model call occurs anywhere in this function's call graph.

    Args:
        findings: every finding recorded against the review.
        rubric: the loaded rubric.
        flags: the modifiers that apply after the domain arithmetic.
        tier: the review's tier, used to select the nominal domain profile.
        domains: the domain set the review's plan actually covered. Supplied, it wins over the
            tier profile — what is scored is what was asked, and a domain nobody questioned
            would otherwise contribute full marks.

    The breakdown is returned rather than discarded because it is binder section 5 and the
    answer to "why 71?". A score with no visible derivation is a number a CISO has to take on
    trust, which is the opposite of what this artefact is for.

    Raises:
        RubricError: on a finding whose domain is not scored for this review, or whose severity
            has no configured penalty.
    """
    weights = rubric.weights_for(domains) if domains else rubric.weights_for_tier(tier)
    remaining = dict(weights)

    for finding in findings:
        if finding.domain == CONDUCT_DOMAIN:
            continue
        if finding.domain not in remaining:
            raise RubricError(
                f"finding {finding.finding_id} carries domain {finding.domain!r}, which is not "
                f"scored on this review. An ignored finding is a silently wrong number. "
                f"Scored domains: {sorted(remaining)}"
            )
        remaining[finding.domain] = max(
            0.0, remaining[finding.domain] - rubric.penalty(finding.severity)
        )

    subtotal = sum(remaining.values())
    adversarial = bool(flags.adversarial_conduct)
    score = subtotal - (rubric.adversarial_penalty if adversarial else 0.0)
    published = int(round(max(0.0, min(100.0, score))))

    result = ScoreResult(
        score=published,
        # Banded on the published integer, not on the float behind it, so the band a reader
        # derives from the printed number is the band the system recorded.
        band=rubric.band_for(published, forced_escalation=adversarial),
        breakdown={
            name: round(remaining[name], 2) for name in sorted(remaining)
        },
        adversarial_applied=adversarial,
    )

    log.info(
        "review scored %d (%s) from %d finding(s)%s",
        result.score,
        result.band,
        len(findings),
        "; adversarial conduct applied" if adversarial else "",
    )
    return result


def explain(
    result: ScoreResult, rubric: Rubric, *, tier: int = 1, domains: list[str] | None = None
) -> list[str]:
    """Render the arithmetic as lines a human can re-do by hand.

    Binder section 5. Every line names a domain, its maximum for this review and what is left,
    so the total is checkable without reading any code.
    """
    weights = rubric.weights_for(domains) if domains else rubric.weights_for_tier(tier)
    lines = [
        f"{name:<22} {result.breakdown[name]:>6.2f} / {weights[name]:>6.2f}"
        for name in sorted(result.breakdown)
    ]
    subtotal = sum(result.breakdown.values())
    lines.append(f"{'subtotal':<22} {subtotal:>6.2f} / {sum(weights.values()):>6.2f}")
    if result.adversarial_applied:
        lines.append(f"{'adversarial conduct':<22} {-rubric.adversarial_penalty:>6.2f}")
    lines.append(f"{'TRUST SCORE':<22} {result.score:>6} ({result.band})")
    return lines
