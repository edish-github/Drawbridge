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
CONSOLE_APP = APP / "(app)"

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

FORBIDDEN_TO_WRITE = frozenset(
    {
        # The one collection the console must never author. An approval is a named person
        # accepting security risk; it is signed by a separate service that re-verifies the caller,
        # and a surface every reviewer can reach must not be able to write one directly. If this
        # set ever loses this entry, the human gate becomes decoration.
        "approvals",
        # Findings and scores are the fleet's conclusions. A console that could edit them could
        # make a vendor look safe without changing anything about the vendor.
        "findings",
        "scores",
        "memos",
        "screenings",
        "evidence_chunks",
        "subprocessors",
        # Durable memory shapes every future review of a vendor. It is written at close-out, from
        # what the review actually concluded, and never by hand.
        "dossiers",
        # Where single use is recorded. A console that could clear a spend could replay an
        # approval.
        "approval_tokens_spent",
    }
)
"""Collections the console may not write, whatever its caller's role.

The complement — reviews, vendors, events, decisions, dashboard_events, qa_responses — is the
*workflow* surface: opening a review, marking a reply thread finished, resolving a park. Those are
things an analyst does, and a product where they need a terminal is a product nobody can use.

The line is drawn here rather than by role, because a role check is a runtime decision and this is
a structural one. An administrator may not edit a finding either.
"""


def console_sources():
    for path in [*LIB.rglob("*.ts"), *APP.rglob("*.tsx"), *APP.rglob("*.ts")]:
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        yield path


def test_the_console_never_writes_a_collection_it_must_not_author():
    """The narrow claim, and the one that matters.

    The console gained write paths when it gained a signup page — a product where opening a review
    requires a terminal is not a product. What it did not gain is the ability to author a
    conclusion or an approval, and that is asserted structurally rather than left to a role check.
    """
    offenders = []
    for path in console_sources():
        source = path.read_text()
        for match in CHAIN.finditer(source):
            called = set(re.findall(r"\.\s*(\w+)\s*\(", match.group(1)))
            if not called & set(WRITE_METHODS):
                continue
            target = re.match(r'\b(?:collection|doc)\s*\(\s*"([^"]+)"', match.group(0))
            if target and target.group(1) in FORBIDDEN_TO_WRITE:
                offenders.append(f"{path.relative_to(REPO)} writes {target.group(1)}")

    assert not offenders, f"the console writes collections it must not author: {offenders}"


def test_no_console_source_mentions_the_approvals_collection_in_a_write():
    """Belt and braces on the one that matters most.

    The check above matches a literal collection name in the chain. This one catches the variable
    indirection — `scoped(org, name).set(...)` where `name` came from somewhere — by requiring
    that the string "approvals" never appears in the same file as a write helper.
    """
    for path in console_sources():
        source = path.read_text()
        if '"approvals"' in source and (".set(" in source or ".add(" in source):
            # `lib/ledger.ts` reads approvals to display them, and holds no write at all.
            assert "export async function approvals" in source, (
                f"{path.relative_to(REPO)} names the approvals collection beside a write"
            )


EXPECTED_ROUTES = {
    "api/session/route.ts",        # exchange a verified identity for a session cookie
    "api/session/token/route.ts",  # mint a short-lived token for a service-to-service hop
    "api/signup/route.ts",         # create an organisation; sign in outside cloud
    "api/workspace/route.ts",      # switch the session to another organisation
    "api/reviews/route.ts",        # open a review
    "api/actions/route.ts",        # the workflow actions an analyst performs
    "api/approve/route.ts",        # proxy to the approval service; signs nothing itself
    "api/binders/[id]/route.ts",   # proxy to the binder service
    "logout/route.ts",             # clears the session cookie
}
"""Every route the console exposes, listed on purpose.

There used to be none, and "no API routes" was the security claim. It stopped being true the
moment the product needed a signup page, and a claim that quietly stops being true is worse than
one that was never made. So the list is enumerated instead: a new route is a deliberate edit here,
and this test is the review.
"""


