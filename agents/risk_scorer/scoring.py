"""The Trust Score: 0–100, higher is safer. Pure arithmetic, no model call anywhere.

Every domain starts at its maximum and loses points per finding by severity and contradiction
flag. The domains sum to exactly 100, so a band read as a percentage is arithmetically true
rather than approximately true — which matters because the binder prints the computation and
a judge can re-do it by hand.

The Adversarial Conduct modifier is applied after the domain arithmetic and regardless of it:
25 trust points, and the band is forced to escalate. Wording discipline that goes with it:
prose may say a vendor has raised your risk; the score always **falls**, because it is a
Trust Score and higher is safer.

Failure semantics: a finding whose domain is not in the rubric raises rather than being
ignored — an ignored finding is a silently wrong number, which is the one failure mode this
whole design exists to prevent. A rubric whose weights do not sum to 100 raises at load time.
No function in this module's call graph makes a model call, and that is the property the
tests assert.
"""

from __future__ import annotations

from pydantic import BaseModel

from shared.domain import Finding, ScoreResult


class Flags(BaseModel):
    adversarial_conduct: bool = False


class RubricError(Exception):
    """The rubric file is malformed, or its weights do not sum to 100."""


class Rubric(BaseModel):
    """The loaded rubric. Config, never prompt."""

    domains: dict[str, float]
    penalties: dict[str, float]
    contradiction_multiplier: float
    adversarial_penalty: float
    bands: dict[str, float]

    def penalty(self, severity: str, contradiction: bool) -> float:
        """Return the points deducted for one finding.

        Raises:
            RubricError: on a severity with no configured penalty.
        """
        raise NotImplementedError

    def band_for(self, score: float, *, forced_escalation: bool = False) -> str:
        """Return the band for ``score``, or ``escalate`` when escalation is forced.

        Must agree with the rubric file and with the binder's score section: one scale, one
        set of bands, one arithmetic, reconciled in all three places.
        """
        raise NotImplementedError


def load_rubric(path: str = "agents/risk_scorer/rubric.yaml") -> Rubric:
    """Load and validate the rubric.

    Raises:
        RubricError: when the domain weights do not sum to exactly 100, when a band boundary
            is missing, or when the file does not parse. A scale that does not add up is a
            number a judge can catch on screen.
    """
    raise NotImplementedError


def compute_score(findings: list[Finding], rubric: Rubric, flags: Flags) -> ScoreResult:
    """Compute the Trust Score, band and per-domain breakdown.

    Pure arithmetic. No model call occurs anywhere in this function's call graph.

    Raises:
        RubricError: on a finding whose domain is not in the rubric, or whose severity has no
            configured penalty.
    """
    raise NotImplementedError
