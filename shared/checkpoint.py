"""Durable workflow steps: checkpoint before executing, skip completed steps on replay.

A review's plan is a list of named steps, so *resume* is "replay the list, skipping the ones
already done." Recoverability is structural rather than a special-case recovery path, which
is why the kill-and-resume demo needs no code of its own.

**Relationship to ADK 2, resolved on day one rather than left ambiguous.** ADK 2 models a
workflow as a graph of nodes (``google.adk.workflow.Workflow``, ``@node``, ``Edge``,
``START``), and every node carries ``rerun_on_resume``, so node execution already has resume
semantics. This module is therefore the *checkpointing layer over* ADK's node execution, not
a competing notion of a step: the node is the unit of execution, and ``step`` is what makes
its result durable in the Firestore ledger the binder is built from. Two notions of "a step"
would give the resume path a corner case nobody finds until it is on camera.

Failure semantics: the checkpoint write happens before execution, so a crash between the two
leaves the step marked started and not completed — the resume path re-runs it, which is safe
because every side effect inside a step is separately guarded by ``shared.idempotency``.
A step whose result cannot be serialised raises rather than being recorded as complete.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def step(name: str, ctx, fn: Callable[[], Any]) -> Any:
    """Run ``fn`` once per review, recording its result under ``name``.

    Returns the recorded result without executing when ``name`` is already in the review's
    completed steps.

    Raises:
        TypeError: when the result cannot be serialised into the ledger. Recording a step as
            complete without its result would make the next resume skip work whose output is
            gone.
        Exception: anything ``fn`` raises propagates; the step stays incomplete and the
            resume path re-runs it.
    """
    raise NotImplementedError


def completed_steps(review_id: str) -> list[str]:
    """Return the ordered step names already completed for this review."""
    raise NotImplementedError


def current_step(review_id: str) -> str | None:
    """Return the step that was in flight when the process last wrote, or ``None``.

    On restart this is what the dashboard shows as "resumed from".
    """
    raise NotImplementedError
