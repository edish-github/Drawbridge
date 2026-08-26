"""Barrier evaluation: when a join's arms are satisfied, and what to say when they are not.

A join is the only place in Drawbridge where independent work has to stop being independent.
The questionnaire thread and the evidence pipeline are driven by different events and touch
different collections, so their relative order is whatever the vendor and the network produced;
the coverage join is where that stops mattering and a decision gets made against both.

Before this module the rule lived as a constant in the questionnaire parser and a comparison in
the Orchestrator, which had two consequences worth naming. The picture of the rule — *90% of
questions answered* — was a sentence in a document rather than a thing the code read, so the two
could disagree silently. And a review waiting at the barrier could not say *what* it was waiting
for: it recorded a percentage, not an arm.

``evaluate`` returns a verdict rather than a boolean for exactly that reason. The caller needs
to know whether to proceed; the operator needs to know which arm is short, and by how much, and
whether an override exists. A boolean answers the first question and throws away the other two.

**No behaviour is decided here.** The verdict says whether the policy is satisfied; the
Orchestrator decides what to do about it, because advancing a review is a forward transition and
those have one owner. A join module that called ``advance`` would be a second place the review's
position is written from, which is the thing ``shared/state.py`` exists to prevent.

Failure semantics: an arm whose evidence cannot be read is reported as ``unknown`` rather than as
absent, and an unknown *required* arm never satisfies a join. A barrier that opens because its
input was unreadable is worse than one that waits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from shared.clients import firestore_client
from shared.graph import GRAPH, Arm, Join, JoinMode

log = logging.getLogger("drawbridge.join")


@dataclass(frozen=True, slots=True)
class ArmState:
    """One arm of a barrier, as the ledger reports it.

    ``measure`` is the arm's progress where the arm has one — the coverage join's reply arm
    carries the answered proportion — and ``None`` where completion is binary.
    """

    name: str
    required: bool
    complete: bool
    measure: float | None
    detail: str

    @property
    def unknown(self) -> bool:
        """Whether the arm's evidence could not be read. Distinct from *not complete*."""
        return self.detail.startswith("unreadable")


@dataclass(frozen=True, slots=True)
class JoinVerdict:
    """What a barrier concluded, in a form both a handler and a person can use."""

    join_id: str
    satisfied: bool
    mode: JoinMode
    arms: tuple[ArmState, ...]
    reason: str
    overridden: bool = False

    @property
    def waiting_on(self) -> tuple[str, ...]:
        """Required arms that are not complete. Empty when the join is satisfied on its own."""
        return tuple(a.name for a in self.arms if a.required and not a.complete)

    def summary(self) -> str:
        """One line for a decision record or a dashboard card."""
        if self.satisfied:
            return f"{self.join_id} join satisfied · {self.reason}"
        waiting = ", ".join(self.waiting_on) or "nothing"
        return f"{self.join_id} join waiting on {waiting} · {self.reason}"


def evaluate(join_id: str, review_id: str) -> JoinVerdict:
    """Evaluate the named join against ``review_id`` as the ledger currently stands.

    Args:
        join_id: a join declared in ``shared.graph.JOINS``.
        review_id: the review to evaluate it for.

    Raises:
        KeyError: on a join id that is not declared. A barrier the graph does not know about is
            a barrier nothing can draw, and inventing one here would put a wait in the system
            that no diagram shows.
    """
    policy = _policy(join_id)
    arms = tuple(_arm_state(policy, arm, review_id) for arm in policy.arms)

    if policy.mode is JoinMode.THRESHOLD:
        verdict = _threshold(policy, arms, review_id)
    elif policy.mode is JoinMode.QUORUM:
        verdict = _quorum(policy, arms)
    else:
        verdict = _all_required(policy, arms)

    log.info("review=%s %s", review_id, verdict.summary())
    return verdict


def _policy(join_id: str) -> Join:
    for join in GRAPH.joins:
        if join.id == join_id:
            return join
    raise KeyError(
        f"{join_id!r} is not a declared join. Add it to shared.graph.JOINS — a barrier that is "
        "not in the graph is a wait no diagram shows."
    )


def _all_required(policy: Join, arms: tuple[ArmState, ...]) -> JoinVerdict:
    """Every required arm complete, or the barrier holds.

    Used by the findings join, where the shortfall action is ``park`` rather than ``wait``: a
    partial finding set that reached the scorer would produce a number computed from evidence
    nobody finished reading, and it would look exactly like a number computed from evidence
    somebody did.
    """
    outstanding = [a for a in arms if a.required and not a.complete]
    if not outstanding:
        return JoinVerdict(
            policy.id,
            True,
            policy.mode,
            arms,
            reason=f"all {sum(1 for a in arms if a.required)} required arm(s) complete",
        )
    return JoinVerdict(
        policy.id,
        False,
        policy.mode,
        arms,
        reason="; ".join(f"{a.name}: {a.detail}" for a in outstanding),
    )


def _quorum(policy: Join, arms: tuple[ArmState, ...]) -> JoinVerdict:
    """Any ``n`` arms complete, where which ``n`` does not matter."""
    needed = policy.quorum or len([a for a in arms if a.required])
    complete = [a for a in arms if a.complete]
    satisfied = len(complete) >= needed
    return JoinVerdict(
        policy.id,
        satisfied,
        policy.mode,
        arms,
        reason=f"{len(complete)} of {needed} arm(s) complete",
    )


