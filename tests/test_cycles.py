"""Bounded cycles, executed rather than declared.

``tests/test_graph.py`` asserts every declared cycle has a budget. This asserts the two that are
enforced at runtime actually stop — and, for the re-review cycle, that stopping does not mean
the vendor falls out of monitoring.

The re-review cycle is the one worth the file. Three of the four cycles in the graph terminate
by arithmetic: three chase rounds, one re-ask per answer, three tiers and a tier that only ever
rises. Re-review has no such bound. Every reopening is a legitimate new review by every rule the
system has, so a vendor in a noisy news cycle can be reopened until somebody notices the cost —
and "until somebody notices" is not a termination condition.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agents.watchdog.agent import chain_depth, open_rereview, rereview_budget
from shared.context import AgentContext
from shared.domain import Vendor
from tests.conftest import emulator_required


def vendor(vendor_id: str) -> Vendor:
    """A vendor id unique to the test.

    Not the seeded ``datadynamo``: ``latest_review_id`` picks the newest decided review a vendor
    has, and a demo run leaves several. A test that shared the vendor would be measuring the
    demo's chain rather than the one it built.
    """
    return Vendor(
        vendor_id=vendor_id,
        name="Chain Test Vendor",
        category="logistics",
        primary_domain=f"{vendor_id}.example",
        tier=1,
    )


def signal(vendor_id: str):
    from agents.watchdog.sources import Signal

    return Signal(
        signal_id=f"sig-{vendor_id}",
        source="www.cisa.gov",
        title="Advisory naming the vendor",
        url="https://www.cisa.gov/advisory/1",
        published="2026-03-01",
        vendor_hint=vendor_id,
    )


def chain(db, vendor_id: str, depth: int) -> str:
    """Write a chain of ``depth`` linked decided reviews and return the newest id."""
    previous = None
    newest = ""
    for i in range(depth + 1):
        review_id = f"chain-{vendor_id}-{i}"
        db.collection("reviews").document(review_id).set(
            {
                "review_id": review_id,
                "vendor_id": vendor_id,
                "state": "decided",
                "tier": 1,
                "plan_version": 1,
                "opened_at": datetime(2026, 1, 1 + i, tzinfo=UTC).isoformat(),
                "decided_at": datetime(2026, 1, 2 + i, tzinfo=UTC).isoformat(),
                "reopened_from": previous,
            }
        )
        previous = review_id
        newest = review_id
    return newest


# --- test_cycle_budget · counting ------------------------------------------------------------


@emulator_required
def test_a_first_review_has_a_chain_depth_of_zero(db, review_id):
    db.collection("reviews").document(review_id).set(
        {"review_id": review_id, "vendor_id": "solo", "state": "decided"}
    )
    assert chain_depth(review_id) == 0


@emulator_required
def test_chain_depth_counts_the_links_behind_a_review(db, review_id):
    newest = chain(db, f"v{review_id}", depth=3)
    assert chain_depth(newest) == 3


@emulator_required
def test_chain_depth_terminates_on_a_link_that_points_at_itself(db, review_id):
    """A corrupt link must not make the counter the thing that hangs the sweep."""
    db.collection("reviews").document(review_id).set(
        {
            "review_id": review_id,
            "vendor_id": "loopy",
            "state": "decided",
            "reopened_from": review_id,
        }
    )
    assert chain_depth(review_id) == 0


def test_chain_depth_of_nothing_is_zero():
    assert chain_depth(None) == 0


# --- test_cycle_budget · enforcement -----------------------------------------------------------


def cards_for(db, vendor_id: str) -> list[dict]:
    from google.cloud.firestore_v1 import FieldFilter

    return [
        c.to_dict()
        for c in db.collection("dashboard_events")
        .where(filter=FieldFilter("vendor_id", "==", vendor_id))
        .stream()
    ]


def reviews_for(db, vendor_id: str) -> int:
    from google.cloud.firestore_v1 import FieldFilter

    return sum(
        1
        for _ in db.collection("reviews")
        .where(filter=FieldFilter("vendor_id", "==", vendor_id))
        .stream()
    )


@emulator_required
def test_a_re_review_inside_the_budget_opens_a_new_linked_review(db, review_id):
    vendor_id = f"v{review_id}"
    newest = chain(db, vendor_id, depth=0)
    ctx = AgentContext(review_id=newest, agent="watchdog", trace_id="t")

    opened = open_rereview(ctx, vendor(vendor_id), signal(vendor_id))

    assert opened is not None
    record = db.collection("reviews").document(opened).get().to_dict()
    assert record["reopened_from"] == newest
    assert record["state"] == "intake"


@emulator_required
def test_a_re_review_past_the_budget_opens_nothing(db, review_id):
    vendor_id = f"v{review_id}"
    newest = chain(db, vendor_id, depth=rereview_budget().max_iterations)
    ctx = AgentContext(review_id=newest, agent="watchdog", trace_id="t")

    before = reviews_for(db, vendor_id)
    opened = open_rereview(ctx, vendor(vendor_id), signal(vendor_id))

    assert opened is None
    assert reviews_for(db, vendor_id) == before, (
        "the budget must stop the review being created, not just stop returning its id"
    )


@emulator_required
def test_a_spent_budget_raises_a_card_rather_than_going_quiet(db, review_id):
    """A vendor nobody is watching is the outcome the Watchdog exists to prevent.

    The budget changes who decides whether to look again. It does not stop the fleet looking.
    """
    vendor_id = f"v{review_id}"
    newest = chain(db, vendor_id, depth=rereview_budget().max_iterations)
    ctx = AgentContext(review_id=newest, agent="watchdog", trace_id="t")

    open_rereview(ctx, vendor(vendor_id), signal(vendor_id))

    triage = [c for c in cards_for(db, vendor_id) if c.get("kind") == "watchdog_triage"]
    assert triage
    assert any("budget" in str(c.get("reason", "")) for c in triage)


@emulator_required
def test_the_card_says_which_of_the_two_triage_reasons_it_is(db, review_id):
    """A signal under the confidence threshold and a signal over it that arrived past the budget
    both end on a card, and they mean different things to whoever picks it up."""
    from agents.watchdog.agent import raise_triage_card

    vendor_id = f"v{review_id}"
    raise_triage_card(vendor(vendor_id), signal(vendor_id))

    assert any("confidence" in str(c.get("reason", "")) for c in cards_for(db, vendor_id))


# --- The budget is the graph's, not the handler's -------------------------------------------------


def test_the_budget_lives_in_the_graph():
    """One object, so the number a diagram prints and the number the sweep applies are the same."""
    from shared.graph import CYCLES

    assert rereview_budget() in CYCLES
