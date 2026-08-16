"""The dashboard is read-only, and that is structural rather than a convention.

There is no test runner for the Next.js app in this repository and adding one would buy a
component-render assertion at the cost of a second toolchain in CI. What is worth asserting
from here is the property that matters: the surface every operator can reach holds no way to
write, and in particular no way to mint an approval. A dashboard that could approve would make
the whole P1 story decorative — the gateway refuses to sign, ``shared.approvals`` exposes no
minting function, and a button three clicks away that did it anyway would undo both.

The rest is source inspection: the screens the demo needs exist, and the palette is the one the
architecture diagrams use rather than a second colour language invented for the UI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DASHBOARD = REPO / "services" / "dashboard"

SCREENS = {
    "queue": DASHBOARD / "app" / "page.tsx",
    "timeline": DASHBOARD / "app" / "review" / "[id]" / "page.tsx",
    "gate": DASHBOARD / "app" / "review" / "[id]" / "gate" / "page.tsx",
}

WRITE_CALLS = (
    ".set(",
    ".update(",
    ".delete(",
    ".add(",
    ".create(",
    "batch(",
    "runTransaction(",
)


def sources() -> list[Path]:
    return sorted(
        p
        for p in DASHBOARD.rglob("*.ts*")
        if "node_modules" not in p.parts and ".next" not in p.parts
    )


# --- The property that matters ----------------------------------------------------------------


def test_the_dashboard_holds_no_write_path():
    """Read-only over the ledger. A write from here would be a second component owning review
    state, and the state-ownership rule has one owner by design."""
    offenders = []
    for path in sources():
        body = path.read_text()
        for call in WRITE_CALLS:
            if call in body:
                offenders.append(f"{path.relative_to(REPO)} contains {call}")

    assert not offenders, offenders


def test_the_dashboard_cannot_mint_an_approval():
    """The gateway refuses to sign and ``shared.approvals`` exposes no minting function. A
    button here that issued a token would undo both of them."""
    forbidden = ("issue_token", "issueToken", "mintApproval", "sign(", "privateKey")

    for path in sources():
        body = path.read_text()
        for name in forbidden:
            # The gate card prints the shell command that issues a token, as text a reader
            # copies into a terminal. Printing the name of the authority is the opposite of
            # holding it, so a monospace code block is allowed and a call is not.
            if name in body and 'className="command mono"' not in body:
                pytest.fail(f"{path.relative_to(REPO)} references {name}")


def test_the_dashboard_has_no_api_routes():
    """An API route is the shape a write would arrive in. There are none, so there is nothing
    to review each time the UI changes."""
    routes = [
        p for p in DASHBOARD.rglob("route.ts*") if "node_modules" not in p.parts
    ]

    assert routes == []


def test_the_gate_card_states_where_the_authority_lives():
    body = SCREENS["gate"].read_text()

    assert "holds no signing key" in body
    assert "single-use" in body
    assert "scripts.issue_token" in body


# --- The three screens --------------------------------------------------------------------------


@pytest.mark.parametrize("screen", sorted(SCREENS))
def test_each_screen_exists(screen):
    assert SCREENS[screen].is_file()


def test_the_queue_defaults_to_the_reviews_that_need_a_person():
    """The product's claim is that humans appear only at decision points. A queue that opened
    on everything would be showing work nobody has to do."""
    body = SCREENS["queue"].read_text()

    assert 'filter = "needs-you"' in body
    assert "needsYou" in body


def test_the_timeline_expands_without_javascript():
    """An expansion that needs hydration is one that fails in a screenshot, in print, and on
    the machine where the bundle did not load five minutes before recording."""
    body = SCREENS["timeline"].read_text()

    assert "<details>" in body and "<summary>" in body
    assert '"use client"' not in body


@pytest.mark.parametrize(
    "field", ["Agent", "Goal", "Decision", "Trace id", "Idem key", "When"]
)
def test_an_expanded_entry_shows_the_mechanism(field):
    """The idem key, the span id and the policy line are the product. They go on the screen in
    mono, not behind a details disclosure nobody opens."""
    assert f'label="{field}"' in SCREENS["timeline"].read_text()


def test_both_card_kinds_render():
    """A policy block naming what refused and a gate card an operator acts on are different
    things, and both belong on the dashboard."""
    body = SCREENS["gate"].read_text()

    assert "policy_block" in body
    assert "parked" in body


# --- One colour language ---------------------------------------------------------------------


def test_the_palette_is_the_architecture_diagrams_palette():
    """A judge who studied diagram 01 already knows what a red rule means on screen at 1:45,
    and the binder uses the same tokens, so the three artefacts read as one product."""
    css = (DASHBOARD / "app" / "globals.css").read_text()
    binder = (REPO / "services" / "binder" / "render.py").read_text()

    expected = {
        "--ink": "#0f172a",
        "--mute": "#64748b",
        "--line": "#e2e8f0",
        "--shell": "#f8fafc",
        "--accent": "#1d4ed8",
        "--danger": "#b91c1c",
        "--warn": "#b45309",
        "--ok": "#15803d",
        "--human": "#c2410c",
        "--violet": "#6d28d9",
    }

    for token, hex_value in expected.items():
        assert f"{token}: {hex_value}" in css, token
        assert f"{token}: {hex_value}" in binder, f"{token} drifted between binder and dashboard"


def test_focus_rings_are_never_removed():
    css = (DASHBOARD / "app" / "globals.css").read_text()

    assert "outline: none" not in css
    assert ":focus-visible" in css


# --- The toolchain -------------------------------------------------------------------------------


def test_the_dashboard_declares_its_dependencies():
    manifest = json.loads((DASHBOARD / "package.json").read_text())

    assert "next" in manifest["dependencies"]
    assert "@google-cloud/firestore" in manifest["dependencies"]
    assert manifest["private"] is True


def test_the_ledger_client_refuses_to_run_outside_the_emulator():
    """A client library that falls through to a real project gives no indication that it did."""
    body = (DASHBOARD / "lib" / "ledger.ts").read_text()

    assert "FIRESTORE_EMULATOR_HOST" in body
    assert re.search(r"if \(!process\.env\.FIRESTORE_EMULATOR_HOST\)", body)
