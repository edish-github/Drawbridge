"""A minimal review worker that can be killed at a named point, for the resume tests.

This is not a mock. It runs the real ``shared.checkpoint.step`` and ``shared.idempotency.once``
against the real emulator, and when told to crash it sends itself SIGKILL — an uncatchable
signal, so no cleanup handler runs and the process dies exactly as it would under a pod
eviction or a pulled plug.

The "email" side effect writes a document to an ``inbox`` collection. Counting documents there
is how the tests assert that a vendor was contacted exactly once across a restart.

Kill points:

``none``
    Run to completion.
``before_send``
    Die after the plan step, before the send is claimed. The restart should claim fresh, send
    once, and complete.
``after_claim``
    Die inside the send, after the idempotency claim and before the effect. The restart must
    refuse to re-run and surface the step for confirmation — nothing was sent.
``after_send``
    Die inside the send, after the effect and before the claim is marked done. The restart must
    also refuse: the effect already happened, and resending is the failure this whole module
    exists to prevent.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from types import SimpleNamespace

from shared.checkpoint import completed_steps, step
from shared.clients import firestore_client
from shared.idempotency import ReconciliationRequired, key_for, once

PLAN_VERSION = 1
SEND_STEP_ID = "questionnaire_send:v1"


def crash() -> None:
    """Die the way a real process dies: uncatchable, no cleanup, no flush."""
    os.kill(os.getpid(), signal.SIGKILL)


def send_email(review_id: str, vendor: str, kill_at: str) -> dict:
    """The guarded side effect. Writes one inbox document per successful send."""
    if kill_at == "after_claim":
        crash()

    firestore_client().collection("inbox").add(
        {"review_id": review_id, "vendor": vendor, "subject": "Security review"}
    )

    if kill_at == "after_send":
        crash()

    return {"sent": True, "vendor": vendor}


def run(review_id: str, vendor: str, kill_at: str) -> int:
    ctx = SimpleNamespace(review_id=review_id, agent="questionnaire", trace_id="t")
    db = firestore_client()
    db.collection("reviews").document(review_id).set({"review_id": review_id}, merge=True)

    step("plan", ctx, lambda: {"tier": 1})

    if kill_at == "before_send":
        crash()

    key = key_for(review_id, PLAN_VERSION, SEND_STEP_ID)
    try:
        step(
            "questionnaire_send",
            ctx,
            lambda: once(key, ctx, send_email, review_id, vendor, kill_at),
        )
    except ReconciliationRequired as exc:
        print(f"RECONCILE {exc}")
        return 3

    step("score", ctx, lambda: {"score": 82})
    print(f"COMPLETED steps={completed_steps(review_id)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--vendor", default="nimbuswrite")
    parser.add_argument(
        "--kill-at",
        default="none",
        choices=("none", "before_send", "after_claim", "after_send"),
    )
    args = parser.parse_args()
    return run(args.review_id, args.vendor, args.kill_at)


if __name__ == "__main__":
    sys.exit(main())
