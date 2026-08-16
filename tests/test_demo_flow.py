"""The whole of J1, end to end, as one test.

Intake to a decision: tiered, planned, refused at the contact gate, released, emailed once,
replies parsed, evidence retrieved and reconciled, scored with a per-domain breakdown, parked
at the decision gate, released, decided.

Runs in fixtures-only mode, so it costs nothing and can execute on every push. What that proves
is the orchestration — every agent, transition, guard, idempotency key and the whole arithmetic
of the Trust Score run exactly as they do live, with only the model's answers substituted. It
proves nothing about the model's judgement, which is measured separately against the live API.
"""

from __future__ import annotations

import pytest

from scenarios.demo_runner import BeatMissing, beats_for, run
from scenarios.fixtures import responding_from
from shared.clients import firestore_client
from shared.domain import Review
from tests.conftest import emulator_required, pubsub_required


@pytest.fixture(scope="module")
def datadynamo_run():
    """One full run, shared across the assertions that read its result."""
    return run("datadynamo", fixtures_only=True)


@emulator_required
@pubsub_required
def test_every_beat_fires(datadynamo_run):
    """A beat that does not fire is a failure rather than a variation."""
    assert datadynamo_run == beats_for("datadynamo")


@emulator_required
@pubsub_required
def test_the_review_ends_decided_with_a_score(datadynamo_run):
    review = _latest_review()

    assert review["state"] == "decided"
    assert review["score"] is not None
    assert review["band"] in ("approve", "conditional", "escalate")


@emulator_required
@pubsub_required
def test_the_vendor_was_emailed_once_per_plan_version_and_never_twice_for_a_question():
    """DataDynamo re-tiers, so it gets two messages: the Tier 2 set, then the questions the
    re-tier added. Two is the right number and it is the arithmetic that proves it — the second
    message is the difference between the sets, so no question is asked twice and none is
    dropped."""
    review = _latest_review()
    sent = _inbox(review["review_id"])
    asked = [line for message in sent for line in _question_ids(message["body"])]

    assert len(sent) == review["plan_version"] == 2
    assert len(asked) == len(set(asked)), "a question was asked twice across the re-plan"
    assert set(asked) == set(review["sent_questions"])


@emulator_required
@pubsub_required
def test_the_review_retiers_on_the_vendors_own_answer(datadynamo_run):
    """The beat where the fleet overrules the person who wants the contract signed. The intake
    form declared internal analytics; DP03 names customer records, and the tier moves."""
    review = _latest_review()
    change = review["tier_history"][-1]

    assert review["tier"] == 1 and review["plan_version"] == 2
    assert change["from_tier"] == 2 and change["to_tier"] == 1
    assert change["source_ref"] == "DP03"
    assert "customer records" in change["reason"].lower()


@emulator_required
@pubsub_required
def test_the_hero_contradiction_is_present_and_cites_a_chunk(datadynamo_run):
    """The MFA contradiction, carried through the whole pipeline rather than asserted in place."""
    from agents.evidence.retrieval import resolve_chunk

    findings = _findings(_latest_review()["review_id"])
    hero = next(
        f for f in findings if f["domain"] == "access_control" and f["contradiction"]
    )

    assert hero["severity"] == "high"
    assert hero["source"] == "model"
    assert hero["claim_ref"] == "AC01"

    chunk = resolve_chunk(hero["evidence_ref"])
    assert chunk is not None
    assert "exception" in chunk.text.lower()


@emulator_required
@pubsub_required
def test_the_expired_certificate_is_a_rule_finding(datadynamo_run):
    """A date comparison should never be a model's job."""
    findings = _findings(_latest_review()["review_id"])
    cert = next(f for f in findings if "expired" in f["summary"].lower())

    assert cert["source"] == "rule"
    assert cert["domain"] == "compliance_posture"
    assert cert["severity"] == "high"


@emulator_required
@pubsub_required
def test_every_finding_reaches_the_score(datadynamo_run):
    """A finding the rubric cannot map would be a silently wrong number."""
    from agents.risk_scorer.agent import scored_domains as domains_of
    from agents.risk_scorer.scoring import load_rubric

    review = _latest_review()
    loaded = Review.model_validate(review)
    scored_domains = set(load_rubric().weights_for(domains_of(loaded) or []))

    for finding in _findings(review["review_id"]):
        assert finding["domain"] in scored_domains | {"conduct"}


@emulator_required
@pubsub_required
def test_the_score_has_a_per_domain_breakdown(datadynamo_run):
    """Binder section 5, and the answer to "why 55?"."""
    review = _latest_review()
    score = firestore_client().collection("scores").document(review["review_id"]).get().to_dict()

    assert score["breakdown"]
    assert sum(score["breakdown"].values()) == pytest.approx(score["score"], abs=1)
    assert any("TRUST SCORE" in line for line in score["arithmetic"])


@emulator_required
@pubsub_required
def test_a_replay_produces_the_same_score_and_the_same_findings(datadynamo_run):
    """Determinism is what makes the demo replayable and the number quotable."""
    first = _latest_review()
    first_findings = _signature(first["review_id"])

    with responding_from("datadynamo"):
        run("datadynamo", fixtures_only=False)

    second = _latest_review()
    assert second["review_id"] != first["review_id"], "the replay must be a distinct review"
    assert second["score"] == first["score"]
    assert second["tier"] == first["tier"], "the replay must re-tier the same way"
    assert _signature(second["review_id"]) == first_findings
    assert _count("inbox", second["review_id"]) == _count("inbox", first["review_id"])


@emulator_required
@pubsub_required
def test_a_run_that_cannot_complete_stops_rather_than_reporting_success():
    """NimbusWrite has no seeded evidence by design, so its run cannot reach a decision.

    It must stop, and it must stop for a stated reason. A demo runner that returned a short
    beat list and exited zero is how a broken path reaches a recording session.
    """
    from scenarios.seed import NotSeedable

    with pytest.raises((BeatMissing, NotSeedable)) as exc:
        run("nimbuswrite", fixtures_only=True)

    assert str(exc.value)


# --- helpers ------------------------------------------------------------------------------------


def _latest_review() -> dict:
    from google.cloud.firestore_v1 import FieldFilter

    reviews = [
        d.to_dict()
        for d in firestore_client()
        .collection("reviews")
        .where(filter=FieldFilter("vendor_id", "==", "datadynamo"))
        .stream()
    ]
    scored = [r for r in reviews if r.get("score") is not None]
    return max(scored or reviews, key=lambda r: r.get("opened_at", ""))


def _findings(review_id: str) -> list[dict]:
    from google.cloud.firestore_v1 import FieldFilter

    return [
        d.to_dict()
        for d in firestore_client()
        .collection("findings")
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    ]


def _inbox(review_id: str) -> list[dict]:
    from google.cloud.firestore_v1 import FieldFilter

    return [
        d.to_dict()
        for d in firestore_client()
        .collection("inbox")
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    ]


def _question_ids(body: str) -> list[str]:
    import re

    return re.findall(r"^\s{2}([A-Z]{2}\d{2})\.", body, re.M)


def _signature(review_id: str) -> set[tuple]:
    return {
        (f["domain"], f["severity"], f["source"], f["contradiction"])
        for f in _findings(review_id)
    }


def _count(collection: str, review_id: str) -> int:
    from google.cloud.firestore_v1 import FieldFilter

    return len(
        list(
            firestore_client()
            .collection(collection)
            .where(filter=FieldFilter("review_id", "==", review_id))
            .stream()
        )
    )
