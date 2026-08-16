"""Load the synthetic vendor pack into Firestore and object storage.

Seeds the three hero vendors plus filler, so the review queue shows ten to twelve reviews
running concurrently rather than one system handling one review. Concurrency you can see costs
a seed script, and the fan-out that makes it real is the event backbone.

Filler reviews are generated with realistic states, tiers and elapsed days. They carry no
evidence and no findings — their job is to populate the queue, and a filler review that
produced findings would pollute the assertions the hero vendors' tests make.

Evidence documents go into the **quarantine** bucket, never the clean bucket. Seeding straight
into the clean bucket would bypass the screening pipeline and silently invalidate every claim
the project makes about how content is promoted.

**The one exception, and why it is not a bypass.** Local mode has no Model Armor, and the stub
is untrustworthy by construction, so the screening pipeline correctly refuses to promote
anything and the Evidence agent has nothing to read. ``seed_clean_evidence`` solves that
*downstream* of the pipeline rather than inside it: nothing in ``shared/armor.py`` changes,
there is no flag and no dev branch, and the documents it writes carry a stamp whose template is
``local-seed`` — as untrustworthy as ``local-stub`` to everything that checks
``verdict_is_trustworthy``. Their screening record says in plain words that they were seeded
rather than screened, so a review built on them is self-describing rather than merely
undocumented.

This module is a fixture loader, not a product code path. It lives under ``scenarios/`` and
``tests/test_seed_isolation.py`` asserts that nothing under ``shared/``, ``agents/`` or
``services/`` imports it. The moment product code can call a fixture loader, the difference
between a seeded document and a screened one stops being enforceable.

NimbusWrite is deliberately **not** seeded. Its document must go through real screening when
Model Armor exists — it is the fixture that carries the planted payload, and a seeded adversary
would be the one fixture in the pack that lies about how it got there.

Failure semantics: the seed is idempotent per vendor — re-running replaces a vendor's fixtures
rather than duplicating them, so a partial failure is recoverable by re-running. A malformed
fixture raises with the file path rather than being skipped; a silently skipped fixture is a
demo beat that does not happen.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared import storage
from shared.armor import (
    CRITICAL_FILTERS,
    EXECUTION_SKIPPED,
    NO_MATCH_FOUND,
    SEEDED_TEMPLATE,
    ScreenResult,
    index_chunks,
    record_screening,
)
from shared.clients import firestore_client
from shared.config import settings
from shared.context import AgentContext
from shared.domain import Review, ReviewState
from shared.events import TOPIC_VENDOR_REPLY_RECEIVED, publish
from shared.extraction import UnextractableDocument, extract

log = logging.getLogger("drawbridge.seed")

REPO = Path(__file__).resolve().parent.parent
VENDOR_PACK_DIR = REPO / "synthetic-vendors"
HERO_VENDORS = ("cleancloud", "datadynamo", "nimbuswrite")
SEEDABLE_VENDORS = ("cleancloud", "datadynamo")
"""Vendors whose evidence may be placed into the clean bucket by this loader.

