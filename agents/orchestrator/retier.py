"""Evidence-corrected re-tiering: the fleet overrules the intake form.

A review is tiered from what the procurement manager typed into the intake form, and in
every real procurement organisation the initiator understates data scope — not maliciously,
but because a Tier 3 review clears in a week and a Tier 1 takes a month, and there is a
contract waiting. Until evidence arrives, the fleet is trusting the most conflicted party in
the process. So the tier is re-evaluated after each reply batch and after evidence
extraction.

Deterministic rules run first — declared data categories, system access level, whether the
vendor is an AI service. The model is used only to classify free-text answers into those
same categories; it never picks the tier.

**Tier only ever moves up.** A downward re-tier would let a vendor's own answers reduce the
scrutiny applied to them, which is an attack surface. Every upward change increments the
plan version, writes a ``TierChange`` naming the answer that caused it, and re-plans with
carried-over steps inheriting their old idempotency keys so completed work is not repeated.

Failure semantics: a classification failure leaves the tier unchanged and logs a
degraded-mode warning — failing to re-tier is a missed upgrade, whereas a re-tier on a bad
classification sends a vendor thirty questions they do not owe. A re-plan that cannot write
its ``TierChange`` aborts the re-plan entirely rather than changing the tier without a
recorded reason: an audit question the binder cannot answer is worse than a stale tier.
"""

from __future__ import annotations

from shared.domain import Review, TierChange


def reassess_tier(ctx, review: Review) -> Review:
    """Re-evaluate the tier against everything currently known and re-plan if it rose.

    Returns the review unchanged when the recomputed tier is equal or lower.
    """
    raise NotImplementedError


def unclassified_answers(review_id: str) -> list[str]:
    """Return free-text answers not yet mapped to a data-scope category."""
    raise NotImplementedError


def replan(review: Review, new_tier: int):
    """Build the plan for ``new_tier``, increment the plan version, inherit carried keys.

    Raises:
        ValueError: when ``new_tier`` is not greater than the review's current tier.
    """
    raise NotImplementedError


def record_tier_change(review: Review, change: TierChange) -> None:
    """Append a tier change to the review's history and the dashboard timeline.

    The reason is written in plain English and names the source answer, because the audit
    question — why did this become a Tier 1? — has to be answerable in the vendor's own
    words.

    Raises:
        ValueError: when ``change.to_tier`` is not greater than ``change.from_tier``.
    """
    raise NotImplementedError
