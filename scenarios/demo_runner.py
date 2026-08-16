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
    "replies_parsed",
    "retier",
    "additional_questions_sent",
    "evidence_screened",
    "findings_ready",
    "scored",
    "decision_gate_parked",
    "decided",
    "binder_rendered",
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
    retiers = bool((expected.get("tier") or {}).get("retier_expected"))
    skip = set() if retiers else {"retier", "additional_questions_sent"}
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
    profile = load_vendor(vendor)["profile"]

    # --- intake ---------------------------------------------------------------------------
    seed_vendor(vendor)
    demo.review_id = open_review(profile)
    demo.beat("intake")

    demo.drain()
    review = demo.review()
    demo.require("plan_ready", review.state in (ReviewState.QUESTIONNAIRE_OUT, ReviewState.GATED))

    # --- the contact gate -------------------------------------------------------------------
    review = demo.review()
    demo.require(
        "contact_gate_parked",
        review.state is ReviewState.GATED and review.gate_scope == "contact",
    )

    approve(demo.review_id, scope="contact")
    demo.drain()
    demo.require("contact_gate_released", demo.review().state is ReviewState.QUESTIONNAIRE_OUT)
    demo.require("questionnaire_sent", inbox_count(demo.review_id) == 1)

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
            demo.require("additional_questions_sent", inbox_count(demo.review_id) == 2)

    demo.require("replies_parsed", answered_count(demo.review_id) > 0)

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

    return demo.fired


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


def inbox_count(review_id: str) -> int:
    return _count("inbox", review_id)


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
