"""Late and out-of-order events.

Pub/Sub guarantees delivery, not sequence. Duplicates are covered by the idempotency guard;
these cover order. Each case has a defined behaviour, and none of them is "drop it".

Six of these are still contracts written against helpers that do not exist. The dead-letter
group at the bottom is not: it was a contract too, describing a park that nothing performed, and
the review-graph work turned it into a failure edge with code behind it. The rest stay skipped
rather than deleted, because a defined behaviour with no test is a gap worth seeing.
"""

import pytest

from shared import tenancy as tenant
from tests.conftest import emulator_required


@pytest.mark.skip(reason="shared/events.py is a contract; unskip when guard() executes")
def test_reply_after_scored_reopens_rather_than_discards():
    """The discarded reply is the one the vendor will quote back."""
    r = run_until("scored", vendor="cleancloud")

    deliver_late(r, reply_changing_a_scored_answer())

    assert published("review.rescore", r.review_id)
    assert ledger_contains(r.review_id, "addendum")


@pytest.mark.skip(reason="shared/events.py is a contract; unskip when guard() executes")
def test_event_for_decided_review_never_mutates():
    r = run_to_decision("cleancloud")
    snapshot = r.score

    deliver_late(r, any_event())

    assert load_review(r.review_id).score == snapshot
    assert ledger_contains(r.review_id, "appended")


@pytest.mark.skip(reason="shared/events.py is a contract; unskip when guard() executes")
def test_evidence_screened_before_the_plan_parks_and_retries():
    r = review_without_a_plan("nimbuswrite")

    deliver(r, evidence_screened_event())

    assert message_parked(r.review_id)
    assert delivery_attempts(r.review_id) < 5


# --- Dead-letter exhaustion, which is executed rather than described ---------------------------
#
# These four replace a skipped placeholder. The behaviour was written down in
# `infra/pubsub.yaml` — a message reaching its dead-letter topic moves its review to
# NEEDS_HUMAN and surfaces on the dashboard — and nothing did it: Pub/Sub took the message off
# the subscription after five deliveries and the review sat in whatever state it had, in flight
# forever, with no card. `shared.subscriber.Runner._exhausted` is the failure edge, and it runs
# on the last delivery rather than after it, because a consumer that has already lost the
# message cannot act on it.
#
# Exercised against the method rather than through five real redeliveries. What is under test is
# the decision — *this delivery is the last one, so park* — and driving Pub/Sub's redelivery
# counter five times would spend a minute of wall clock to arrive at the same assertion through
# the emulator's backoff.


def envelope_for(review_id: str, topic: str = "evidence.screened"):
    from shared.events import EventEnvelope

    return EventEnvelope(
        org_id=tenant.current_org(),
        type=topic,
        review_id=review_id,
        idem_key=f"{review_id}:plan_v1:{topic}",
        trace_id="t",
        source="test",
    )


class Delivery:
    """The one attribute of a Pub/Sub ReceivedMessage this path reads."""

    def __init__(self, attempt: int | None) -> None:
        if attempt is not None:
            self.delivery_attempt = attempt


def open_review_in(db, review_id: str, state: str = "questionnaire_out") -> None:
    db.collection("reviews").document(review_id).set(
        {
            "review_id": review_id,
            "vendor_id": "nimbuswrite",
            "state": state,
            "tier": 2,
            "plan_version": 1,
            "opened_at": "2026-03-01T09:00:00+00:00",
        }
    )


@emulator_required
def test_the_last_delivery_parks_the_review(review_id, db):
    from shared.subscriber import Runner

    open_review_in(db, review_id)

    Runner._exhausted(Delivery(5), envelope_for(review_id), "evidence.screened", "unexpected_state")

    record = db.collection("reviews").document(review_id).get().to_dict()
    assert record["state"] == "needs_human"
    assert "dlq:evidence.screened" in record["park_reason"]


@emulator_required
def test_the_park_raises_a_card_naming_what_stalled(review_id, db):
    """A review that stopped and produced no card is a review nobody is coming back to."""
    from google.cloud.firestore_v1 import FieldFilter

    from shared.subscriber import Runner

    open_review_in(db, review_id)

    Runner._exhausted(Delivery(5), envelope_for(review_id), "evidence.screened", "unexpected_state")

    cards = [
        c.to_dict()
        for c in db.collection("dashboard_events")
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    ]
    assert any("evidence.screened" in str(c.get("reason", "")) for c in cards)


@emulator_required
def test_an_earlier_delivery_leaves_the_review_alone(review_id, db):
    """Redelivery is the point. Parking on the first failure would turn one transient dependency
    blip into a parked review, which is the opposite of what the nack exists to allow."""
    from shared.subscriber import Runner

    open_review_in(db, review_id)

    for attempt in (1, 2, 3, 4):
        Runner._exhausted(Delivery(attempt), envelope_for(review_id), "evidence.screened", "x")

    assert db.collection("reviews").document(review_id).get().to_dict()["state"] == (
        "questionnaire_out"
    )


@emulator_required
def test_a_subscription_with_no_dead_letter_policy_never_parks(review_id, db):
    """``delivery_attempt`` is populated only on a subscription that has a dead-letter policy.

    Absent is read as *not the last attempt*. Reading it as *park* would make every failure on a
    plain subscription terminal, which is how a local emulator run would start parking reviews
    that cloud would have retried.
    """
    from shared.subscriber import Runner

    open_review_in(db, review_id)

    Runner._exhausted(Delivery(None), envelope_for(review_id), "evidence.screened", "x")

    assert db.collection("reviews").document(review_id).get().to_dict()["state"] == (
        "questionnaire_out"
    )


@pytest.mark.skip(reason="shared/events.py is a contract; unskip when guard() executes")
def test_watchdog_hit_on_a_reopened_review_deduplicates_on_signal_id():
    r = run_to_decision("cleancloud")
    signal = watchdog_signal(signal_id="sig-001")

    deliver(r, watchdog_hit(signal))
    deliver(r, watchdog_hit(signal))

    assert reopened_review_count(r.vendor_id) == 1


@pytest.mark.skip(reason="shared/events.py is a contract; unskip when guard() executes")
def test_watchdog_hit_opens_a_new_linked_review_not_a_mutation():
    r = run_to_decision("cleancloud")

    deliver(r, watchdog_hit(watchdog_signal(signal_id="sig-002")))

    new = latest_review(r.vendor_id)
    assert new.review_id != r.review_id
    assert new.reopened_from == r.review_id
    assert load_review(r.review_id).state == ReviewState.DECIDED


@pytest.mark.skip(reason="shared/events.py is a contract; unskip when guard() executes")
def test_an_event_type_without_defined_out_of_phase_behaviour_raises():
    """A new topic must declare its behaviour before it can be consumed."""
    with pytest.raises(NotImplementedError):
        handle_out_of_phase(undeclared_event(), review=any_review())
