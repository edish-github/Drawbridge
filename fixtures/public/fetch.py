"""Fetch the real documents the extractor is measured against.

    python -m fixtures.public.fetch              # fetch anything missing
    python -m fixtures.public.fetch --verify     # check what is already here, fetch nothing
    python -m fixtures.public.fetch --list       # what would be fetched, and from where

The documents in ``SOURCES.yaml`` are not vendored: they belong to their publishers and this
repository stores a URL and a checksum instead. That makes the tests that use them skip on a
clean checkout, which is the correct behaviour — a test that silently downloads the internet is
a test that fails differently on every machine.

The checksum is not a formality. These are live URLs on live sites, and a publisher who
replaces a report with a revised one has changed the input to a measurement whose results are
recorded in ``EXTRACTION-NOTES.md``. A mismatch stops rather than continues: a different
document producing different extraction results is not a regression, and reporting it as one
would be worse than not running at all.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import urllib.request
from pathlib import Path

import yaml

log = logging.getLogger("drawbridge.fixtures.public")

HERE = Path(__file__).resolve().parent
SOURCES = HERE / "SOURCES.yaml"
DOCUMENTS = HERE / "documents"

TIMEOUT_SECONDS = 120
USER_AGENT = "drawbridge-fixture-fetch/1.0 (+security-review-agent-evaluation)"


class ChecksumMismatch(Exception):
    """The bytes on the far end are not the bytes the recorded results came from."""


def sources() -> list[dict]:
    """Return the declared documents."""
    return list(yaml.safe_load(SOURCES.read_text(encoding="utf-8")).get("documents") or [])


def path_for(entry: dict) -> Path:
    return DOCUMENTS / str(entry["filename"])


def present(entry: dict) -> bool:
    """Return whether this document is here and is the revision that was measured."""
    target = path_for(entry)
    return target.exists() and digest(target.read_bytes()) == entry["sha256"]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def fetch(entry: dict, *, force: bool = False) -> Path:
    """Download one document and verify it. Returns its path.

    Raises:
        ChecksumMismatch: when the published bytes have changed. Nothing is written — a file
            that failed verification sitting on disk is a file some later run will trust.
    """
    target = path_for(entry)
    if present(entry) and not force:
        log.info("%s is already here and verifies", entry["id"])
        return target

    log.info("fetching %s from %s", entry["id"], entry["url"])
    request = urllib.request.Request(entry["url"], headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        raw = response.read()

    got = digest(raw)
    if got != entry["sha256"]:
        raise ChecksumMismatch(
            f"{entry['id']}: expected sha256 {entry['sha256']}, got {got} ({len(raw)} bytes). "
            "The publisher has revised this document. The recorded extraction results in "
            "EXTRACTION-NOTES.md were produced from the old revision; update the checksum and "
            "re-run the measurement rather than assuming the results still hold."
        )

    DOCUMENTS.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    log.info("wrote %s (%d bytes)", target, len(raw))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="check what is here, fetch nothing")
    parser.add_argument("--list", action="store_true", help="print the sources and do nothing")
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[fixtures] %(message)s")

    entries = sources()
    if args.list:
        for entry in entries:
            print(f"{entry['id']}\n  {entry['url']}\n  sha256 {entry['sha256']}")
        return 0

    failed = 0
    for entry in entries:
        if args.verify:
            state = "verifies" if present(entry) else "missing or altered"
            print(f"  {state:20s} {entry['id']}")
            failed += 0 if present(entry) else 1
            continue
        try:
            fetch(entry, force=args.force)
        except Exception as exc:  # noqa: BLE001 — one unreachable publisher never hides the rest
            log.error("%s: %s", entry["id"], exc)
            failed += 1

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
