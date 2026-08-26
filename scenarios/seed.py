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
from shared import tenancy as tenant
from shared.armor import (
    CRITICAL_FILTERS,
    EXECUTION_SKIPPED,
    NO_MATCH_FOUND,
    SEEDED_TEMPLATE,
    ScreenResult,
    index_chunks,
    record_screening,
    sign_stamp,
)
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

    tenant.collection("vendors").document(profile["vendor_id"]).set(profile)

    uploaded = 0
    for path in fixture["evidence"]:
        ref = storage.ref_for(
            settings().bucket_quarantine, f"{profile['vendor_id']}/{path.name}"
        )
        storage.write_object(ref, path.read_bytes())
        uploaded += 1

    log.info("seeded vendor=%s with %d document(s) in quarantine", slug, uploaded)
    return profile["vendor_id"]


APPROVED_VENDOR_REGISTER = (
    # The internal system the Evidence agent queries. Eight companies the organisation has
    # already put through a review of its own, with the dates those reviews are good until.
    #
    # Three names are deliberately absent, and the absences are the feature. Sendline
    # Notifications carries DataDynamo's delivery notifications and every address in them, and
    # nobody here has ever reviewed it. Veritas Lumen Models receives NimbusWrite's customer
    # text, same shape. Pathview Telemetry is the quiet control case: also absent, and raises
    # nothing, because it receives only aggregate counters.
    #
    # Wayfarer Geocoding is on the register with a review that lapsed in September 2025 — a
    # company somebody did the work on once, whose paperwork went stale while it carried on
    # receiving origin and destination addresses.
    {
        "name": "Aurelius Cloud Services",
        "last_review_id": "rv-2024-0117",
        "review_status": "current",
        "review_valid_until": "2027-03-31",
        "jurisdictions": ["EU"],
    },
    {
        "name": "Wayfarer Geocoding",
        "last_review_id": "rv-2023-0442",
        "review_status": "current",
        "review_valid_until": "2025-09-30",
        "jurisdictions": ["EU"],
    },
    {
        "name": "Ledgerbridge Analytics",
        "last_review_id": "rv-2025-0088",
        "review_status": "current",
        "review_valid_until": "2027-01-15",
        "jurisdictions": ["EU"],
    },
    {
        "name": "Northgate Cloud Infrastructure",
        "last_review_id": "rv-2024-0301",
        "review_status": "current",
        "review_valid_until": "2027-06-30",
        "jurisdictions": ["EU"],
    },
    {
        "name": "Postmark Relay Services",
        "last_review_id": "rv-2025-0121",
        "review_status": "current",
        "review_valid_until": "2027-02-28",
        "jurisdictions": ["EU"],
    },
    {
        "name": "Helpdesk Loop",
        "last_review_id": "rv-2025-0202",
        "review_status": "current",
        "review_valid_until": "2026-12-31",
        "jurisdictions": ["EU"],
    },
    {
        "name": "Stratos Compute",
        "last_review_id": "rv-2024-0509",
        "review_status": "current",
        "review_valid_until": "2027-04-30",
        "jurisdictions": ["US", "EU"],
    },
    {
        "name": "Cadence Mail",
        "last_review_id": "rv-2025-0310",
        "review_status": "current",
        "review_valid_until": "2027-05-31",
        "jurisdictions": ["US"],
    },
)
"""The approved-vendor register, as a fixture.

Seeded here because it stands in for an internal system the organisation already runs — a GRC
tool, a procurement database, a spreadsheet somebody owns. Nothing in the fleet writes to it,
and the permission matrix gives it readers and no writer: an agent that could add a name could
make an unknown fourth party known by writing one document, which is the finding rather than
the fix.
"""


