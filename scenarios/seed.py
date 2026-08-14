"""Load the synthetic vendor pack into Firestore and Cloud Storage.

Seeds the three hero vendors plus filler, so the review queue shows ten to twelve reviews
running concurrently rather than one system handling one review. Concurrency you can see costs
a seed script, and the fan-out that makes it real is the event backbone.

Filler reviews are generated with realistic states, tiers and elapsed days. They carry no
evidence and no findings — their job is to populate the queue, and a filler review that
produced findings would pollute the assertions the hero vendors' tests make.

Evidence documents go into the **quarantine** bucket, never the clean bucket. Seeding straight
into the clean bucket would bypass the screening pipeline and silently invalidate every claim
the project makes about how content is promoted.

Failure semantics: the seed is idempotent per vendor — re-running replaces a vendor's fixtures
rather than duplicating them, so a partial failure is recoverable by re-running. A malformed
fixture raises with the file path rather than being skipped; a silently skipped fixture is a
demo beat that does not happen. Refuses to run against a project whose Firestore already holds
reviews not created by this script, so a seed cannot overwrite real work.
"""

from __future__ import annotations

import argparse

VENDOR_PACK_DIR = "synthetic-vendors"
HERO_VENDORS = ("cleancloud", "datadynamo", "nimbuswrite")
FILLER_COUNT = 9


class FixtureError(Exception):
    """A vendor fixture is missing or malformed. Raised with the offending path."""


def load_vendor(slug: str) -> dict:
    """Read one vendor's profile, answers and expectations from disk.

    Raises:
        FixtureError: when a required file is missing or does not parse.
    """
    raise NotImplementedError


def seed_vendor(slug: str, *, clock) -> str:
    """Create the vendor, its review and its fixtures. Returns the review id.

    Evidence is uploaded to the quarantine bucket so the screening pipeline processes it
    exactly as it would a real upload.
    """
    raise NotImplementedError


def seed_filler(count: int, *, clock) -> list[str]:
    """Create filler reviews with realistic states, tiers and elapsed days.

    Filler carries no evidence and no findings.
    """
    raise NotImplementedError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendors", nargs="*", default=list(HERO_VENDORS))
    parser.add_argument("--filler", type=int, default=FILLER_COUNT)
    parser.add_argument("--compress", type=int, default=1)
    parser.parse_args()
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
