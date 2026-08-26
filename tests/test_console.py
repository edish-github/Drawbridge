"""The operator console, against the backend it renders.

The console is TypeScript and the fleet is Python, so three things reach the screen through
generated files: the review graph, the policy the fleet runs on, and a handful of constants
restated in `lib/ledger.ts` because they are needed in both languages. Every one of those is a
place the two can silently disagree, and a console that disagreed with the ledger would be the
most damaging artefact in this repository — a number on screen that nothing computed.

So: regenerate and diff, or read the constant out of the source and compare.

The last two tests are about what the console cannot do. It holds no write path and no signing
key, which is a security claim in the README, in the architecture document and on the gate card
itself. A claim asserted in three documents and nowhere in code is a claim one hurried commit
away from being false.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CONSOLE = REPO / "services" / "dashboard"
LIB = CONSOLE / "lib"
APP = CONSOLE / "app"

POLICY_JSON = LIB / "policy.json"
LEDGER_TS = LIB / "ledger.ts"


# --- The generated artefacts are fresh --------------------------------------------------------


def test_the_committed_policy_json_is_not_stale():
    """``make console`` regenerates it; this fails when somebody edited the rubric and forgot."""
    from scripts.policy_dump import build

    committed = json.loads(POLICY_JSON.read_text())
    assert committed == json.loads(json.dumps(build())), (
        "services/dashboard/lib/policy.json no longer matches the files it is generated from. "
        "Run `make console` and commit the result."
    )


# --- Constants restated in TypeScript match their source --------------------------------------


def test_the_band_boundaries_match_the_rubric():
    """The console decides which colour a score is. The rubric decides which band it is.

    Two numbers that must be one. The console cannot import YAML, so it restates them — and a
    restatement nothing checks is how a score renders green on a screen while the binder calls it
    conditional.
    """
    rubric = yaml.safe_load((REPO / "agents" / "risk_scorer" / "rubric.yaml").read_text())
    source = LEDGER_TS.read_text()

    match = re.search(r"export const BANDS = \{ approve: (\d+), conditional: (\d+) \}", source)
    assert match, "lib/ledger.ts no longer exports BANDS in the expected shape"

    assert int(match.group(1)) == rubric["bands"]["approve"]
    assert int(match.group(2)) == rubric["bands"]["conditional"]


def test_every_review_state_has_a_tone_in_the_console():
    """A state the console does not recognise renders in the default colour and reads as normal.

    ``NEEDS_HUMAN`` falling through to blue would make a parked review look like one in flight,
    on the screen whose entire job is showing which reviews need a person.
    """
    from shared.domain import ReviewState

    source = LEDGER_TS.read_text()
    body = re.search(r"export function stateTone\(.*?\n\}", source, re.DOTALL)
    assert body, "lib/ledger.ts no longer defines stateTone"

    # Every state is either named explicitly or covered by the documented default.
    named = set(re.findall(r'state === "([a-z_]+)"', body.group(0)))
    unnamed = {s.value for s in ReviewState} - named
    assert unnamed <= {"intake", "questionnaire_out", "replies_in"}, (
        f"these states fall through to the default tone without being considered: {sorted(unnamed)}"
    )


# --- The console reads and never writes -------------------------------------------------------


WRITE_METHODS = ("set", "add", "update", "delete", "create")
"""Firestore mutations. Matched on a *reference chain* rather than anywhere in the file.

