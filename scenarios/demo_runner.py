"""Replay the demo deterministically: one vendor, one compression factor, the same beats.

    python -m scenarios.demo_runner --vendor nimbuswrite --compress 240

Runs the seeded reply schedule against the injected clock so replies, chases and follow-ups
arrive in the same order every time. This is what stops the demo path breaking on the 24th and
being discovered on the 28th, and it is why the CI smoke run replays this against fixtures
rather than live models.

Failure semantics: a beat that does not occur within its expected window fails the run loudly
with the beat name, rather than continuing to the next one — a demo runner that tolerates a
missing beat is a demo runner that lets a broken path reach a recording session. Against
fixtures no model call is made at all, which is what keeps the CI run free.
"""

from __future__ import annotations

import argparse

BEATS = (
    "intake",
    "plan_ready",
    "contact_gate_parked",
    "contact_gate_released",
    "questionnaire_sent",
    "replies_parsed",
    "retier",
    "evidence_screened",
    "injection_blocked",
    "findings_ready",
    "scored",
    "decision_gate_parked",
    "binder_exported",
)
"""The ordered beats a full run produces. The demo script is timed against this list, and a
beat that does not fire is a failure rather than a variation.
"""


class BeatMissing(Exception):
    """An expected beat did not occur inside its window."""


def run(vendor: str, *, compress: int, fixtures_only: bool) -> list[str]:
    """Run the scenario and return the beats that fired, in order.

    Args:
        vendor: the vendor slug to run.
        compress: simulated seconds per real second; 1 is real time.
        fixtures_only: when true, no model or cloud call is made. This is the mode CI uses.

    Raises:
        BeatMissing: naming the beat that did not fire and the beats that did.
    """
    raise NotImplementedError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", default="nimbuswrite")
    parser.add_argument("--compress", type=int, default=240)
    parser.add_argument("--fixtures-only", action="store_true")
    parser.parse_args()
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