NimbusWrite is absent by design. See the module docstring.
"""

FILLER_COUNT = 9
FILLER_STATES = (
    ReviewState.QUESTIONNAIRE_OUT,
    ReviewState.REPLIES_IN,
    ReviewState.EVIDENCE_REVIEW,
    ReviewState.SCORED,
    ReviewState.GATED,
)


class FixtureError(Exception):
    """A vendor fixture is missing or malformed. Raised with the offending path."""


class NotSeedable(Exception):
    """A vendor whose evidence must go through real screening was asked to be seeded."""


def load_vendor(slug: str) -> dict:
    """Read one vendor's profile, answers and expectations from disk.

    Raises:
        FixtureError: when a required file is missing or does not parse.
    """
    root = VENDOR_PACK_DIR / slug
    out: dict = {"slug": slug, "root": root}

    for key, name, required in (
        ("profile", "profile.json", True),
        ("answers", "questionnaire_answers.json", True),
        ("expected", "expected.json", False),
    ):
        path = root / name
        if not path.is_file():
            if required:
                raise FixtureError(f"{path} is missing")
            continue
        try:
            out[key] = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{path} does not parse: {exc}") from exc

    out["evidence"] = sorted((root / "evidence").glob("*")) if (root / "evidence").is_dir() else []
    return out


def seed_vendor(slug: str, *, clock=None) -> str:
    """Create the vendor record and upload its evidence to quarantine. Returns the vendor id.

    Quarantine, not clean. This is the honest path and it is the one that will run unchanged
    once Model Armor exists — the screening pipeline reads from here, screens, and promotes.
    """
    fixture = load_vendor(slug)
    profile = fixture["profile"]
    db = firestore_client()

    db.collection("vendors").document(profile["vendor_id"]).set(profile)

    uploaded = 0
    for path in fixture["evidence"]:
        ref = storage.ref_for(
            settings().bucket_quarantine, f"{profile['vendor_id']}/{path.name}"
        )
        storage.write_object(ref, path.read_bytes())
        uploaded += 1

    log.info("seeded vendor=%s with %d document(s) in quarantine", slug, uploaded)
    return profile["vendor_id"]


def seed_clean_evidence(review_id: str, slug: str) -> list[str]:
    """Place a vendor's evidence into the clean bucket as fixtures. Returns the clean refs.

    The local-only path, and the reason it exists is in the module docstring. Each document is
    extracted with the same extractor the screening pipeline uses, written to the clean bucket,
    given a ``local-seed`` screening record, and indexed for retrieval.

    Raises:
        NotSeedable: for a vendor whose evidence must go through real screening.
        FixtureError: on a document that cannot be extracted. A fixture that yields no text is
            a broken fixture, not a blind spot, so it fails loudly here.
    """
    if slug not in SEEDABLE_VENDORS:
        raise NotSeedable(
            f"{slug!r} is not seedable. Its evidence carries the planted payload and must go "
            "through real screening; seeding it would produce the one fixture in the pack that "
            "lies about how it reached the clean bucket."
        )

    fixture = load_vendor(slug)
    cfg = settings()
    refs: list[str] = []

    for path in fixture["evidence"]:
        try:
            text = extract(path.read_bytes(), ref=str(path))
        except UnextractableDocument as exc:
            raise FixtureError(f"{path} yields no text: {exc}") from exc

        clean_ref = storage.ref_for(cfg.bucket_clean, f"{review_id}/{path.stem}.txt")
        storage.write_object(clean_ref, text)

        record_screening(review_id, _seed_stamp(clean_ref))
        index_chunks(clean_ref, review_id)
        refs.append(clean_ref)

    log.info(
        "seeded %d clean-bucket fixture(s) for review=%s (template=%s, not a verdict)",
        len(refs),
        review_id,
        SEEDED_TEMPLATE,
    )
    return refs


def seed_reply(review_id: str, body: str, message_id: str) -> str:
    """Publish a vendor reply as if the inbox had screened it. Returns the message id.

    The reply-body equivalent of ``seed_clean_evidence``, and it exists for the same reason:
    ``services.vendor_inbox`` screens what it receives, the local stub is untrustworthy by
    construction, so every seeded reply would park the review. This writes the ``local-seed``
    screening record the Questionnaire agent checks for, then publishes on the real topic.

    Downstream of the pipeline, not inside it. ``services/vendor_inbox`` is unchanged, and the
    record this writes is as untrustworthy as any other seeded one.
    """
    origin_ref = f"reply:{message_id}"
    record_screening(review_id, _seed_stamp(origin_ref))

    ctx = AgentContext(review_id=review_id, agent="seed", trace_id="")
    publish(
        TOPIC_VENDOR_REPLY_RECEIVED,
        review_id,
        {"body": body, "message_id": message_id, "seeded": True},
        ctx=ctx,
    )
    return message_id


def replies_for(slug: str) -> list[tuple[str, str]]:
    """Return the vendor's scripted replies as ``(message_id, body)``, in schedule order.

    One message per scheduled batch, exactly as the fixture describes it — replies arrive
    across days and partially, and collapsing them into one message would test a code path the
    product does not have.
    """
    fixture = load_vendor(slug)
    answers = fixture["answers"]
    out: list[tuple[str, str]] = []

    for batch in answers.get("reply_schedule", []):
        day = batch.get("simulated_day", 0)
        ids = [qid for qid in batch.get("question_ids", []) if qid in answers.get("answers", {})]
        if not ids:
            continue
        body = "\n\n".join(f"{qid}: {answers['answers'][qid]['text']}" for qid in ids)
        out.append((f"{slug}-day{day:02d}", body))

    return out


def _seed_stamp(clean_ref: str) -> ScreenResult:
    """Build the screening record a seeded document carries.

    Every critical filter is reported as skipped, because none of them ran. That is the same
    shape the local stub produces and it has the same consequence: ``verdict_is_trustworthy``
    is ``False``, so nothing downstream can treat this as a screening verdict.
    """
    return ScreenResult(
        clean=True,
        template=SEEDED_TEMPLATE,
        template_version="0",
        filters={f: NO_MATCH_FOUND for f in CRITICAL_FILTERS},
        execution={f: EXECUTION_SKIPPED for f in CRITICAL_FILTERS},
        sanitised=False,
        excerpt=None,
        origin_ref=clean_ref,
    )


def seed_filler(count: int, *, clock=None) -> list[str]:
    """Create filler reviews with realistic states, tiers and elapsed days.

    Filler carries no evidence and no findings.
    """
    now = (clock.now() if clock else datetime.now(UTC))
    db = firestore_client()
    ids: list[str] = []

    for i in range(count):
        review_id = f"filler-{i:02d}"
        state = FILLER_STATES[i % len(FILLER_STATES)]
        review = Review(
            review_id=review_id,
            vendor_id=f"filler-vendor-{i:02d}",
            state=state,
            gate_scope="decision" if state is ReviewState.GATED else None,
            tier=(i % 3) + 1,
            opened_at=now - timedelta(days=3 + i * 2),
        )
        db.collection("vendors").document(review.vendor_id).set(
            {
                "vendor_id": review.vendor_id,
                "name": f"Filler Vendor {i:02d}",
                "category": "queue filler",
                "tier": review.tier,
            }
        )
        db.collection("reviews").document(review_id).set(review.model_dump(mode="json"))
        ids.append(review_id)

    log.info("seeded %d filler review(s)", len(ids))
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendors", nargs="*", default=list(HERO_VENDORS))
    parser.add_argument("--filler", type=int, default=FILLER_COUNT)
    parser.add_argument("--compress", type=int, default=1)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[seed] %(message)s")

    for slug in args.vendors:
        seed_vendor(slug)
    seed_filler(args.filler)

    print(f"seeded {len(args.vendors)} vendor(s) and {args.filler} filler review(s)")
    print("evidence is in quarantine; the demo runner seeds clean-bucket fixtures per review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
