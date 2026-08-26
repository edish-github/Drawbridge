"""Graph replay: reconstructing a review's path from the ledger.

The claim under test is *why did the system reach this conclusion* being answerable in a form
somebody can check. These build real ledger state in the emulator and project it, because the
projection reads records written for other reasons and the only way to know it reads them
correctly is to write them the way the fleet does.

The property that matters most is at the bottom: the projection needs **no instrumentation**. It
reads the review document, the event ledger, the reasoning records, the cards and the findings,
all of which existed before ``shared/graph_run.py`` did — so a review that ran last month
projects exactly as well as one that ran after the module was written. A replay feature that
only works on data recorded after the feature shipped cannot be demonstrated on the evidence you
already have.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from shared.graph import GRAPH
from shared.graph_run import NodeStatus, project
from tests.conftest import emulator_required


def stamp(offset_seconds: int = 0) -> str:
    from datetime import timedelta

    return (datetime(2026, 3, 1, 9, 0, tzinfo=UTC) + timedelta(seconds=offset_seconds)).isoformat()


def open_review(db, review_id: str, **fields) -> None:
    db.collection("reviews").document(review_id).set(
        {
            "review_id": review_id,
            "vendor_id": "datadynamo",
            "state": "intake",
            "tier": 1,
            "plan_version": 1,
            "opened_at": stamp(),
            **fields,
        }
    )


def add_event(db, review_id: str, event_type: str, *, at: str, **fields) -> None:
    event_id = uuid.uuid4().hex
    db.collection("events").document(event_id).set(
        {
            "event_id": event_id,
            "type": event_type,
            "review_id": review_id,
            "idem_key": f"{review_id}:plan_v1:{event_type}",
            "trace_id": "t",
            "source": "test",
            "ts": at,
            **fields,
        }
    )


def add_decision(db, review_id: str, node: str, *, at: str, decision: str = "done") -> None:
    db.collection("decisions").add(
        {
            "review_id": review_id,
            "agent": "test",
            "node": node,
            "goal": "g",
            "decision": decision,
            "at": at,
        }
    )


# --- test_graph_replay -------------------------------------------------------------------------


@emulator_required
def test_a_review_that_does_not_exist_is_an_error_rather_than_an_empty_graph(review_id):
    """An all-pending projection and a review nobody opened look identical and mean opposite
    things, so the projection refuses rather than rendering the ambiguous one."""
    with pytest.raises(LookupError, match=review_id):
        project(review_id)


@emulator_required
def test_a_fresh_review_has_run_only_its_entry_node(review_id, db):
    open_review(db, review_id)
    add_event(db, review_id, "review.intake", at=stamp(0))

    run = project(review_id)

    assert run.status("intake") is NodeStatus.COMPLETE
    assert run.status("score") is NodeStatus.PENDING
    assert run.status("decide") is NodeStatus.PENDING


@emulator_required
def test_a_checkpointed_step_completes_its_node(review_id, db):
    open_review(
        db,
        review_id,
        state="questionnaire_out",
        completed_steps=["plan"],
        step_results={"plan": {"tier": 1}},
        step_started=stamp(5),
    )
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "review.plan_ready", at=stamp(6))

    run = project(review_id)

    assert run.status("plan") is NodeStatus.COMPLETE


@emulator_required
def test_a_step_in_flight_reads_as_running_not_complete(review_id, db):
    """What "resumed from" names after a restart. The checkpoint is claimed and not recorded."""
    open_review(
        db,
        review_id,
        state="intake",
        current_step="plan",
        step_started=stamp(5),
    )
    add_event(db, review_id, "review.intake", at=stamp(0))

    run = project(review_id)

    assert run.status("plan") is NodeStatus.RUNNING


@emulator_required
def test_the_path_is_ordered_by_when_each_node_started(review_id, db):
    open_review(
        db,
        review_id,
        state="evidence_review",
        completed_steps=["plan"],
        step_started=stamp(5),
    )
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "review.plan_ready", at=stamp(10))
    add_event(db, review_id, "evidence.screened", at=stamp(20))

    path = project(review_id).path()

    assert path.index("intake") < path.index("plan")


@emulator_required
def test_a_replayed_node_carries_the_evidence_its_status_rests_on(review_id, db):
    """A status with nothing behind it is the projection asserting rather than reporting."""
    open_review(db, review_id)
    add_event(db, review_id, "review.intake", at=stamp(0))

    run = project(review_id)

    assert run.runs["intake"].evidence == ("event:review.intake",)


@emulator_required
def test_a_decision_stamped_with_a_node_id_is_what_makes_the_projection_exact(review_id, db):
    """The one field added to an existing write path, and this is what it buys.

    ``recall`` has no checkpoint, publishes no event and writes no collection of its own. Without
    the stamp its only evidence is a dashboard card that a first-time vendor never produces, so a
    repeat review would show the node as never having run.
    """
    open_review(db, review_id)
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_decision(db, review_id, "recall", at=stamp(1), decision="prior review found")

    run = project(review_id)

    assert run.status("recall") is NodeStatus.COMPLETE
    assert "prior review found" in run.runs["recall"].detail


@emulator_required
def test_the_projection_needs_no_records_written_for_it(review_id, db):
    """The property the whole module was constrained to have.

    Nothing here is stamped with a node id, nothing is written to a collection the projection
    owns, and there is no collection the projection owns. A review recorded entirely by the
    pre-existing write paths still projects.
    """
    open_review(
        db,
        review_id,
        state="scored",
        completed_steps=["plan", "evidence_review", "score"],
        step_started=stamp(30),
        score=64,
        band="conditional",
    )
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "review.plan_ready", at=stamp(10))
    add_event(db, review_id, "review.findings_ready", at=stamp(20))

    run = project(review_id)

    assert run.status("plan") is NodeStatus.COMPLETE
    assert run.status("findings_join") is NodeStatus.COMPLETE
    assert run.status("score") is NodeStatus.COMPLETE


@emulator_required
def test_a_run_renders_to_json_without_losing_its_path(review_id, db):
    """The dashboard and the dump both read this shape."""
    open_review(db, review_id)
    add_event(db, review_id, "review.intake", at=stamp(0))

    payload = project(review_id).to_dict()

    assert payload["review_id"] == review_id
    assert "intake" in payload["path"]
    assert payload["nodes"]["intake"]["status"] == "complete"


# --- Failure and waiting ------------------------------------------------------------------------


@emulator_required
def test_a_parked_review_marks_the_node_that_failed(review_id, db):
    """The park reason is the join between a stopped review and the node that stopped it."""
    open_review(
        db,
        review_id,
        state="needs_human",
        park_reason="scoring_failed",
        completed_steps=["plan", "evidence_review"],
        step_started=stamp(20),
    )
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "review.findings_ready", at=stamp(20))

    run = project(review_id)

    assert run.status("score") is NodeStatus.FAILED
    assert run.park_reason == "scoring_failed"


@emulator_required
def test_a_failure_marks_one_node_and_not_the_band_around_it(review_id, db):
    """Failure isolation, observed rather than declared."""
    open_review(
        db,
        review_id,
        state="needs_human",
        park_reason="memo_failed",
        completed_steps=["plan", "evidence_review", "score"],
        step_started=stamp(25),
    )
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "review.findings_ready", at=stamp(20))

    run = project(review_id)

    assert run.status("memo") is NodeStatus.FAILED
    assert run.status("score") is NodeStatus.COMPLETE


# --- test_gate_resume ----------------------------------------------------------------------------


@emulator_required
def test_a_gated_review_shows_its_gate_waiting_rather_than_complete(review_id, db):
    open_review(
        db,
        review_id,
        state="gated",
        gate_scope="decision",
        completed_steps=["plan", "evidence_review", "score", "memo"],
        step_started=stamp(30),
        score=64,
    )
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "review.score_ready", at=stamp(30))
    db.collection("dashboard_events").add(
        {"review_id": review_id, "kind": "gate", "gate_scope": "decision", "at": stamp(31)}
    )

    run = project(review_id)

    assert run.status("decision_gate") is NodeStatus.WAITING


@emulator_required
def test_a_released_gate_completes_and_the_node_after_it_runs(review_id, db):
    """The resume half of the gate. A gate that never reads as released is a gate the projection
    would show as blocking a review that had already closed."""
    open_review(
        db,
        review_id,
        state="decided",
        gate_scope=None,
        completed_steps=["plan", "evidence_review", "score", "memo"],
        step_started=stamp(30),
        score=64,
        decided_at=stamp(40),
    )
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "review.score_ready", at=stamp(30))
    add_event(db, review_id, "review.approved", at=stamp(39))
    db.collection("events").document(uuid.uuid4().hex).set(
        {
            "event_id": uuid.uuid4().hex,
            "type": "review.advance",
            "review_id": review_id,
            "from_state": "gated",
            "to_state": "decided",
            "reason": "risk accepted by ciso@example.com",
            "ts": stamp(40),
        }
    )

    run = project(review_id)

    assert run.status("decide") is NodeStatus.COMPLETE
    assert run.status("decision_gate") is not NodeStatus.WAITING


@emulator_required
def test_a_contact_gate_and_a_decision_gate_are_told_apart_by_scope(review_id, db):
    """One state, two scopes. A projection that conflated them would show a review waiting on
    risk acceptance when it is waiting for permission to send an email."""
    open_review(db, review_id, state="gated", gate_scope="contact")
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "review.plan_ready", at=stamp(10))
    db.collection("dashboard_events").add(
        {"review_id": review_id, "kind": "gate", "gate_scope": "contact", "at": stamp(11)}
    )

    run = project(review_id)

    assert run.status("contact_gate") is NodeStatus.WAITING
    assert run.status("decision_gate") is NodeStatus.PENDING


# --- Joins, projected -----------------------------------------------------------------------------


@emulator_required
def test_a_join_that_has_not_opened_reads_as_waiting(review_id, db):
    open_review(db, review_id, state="replies_in")
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "vendor.reply_received", at=stamp(20))

    run = project(review_id)

    assert run.status("coverage_join") is NodeStatus.WAITING


@emulator_required
def test_a_join_the_review_moved_past_reads_as_complete(review_id, db):
    open_review(db, review_id, state="evidence_review")
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "vendor.reply_received", at=stamp(20))
    db.collection("events").document(uuid.uuid4().hex).set(
        {
            "event_id": uuid.uuid4().hex,
            "type": "review.advance",
            "review_id": review_id,
            "from_state": "replies_in",
            "to_state": "evidence_review",
            "reason": "coverage 92%",
            "ts": stamp(25),
        }
    )

    run = project(review_id)

    assert run.status("coverage_join") is NodeStatus.COMPLETE


# --- Plan versions --------------------------------------------------------------------------------


@emulator_required
def test_a_re_tiered_send_is_the_same_node_at_a_later_plan_version(review_id, db):
    """``questionnaire_send@plan_v2`` is the node a re-tier re-ran, not an unknown step.

    A projection that treated it as unknown would show the send the re-tier was *for* as never
    having happened, on the one review where it most obviously did.
    """
    open_review(
        db,
        review_id,
        state="questionnaire_out",
        plan_version=2,
        completed_steps=["plan", "questionnaire_send", "questionnaire_send@plan_v2"],
        step_started=stamp(40),
    )
    add_event(db, review_id, "review.intake", at=stamp(0))
    add_event(db, review_id, "review.plan_ready", at=stamp(10))

    run = project(review_id)

    assert run.status("questionnaire_send") is NodeStatus.COMPLETE
    assert run.plan_version == 2


# --- Every node the graph declares is projectable -------------------------------------------


@emulator_required
def test_every_declared_node_appears_in_a_projection(review_id, db):
    """A node missing from the projection is a node the dashboard would silently not draw."""
    open_review(db, review_id)
    add_event(db, review_id, "review.intake", at=stamp(0))

    run = project(review_id)

    assert set(run.runs) == {n.id for n in GRAPH.nodes}
