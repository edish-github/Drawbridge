"""The fixture loader is not a product code path, and cannot become one by accident.

``scenarios.seed`` writes documents into the clean bucket that never went through screening.
That is defensible only while it stays a fixture loader: the moment product code can call it,
the difference between a seeded document and a screened one stops being enforceable, and every
claim about how content is promoted becomes conditional on nobody having taken a shortcut.

So the isolation is asserted, and so is the labelling — a seeded stamp has to be as
untrustworthy as a stub one to everything that checks, or the shortcut is a lie rather than a
fixture.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scenarios.seed import SEEDABLE_VENDORS, NotSeedable, seed_clean_evidence
from shared.armor import (
    SEEDED_TEMPLATE,
    STUB_TEMPLATE,
    UNTRUSTED_TEMPLATES,
    ScreenResult,
    verdict_is_trustworthy,
)
from shared.gateway import admissible

REPO = Path(__file__).resolve().parent.parent
PRODUCT_ROOTS = ("shared", "agents", "services")


def product_files():
    for root in PRODUCT_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def test_no_product_module_imports_the_fixture_loader():
    offenders = []

    for path in product_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("scenarios"):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
            if isinstance(node, ast.Import):
                if any(alias.name.startswith("scenarios") for alias in node.names):
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")

    assert not offenders, (
        f"product code imports the fixture loader: {offenders}. A seeded document and a "
        "screened one must stay distinguishable."
    )


def test_the_screening_module_has_no_seeding_branch():
    """No flag, no env var, no dev path. The stub failing closed is the control working.

    The module may *name* the fixture loader in prose — it should, because the reader needs to
    know why local mode has documents at all — but it must never call it or branch on it.
    """
    armor = REPO / "shared" / "armor.py"
    tree = ast.parse(armor.read_text())

    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }

    assert "seed_clean_evidence" not in names
    assert "seed" not in names


# --- A seeded stamp is not a verdict ----------------------------------------------------------


def seeded() -> ScreenResult:
    return ScreenResult(
        clean=True,
        template=SEEDED_TEMPLATE,
        template_version="0",
        filters={"pi_and_jailbreak": "NO_MATCH_FOUND"},
        execution={"pi_and_jailbreak": "EXECUTION_SKIPPED"},
        origin_ref="gs://clean/x.txt",
    )


def test_a_seeded_stamp_is_never_trustworthy():
    assert verdict_is_trustworthy(seeded()) is False
    assert seeded().is_untrusted is True


def test_a_seeded_stamp_is_admissible_nowhere():
    """Same consequence as a stub verdict, through the same rule rather than a parallel one."""
    assert admissible(seeded(), "cross_examine") is False
    assert admissible(seeded(), "risk_memo") is False


def test_both_local_templates_are_recognised_as_untrusted():
    assert UNTRUSTED_TEMPLATES == frozenset({STUB_TEMPLATE, SEEDED_TEMPLATE})


def test_a_seeded_stamp_is_identifiable_in_the_ledger():
    """A reader can tell a seeded document from a screened one at a glance."""
    assert seeded().template == "local-seed"
    assert "local" in seeded().summary()


# --- The adversary is not seedable -------------------------------------------------------------


def test_nimbuswrite_cannot_be_seeded():
    """Its document must go through real screening. A seeded adversary would be the one fixture
    in the pack that lies about how it reached the clean bucket."""
    with pytest.raises(NotSeedable, match="planted payload"):
        seed_clean_evidence("some-review", "nimbuswrite")


def test_only_the_two_clean_vendors_are_seedable():
    assert set(SEEDABLE_VENDORS) == {"cleancloud", "datadynamo"}