The first version of this check looked for `.set(` in the source and failed on `Map.set` — which
is not a write to anything, and a check that cries wolf is a check somebody deletes. What it
looks for now is a write method reached from a `collection(...)` or `doc(...)` call, which is the
only way this codebase can produce a Firestore mutation.
"""

CHAIN = re.compile(r"\b(?:collection|doc)\s*\([^)]*\)((?:\s*\.\s*\w+\s*\([^()]*\))*)")


def console_sources():
    for path in [*LIB.rglob("*.ts"), *APP.rglob("*.tsx"), *APP.rglob("*.ts")]:
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        yield path


def test_the_console_holds_no_firestore_write():
    """A surface every reviewer can reach must not be one that can act.

    Asserted against the source tree rather than trusted, because the write that breaks this is
    the one somebody adds to "just mark it read" — and it would be a perfectly ordinary line of
    code that quietly makes the gateway's refusal to sign decorative.
    """
    offenders = []
    for path in console_sources():
        for match in CHAIN.finditer(path.read_text()):
            called = set(re.findall(r"\.\s*(\w+)\s*\(", match.group(1)))
            for write in called & set(WRITE_METHODS):
                offenders.append(f"{path.relative_to(REPO)}: .{write}() on a Firestore reference")

    assert not offenders, f"the console writes: {offenders}"


def test_the_console_has_no_api_routes():
    """No route handler, so there is no server surface behind the read-only pages at all."""
    handlers = [
        p.relative_to(REPO)
        for p in APP.rglob("route.ts*")
        if "node_modules" not in p.parts
    ]
    assert not handlers, f"the console defines API routes: {handlers}"


def test_the_console_never_imports_the_signing_path():
    """The approval service holds the private half. Nothing on this surface goes near it."""
    for path in console_sources():
        source = path.read_text().lower()
        assert "drawbridge-approval-key" not in source
        assert "sign_stamp" not in source


# --- Every page the navigation offers exists ---------------------------------------------------


def test_every_nav_destination_is_a_real_page():
    """A sidebar link with no page behind it is a 404 an operator finds during a demo."""
    nav = (APP / "nav.tsx").read_text()
    hrefs = set(re.findall(r'href="(/[a-z-]*)"', nav))

    missing = []
    for href in sorted(hrefs):
        segment = href.strip("/")
        page = APP / "page.tsx" if not segment else APP / segment / "page.tsx"
        if not page.exists():
            missing.append(href)

    assert not missing, f"the sidebar links to pages that do not exist: {missing}"


@pytest.mark.parametrize(
    "route",
    [
        "page.tsx",
        "queue/page.tsx",
        "vendors/page.tsx",
        "vendors/[id]/page.tsx",
        "reviews/[id]/page.tsx",
        "reviews/[id]/gate/page.tsx",
        "reviews/[id]/graph/page.tsx",
        "monitoring/page.tsx",
        "evidence/page.tsx",
        "findings/page.tsx",
        "binders/page.tsx",
        "agents/page.tsx",
        "activity/page.tsx",
        "settings/page.tsx",
    ],
)
def test_the_designed_screen_exists(route):
    """Eleven screens were designed; these are the routes they became."""
    assert (APP / route).exists(), f"{route} is missing"


# --- The fonts are self-hosted ------------------------------------------------------------------


def test_the_console_depends_on_no_font_host():
    """A demo that depends on fonts.googleapis.com renders in Arial on conference wifi.

    Both faces were extracted from the design bundle into `public/fonts`, so the console needs no
    network at all beyond the emulator it reads.
    """
    # Comments stripped first: this file explains *why* it does not use a font host, and a naive
    # substring check fails on its own rationale.
    css = re.sub(r"/\*.*?\*/", "", (APP / "globals.css").read_text(), flags=re.DOTALL)
    layout = (APP / "layout.tsx").read_text()

    for host in ("fonts.googleapis.com", "fonts.gstatic.com"):
        assert host not in css, f"globals.css reaches {host}"
        assert host not in layout, f"layout.tsx reaches {host}"
    assert "/fonts/" in css

    for family in ("plus-jakarta-sans", "jetbrains-mono"):
        assert list((CONSOLE / "public" / "fonts").glob(f"{family}-*.woff2")), (
            f"{family} is referenced but not present in public/fonts"
        )


# --- What the console cannot mint ---------------------------------------------------------------


def test_the_console_cannot_mint_an_approval():
    """The gateway refuses to sign and ``shared.approvals`` exposes no minting function.

    A button here that issued a token would undo both. The gate card *prints* the command that
    issues one, as text a reader copies into a terminal — naming the authority is the opposite of
    holding it — so a monospace block is allowed and a call is not.
    """
    forbidden = ("issueToken", "mintApproval", "privateKey", "createSign")

    for path in console_sources():
        body = path.read_text()
        for name in forbidden:
            assert name not in body, f"{path.relative_to(REPO)} references {name}"


def test_the_gate_card_states_where_the_authority_lives():
    """A disabled button with no explanation reads as a bug. This one says why."""
    body = (APP / "reviews" / "[id]" / "gate" / "page.tsx").read_text()

    assert "holds no signing key" in body
    assert "single-use" in body
    assert "scripts.issue_token" in body


# --- The screens behave as the product claims ----------------------------------------------------


def test_the_queue_defaults_to_the_reviews_that_need_a_person():
    """The product's claim is that humans appear only at decision points. A queue that opened on
    everything would be showing work nobody has to do."""
    body = (APP / "queue" / "page.tsx").read_text()

    assert 'filter = "needs-you"' in body
    assert "needsYou" in body


@pytest.mark.parametrize(
    "screen",
    ["reviews/[id]/page.tsx", "reviews/[id]/graph/page.tsx", "activity/page.tsx"],
)
def test_expansions_work_without_javascript(screen):
    """An expansion that needs hydration is one that fails in a screenshot, in print, and on the
    machine where the bundle did not load five minutes before recording."""
    body = (APP / screen).read_text()

    assert "<details" in body and "<summary>" in body
    assert '"use client"' not in body


def test_only_the_navigation_is_a_client_component():
    """It needs the URL to know which leaf is lit, and nothing else. Every other screen reads
    Firestore on the server, so a page that shipped a bundle would be a page that can be slow."""
    client = [
        p.relative_to(REPO)
        for p in console_sources()
        if '"use client"' in p.read_text()
    ]
    assert client == [Path("services/dashboard/app/nav.tsx")], client


@pytest.mark.parametrize("field", ["Agent", "Graph node", "Idempotency key", "Trace"])
def test_an_expanded_entry_shows_the_mechanism(field):
    """The idempotency key, the trace id and the graph node are the product. They go on the
    screen in mono, not behind a disclosure nobody opens."""
    body = (APP / "reviews" / "[id]" / "page.tsx").read_text()
    assert f'"{field}"' in body


# --- One colour language across the two artefacts a customer sees ---------------------------------


def test_the_console_and_the_binder_share_a_palette():
    """The screen an analyst decides on and the document an auditor receives.

    These are the two artefacts a customer sees, and a red rule has to mean the same thing in
    both. The architecture diagrams keep the older slate palette on purpose — they are
    documentation *about* the system rather than output *from* it — and that split is recorded in
    the comment above the binder's stylesheet.
    """
    css = (APP / "globals.css").read_text()
    binder = (REPO / "services" / "binder" / "render.py").read_text()

    expected = {
        "--ink": "#191a1c",
        "--mute": "#6f6c66",
        "--faint": "#96938c",
        "--shell": "#f6f4f0",
        "--ok": "#3d8a67",
        "--warn": "#a9722a",
        "--danger": "#bb4a3a",
        "--accent": "#4a6ca4",
        "--violet": "#6a57a3",
    }

    # The console names its hues semantically (--green, --red) and the binder names them by role
    # (--ok, --danger). The values are what must agree, not the names.
    console_values = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-f]{6})", css))
    for token, value in expected.items():
        assert f"{token}: {value}" in binder, f"{token} drifted in the binder"
        assert value in console_values.values(), f"{value} ({token}) is not in the console"


def test_focus_rings_are_never_removed():
    css = (APP / "globals.css").read_text()

    assert "outline: none" not in css
    assert ":focus-visible" in css


def test_motion_respects_the_reduced_motion_preference():
    """Six keyframe animations, and a person who asked their system not to move things."""
    css = (APP / "globals.css").read_text()
    assert "prefers-reduced-motion" in css


# --- The toolchain -------------------------------------------------------------------------


def test_the_console_declares_its_dependencies():
    manifest = json.loads((CONSOLE / "package.json").read_text())

    assert "next" in manifest["dependencies"]
    assert "@google-cloud/firestore" in manifest["dependencies"]
    assert manifest["private"] is True


def test_the_ledger_client_refuses_to_run_outside_the_emulator():
    """A client library that falls through to a real project gives no indication that it did."""
    body = LEDGER_TS.read_text()

    assert "FIRESTORE_EMULATOR_HOST" in body
    assert re.search(r"if \(!process\.env\.FIRESTORE_EMULATOR_HOST\)", body)
