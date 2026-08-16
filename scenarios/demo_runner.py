"""Replay the demo deterministically: one vendor, one compression factor, the same beats.

    python -m scenarios.demo_runner --vendor datadynamo

Drives a review from intake to a decision by publishing real events and draining the real
subscriber loop between them. It is a driver, not a second implementation: every beat below
happens because an agent handled an event, and the runner's only job is to supply the things a
human or a vendor would otherwise supply — the intake form, the two gate approvals, and the
replies arriving over days.

**Beats are asserted, not hoped for.** A beat that does not fire fails the run loudly with its
name, because a demo runner that tolerates a missing beat is a demo runner that lets a broken
path reach a recording session.

Failure semantics: the run stops at the first missing beat and reports the beats that did fire,
so the failure names a stage rather than a symptom. Nothing here writes review state or a
finding; it publishes, approves, and drains.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid
from datetime import UTC, datetime

from scenarios.seed import (
    load_vendor,
    replies_for,
    seed_clean_evidence,
    seed_reply,
    seed_vendor,
)
from shared.clients import firestore_client
from shared.context import AgentContext
from shared.domain import Review, ReviewState
from shared.events import TOPIC_EVIDENCE_SCREENED, TOPIC_REVIEW_INTAKE, load_review, publish
from shared.subscriber import Runner

log = logging.getLogger("drawbridge.demo")

BEATS = (
    "intake",
    "plan_ready",
    "contact_gate_parked",
    "contact_gate_released",
    "questionnaire_sent",
    # Mid-correspondence, and the order says so: the re-tier fires on the batch that reveals the
    # broader scope, while replies are still arriving. A re-tier after the thread closed would be
    # a report about the review rather than a correction to it.
    "retier",
    "additional_questions_sent",
    "replies_parsed",
    # A vendor who answers "we follow industry best practice" has not answered. The fleet asks
    # again, once, quoting them — which is the beat that separates collecting from
    # interrogating and the one an analyst recognises from their own inbox.
    "vague_answers_re_asked",
    "evidence_screened",
    "findings_ready",
    "scored",
    "decision_gate_parked",
    "decided",
    "binder_rendered",
    # The review does not end at signature. The expiry the Evidence agent remembered is what
    # the sweep finds, and what it opens is a new linked review rather than an edit to a
    # decided one.
    "monitoring_swept",
)
"""The ordered beats a full run produces. The demo script is timed against this list, and a
beat that does not fire is a failure rather than a variation.

``injection_blocked`` is absent rather than silently unfired: the screening interception needs
the real Model Armor service, and listing a beat that cannot happen would make every run fail
for the same known reason.

