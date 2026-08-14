"""Late and out-of-order events.

Pub/Sub guarantees delivery, not sequence. Duplicates are covered by the idempotency guard;
these cover order. Each case has a defined behaviour, and none of them is "drop it".
"""

import pytest


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


@pytest.mark.skip(reason="shared/events.py is a contract; unskip when guard() executes")
def test_five_failed_attempts_dead_letter_and_park_the_review():
    r = review_without_a_plan("nimbuswrite")

    for _ in range(5):
        deliver(r, evidence_screened_event())

    assert dlq_contains("evidence.screened.dlq", r.review_id)
    assert load_review(r.review_id).state == ReviewState.NEEDS_HUMAN
    assert dashboard_card(r.review_id, kind="dlq")


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