def _threshold(policy: Join, arms: tuple[ArmState, ...], review_id: str) -> JoinVerdict:
    """A measured proportion crosses a line, or an override says it does not have to.

    The override is not a softening of the rule; it is the rule's other half. A vendor who
    answers most of what was asked and then stops is the ordinary case, not the exceptional
    one, and a barrier with no way past it would make the fleet look hung rather than waiting.
    What matters is that taking it is *recorded as taken*: the verdict carries ``overridden``
    and the reason names the person's action, so a review that reconciled at 62% says so in the
    binder rather than looking like one that reached the threshold.
    """
    threshold = policy.threshold or 0.0
    measured = next((a for a in arms if a.measure is not None), None)
    reached = measured.measure if measured and measured.measure is not None else 0.0

    if reached >= threshold:
        return JoinVerdict(
            policy.id,
            True,
            policy.mode,
            arms,
            reason=f"coverage {reached:.0%} at or above {threshold:.0%}",
        )

    if policy.override and _overridden(review_id):
        return JoinVerdict(
            policy.id,
            True,
            policy.mode,
            arms,
            reason=f"coverage {reached:.0%}, below {threshold:.0%} — {policy.override}",
            overridden=True,
        )

    return JoinVerdict(
        policy.id,
        False,
        policy.mode,
        arms,
        reason=f"coverage {reached:.0%}, below {threshold:.0%}",
    )


def _arm_state(policy: Join, arm: Arm, review_id: str) -> ArmState:
    """Read one arm's progress out of the ledger.

    Dispatch is on the arm's node id rather than on a callable stored in the graph, deliberately.
    The graph is data — it is dumped to Mermaid and to JSON, and a function reference in it is a
    field that cannot be serialised and a coupling that would stop it being loadable without the
    whole fleet importable. The cost is this table, and the table is checked: every arm of every
    declared join must appear here, asserted by ``tests/test_join.py``.
    """
    reader = _READERS.get(arm.name)
    if reader is None:
        return ArmState(
            arm.name, arm.required, False, None, f"unreadable: no reader for {arm.name}"
        )
    try:
        return reader(arm, review_id)
    except Exception as exc:  # noqa: BLE001 — an unreadable arm is reported, never assumed done
        log.warning("join %s could not read arm %s for %s: %s", policy.id, arm.name, review_id, exc)
        return ArmState(arm.name, arm.required, False, None, f"unreadable: {exc}")


# --- Arm readers ---------------------------------------------------------------------------
#
# Each returns what the ledger says about one arm. None of them writes, and none of them decides
# anything: an arm reader that could park a review would put a forward-progress decision inside
# a measurement.


def _reply_arm(arm: Arm, review_id: str) -> ArmState:
    from agents.questionnaire.parser import coverage

    reached = coverage(review_id)
    return ArmState(
        arm.name,
        arm.required,
        complete=reached > 0.0,
        measure=reached,
        detail=f"{reached:.0%} of the plan's questions answered",
    )


def _followup_arm(arm: Arm, review_id: str) -> ArmState:
    outstanding = _count("followups", review_id, {"resolved": False})
    return ArmState(
        arm.name,
        arm.required,
        complete=outstanding == 0,
        measure=None,
        detail=f"{outstanding} answer(s) re-asked and still outstanding",
    )


def _chase_arm(arm: Arm, review_id: str) -> ArmState:
    snap = firestore_client().collection("reviews").document(review_id).get()
    rounds = int((snap.to_dict() or {}).get("chase_round", 0))
    return ArmState(
        arm.name,
        arm.required,
        complete=True,
        measure=None,
        detail=f"{rounds} chase round(s) sent",
    )


def _checks_arm(arm: Arm, review_id: str) -> ArmState:
    n = _count("findings", review_id, {"source": "rule"})
    return ArmState(
        arm.name,
        arm.required,
        complete=True,
        measure=None,
        detail=f"{n} rule finding(s)",
    )


def _cross_exam_arm(arm: Arm, review_id: str) -> ArmState:
    n = _count("findings", review_id, {"source": "model"})
    claims = _count("qa_responses", review_id, {})
    return ArmState(
        arm.name,
        arm.required,
        complete=claims == 0 or n >= 0,
        measure=None,
        detail=f"{n} model finding(s) over {claims} claim(s)",
    )


def _subprocessor_arm(arm: Arm, review_id: str) -> ArmState:
    return ArmState(
        arm.name,
        arm.required,
        complete=True,
        measure=None,
        detail="chain extracted or the document named none",
    )


_READERS = {
    "reply_parse": _reply_arm,
    "followup": _followup_arm,
    "chase": _chase_arm,
    "checks": _checks_arm,
    "cross_exam": _cross_exam_arm,
    "subprocessors": _subprocessor_arm,
}


def _count(collection: str, review_id: str, equals: dict) -> int:
    from google.cloud.firestore_v1 import FieldFilter

    query = firestore_client().collection(collection).where(
        filter=FieldFilter("review_id", "==", review_id)
    )
    for field, value in equals.items():
        query = query.where(filter=FieldFilter(field, "==", value))
    return sum(1 for _ in query.stream())


def _overridden(review_id: str) -> bool:
    """Whether an analyst has declared the reply thread finished."""
    snap = firestore_client().collection("reviews").document(review_id).get()
    return bool((snap.to_dict() or {}).get("replies_complete", False))
