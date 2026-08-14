"""Chasing missing answers on a schedule.

Three rounds, capped, escalating in tone: reminder, then deadline notice, then escalation to
the analyst. Each round carries its own idempotency key (``chase:round2``), so a restart
mid-round does not send the same reminder twice.

Failure semantics: three rounds without a response ends in ``NEEDS_HUMAN`` rather than a
fourth round — politeness that cannot terminate is a loop. A send blocked under P1 parks the
review at the contact gate; the chase schedule resumes on release rather than restarting. If
the outbound body fails output screening the message is not sent and the review parks with
reason ``outbound_screening``.
"""

from __future__ import annotations

from datetime import datetime

MAX_CHASE_ROUNDS = 3
CHASE_INTERVAL_DAYS = 3


def schedule_chase(review_id: str, *, days: int = CHASE_INTERVAL_DAYS) -> datetime:
    """Schedule the next chase and return when it fires.

    During a compressed demo run the time is computed against the injected clock, so a chase
    timer accelerates with everything else.
    """
    raise NotImplementedError


def chase_round(review_id: str) -> int:
    """Return how many chases have already been sent for this review."""
    raise NotImplementedError


def send_chase(ctx, review_id: str) -> str | None:
    """Compose and send the next chase, escalating in tone by round.

    Returns:
        The message id, or ``None`` when the cap is reached — at which point the review moves
        to ``NEEDS_HUMAN`` with a card explaining that the vendor stopped responding.
    """
    raise NotImplementedError
