"""Anything can stop a review; only the Orchestrator can advance one.

The rule is asserted against the source tree rather than trusted, because the second agent that
"just needed to set REPLIES_IN" is how single ownership quietly ends — and it would end without
any test failing, since each individual write is perfectly valid.

Parking stays available everywhere on purpose. A park that had to travel through the
Orchestrator would mean a lost message leaves a review claiming to be in flight after it has
already stopped, and a stopped review that says so is safer than a stopped review that says it
is running.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ORCHESTRATOR = REPO / "agents" / "orchestrator"

SOURCE_ROOTS = ("agents", "services", "scenarios", "scripts", "shared")

FORWARD_STATES = {
    "QUESTIONNAIRE_OUT",
    "REPLIES_IN",
    "EVIDENCE_REVIEW",
    "SCORED",
    "DECIDED",
    "MONITORED",
}
"""States a review can only be moved *into* by the Orchestrator.

``GATED`` and ``NEEDS_HUMAN`` are absent: those are parks, and every component may perform one.
"""


def python_files():
    for root in SOURCE_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def calls_to(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def test_only_the_orchestrator_advances_a_review():
    """``shared.state.advance`` is the only forward transition, and it has one caller package."""
    offenders = []

    for path in python_files():
        if ORCHESTRATOR in path.parents or path == REPO / "shared" / "state.py":
            continue
        tree = ast.parse(path.read_text())
        if calls_to(tree, "advance"):
            offenders.append(str(path.relative_to(REPO)))

    assert not offenders, (
        "these modules advance a review and are not the Orchestrator: "
        f"{offenders}. Anything can stop a review; only the Orchestrator can advance one."
    )


def test_no_module_writes_a_forward_state_directly():
    """A direct Firestore write bypassing ``advance`` would evade the rule entirely.

    Catches the shape ``{"state": ReviewState.SCORED.value}`` anywhere outside the Orchestrator
    and the state module, which is how the rule would be broken by someone who never looked at
    ``shared/state.py``.
    """
    offenders = []

    for path in python_files():
        if ORCHESTRATOR in path.parents or path == REPO / "shared" / "state.py":
            continue
        tree = ast.parse(path.read_text())

        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if not (isinstance(key, ast.Constant) and key.value == "state"):
                    continue
                if _names_a_forward_state(value):
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")

    assert not offenders, (
        f"these write a forward state directly instead of through advance(): {offenders}"
    )


def test_park_is_available_everywhere():
    """The other half of the rule. Parking is deliberately unrestricted."""
    from shared.state import park

    assert callable(park)


@pytest.mark.parametrize(
    "module",
    [
        "agents.questionnaire.agent",
        "agents.evidence.agent",
        "agents.risk_scorer.agent",
        "shared.armor",
        "shared.routing",
    ],
)
def test_components_that_must_be_able_to_stop_a_review_can(module):
    """Every component with a failure path reaches ``park`` — a documented but unreachable
    failure path is worse than one never written down."""
    import importlib

    source = Path(importlib.import_module(module).__file__).read_text()

    assert "park" in source, f"{module} has no way to stop a review"


def _names_a_forward_state(value: ast.AST) -> bool:
    """Whether an expression is a forward ``ReviewState`` member or its value."""
    node = value
    if isinstance(node, ast.Attribute) and node.attr == "value":
        node = node.value
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ReviewState"
        and node.attr in FORWARD_STATES
    )