def seed_register() -> int:
    """Load the approved-vendor register. Returns the number of entries written.

    Idempotent on the company name, so re-seeding replaces rather than duplicates.
    """
    from agents.evidence.subprocessors import COLLECTION_REGISTER, _normalise

    for entry in APPROVED_VENDOR_REGISTER:
        tenant.collection(COLLECTION_REGISTER).document(_normalise(entry["name"])).set(entry)

    log.info("seeded the approved-vendor register with %d entries", len(APPROVED_VENDOR_REGISTER))
    return len(APPROVED_VENDOR_REGISTER)


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

        # The sidecar too, so the clean bucket is self-describing here exactly as it is on the
        # real promotion path. An object with no stamp beside it would be the one thing in the
        # bucket that could not say how it got there.
        stamp = _seed_stamp(clean_ref)
        storage.write_object(
            storage.ref_for(cfg.bucket_clean, f"{review_id}/{path.stem}.stamp"),
            sign_stamp(stamp.model_dump(mode="json")),
            content_type="application/json",
        )
        record_screening(review_id, stamp)
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

    ctx = AgentContext(org_id=tenant.current_org(), review_id=review_id, agent="seed", trace_id="")
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
        tenant.collection("vendors").document(review.vendor_id).set(
            {
                "vendor_id": review.vendor_id,
                "name": f"Filler Vendor {i:02d}",
                "category": "queue filler",
                "tier": review.tier,
            }
        )
        tenant.collection("reviews").document(review_id).set(review.model_dump(mode="json"))
        ids.append(review_id)

    log.info("seeded %d filler review(s)", len(ids))
    return ids


RESETTABLE_COLLECTIONS = (
    "reviews",
    "events",
    "decisions",
    "dashboard_events",
    "qa_responses",
    "qa_responses_superseded",
    "screenings",
    "evidence_chunks",
    "findings",
    "scores",
    "memos",
    "inbox",
    "idempotency",
    "approvals",
    "approval_tokens_spent",
    "data_scope_classifications",
    "inert_excerpts",
    "subprocessors",
    "followups",
    "approved_vendors",
    # Both are vendor-scoped rather than review-scoped, and both are cleared for the same
    # reason: they are what makes a run *not* repeat itself. A dossier holding last run's
    # certificate expiry and a tasks collection holding last run's signal id are exactly what a
    # correct Watchdog deduplicates against, so leaving them would make the monitoring beat fire
    # once on a fresh emulator and never again.
    "dossiers",
    "tasks",
)
"""Collections a reset clears. Every one is run artefact rather than fixture.

``vendors`` is absent: a vendor record is a fixture rather than a run artefact, and clearing it
would mean the next seed had to rebuild storage as well.

A local emulator accumulates every review every test run ever created, and a queue holding a
thousand of them is not a queue an operator would recognise — it is also the first thing a
viewer sees on the dashboard. This is a fixture-loader convenience and it runs against the
emulator only.
"""


def reset() -> int:
    """Delete the working state of every review in the local emulator. Returns the count.

    Raises:
        RuntimeError: in cloud mode. There is no version of this that should ever run against a
            real project, so it refuses rather than relying on nobody typing it.
    """
    cfg = settings()
    if not cfg.is_local:
        raise RuntimeError(
            "reset deletes review state wholesale and runs against the emulator only; "
            f"RUNTIME_MODE is {cfg.mode.value}"
        )

    deleted = 0
    for name in RESETTABLE_COLLECTIONS:
        for doc in tenant.collection(name).stream():
            doc.reference.delete()
            deleted += 1

    log.info("reset %d document(s) across %d collection(s)", deleted, len(RESETTABLE_COLLECTIONS))
    return deleted


def main() -> int:
    # Every entry point adopts a tenant before it touches anything. Library code never
    # defaults one; a CLI does, and only outside cloud mode.
    with tenant.acting_for(tenant.cli_org()):
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--vendors", nargs="*", default=list(HERO_VENDORS))
        parser.add_argument("--filler", type=int, default=FILLER_COUNT)
        parser.add_argument("--compress", type=int, default=1)
        parser.add_argument(
            "--reset",
            action="store_true",
            help="clear every review's working state first; local emulator only",
        )
        args = parser.parse_args()

        logging.basicConfig(level=logging.INFO, format="[seed] %(message)s")

        if args.reset:
            print(f"cleared {reset()} document(s) of prior review state")

        for slug in args.vendors:
            seed_vendor(slug)
        seed_filler(args.filler)
        entries = seed_register()

        print(
            f"seeded {len(args.vendors)} vendor(s), {args.filler} filler review(s) and "
            f"{entries} approved-vendor register entries"
        )
        print("evidence is in quarantine; the demo runner seeds clean-bucket fixtures per review")
        return 0


if __name__ == "__main__":
    sys.exit(main())
