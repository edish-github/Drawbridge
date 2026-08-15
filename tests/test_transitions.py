"""The transition table, exhaustively.

The state machine is the one part of the kernel with no external dependency, so it is fully
testable before any cloud resource exists. Every legal transition has a test; every illegal one
is covered by the exhaustive rejection test at the bottom.

The point these tests defend: the system refuses to guess. An illegal transition raises and the
caller parks the review — it is never quietly corrected into the nearest legal state.
"""

from __future__ import annotations

import itertools

import pytest

from shared.domain import (
    ALLOWED,
    InvalidTransition,
    ReviewState,
    is_terminal,
    validate_transition,
)

S = ReviewState


# --- Legal transitions ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.INTAKE, S.QUESTIONNAIRE_OUT),
        (S.QUESTIONNAIRE_OUT, S.REPLIES_IN),
        (S.REPLIES_IN, S.EVIDENCE_REVIEW),
        (S.EVIDENCE_REVIEW, S.SCORED),
        (S.DECIDED, S.MONITORED),
        (S.MONITORED, S.MONITORED),
    ],
)
def test_forward_progress_is_legal(current, target):
    validate_transition(current, target)


@pytest.mark.parametrize(
    "current",
    [S.REPLIES_IN, S.EVIDENCE_REVIEW],
)
def test_retier_moves_backward_into_questionnaire_out(current):
    """The one legitimate backward transition inside a single review."""
    validate_transition(current, S.QUESTIONNAIRE_OUT)


@pytest.mark.parametrize("current", list(ReviewState))
def test_needs_human_is_reachable_from_every_state(current):
    """A failure path that is documented but cannot be executed is worse than none."""
    if current is S.NEEDS_HUMAN:
        pytest.skip("NEEDS_HUMAN does not transition to itself")
    validate_transition(current, S.NEEDS_HUMAN)


@pytest.mark.parametrize(
    "target",
    [S.QUESTIONNAIRE_OUT, S.EVIDENCE_REVIEW, S.SCORED, S.DECIDED],
)
def test_needs_human_releases_forward(target):
    validate_transition(S.NEEDS_HUMAN, target)


def test_needs_human_releases_to_gated_with_a_scope():
    validate_transition(S.NEEDS_HUMAN, S.GATED, gate_scope="decision")


# --- Gate scope ----------------------------------------------------------------------------


def test_park_at_the_contact_gate():
    validate_transition(S.QUESTIONNAIRE_OUT, S.GATED, gate_scope="contact")


def test_park_at_the_decision_gate():
    validate_transition(S.SCORED, S.GATED, gate_scope="decision")


def test_entering_gated_without_a_scope_is_rejected():
    """Without the scope the two parks are indistinguishable."""
    with pytest.raises(InvalidTransition, match="gate_scope"):
        validate_transition(S.SCORED, S.GATED)


def test_contact_gate_releases_to_questionnaire_out():
    validate_transition(S.GATED, S.QUESTIONNAIRE_OUT, gate_scope="contact")


def test_decision_gate_releases_to_decided():
    validate_transition(S.GATED, S.DECIDED, gate_scope="decision")


def test_contact_gate_cannot_release_to_decided():
    """The one that matters: authorising an email must not approve a vendor."""
    with pytest.raises(InvalidTransition, match="contact"):
        validate_transition(S.GATED, S.DECIDED, gate_scope="contact")


def test_decision_gate_cannot_resume_the_questionnaire():
    with pytest.raises(InvalidTransition, match="decision"):
        validate_transition(S.GATED, S.QUESTIONNAIRE_OUT, gate_scope="decision")


def test_releasing_a_gated_review_without_its_scope_is_rejected():
    with pytest.raises(InvalidTransition, match="gate_scope"):
        validate_transition(S.GATED, S.DECIDED)


def test_a_gated_review_may_always_park_for_a_human_without_a_scope():
    validate_transition(S.GATED, S.NEEDS_HUMAN)


# --- Illegal transitions -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.INTAKE, S.SCORED),
        (S.INTAKE, S.DECIDED),
        (S.QUESTIONNAIRE_OUT, S.SCORED),
        (S.QUESTIONNAIRE_OUT, S.INTAKE),
        (S.REPLIES_IN, S.DECIDED),
        (S.EVIDENCE_REVIEW, S.DECIDED),
        (S.SCORED, S.DECIDED),
        (S.SCORED, S.EVIDENCE_REVIEW),
        (S.DECIDED, S.SCORED),
        (S.DECIDED, S.GATED),
        (S.MONITORED, S.DECIDED),
    ],
)
def test_illegal_transitions_raise(current, target):
    with pytest.raises(InvalidTransition):
        validate_transition(current, target, gate_scope="decision")


def test_scored_cannot_reach_decided_without_passing_the_gate():
    """No code path sets DECIDED without a gate, which is where the token is checked."""
    with pytest.raises(InvalidTransition):
        validate_transition(S.SCORED, S.DECIDED, gate_scope="decision")


def test_every_pair_outside_the_table_is_rejected():
    """Exhaustive: nothing is legal by omission."""
    for current, target in itertools.product(ReviewState, repeat=2):
        if target in ALLOWED[current]:
            continue
        with pytest.raises(InvalidTransition):
            validate_transition(current, target, gate_scope="decision")


def test_a_state_missing_from_the_table_is_rejected_loudly():
    with pytest.raises(InvalidTransition, match="not a state"):
        validate_transition("not_a_state", S.NEEDS_HUMAN)  # type: ignore[arg-type]


# --- Immutability --------------------------------------------------------------------------


def test_decided_is_terminal():
    assert is_terminal(S.DECIDED)


@pytest.mark.parametrize("state", [s for s in ReviewState if s is not S.DECIDED])
def test_nothing_else_is_terminal(state):
    """MONITORED in particular: the Watchdog re-enters it on every sweep."""
    assert not is_terminal(state)


def test_the_table_covers_every_state():
    """A state with no row is a state nothing can leave."""
    assert set(ALLOWED) == set(ReviewState)