def test_the_console_exposes_only_the_routes_it_declares():
    found = {
        str(p.relative_to(APP))
        for p in APP.rglob("route.ts*")
        if "node_modules" not in p.parts
    }
    assert found == EXPECTED_ROUTES, (
        f"unexpected: {sorted(found - EXPECTED_ROUTES)}; missing: {sorted(EXPECTED_ROUTES - found)}"
    )


@pytest.mark.parametrize(
    "route",
    sorted(EXPECTED_ROUTES - {"api/session/route.ts", "api/signup/route.ts", "logout/route.ts"}),
)
def test_every_route_that_acts_checks_who_is_asking(route):
    """A route that writes without resolving a principal is an unauthenticated write.

    Signup and session are excluded because they are how a principal comes to exist; every other
    route resolves one before it does anything.
    """
    source = (APP / route).read_text()
    assert "currentPrincipal" in source or "unseal" in source, (
        f"{route} does not identify its caller"
    )


def test_the_approval_route_signs_nothing_itself():
    """It proxies to the service that holds the key, and does not decide the outcome.

    The role check in that route is a courtesy — it turns a request that would be refused into a
    clear message before a network hop. The control is the approval service re-verifying the
    caller, because a service that trusted this one would have its security bounded by this one
    being correct.
    """
    source = (APP / "api/approve/route.ts").read_text()

    assert "APPROVALS_URL" in source
    assert "createSign" not in source and "privateKey" not in source
    assert "collection(\"approvals\")" not in source


def test_the_console_never_imports_the_signing_path():
    """The approval service holds the private half. Nothing on this surface goes near it."""
    for path in console_sources():
        source = path.read_text().lower()
        assert "drawbridge-approval-key" not in source
        assert "sign_stamp" not in source


# --- Every page the navigation offers exists ---------------------------------------------------


def test_every_nav_destination_resolves():
    """A sidebar link with nothing behind it is a 404 somebody finds in front of a customer.

    Two shapes count as resolving: a page inside the console shell, and a route handler outside it
    — `/logout` is the second, because clearing a cookie is something only a handler may do.
    """
    nav = (APP / "nav.tsx").read_text()
    hrefs = set(re.findall(r'href="(/[a-z-]*)"', nav))

    missing = []
    for href in sorted(hrefs):
        segment = href.strip("/")
        candidates = [
            CONSOLE_APP / "page.tsx" if not segment else CONSOLE_APP / segment / "page.tsx",
            APP / segment / "page.tsx",
            APP / segment / "route.ts",
        ]
        if not any(c.exists() for c in candidates):
            missing.append(href)

    assert not missing, f"the sidebar links nowhere for: {missing}"


