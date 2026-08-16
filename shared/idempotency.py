"""The exactly-once guard: execute a side effect at most once across retries and restarts.

Pub/Sub is at-least-once, so a duplicate is not an error condition, it is Tuesday. Every side
effect — an email, a score write, an approval record — claims its key transactionally **before**
the effect runs, so a crash mid-effect leaves an ``in_progress`` marker rather than an
ambiguous silence. The one exception is a failure raised *before* dispatch — a gateway policy
refusal — which is known not to have run the effect and so releases its claim instead of
leaving one behind for a human to reconcile.

Key derivation rule: ``review_id:plan_vN:step_id`` where ``step_id`` is deterministic from the
workflow position (``questionnaire_send:v1``, ``chase:round2``, ``followup:q14:v1``) and never
a timestamp or uuid. If the key is not reproducible after a restart, the guard is worthless.

The plan version is in the key because a re-tiered review is re-planned mid-flight: a step name
that meant one thing in plan v1 must not silently satisfy a different step in plan v2, and a
legitimately new step must not collide with a completed key and be skipped. Steps carried over
unchanged from the previous plan explicitly inherit their old key, so completed work is still
skipped.

Failure semantics: an ``in_progress`` record older than the reconciliation timeout is surfaced
for human confirmation rather than blindly re-run. **For email the safe default is do not
resend, flag for confirmation** — the conservative choice a security product should make, and
the honest answer to "why might a resumable agent order two laptops". Completed records carry a
``done_ts`` for the Firestore TTL policy: a spent key is an execution artefact, and the ledger
is where history lives.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from google.cloud import firestore

from shared.clients import firestore_client

log = logging.getLogger("drawbridge.idempotency")

COLLECTION = "idempotency"
RECONCILE_AFTER_SECONDS = 900
"""How long an ``in_progress`` record may sit before it is surfaced for reconciliation."""

STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"


class ReconciliationRequired(Exception):
    """A prior attempt claimed this key and never completed. A human confirms; nothing reruns."""

    def __init__(self, idem_key: str, claimed_at: str, source: str) -> None:
        super().__init__(
            f"{idem_key} was claimed by {source} at {claimed_at} and never completed. "
            "It is surfaced for human confirmation and will not be re-run automatically."
        )
        self.idem_key = idem_key
        self.claimed_at = claimed_at
        self.source = source


def key_for(review_id: str, plan_version: int, step_id: str) -> str:
    """Derive the idempotency key for ``step_id`` under a given plan version.

    Pure and deterministic from workflow position. Nothing here reads a clock or a uuid,
    because a key that cannot be recomputed after a restart guards nothing.
    """
    return f"{review_id}:plan_v{plan_version}:{step_id}"


def key_for_review(review, step_id: str, *, inherited: dict[str, str] | None = None) -> str:
    """Derive the key for a step on a review, honouring keys inherited from a previous plan.

    A step carried over unchanged from an earlier plan returns its old key, so work completed
    under plan v1 is still recognised as done under plan v2 and the vendor is not emailed
    twice by a re-tier.
    """
    if inherited and step_id in inherited:
        return inherited[step_id]
    return key_for(review.review_id, review.plan_version, step_id)


def once(idem_key: str, ctx, fn: Callable[..., Any], *args, claim: Any = None, **kwargs) -> Any:
    """Execute ``fn`` at most once across all retries, restarts and redeliveries.

    Claims the key in a Firestore transaction, runs the effect, then records the result. A
    second call with the same key returns the recorded result and logs a visible skip — the
    ``idempotency SKIP`` line is a demo asset, not just a log entry.

    Args:
        claim: what this step is about to do, recorded with the claim rather than with the
            result. Two things need it. An operator reconciling a crashed step is looking at a
            marker that says a key was claimed and nothing else, and "about to email these
            thirty questions to this address" is the difference between a decision and a guess.
            And when they confirm it, ``confirm`` promotes this to the result — so a step whose
            process died before it could record what it did is still replayable without the
            caller having to re-derive it.

    Raises:
        ReconciliationRequired: when an ``in_progress`` record older than
            ``RECONCILE_AFTER_SECONDS`` exists. The step is surfaced for a human to confirm; it
            is never re-run automatically.
        Exception: anything ``fn`` raises propagates unchanged, leaving the ``in_progress``
            marker in place so the next attempt reconciles rather than duplicating — unless the
            exception declares ``effect_attempted = False``, which means the effect provably did
            not run and the claim is released.
    """
    db = firestore_client()
    ref = db.collection(COLLECTION).document(idem_key)
    source = getattr(ctx, "agent", "unknown")

    @firestore.transactional
    def claim_the_key(tx) -> dict | None:
        snap = ref.get(transaction=tx)
        if snap.exists:
            return snap.to_dict()
        tx.set(
            ref,
            {
                "status": STATUS_IN_PROGRESS,
                "ts": datetime.now(UTC).isoformat(),
                "source": source,
                "review_id": getattr(ctx, "review_id", None),
                "claim": _serialise(claim),
            },
        )
        return None

    prior = claim_the_key(db.transaction())

    if prior is not None:
        if prior.get("status") == STATUS_DONE:
            log_idempotent_skip(idem_key, ctx)
            return prior.get("result")

        # Claimed but never completed. The effect may or may not have happened, and there is no
        # way to tell from here — so it is surfaced rather than guessed at. Both the crashed
        # case and the still-running case refuse to re-run; only the log line differs, because
        # the operator's next action differs.
        claimed_at = prior.get("ts", "")
        source_of_claim = prior.get("source", "unknown")
        if _older_than(claimed_at, RECONCILE_AFTER_SECONDS):
            log.warning(
                "idempotency STALE %s — claimed by %s at %s and never completed; "
                "surfaced for confirmation, not re-run",
                idem_key,
                source_of_claim,
                claimed_at,
            )
        else:
            log.info(
                "idempotency IN PROGRESS %s — claimed by %s at %s, another worker may hold it",
                idem_key,
                source_of_claim,
                claimed_at,
            )
        raise ReconciliationRequired(idem_key, claimed_at, source_of_claim)

    try:
        result = fn(*args, **kwargs)
    except Exception as exc:
        # A failure that is *known* not to have attempted the effect releases its claim; a
        # failure that might have half-happened keeps it. The only failures that qualify are
        # the ones raised before dispatch — a gateway policy refusal is the case that matters,
        # because the review parks at a gate and must be able to run the step once released.
        # Everything else keeps the marker, which is what makes reconciliation conservative.
        if getattr(exc, "effect_attempted", True) is False:
            ref.delete()
            log.info(
                "idempotency RELEASED %s — %s refused before the effect; the step may run "
                "again once the refusal is resolved",
                idem_key,
                type(exc).__name__,
            )
        raise

    ref.update(
        {
            "status": STATUS_DONE,
            "result": _serialise(result),
            "done_ts": datetime.now(UTC),
        }
    )
    return result


def log_idempotent_skip(idem_key: str, ctx) -> None:
    """Record that a claimed key was replayed and the effect was not repeated.

    This line is what the kill-and-resume demo points at on screen, so it names the agent that
    replayed the step rather than only the key.
    """
    log.info(
        "idempotency SKIP %s — already done, effect not repeated (replayed by %s)",
        idem_key,
        getattr(ctx, "agent", "unknown"),
    )


def reconcile(review_id: str, *, older_than_seconds: int = RECONCILE_AFTER_SECONDS) -> list[str]:
    """Return the idempotency keys for this review that need human confirmation.

    Args:
        older_than_seconds: how old a claim must be before it is reported. The default assumes
            another worker may legitimately hold a fresh claim, so it waits. **A resume path
            that knows the previous process died should pass 0**: after a crash there is no
            other worker, and waiting fifteen minutes to surface a stuck step means the
            dashboard shows nothing at exactly the moment an operator is looking at it.

    Returns:
        The claimed-but-incomplete keys. An empty list means every claimed key either completed
        or is younger than the threshold.
    """
    from google.cloud.firestore_v1 import FieldFilter

    stale: list[str] = []
    docs = (
        firestore_client()
        .collection(COLLECTION)
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    )
    for doc in docs:
        data = doc.to_dict() or {}
        if data.get("status") != STATUS_IN_PROGRESS:
            continue
        if older_than_seconds <= 0 or _older_than(data.get("ts", ""), older_than_seconds):
            stale.append(doc.id)
    return stale


def confirm(idem_key: str, *, confirmed_by: str, result: Any = None) -> dict:
    """Close a claimed-but-incomplete key on a human's word that the effect did happen.

    The other half of "flag for confirmation". A crashed worker leaves an ``in_progress``
    marker, and nothing in the system can tell from the outside whether the email left the
    building — so a person looks, decides, and records the decision here. Until they do, the
    step is refused rather than repeated, which is the conservative direction.

    Only ever moves a claim to ``done``. There is deliberately no "it did not happen, run it
    again" here: releasing a claim for an effect that might have occurred is the operation that
    sends the second email, and if an operator has established it did not happen then a new
    step under a new key is the honest way to redo it.

    Raises:
        ReconciliationRequired: when the key was never claimed. Confirming an effect nobody
            attempted would write a done marker that silently skips real work later.
    """
    ref = firestore_client().collection(COLLECTION).document(idem_key)
    snap = ref.get()
    if not snap.exists:
        raise ReconciliationRequired(idem_key, "", "nobody")

    prior = snap.to_dict() or {}
    if prior.get("status") == STATUS_DONE:
        log.info("idempotency ALREADY DONE %s — nothing to confirm", idem_key)
        return prior

    # The claim recorded what the step was about to do. Promoting it is what lets a replay
    # after confirmation hand the caller back the same thing a completed run would have — the
    # question ids that went out, in the case this exists for.
    restored = result if result is not None else prior.get("result") or prior.get("claim")

    ref.update(
        {
            "status": STATUS_DONE,
            "result": _serialise(restored),
            "done_ts": datetime.now(UTC),
            "confirmed_by": confirmed_by,
        }
    )
    log.warning(
        "idempotency CONFIRMED %s — %s confirmed the effect happened; the step will be skipped "
        "on the next replay rather than re-run",
        idem_key,
        confirmed_by,
    )
    return ref.get().to_dict() or {}


def record_status(idem_key: str) -> dict | None:
    """Return the raw idempotency record, or ``None`` when the key was never claimed."""
    snap = firestore_client().collection(COLLECTION).document(idem_key).get()
    return snap.to_dict() if snap.exists else None


def _older_than(iso_ts: str, seconds: int) -> bool:
    """Return whether ``iso_ts`` is further in the past than ``seconds``."""
    if not iso_ts:
        return True
    try:
        claimed = datetime.fromisoformat(iso_ts)
    except ValueError:
        return True
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=UTC)
    return datetime.now(UTC) - claimed > timedelta(seconds=seconds)


def _serialise(result: Any) -> Any:
    """Reduce a result to something Firestore can store.

    Raises:
        TypeError: when the result cannot be represented. Recording a key as done without its
            result would make the next replay skip work whose output is gone.
    """
    if result is None or isinstance(result, (str, int, float, bool, dict, list)):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    raise TypeError(
        f"cannot record the result of an idempotent step: {type(result).__name__} is not "
        "serialisable. Return a primitive or a Pydantic model."
    )
