"""Review the same vendor twice, and watch the second one open already knowing things.

    make demo-second

The payoff for durable memory being a layer rather than a cache. Without this beat, Memory Bank
is Firestore with extra steps: something is written, nothing visibly reads it, and a judge is
asked to take the hierarchy on trust.

The script runs DataDynamo end to end, closes it with a named person attaching two conditions,
then opens a second review of the same vendor six months later and prints what carried forward
and what changed because of it. Both are asserted:

**Carried** — the outcome and the tier it ended at, the band and score, the conditions in the
approver's own words, the certificate that had expired, the contact address, and the rating of
every answer the vendor gave. For a vendor with a conduct flag, the flag carries too, and the
second review opens knowing what they tried the first time.

**Changed** — the tier starts where the last one ended and never lower, and the questions
answered well enough last time are not asked again. The second questionnaire is strictly
shorter than the first, and the difference is named question by question.

Nothing here requires a project. The compression is honest: the second review's *timestamps*
are real, and what is six months old is the review being recalled.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import UTC, datetime, timedelta

from scenarios.demo_runner import (
    Demo,
    announce_fixtures,
    approve,
    confirm_first_contact,
    open_and_plan,
    release_the_contact_gate,
    to_a_decision,
)
from scenarios.fixtures import responding_from
from scenarios.seed import seed_vendor
from shared.clients import firestore_client
from shared.events import load_review

log = logging.getLogger("drawbridge.second")

RULE = "─" * 78
MONTHS_LATER = 6

CONDITIONS = [
    "Enrol every administrative account in multi-factor authentication and provide the "
    "access review evidencing it by 30 November.",
    "Renew ISO/IEC 27001 certification, or provide the certification body's audit schedule "
    "with a completion date.",
]
"""What the approver required. Sentences a person typed, which is why they live on the approval
record and not in durable memory: the dossier holds terms and identifiers, and a store recalled
into a planning prompt before any screening has run in the new review is not where prose goes.
"""


class SecondReviewFailed(Exception):
    """The second review did not open knowing what the first one left it."""


def run(vendor: str) -> int:
    print(f"\n{RULE}\n  THE SECOND REVIEW · {vendor}\n{RULE}")
    announce_fixtures()

    first = first_review(vendor)
    print(f"\n  1 · first review {first.review_id} closed")
    _print_close(first)

    prior = _recalled(vendor)
    if not prior.is_repeat:
        raise SecondReviewFailed(
            "the second review found no dossier. The first review closed without writing one, "
            "which makes durable memory a store nothing reads."
        )

    print(f"\n  2 · six months later, the second review opens holding\n        {prior.summary()}")
    for condition in prior.conditions:
        print(f"        condition · {condition}")

    second = second_review(vendor)
    print(f"\n  3 · second review {second.review_id} planned")
    _print_plan(first, second, prior)
    _check(first, second, prior)

    print(f"\n{RULE}\n  the second review asked {_asked(second)} questions, not {_asked(first)}"
          f"\n{RULE}\n")
    return 0


# --- the two reviews -----------------------------------------------------------------------


def first_review(vendor: str):
    """Run one review to a decision, closing it with conditions a person attached."""
    demo = Demo(vendor)
    with responding_from(vendor):
        open_and_plan(demo)
        release_the_contact_gate(demo)
        confirm_first_contact(demo)
        to_a_decision(demo, conditions=CONDITIONS)
    return load_review(demo.review_id)


def second_review(vendor: str):
    """Open a second review of the same vendor and plan it. Stops at first contact."""
    demo = Demo(vendor)
    with responding_from(vendor):
        seed_vendor(vendor)
        demo.review_id = _open(vendor)
        demo.drain()
        release_the_contact_gate(demo)
        approve(demo.review_id, scope="contact")
        demo.drain()
    return load_review(demo.review_id)


def _open(vendor: str) -> str:
    """Write the second review at intake and publish it, dated six months on."""
    from shared.context import AgentContext
    from shared.domain import Review, ReviewState
    from shared.events import TOPIC_REVIEW_INTAKE, publish

    profile = firestore_client().collection("vendors").document(vendor).get().to_dict() or {}
    review = Review(
        review_id=f"second-{vendor}-{uuid.uuid4().hex[:6]}",
        vendor_id=vendor,
        state=ReviewState.INTAKE,
        # The intake form for the renewal is filled in fresh, and this time it declares the
        # modest scope the first one did. Nothing about that lowers the tier, which is the
        # point: the fleet remembers what the evidence said even when the form forgets.
        tier=int(profile.get("tier", 2)),
        opened_at=datetime.now(UTC) + timedelta(days=30 * MONTHS_LATER),
    )
    firestore_client().collection("reviews").document(review.review_id).set(
        {**review.model_dump(mode="json"), "opened_reason": "annual renewal"}
    )
    publish(
        TOPIC_REVIEW_INTAKE,
        review.review_id,
        {"vendor_id": vendor, "renewal": True},
        ctx=AgentContext(review_id=review.review_id, agent="demo", trace_id=uuid.uuid4().hex),
    )
    return review.review_id


# --- what is printed and what is checked ---------------------------------------------------


def _print_close(review) -> None:
    print(f"        outcome         {review.band} at Tier {review.tier}, score {review.score}")
    print(f"        conditions      {len(CONDITIONS)} attached by the approver")
    print(f"        dossier notes   {len(_notes(review.vendor_id))} written")


def _print_plan(first, second, prior) -> None:
    carried = _plan(second.review_id).get("carried_questions") or []
    print(f"        tier            {second.tier}   (first review ended at {first.tier})")
    print(f"        questions       {_asked(second)} asked, {len(carried)} carried")
    if carried:
        print(f"        carried         {', '.join(carried[:8])}"
              f"{' …' if len(carried) > 8 else ''}")
    print(f"        conduct flag    {'on record' if prior.adversarial else 'none'}")


def _check(first, second, prior) -> None:
    if second.tier > first.tier:
        raise SecondReviewFailed(
            f"the second review opened at Tier {second.tier} after the first ended at Tier "
            f"{first.tier}. Scrutiny never falls across reviews."
        )
    if not prior.conditions:
        raise SecondReviewFailed(
            "the conditions the approver attached did not carry forward. They live on the "
            "approval record; the dossier names the review they belong to."
        )
    if not prior.answered_well:
        raise SecondReviewFailed("no answer was rated usable, so nothing could be carried")

    asked_first, asked_second = _asked(first), _asked(second)
    if asked_second >= asked_first:
        raise SecondReviewFailed(
            f"the second review asked {asked_second} questions against the first review's "
            f"{asked_first}. A second review that asks everything again has recalled nothing."
        )


def _recalled(vendor: str):
    from agents.orchestrator.recall import recall

    return recall(vendor)


def _notes(vendor_id: str) -> list:
    from google.cloud.firestore_v1 import FieldFilter

    return [
        d.to_dict()
        for d in firestore_client()
        .collection("dossiers")
        .where(filter=FieldFilter("vendor_id", "==", vendor_id))
        .stream()
    ]


def _plan(review_id: str) -> dict:
    from shared.checkpoint import step_result

    return step_result(review_id, "plan") or {}


def _asked(review) -> int:
    raw = firestore_client().collection("reviews").document(review.review_id).get().to_dict()
    return len((raw or {}).get("sent_questions", []))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", default="datadynamo")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s · %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    try:
        return run(args.vendor)
    except SecondReviewFailed as exc:
        print(f"\nSECOND REVIEW FAILED · {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
