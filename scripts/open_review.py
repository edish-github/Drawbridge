"""Open a review for a synthetic vendor and publish its intake event.

    python -m scripts.open_review --vendor nimbuswrite

Stands in for the vendor portal, which is where a real intake form is submitted. It writes the
vendor record and the review at ``INTAKE``, then publishes ``review.intake`` — after which
nothing else here is involved: the worker picks the event up, the Orchestrator tiers and plans,
and the Questionnaire agent walks into the contact gate.

The review is written **before** the event is published, in that order. An intake event for a
review that does not exist yet parks for redelivery, which is correct behaviour but a confusing
first line of output for something that is only ever an ordering mistake.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from shared.clients import firestore_client
from shared.context import AgentContext
from shared.domain import Review, ReviewState
from shared.events import TOPIC_REVIEW_INTAKE, publish

REPO = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO / "synthetic-vendors"


def load_profile(slug: str) -> dict:
    """Read a synthetic vendor's profile.

    Raises:
        FileNotFoundError: naming the path, rather than a KeyError three frames later.
    """
    path = VENDOR_DIR / slug / "profile.json"
    if not path.is_file():
        raise FileNotFoundError(f"no vendor profile at {path}")
    return json.loads(path.read_text())


def open_review(slug: str, *, review_id: str | None = None) -> str:
    """Write the vendor and review records and publish ``review.intake``. Returns the id."""
    profile = load_profile(slug)
    db = firestore_client()

    db.collection("vendors").document(profile["vendor_id"]).set(profile)

    review = Review(
        review_id=review_id or f"rev-{slug}-{uuid.uuid4().hex[:8]}",
        vendor_id=profile["vendor_id"],
        state=ReviewState.INTAKE,
        tier=int(profile.get("tier", 2)),
        opened_at=datetime.now(UTC),
    )
    db.collection("reviews").document(review.review_id).set(review.model_dump(mode="json"))

    ctx = AgentContext(review_id=review.review_id, agent="portal", trace_id=uuid.uuid4().hex)
    publish(
        TOPIC_REVIEW_INTAKE,
        review.review_id,
        {"vendor_id": profile["vendor_id"], "submitted_by": profile.get("intake", {}).get(
            "submitted_by", "unknown"
        )},
        ctx=ctx,
    )
    return review.review_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", default="nimbuswrite")
    parser.add_argument("--review-id", default=None)
    args = parser.parse_args()

    review_id = open_review(args.vendor, review_id=args.review_id)
    print(f"opened review {review_id} for {args.vendor}")
    print("the worker will tier it, plan it, and park it at the contact gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
