"""Post-approval monitoring, and the noise it refuses to make.

The review does not end at signature. What makes that a feature rather than an alert firehose
is the set of things the Watchdog declines to do: it does not match on a bare company name, it
does not open a review on a signal it is unsure about, it does not edit a decided review, and
it does not let an allowlisted host put unscreened text in front of a model.

Those four refusals are what most of this file asserts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from agents.watchdog.agent import (
    already_seen,
    collect,
    open_rereview,
    resolve_vendor,
    triage,
)
from agents.watchdog.relevance import Action, evaluate, matches_identity
from agents.watchdog.sources import EXPIRY_WARNING_DAYS, Signal, expiry_signals
from shared import tenancy
from shared.context import AgentContext
from shared.domain import MemoryNote, RelevanceJudgement, Review, ReviewState, Vendor
from tests.conftest import emulator_required

TODAY = date(2026, 8, 16)

VENDOR = Vendor(
    vendor_id="northgatecloud",
    name="Northgate Cloud",
    legal_entity_name="Northgate Cloud Infrastructure Ltd",
    primary_domain="northgate-cloud.example",
    category="Hosting and storage",
    tier=2,
)


def ctx(review_id: str = "watchdog") -> AgentContext:
    return AgentContext(
        org_id=tenancy.current_org(),
        review_id=review_id,
        agent="watchdog",
        trace_id="t",
    )


def feed_signal(**overrides) -> Signal:
    return Signal(
        **{
            "signal_id": "feed:1",
            "source": "feed:www.cisa.gov",
            "vendor_hint": "",
            "title": "Breach disclosed",
            "body": "",
            **overrides,
        }
    )


@pytest.fixture
def decided(review_id, db):
    """A vendor with one decided review, which is what makes it a monitoring subject."""
    db.collection("vendors").document(VENDOR.vendor_id).set(VENDOR.model_dump(mode="json"))
    db.collection("reviews").document(review_id).set(
        Review(
            review_id=review_id,
            vendor_id=VENDOR.vendor_id,
            state=ReviewState.DECIDED,
            tier=2,
            opened_at=datetime.now(UTC),
            decided_at=datetime.now(UTC),
        ).model_dump(mode="json")
    )
    return review_id


def remember_expiry(vendor_id: str, expires_at: date, certificate: str = "ISO-27001-NC-1") -> None:
    from shared.memory import remember

    remember(
        vendor_id,
        MemoryNote(
            vendor_id=vendor_id,
            type="cert_expiry",
            provenance="rule",
            value={"certificate": certificate, "expires_at": expires_at.isoformat()},
            at=datetime.now(UTC),
        ),
    )


# --- Identity, not name ------------------------------------------------------------------------


def test_a_shared_word_is_not_a_match():
    """"Northgate" appears in unrelated news constantly. This is the guard that stops a
    monitoring feature becoming the one everybody mutes."""
    signal = feed_signal(title="Northgate Retail Group reports strong quarter")

    assert not matches_identity(signal, VENDOR.primary_domain, VENDOR.legal_entity_name)


def test_the_registered_domain_matches():
    signal = feed_signal(
        title="Incident disclosed", url="https://status.northgate-cloud.example/2026-08"
    )

    assert matches_identity(signal, VENDOR.primary_domain, VENDOR.legal_entity_name)


def test_the_whole_legal_entity_matches():
    signal = feed_signal(title="Northgate Cloud Infrastructure Ltd confirms unauthorised access")

    assert matches_identity(signal, VENDOR.primary_domain, VENDOR.legal_entity_name)


def test_a_vendor_with_neither_identifier_produces_no_automatic_review():
    """An unidentifiable vendor is the conservative direction: nothing opens on its own."""
    assert not matches_identity(feed_signal(title="Anything at all"), None, None)


def test_an_unmatched_signal_never_reaches_the_model(monkeypatch):
    from agents.watchdog import relevance

    monkeypatch.setattr(
        relevance, "judge", lambda *a, **k: pytest.fail("the model was called on a non-match")
    )

    assert evaluate(ctx(), VENDOR, feed_signal(title="Unrelated news")) is Action.DISCARD


# --- The confidence threshold --------------------------------------------------------------------


def judging(monkeypatch, judgement):
    from agents.watchdog import relevance

    monkeypatch.setattr(relevance, "judge", lambda *a, **k: judgement)


def test_a_high_confidence_material_signal_opens_a_review(monkeypatch):
    judging(
        monkeypatch,
        RelevanceJudgement(relevant=True, confidence=0.94, reason="breach", source_url="u"),
    )
    signal = feed_signal(title="Northgate Cloud Infrastructure Ltd discloses a breach")

    assert evaluate(ctx(), VENDOR, signal) is Action.OPEN_REREVIEW


def test_a_low_confidence_signal_goes_to_triage_rather_than_opening_anything(monkeypatch):
    """A re-review opened on a false positive costs an analyst an hour and destroys trust in
    the feature. Below the threshold a person looks at it instead."""
    judging(
        monkeypatch,
        RelevanceJudgement(relevant=True, confidence=0.41, reason="maybe", source_url="u"),
    )
    signal = feed_signal(title="Northgate Cloud Infrastructure Ltd named in filing")

    assert evaluate(ctx(), VENDOR, signal) is Action.TRIAGE


def test_a_failed_relevance_call_is_triage_not_a_guess(monkeypatch):
    """Failing open opens reviews on noise; failing closed silently discards a real breach."""
    judging(monkeypatch, None)
    signal = feed_signal(title="Northgate Cloud Infrastructure Ltd outage")

    assert evaluate(ctx(), VENDOR, signal) is Action.TRIAGE


def test_a_signal_judged_irrelevant_is_discarded(monkeypatch):
    judging(
        monkeypatch,
        RelevanceJudgement(relevant=False, confidence=0.9, reason="funding round", source_url="u"),
    )
    signal = feed_signal(title="Northgate Cloud Infrastructure Ltd raises Series C")

    assert evaluate(ctx(), VENDOR, signal) is Action.DISCARD


# --- Expiry math, which needs no network ---------------------------------------------------------


@emulator_required
def test_a_certificate_inside_the_window_raises_a_signal(decided):
    remember_expiry(VENDOR.vendor_id, TODAY + timedelta(days=30))

    signals = expiry_signals(TODAY, [VENDOR.vendor_id])

    assert len(signals) == 1
    assert signals[0].source == "dossier"
    assert "expires in 30 days" in signals[0].title


@emulator_required
def test_a_certificate_beyond_the_window_raises_nothing(decided):
    remember_expiry(VENDOR.vendor_id, TODAY + timedelta(days=EXPIRY_WARNING_DAYS + 1))

    assert expiry_signals(TODAY, [VENDOR.vendor_id]) == []


@emulator_required
def test_an_expired_certificate_says_so(decided):
    remember_expiry(VENDOR.vendor_id, TODAY - timedelta(days=5))

    assert "expired 5 days ago" in expiry_signals(TODAY, [VENDOR.vendor_id])[0].title


@emulator_required
def test_expiry_math_needs_no_model_and_no_network(decided, monkeypatch):
    """The half of the sweep that works when every feed is down, which is also the half that is
    certain rather than probable."""
    from shared import routing

    monkeypatch.setattr(
        routing, "generate", lambda *a, **k: pytest.fail("expiry math called a model")
    )
    remember_expiry(VENDOR.vendor_id, TODAY + timedelta(days=10))
    signal = expiry_signals(TODAY, [VENDOR.vendor_id])[0]

    assert evaluate(ctx(), VENDOR, signal) is Action.OPEN_REREVIEW


# --- A decided review is immutable ----------------------------------------------------------------


@emulator_required
def test_a_hit_opens_a_new_linked_review_and_edits_nothing(decided, db):
    """A decided review records what a named person decided on a named date against a named set
    of evidence. Editing it six months later would make the binder a document about the present."""
    before = db.collection("reviews").document(decided).get().to_dict()
    signal = feed_signal(source="dossier", vendor_hint=VENDOR.vendor_id, title="cert expired")

    new_id = open_rereview(ctx(), VENDOR, signal)

    after = db.collection("reviews").document(decided).get().to_dict()
    opened = db.collection("reviews").document(new_id).get().to_dict()

    assert after == before, "the closed review was modified"
    assert opened["reopened_from"] == decided
    assert opened["state"] == ReviewState.INTAKE.value
    assert opened["opened_by"] == "watchdog"


@emulator_required
def test_the_same_signal_never_opens_two_reviews(decided, db):
    """The same breach reported by two outlets is two signals and one event, and a portfolio
    sweep that ran twice in a day must not open two reviews for it."""
    signal = feed_signal(
        signal_id=f"expiry:{decided}",
        source="dossier",
        vendor_hint=VENDOR.vendor_id,
        title="cert expired",
    )

    first, _, _ = triage(ctx(), [signal])
    second, _, _ = triage(ctx(), [signal])

    assert len(first) == 1
    assert second == []
    assert already_seen(signal.signal_id)


@emulator_required
def test_a_signal_naming_nobody_the_org_bought_from_resolves_to_no_vendor(decided):
    assert resolve_vendor(feed_signal(title="Some other company had a breach")) is None


# --- Fetched content is untrusted --------------------------------------------------------------


def test_relevance_is_an_external_input_task():
    """P3 bounds where the fleet fetches from; it says nothing about what comes back. Tool
    poisoning is the third threat Model Armor names and the only one no other path covers."""
    from shared.routing import EXTERNAL_INPUT_TASKS

    assert "relevance" in EXTERNAL_INPUT_TASKS


def test_the_fetch_is_registered_as_a_gateway_tool_and_holds_no_allowlist():
    """A tool that checked its own allowlist would be a tool that could be persuaded to stop."""
    import inspect

    from agents.watchdog import fetch
    from shared.gateway import TOOL_REGISTRY

    assert TOOL_REGISTRY[fetch.TOOL_FETCH_URL] is fetch.fetch_url
    assert "FEED_ALLOWLIST" not in inspect.getsource(fetch)


def test_a_host_outside_the_allowlist_is_refused_before_anything_is_fetched():
    from agents.watchdog.sources import fetch_feed_signals
    from shared.gateway import PolicyViolation

    with pytest.raises(PolicyViolation) as exc:
        fetch_feed_signals("not-a-feed.example", 7, ctx())

    assert exc.value.policy == "P3"


def test_nothing_hands_a_fetched_body_on_before_it_is_screened():
    """An import-graph assertion rather than a behavioural one, because the ordering is the
    control: there is exactly one path from a fetch to a signal and it goes through screening."""
    import inspect

    from agents.watchdog import sources

    body = inspect.getsource(sources.fetch_feed_signals)
    screening = body.index("screen_fetched")
    parsing = body.index("_parse(body")

    assert screening < parsing, "the fetched body reached the parser before it was screened"


# --- The sweep never degrades an active review -------------------------------------------------


@emulator_required
def test_a_feed_outage_is_logged_and_skipped(decided, monkeypatch):
    from agents.watchdog import agent as watchdog

    remember_expiry(VENDOR.vendor_id, TODAY + timedelta(days=10))
    monkeypatch.setattr(
        watchdog, "fetch_feed_signals", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("down"))
    )

    signals = collect(ctx(), TODAY, [VENDOR.vendor_id])

    assert len(signals) == 1, "the expiry signal survived every feed being down"
