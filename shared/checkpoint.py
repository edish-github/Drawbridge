"""Durable workflow steps: checkpoint before executing, skip completed steps on replay.

A review's plan is a list of named steps, so *resume* is "replay the list, skipping the ones
already done." Recoverability is structural rather than a special-case recovery path, which is
why the kill-and-resume demo needs no code of its own.

**Relationship to ADK 2, resolved rather than left ambiguous.** ADK 2 models a workflow as a
graph of nodes (``google.adk.workflow.Workflow``, ``@node``, ``Edge``, ``START``), and every
node carries ``rerun_on_resume``, so node execution already has resume semantics. This module
is the *checkpointing layer over* ADK's node execution, not a competing notion of a step: the
node is the unit of execution, and ``step`` is what makes its result durable in the Firestore
ledger the audit binder is built from. Two notions of "a step" would give the resume path a
corner case nobody finds until it is on camera.

Failure semantics: the checkpoint write happens before execution, so a crash between the two
leaves the step marked started and not completed — the resume path re-runs it, which is safe
because every side effect inside a step is separately guarded by ``shared.idempotency``. A
step whose result cannot be serialised raises rather than being recorded as complete, since
recording completion without the result would make the next resume skip work whose output is
gone.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Callable

from google.cloud import firestore

from shared.clients import firestore_client
from shared.idempotency import _serialise

log = logging.getLogger("drawbridge.checkpoint")

COLLECTION_REVIEWS = "reviews"


def step(name: str, ctx, fn: Callable[[], Any]) -> Any:
    """Run ``fn`` once per review, recording its result under ``name``.

    Returns the recorded result without executing when ``name`` is already in the review's
    completed steps.

    Raises:
        TypeError: when the result cannot be serialised into the ledger.
        Exception: anything ``fn`` raises propagates; the step stays incomplete and the resume
            path re-runs it.
    """
    review_id = ctx.review_id
    doc = firestore_client().collection(COLLECTION_REVIEWS).document(review_id)

    snap = doc.get()
    state = snap.to_dict() or {}

    if name in state.get("completed_steps", []):
        log.info("checkpoint SKIP %s for review=%s — already completed", name, review_id)
        return (state.get("step_results") or {}).get(name)

    doc.set(
        {"current_step": name, "step_started": datetime.now(UTC).isoformat()},
        merge=True,
    )

    result = fn()

    doc.set(
        {
            "completed_steps": firestore.ArrayUnion([name]),
            "step_results": {name: _serialise(result)},
            "current_step": None,
        },
        merge=True,
    )
    log.info("checkpoint DONE %s for review=%s", name, review_id)
    return result


def completed_steps(review_id: str) -> list[str]:
    """Return the ordered step names already completed for this review."""
    snap = firestore_client().collection(COLLECTION_REVIEWS).document(review_id).get()
    return list((snap.to_dict() or {}).get("completed_steps", []))


def current_step(review_id: str) -> str | None:
    """Return the step that was in flight when the process last wrote, or ``None``.

    On restart this is what the dashboard shows as "resumed from".
    """
    snap = firestore_client().collection(COLLECTION_REVIEWS).document(review_id).get()
    return (snap.to_dict() or {}).get("current_step")


def step_result(review_id: str, name: str) -> Any:
    """Return the recorded result of a completed step, or ``None``."""
    snap = firestore_client().collection(COLLECTION_REVIEWS).document(review_id).get()
    return ((snap.to_dict() or {}).get("step_results") or {}).get(name)
