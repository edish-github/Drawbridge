"""The barrier evaluator, against real ledger state.

``tests/test_graph.py`` asserts the join *policies* are well-formed; this asserts the evaluator
applies them. The two are separate files because they fail for different reasons: a policy is
wrong when somebody edits the graph, and an evaluator is wrong when somebody edits an arm reader.

The behaviour under test is the one the module exists for. A barrier that returns a boolean
answers *may I proceed* and throws away *what am I waiting for*, and the second question is the
one an operator looking at a stalled review is actually asking.
"""

from __future__ import annotations

import pytest

from shared.graph import GRAPH, JoinMode
from shared.join import evaluate
from tests.conftest import emulator_required


def open_review(db, review_id: str, **fields) -> None:
    db.collection("reviews").document(review_id).set(
        {
            "review_id": review_id,
            "vendor_id": "datadynamo",
            "state": "replies_in",
            "tier": 1,
            "plan_version": 1,
            "opened_at": "2026-03-01T09:00:00+00:00",
            **fields,
        }
    )


def answer(db, review_id: str, question_id: str, *, answered: bool = True) -> None:
    db.collection("qa_responses").document(f"{review_id}:{question_id}").set(
        {
            "review_id": review_id,
            "question_id": question_id,
            "answer": "we retain customer data for 90 days" if answered else "",
            "answered": answered,
            "usable": answered,
        }
    )


# --- The policy is read from the graph, not restated ---------------------------------------


def test_an_undeclared_join_is_an_error_rather_than_a_default():
    """A barrier the graph does not know about is a wait no diagram shows."""
    with pytest.raises(KeyError, match="not a declared join"):
        evaluate("no_such_join", "r1")


@emulator_required
def test_the_verdict_names_the_join_it_evaluated(review_id, db):
    open_review(db, review_id)
    assert evaluate("coverage", review_id).join_id == "coverage"


# --- test_join_semantics · threshold --------------------------------------------------------


@emulator_required
def test_a_review_with_no_answers_is_below_the_threshold(review_id, db):
    open_review(db, review_id)

    verdict = evaluate("coverage", review_id)

    assert not verdict.satisfied
    assert verdict.mode is JoinMode.THRESHOLD


@emulator_required
def test_the_verdict_says_which_arm_it_is_waiting_on(review_id, db):
    """The half a boolean throws away."""
    open_review(db, review_id)

    verdict = evaluate("coverage", review_id)

    assert "reply_parse" in verdict.waiting_on
    assert "coverage" in verdict.summary()


@emulator_required
def test_an_analyst_override_opens_the_barrier_and_is_recorded_as_the_reason(review_id, db):
    """The override is the rule's other half, not a softening of it.

    A vendor who answers most of what was asked and then stops is the ordinary case. What matters
    is that the verdict says the barrier was overridden rather than reporting a coverage figure
    it never reached, so the binder shows a review that reconciled at 40% saying so.
    """
    open_review(db, review_id, replies_complete=True)
    answer(db, review_id, "DP01")

    verdict = evaluate("coverage", review_id)

    assert verdict.satisfied
    assert verdict.overridden
    assert "analyst" in verdict.reason


@emulator_required
def test_without_the_override_a_short_review_keeps_waiting(review_id, db):
    open_review(db, review_id, replies_complete=False)
    answer(db, review_id, "DP01")

    verdict = evaluate("coverage", review_id)

    assert not verdict.satisfied
    assert not verdict.overridden


@emulator_required
def test_an_optional_arm_never_holds_the_barrier(review_id, db):
    """``chase`` and ``followup`` are optional. A review that reached coverage without either
    proceeds, and a required arm is the only thing that can stop it."""
    open_review(db, review_id, replies_complete=True)

    verdict = evaluate("coverage", review_id)
    optional = [a for a in verdict.arms if not a.required]

    assert optional
    assert verdict.satisfied


# --- test_join_semantics · all-required ------------------------------------------------------


@emulator_required
def test_the_findings_join_requires_every_arm(review_id, db):
    open_review(db, review_id, state="evidence_review")

    verdict = evaluate("findings", review_id)

    assert verdict.mode is JoinMode.ALL_REQUIRED
    assert all(a.required for a in verdict.arms)


@emulator_required
def test_the_findings_join_reports_every_arm_it_examined(review_id, db):
    """A verdict that named only the failing arm would make a two-arm shortfall look like one."""
    open_review(db, review_id, state="evidence_review")

    verdict = evaluate("findings", review_id)

    assert {a.name for a in verdict.arms} == {"checks", "cross_exam", "subprocessors"}


# --- Unreadable evidence is not absent evidence ------------------------------------------------


@emulator_required
def test_an_unreadable_arm_never_satisfies_a_required_barrier(review_id, db, monkeypatch):
    """A barrier that opens because its input could not be read is worse than one that waits."""
    import shared.join as join

    def explode(arm, rid):
        raise RuntimeError("firestore is unavailable")

    monkeypatch.setitem(join._READERS, "checks", explode)
    open_review(db, review_id, state="evidence_review")

    verdict = evaluate("findings", review_id)

    assert not verdict.satisfied
    assert "checks" in verdict.waiting_on
    assert any(a.unknown for a in verdict.arms)


@emulator_required
def test_an_unreadable_arm_is_distinguishable_from_an_incomplete_one(review_id, db, monkeypatch):
    """The two look the same on a diagram and mean opposite things."""
    import shared.join as join

    def explode(arm, rid):
        raise RuntimeError("firestore is unavailable")

    monkeypatch.setitem(join._READERS, "cross_exam", explode)
    open_review(db, review_id, state="evidence_review")

    verdict = evaluate("findings", review_id)
    by_name = {a.name: a for a in verdict.arms}

    assert by_name["cross_exam"].unknown
    assert not by_name["subprocessors"].unknown


# --- The evaluator decides nothing --------------------------------------------------------------


def test_the_join_module_never_advances_a_review():
    """Advancing is a forward transition, and forward transitions have one owner.

    A join module that called ``advance`` would be a second place a review's position is written
    from, which is precisely what ``shared/state.py`` exists to prevent.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "shared" / "join.py"
    tree = ast.parse(source.read_text())

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "advance" not in called
    assert "park" not in called


def test_every_declared_arm_has_a_reader():
    """The cost of keeping the graph serialisable is this table, so the table is checked."""
    from shared.join import _READERS

    for join in GRAPH.joins:
        for arm in join.arms:
            assert arm.name in _READERS
