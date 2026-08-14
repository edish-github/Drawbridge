"""The exactly-once guard: execute a side effect at most once across retries and restarts.

Pub/Sub is at-least-once, so a duplicate is not an error condition, it is Tuesday. Every
side effect — an email, a score write, an approval record — claims its key transactionally
**before** the effect runs, so a crash mid-effect leaves an ``in_progress`` marker rather
than an ambiguous silence.

Key derivation rule: ``f"{review_id}:plan_v{n}:{step_id}"`` where ``step_id`` is
deterministic from the workflow position (``questionnaire_send:v1``, ``chase:round2``,
``followup:q14:v1``) and never from a timestamp or uuid. If the key is not reproducible
after a restart, the guard is worthless.

The plan version is in the key because a re-tiered review is re-planned mid-flight: a step
name that meant one thing in plan v1 must not silently satisfy a different step in plan v2,
and a legitimately new step must not collide with a completed key and be skipped. Steps
carried over unchanged from the previous plan explicitly inherit their old key, so completed
work is still skipped.

Failure semantics: an ``in_progress`` record older than the reconciliation timeout is
surfaced for human confirmation rather than blindly re-run. For email the safe default is
**do not resend, flag for confirmation** — the conservative choice a security product should
make. Completed records carry a Firestore TTL: a spent key is an execution artefact, and the
ledger is where history lives.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from shared.domain import Review

RECONCILE_AFTER_SECONDS = 900
"""How long an ``in_progress`` record may sit before it is surfaced for reconciliation."""


class ReconciliationRequired(Exception):
    """A prior attempt claimed this key and never completed. A human confirms; nothing reruns."""


def key_for(review: Review, step_id: str) -> str:
    """Derive the idempotency key for ``step_id`` under the review's current plan.

    A step carried over unchanged from a previous plan returns its inherited key, so work
    completed under plan v1 is still recognised as done under plan v2.
    """
    raise NotImplementedError


def once(idem_key: str, ctx, fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Execute ``fn`` at most once across all retries, restarts and redeliveries.

    Claims the key in a Firestore transaction, runs the effect, then records the result.
    A second call with the same key returns the recorded result and logs a visible skip —
    the ``idempotency SKIP`` line is a demo asset, not just a log entry.

    Raises:
        ReconciliationRequired: when an ``in_progress`` record older than
            ``RECONCILE_AFTER_SECONDS`` exists. The step is surfaced on the dashboard for a
            human to confirm; it is never re-run automatically.
        Exception: anything ``fn`` raises propagates unchanged, leaving the ``in_progress``
            marker in place so the next attempt reconciles rather than duplicating.
    """
    raise NotImplementedError


def log_idempotent_skip(idem_key: str, ctx) -> None:
    """Record that a claimed key was replayed and the effect was not repeated."""
    raise NotImplementedError


def reconcile(review_id: str) -> list[str]:
    """Return the idempotency keys for this review that need human confirmation.

    Called on resume. An empty list means every claimed key either completed or is still
    inside its timeout.
    """
    raise NotImplementedError
