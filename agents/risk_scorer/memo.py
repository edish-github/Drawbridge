"""The risk memo, and the Adversarial Conduct signal.

One deep-model call per review. Input: findings, the score breakdown, vendor context and the
prior dossier. Output: a one-page memo written for a CISO with a fixed structure — the
recommendation, the three things that drove it, the mitigations required for conditional
approval, and what to re-check in 90 days. This is the single artefact a human reads before
approving, which is why it gets the expensive model.

The memo is screened before a human reads it. It is the only control in the system that
assumes every earlier one failed: if an injected instruction ever survived into a memo —
steering the recommendation, embedding a URL, echoing dossier content — this catches it at the
last gate before a CISO acts on it.

Adversarial Conduct is raised here rather than in the screening pipeline, because the
screening identity holds no ``findings`` write. The pipeline records the screening and
publishes; the consumer that holds the write records the consequence. Three things happen at
once: the Trust Score drops 25 points, the band is forced to escalate regardless of the
arithmetic, and the vendor record carries the flag into every future review.

Failure semantics: a memo that fails output screening is never published; the review parks in
``NEEDS_HUMAN`` with reason ``output_screening``. A memo generation failure parks the review
rather than presenting a score with no reasoning behind it. Raising Adversarial Conduct is
idempotent on the review: a second screening verdict for the same origin reference does not
apply a second 25-point penalty.
"""

from __future__ import annotations

from shared.armor import ScreenResult

ADVERSARIAL_PENALTY = 25

MEMO_PROMPT = """\
Write a one-page vendor risk memo for a CISO.

Structure, fixed:
1. The recommendation.
2. The three findings that drove it, each with its provenance (rule or model).
3. The mitigations required for conditional approval.
4. What to re-check in 90 days.

You do not compute or adjust the Trust Score, and you do not change any severity.
Report what the findings say.

VENDOR: {vendor}
SCORE BREAKDOWN: {breakdown}
FINDINGS: {findings}
PRIOR DOSSIER: {dossier}
"""


def write_memo(ctx, review_id: str) -> str:
    """Generate the memo, screen it, and return its reference.

    Raises:
        ArmorUnavailable: the memo is not published and the review parks. Output screening is
            a mandatory control on the last artefact before a human decision.
    """
    raise NotImplementedError


def raise_adversarial_conduct(review_id: str, screen: ScreenResult) -> None:
    """Apply the Adversarial Conduct consequence for a prompt-injection verdict.

    Sets the review flag and the vendor flag, writes a ``conduct`` finding at high severity
    labelled ``source="rule"`` and carrying the blocked excerpt as inert evidence, publishes
    ``review.rescore``, and raises the dashboard banner.

    The finding's summary names the template and version that fired, because a verdict
    without its policy is not reproducible six months later.
    """
    raise NotImplementedError