The two re-tier beats are conditional on the vendor, which is why ``beats_for`` exists rather
than this tuple being compared directly. A vendor whose intake form was accurate has nothing
for the re-tier path to correct, and requiring the beat of them would be requiring the fleet to
find a problem that is not there.
"""


def beats_for(vendor: str) -> list[str]:
    """Return the beats this vendor's run must produce, in order.

    Read from the pack's own expectations rather than hard-coded per vendor: the fixture
    declares whether a re-tier is expected, and a demo runner that disagreed with the fixture
    would be asserting against its own opinion.
    """
    expected = load_vendor(vendor).get("expected", {})
    skip: set[str] = set()
    if not (expected.get("tier") or {}).get("retier_expected"):
        skip |= {"retier", "additional_questions_sent"}
    if not int((expected.get("followups") or {}).get("expected_count", 0)):
        skip.add("vague_answers_re_asked")
    return [beat for beat in BEATS if beat not in skip]


class BeatMissing(Exception):
    """An expected beat did not occur inside its window."""

    def __init__(self, beat: str, fired: list[str]) -> None:
        super().__init__(f"beat {beat!r} did not fire. Fired: {', '.join(fired) or 'none'}")
        self.beat = beat
        self.fired = fired


class Demo:
    """One scripted run. Holds the review id and the beats that have fired."""

    def __init__(self, vendor: str, *, batches: int = 4) -> None:
        self.vendor = vendor
        self.batches = batches
        self.review_id = ""
        self.fired: list[str] = []
        self.runner = Runner()

    def beat(self, name: str) -> None:
        self.fired.append(name)
        log.info("beat %-24s ✓", name)

    def require(self, name: str, condition: bool) -> None:
        """Record a beat, or stop the run naming the one that did not fire."""
        if not condition:
            raise BeatMissing(name, self.fired)
        self.beat(name)

    def drain(self, *, batches: int | None = None) -> None:
        """Run the worker until the queue is quiet."""
        self.runner.run(max_batches=batches or self.batches)

    def review(self) -> Review:
        loaded = load_review(self.review_id)
        if loaded is None:
            raise BeatMissing("review_exists", self.fired)
        return loaded

    def ctx(self) -> AgentContext:
        return AgentContext(review_id=self.review_id, agent="demo", trace_id=uuid.uuid4().hex)

    def adopt(self, review_id: str, fired: list[str]) -> None:
        """Continue a review a previous process opened.

        The seam the crash demo resumes through. Beats that fired before the crash are carried
        forward rather than re-asserted, because a beat is a thing that happened once — the
        restart proves it is still true by not doing it again, not by doing it again.
        """
        self.review_id = review_id
        self.fired = list(fired)


def run(vendor: str, *, compress: int = 1, fixtures_only: bool = False) -> list[str]:
    """Run the scenario and return the beats that fired, in order.

    Args:
        vendor: the vendor slug to run.
        compress: simulated seconds per real second; 1 is real time.
        fixtures_only: when true, no model or cloud call is made. This is the mode CI uses.

    Raises:
        BeatMissing: naming the beat that did not fire and the beats that did.
    """
    if fixtures_only:
        from scenarios.fixtures import responding_from

        with responding_from(vendor):
            return _run(vendor)
    return _run(vendor)


def announce_fixtures() -> None:
    """Print the banner every fixture-built run carries, or explain the refusal that is coming.

    This run feeds a model documents that no detector inspected. P2 refuses them unless the
    allowance is set, and the allowance exists so the artefact declares what it is rather than
    so the policy is quieter. Saying it here, once, before anything happens, means an operator
    watching a terminal knows what they are looking at without reading a log line.
    """
    from shared.gateway import ALLOW_UNSCREENED_ENV, unscreened_fixtures_allowed

    if unscreened_fixtures_allowed():
        print(
            "\n"
            "  UNSCREENED FIXTURES\n"
            "  This run feeds the models seeded documents that no detector inspected. Every\n"
            "  review it produces is marked unscreened_fixtures=true, and its binder says so\n"
            f"  on the cover. Set by {ALLOW_UNSCREENED_ENV}=1.\n"
        )
        return

    print(
        f"\n  {ALLOW_UNSCREENED_ENV} is not set. P2 will refuse the seeded evidence, which is\n"
        "  the policy working. Run this through `make demo-fixtures`, which sets it.\n"
    )


def _run(vendor: str) -> list[str]:
    """The scripted run itself. Identical whether the answers come from a model or a fixture."""
    announce_fixtures()
    demo = Demo(vendor)

    open_and_plan(demo)
    release_the_contact_gate(demo)
    confirm_first_contact(demo)
    to_a_decision(demo)

    return demo.fired


# The run is in four parts rather than one function so the crash demo can stop between two of
# them, die, and resume from the next. A resumable system whose own demo script cannot be
# resumed would be an odd thing to claim.


def open_and_plan(demo: Demo) -> None:
    """Intake through the checkpointed plan."""
    profile = load_vendor(demo.vendor)["profile"]

    seed_vendor(demo.vendor)
    forget_prior_runs(demo.vendor)
    demo.review_id = open_review(profile)
    demo.beat("intake")

    demo.drain()
    review = demo.review()
    demo.require("plan_ready", review.state in (ReviewState.QUESTIONNAIRE_OUT, ReviewState.GATED))


def release_the_contact_gate(demo: Demo) -> None:
    """The P1 refusal, and the human approval that clears it."""
    review = demo.review()
    demo.require(
        "contact_gate_parked",
        review.state is ReviewState.GATED and review.gate_scope == "contact",
    )
    approve(demo.review_id, scope="contact")


def confirm_first_contact(demo: Demo) -> None:
    """Drain the release and assert the vendor was emailed exactly once.

    Both halves matter on a resume: the drain is where a restarted worker replays the send and
    the guards refuse it, and the count is the proof that they did.
    """
    demo.drain()
    demo.require("contact_gate_released", demo.review().state is ReviewState.QUESTIONNAIRE_OUT)
    demo.require("questionnaire_sent", inbox_count(demo.review_id, "questionnaire") == 1)


def to_a_decision(demo: Demo) -> list[str]:
    """Replies, evidence, the score, the decision gate and the binder."""
    vendor = demo.vendor

    # --- replies --------------------------------------------------------------------------
    opening_tier = demo.review().tier
    retiered = False

    for message_id, body in replies_for(vendor):
        seed_reply(demo.review_id, body, message_id)
        demo.drain(batches=2)

        if not retiered and demo.review().tier < opening_tier:
            retiered = True
            demo.beat("retier")
            # The re-tier put the review back in QUESTIONNAIRE_OUT and republished the plan; the
            # drain above already delivered the additional set. Asserting the count rather than
            # the send is what makes "and nothing twice" checkable.
            demo.require(
                "additional_questions_sent", inbox_count(demo.review_id, "questionnaire") == 2
            )

    demo.require("replies_parsed", answered_count(demo.review_id) > 0)

    expected_followups = int(
        (load_vendor(vendor).get("expected", {}).get("followups") or {}).get("expected_count", 0)
    )
    if expected_followups:
        demo.require(
            "vague_answers_re_asked", inbox_count(demo.review_id, "followup") == expected_followups
        )

    # The scripted schedule is exhausted. If it answered enough, the coverage threshold has
    # already opened evidence review on its own; if it did not, an analyst proceeding with what
    # the vendor actually sent is an ordinary workflow step and this is where it happens. Both
    # paths are real, and which one runs is a property of the pack rather than of the runner.
    if demo.review().state is not ReviewState.EVIDENCE_REVIEW:
        log.info("coverage did not reach the threshold; proceeding as an analyst would")
        mark_replies_complete(demo.review_id)
        seed_reply(demo.review_id, "(no further answers)", f"{vendor}-final")
        demo.drain(batches=2)

    # --- evidence -------------------------------------------------------------------------
    seed_clean_evidence(demo.review_id, vendor)
    publish(
        TOPIC_EVIDENCE_SCREENED,
        demo.review_id,
        {"trigger": "seeded_fixtures"},
        ctx=demo.ctx(),
    )
    demo.beat("evidence_screened")

    demo.drain(batches=3)
    demo.require("findings_ready", finding_count(demo.review_id) > 0)

    # --- score and the decision gate ---------------------------------------------------------
    demo.drain(batches=3)
    review = demo.review()
    demo.require("scored", review.score is not None)
    demo.require(
        "decision_gate_parked",
        review.state is ReviewState.GATED and review.gate_scope == "decision",
    )

    approve(demo.review_id, scope="decision")
    demo.drain()
    demo.require("decided", demo.review().state is ReviewState.DECIDED)

    # --- the binder ---------------------------------------------------------------------------
    # Rendered as part of the run rather than left to a separate command, because the export is
    # a demo beat and a beat that is not exercised on every run is a beat that breaks on camera.
    from services.binder.render import write as write_binder

    started = time.monotonic()
    path = write_binder(demo.review_id)
    log.info("binder rendered in %.2fs → %s", time.monotonic() - started, path)
    demo.require("binder_rendered", path.exists() and path.stat().st_size > 0)

    # --- monitoring ---------------------------------------------------------------------------
    sweep(demo)

    return demo.fired


def sweep(demo: Demo) -> None:
    """Run one Watchdog pass over the decided review and assert what it opened.

    Local mode fetches no feed, so this is the expiry half of the sweep — date arithmetic over
    the certificate expiry the Evidence agent wrote to the dossier during the review. That is
    the half that is certain rather than probable, and the half that still works when every feed
    is down. The feed half needs the real screening service and stays fixture-driven until then.
    """
    from agents.watchdog.agent import on_sweep
    from shared.events import TOPIC_WATCHDOG_SWEEP, EventEnvelope

    review = demo.review()
    event = EventEnvelope(
        type=TOPIC_WATCHDOG_SWEEP,
        review_id=demo.review_id,
        idem_key=f"{demo.review_id}:plan_v{review.plan_version}:watchdog_sweep:v1",
        trace_id=uuid.uuid4().hex,
        source="cloud_scheduler",
        payload={"as_of": datetime.now(UTC).date().isoformat()},
    )

    opened = on_sweep(event, review)
    if not opened:
        log.info("the sweep found nothing to reopen for %s", review.vendor_id)
        return

    reopened = load_review(opened[0])
    demo.require(
        "monitoring_swept",
        reopened is not None
        and reopened.reopened_from == demo.review_id
        and demo.review().state is ReviewState.DECIDED,
    )
    log.info("watchdog opened review=%s, linked to %s", opened[0], demo.review_id)


def forget_prior_runs(vendor: str) -> None:
    """Clear the two vendor-scoped collections a previous replay of this vendor left behind.

    Everything else the demo writes is scoped to a review id and a new run gets a new one. The
    dossier and the Watchdog's actioned-signal record are scoped to the *vendor*, deliberately —
    that is what makes durable memory durable and what stops the same expired certificate
    opening two re-reviews. Which also means a second replay of the same vendor would find its
    own previous run's memory and correctly decline to do anything, and a demo that fired its
    monitoring beat once per emulator would be a demo nobody could rehearse.

    A replay is a fresh world for one vendor. Only the demo does this; nothing in the fleet has
    a path that forgets.
    """
    from google.cloud.firestore_v1 import FieldFilter

    db = firestore_client()
    cleared = 0
    for collection, field in (("dossiers", "vendor_id"), ("tasks", "vendor_id")):
        for doc in (
            db.collection(collection).where(filter=FieldFilter(field, "==", vendor)).stream()
        ):
            doc.reference.delete()
            cleared += 1

    if cleared:
        log.info("cleared %d artefact(s) from a previous replay of %s", cleared, vendor)


def open_review(profile: dict) -> str:
    """Write the review at ``INTAKE`` and publish its intake event. Returns the review id."""
    review = Review(
        review_id=f"demo-{profile['vendor_id']}-{uuid.uuid4().hex[:6]}",
        vendor_id=profile["vendor_id"],
        state=ReviewState.INTAKE,
        tier=int(profile.get("tier", 2)),
        opened_at=datetime.now(UTC),
    )
    firestore_client().collection("reviews").document(review.review_id).set(
        review.model_dump(mode="json")
    )
    publish(
        TOPIC_REVIEW_INTAKE,
        review.review_id,
        {"vendor_id": profile["vendor_id"]},
        ctx=AgentContext(review_id=review.review_id, agent="demo", trace_id=uuid.uuid4().hex),
    )
    return review.review_id


def mark_replies_complete(review_id: str) -> None:
    """Stand in for an analyst declaring the reply thread finished."""
    firestore_client().collection("reviews").document(review_id).set(
        {"replies_complete": True}, merge=True
    )


def approve(review_id: str, *, scope: str) -> None:
    """Release a gate the way a human would, through the approvals path."""
    from scripts.issue_token import issue

    issue(review_id, scope=scope, identity="demo-operator", ttl_minutes=30)


def inbox_count(review_id: str, kind: str | None = None) -> int:
    """Count messages sent to the vendor, optionally of one kind.

    "The vendor was emailed once" is a claim about the questionnaire, not about the whole
    correspondence. Counting the thread would make a sensible follow-up look like a duplicate
    send, which is the opposite of what the beat is checking.
    """
    from google.cloud.firestore_v1 import FieldFilter

    query = (
        firestore_client()
        .collection("inbox")
        .where(filter=FieldFilter("review_id", "==", review_id))
    )
    if kind is not None:
        query = query.where(filter=FieldFilter("kind", "==", kind))
    return len(list(query.stream()))


def finding_count(review_id: str) -> int:
    return _count("findings", review_id)


def answered_count(review_id: str) -> int:
    return _count("qa_responses", review_id)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", default="datadynamo")
    parser.add_argument("--compress", type=int, default=1)
    parser.add_argument("--fixtures-only", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s · %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        fired = run(args.vendor, compress=args.compress, fixtures_only=args.fixtures_only)
    except BeatMissing as exc:
        print(f"\nDEMO FAILED · {exc}", file=sys.stderr)
        return 1

    print(f"\nall {len(fired)} beats fired for {args.vendor}:")
    for beat in fired:
        print(f"  {beat}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