@pytest.mark.parametrize(
    "route",
    [
        "page.tsx",
        "queue/page.tsx",
        "vendors/page.tsx",
        "vendors/[id]/page.tsx",
        "reviews/new/page.tsx",
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
    """The signed-in application. All of it lives in the (app) route group, so the shell wraps
    it and nothing outside it."""
    assert (CONSOLE_APP / route).exists(), f"{route} is missing"


@pytest.mark.parametrize(
    "route", ["login/page.tsx", "signup/page.tsx", "workspaces/page.tsx", "logout/route.ts"]
)
def test_the_authentication_screens_are_outside_the_shell(route):
    """A shell whose navigation points into a workspace is the wrong thing to render to somebody
    who has not chosen one — or who has just been told they are no longer a member."""
    assert (APP / route).exists(), f"{route} is missing"
    assert not (CONSOLE_APP / route).exists(), f"{route} must not be inside the console shell"


def test_every_console_page_resolves_a_principal():
    """A page that renders without one is a page that leaks a workspace to a stranger."""
    unguarded = [
        str(p.relative_to(CONSOLE_APP))
        for p in CONSOLE_APP.rglob("page.tsx")
        if "requirePrincipal" not in p.read_text() and "requireCapability" not in p.read_text()
    ]
    assert not unguarded, f"these pages render without resolving a principal: {unguarded}"


def test_the_console_shell_guards_itself():
    """Belt and braces: a page added later that forgets its own guard still cannot render inside
    an authenticated shell."""
    assert "requirePrincipal" in (CONSOLE_APP / "layout.tsx").read_text()


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
    """The card says what signs, and what does not. It used to print a shell command; it now has
    a control, and the claim underneath it has to survive that change."""
    body = (CONSOLE_APP / "reviews" / "[id]" / "gate" / "page.tsx").read_text()

    assert "holds no signing key" in body
    assert "single-use" in body
    assert "verifies you independently" in body


def test_approving_requires_the_approver_role_and_a_step_up():
    """An approval is a named person accepting risk. A session cookie left open on an unattended
    laptop is not evidence that the named person was present, so the signature is bound to a fresh
    proof rather than to a browser."""
    control = (CONSOLE_APP / "reviews" / "[id]" / "gate" / "approve.tsx").read_text()

    assert "canApprove" in control
    assert "password" in control.lower()
    assert "never stored" in control or "sent once" in control


# --- The screens behave as the product claims ----------------------------------------------------


def test_the_queue_defaults_to_the_reviews_that_need_a_person():
    """The product's claim is that humans appear only at decision points. A queue that opened on
    everything would be showing work nobody has to do."""
    body = (CONSOLE_APP / "queue" / "page.tsx").read_text()

    assert 'filter = "needs-you"' in body
    assert "needsYou" in body


@pytest.mark.parametrize(
    "screen",
    ["reviews/[id]/page.tsx", "reviews/[id]/graph/page.tsx", "activity/page.tsx"],
)
def test_expansions_work_without_javascript(screen):
    """An expansion that needs hydration is one that fails in a screenshot, in print, and on the
    machine where the bundle did not load five minutes before recording."""
    body = (CONSOLE_APP / screen).read_text()

    assert "<details" in body and "<summary>" in body
    assert '"use client"' not in body


EXPECTED_CLIENT_COMPONENTS = {
    "app/nav.tsx",                          # needs the URL to know which leaf is lit
    "app/login/form.tsx",                   # a form
    "app/signup/form.tsx",                  # a form
    "app/workspaces/switcher.tsx",          # a form
    "app/(app)/reviews/new/form.tsx",       # a form with a live tier preview
    "app/(app)/reviews/[id]/gate/approve.tsx",  # a form with a step-up
}
"""Every component that ships JavaScript, enumerated.

The rule is not "no client components" — forms need one. The rule is that **no page** is a client
component: every screen renders on the server from a Firestore read, so a page that shipped a
bundle would be a page that can be slow and a page that could leak a query to the browser. The
client components here are all leaves inside a server-rendered page.
"""


def test_only_the_declared_leaves_ship_javascript():
    client = {
        str(p.relative_to(CONSOLE)).removeprefix("app/").join(("app/", ""))
        if False
        else str(p.relative_to(CONSOLE))
        for p in console_sources()
        if '"use client"' in p.read_text()
    }
    assert client == EXPECTED_CLIENT_COMPONENTS, (
        f"unexpected: {sorted(client - EXPECTED_CLIENT_COMPONENTS)}; "
        f"missing: {sorted(EXPECTED_CLIENT_COMPONENTS - client)}"
    )


def test_no_page_is_a_client_component():
    """The rule that matters. A form may hydrate; a screen may not."""
    offenders = [
        str(p.relative_to(CONSOLE))
        for p in APP.rglob("page.tsx")
        if '"use client"' in p.read_text()
    ]
    assert not offenders, offenders


@pytest.mark.parametrize("field", ["Agent", "Graph node", "Idempotency key", "Trace"])
def test_an_expanded_entry_shows_the_mechanism(field):
    """The idempotency key, the trace id and the graph node are the product. They go on the
    screen in mono, not behind a disclosure nobody opens."""
    body = (CONSOLE_APP / "reviews" / "[id]" / "page.tsx").read_text()
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
