"""Kill the worker mid-send, restart it, and prove the vendor was emailed exactly once.

    make demo-crash

Pillar three of four, run as a scripted beat rather than described. A worker is driven to the
instant the questionnaire has left the building and sent SIGKILL — uncatchable, so no handler
runs, no buffer flushes and nothing gets a chance to tidy up. Two more workers then take the
review the rest of the way.

The kill lands in the narrowest window that exists: **after the email was sent and before its
idempotency claim was closed**. From the outside, that state is indistinguishable from a worker
that died a millisecond earlier and sent nothing — which is the honest answer to *"why might a
resumable agent order two laptops"*, and the reason the resume refuses to guess.

Three acts, six printed lines, each checked rather than merely emitted:

1. the worker dies; the ledger names the step it was in
2. the first restart replays the send and **refuses** it, because a claim it cannot verify is
   not a claim it may repeat — nothing is sent, and the step is surfaced for confirmation
3. a named person confirms the email did go out; the second restart replays, logs
   ``idempotency SKIP``, banks the checkpoint and finishes the whole review

Real processes, not threads and not a mocked crash: each worker is a ``python -m`` run that
either dies or exits, and the parent reads the ledger between them. A test that faked the death
would be testing the fake.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from shared import tenancy as tenant

log = logging.getLogger("drawbridge.crash")

REPO = Path(__file__).resolve().parent.parent
KILL_STEP = "questionnaire_send"
OPERATOR = "priya.raghunathan@example.com"

RULE = "─" * 78


class CrashDemoFailed(Exception):
    """The crash beat did not behave the way the resume claim says it does."""


# --- the workers ------------------------------------------------------------------------------


def crash_after_the_effect() -> None:
    """Arrange for the worker to die once the email has been sent and before the claim closes.

    Re-registers the outbound tool rather than patching a checkpoint, so the death lands where
    a real one would: inside the guarded call, after the effect on the world and before
    anything has recorded that it happened.
    """
    from agents.questionnaire.delivery import TOOL_SEND_EMAIL, send_email
    from shared.gateway import register_tool

    def dying_send(**kwargs):
        send_email(**kwargs)
        print("CRASH the questionnaire is out; the worker dies before recording it", flush=True)
        sys.stdout.flush()
        os.kill(os.getpid(), signal.SIGKILL)

    register_tool(TOOL_SEND_EMAIL, dying_send)


def child_open(vendor: str) -> int:
    """Open a review, clear the contact gate, and die with the questionnaire in flight."""
    from scenarios.demo_runner import (
        Demo,
        announce_fixtures,
        open_and_plan,
        release_the_contact_gate,
    )
    from scenarios.fixtures import responding_from

    announce_fixtures()
    demo = Demo(vendor)

    with responding_from(vendor):
        open_and_plan(demo)
        release_the_contact_gate(demo)
        _handoff(demo.review_id, demo.fired)
        crash_after_the_effect()
        demo.drain()

    raise CrashDemoFailed(f"the worker was supposed to die inside {KILL_STEP!r} and did not")


def child_replay(vendor: str, review_id: str) -> int:
    """Replay the send the dead worker was holding, and let the guards speak in the log.

    The refusal does not surface as an exception here on purpose: ``shared.subscriber`` catches
    a failing handler, nacks the message and carries on, because one bad message must not take
    the worker down. So the evidence is the log line the guard wrote, which is also what an
    operator would be looking at.
    """
    from scenarios.demo_runner import Demo
    from scenarios.fixtures import responding_from

    demo = Demo(vendor)
    demo.adopt(review_id, [])

    with responding_from(vendor):
        redeliver_the_event_the_dead_worker_never_acked(demo)
        demo.drain(batches=2)
    return 0


def child_finish(vendor: str, review_id: str, fired: list[str]) -> int:
    """Take the confirmed review the rest of the way, and print the beats that fired."""
    from scenarios.demo_runner import Demo, confirm_first_contact, to_a_decision
    from scenarios.fixtures import responding_from

    demo = Demo(vendor)
    demo.adopt(review_id, fired)

    with responding_from(vendor):
        redeliver_the_event_the_dead_worker_never_acked(demo)
        confirm_first_contact(demo)
        to_a_decision(demo)

    print(f"BEATS {json.dumps(demo.fired)}", flush=True)
    return 0


def redeliver_the_event_the_dead_worker_never_acked(demo) -> None:
    """Publish the event the crashed worker was holding when it died.

    A worker killed mid-handler never acknowledges its message, so Pub/Sub redelivers it once
    the ack deadline expires — sixty seconds, which is correct and is a minute of a demo
    watching nothing happen. Republishing is the same duplicate delivery arriving sooner, and
    the duplicate is the interesting part: the guards have to refuse it either way.

    This is the one thing the driver supplies that a real deployment would not, and it is
    supplied here rather than hidden inside the resume path so it cannot be mistaken for
    something the fleet does.
    """
    from shared.events import TOPIC_REVIEW_PLAN_READY, publish

    publish(TOPIC_REVIEW_PLAN_READY, demo.review_id, {"resumed": True}, ctx=demo.ctx())


def _handoff(review_id: str, fired: list[str]) -> None:
    """Tell the parent which review to watch, before the child stops being able to."""
    print(f"REVIEW {review_id}", flush=True)
    print(f"BEATS {json.dumps(fired)}", flush=True)


# --- the driver -------------------------------------------------------------------------------


def run(vendor: str) -> int:
    """Drive the three workers and check what each of them did."""
    print(f"\n{RULE}\n  KILL AND RESUME · {vendor}\n{RULE}")

    first = _spawn(["--child", "open", "--vendor", vendor])
    review_id, fired = _parse_handoff(first.stdout)

    if first.returncode != -signal.SIGKILL:
        raise CrashDemoFailed(
            f"the first worker exited {first.returncode} rather than dying on SIGKILL. "
            "A crash beat that did not crash proves nothing."
        )

    _act_one(review_id)
    _act_two(vendor, review_id)
    beats = _act_three(vendor, review_id, fired)

    print(
        f"\n{RULE}\n  the crash added no message and lost no work; "
        f"the review finished in {len(beats)} beats\n{RULE}\n"
    )
    return 0


def _act_one(review_id: str) -> None:
    review = _review(review_id)
    completed = review.get("completed_steps", [])
    key = _send_key(review_id)

    print(f"\n  1 · SIGKILL inside step {_died_on(review_id)!r} on review={review_id}")
    print(f"        current_step    {review.get('current_step')}   (in flight when it died)")
    print(f"        completed_steps {completed}")
    print(f"        idempotency     {key} · {_status(key)}")
    print(f"        messages sent   {_sent(review_id)}")

    if KILL_STEP in completed:
        raise CrashDemoFailed(
            f"{KILL_STEP!r} was recorded complete, so the crash landed after the checkpoint "
            "and the interesting window was never entered."
        )
    if _status(key) != "in_progress":
        raise CrashDemoFailed(
            f"the send claim is {_status(key)!r}. The window this beat demonstrates is the one "
            "where the effect happened and the claim had not yet closed."
        )
    if _sent(review_id) != 1:
        raise CrashDemoFailed(f"{_sent(review_id)} message(s) were sent before the crash")


def _act_two(vendor: str, review_id: str) -> None:
    replay = _spawn(["--child", "replay", "--vendor", vendor, "--review-id", review_id])
    refusals = [
        line
        for line in replay.stdout.splitlines()
        if "idempotency IN PROGRESS" in line or "idempotency STALE" in line
    ]

    print("\n  2 · a second worker replays the send and refuses to repeat it")
    if not refusals:
        raise CrashDemoFailed(
            "the replay did not refuse. Either it resent the questionnaire or it never "
            "replayed the step — and the first is the failure this guard exists to prevent."
        )
    for line in refusals:
        print(f"        {line.split('·')[-1].strip()}")
    print(f"        messages sent   {_sent(review_id)}   (unchanged)")

    if _sent(review_id) != 1:
        raise CrashDemoFailed(f"the replay sent a second message: {_sent(review_id)} in total")


def _act_three(vendor: str, review_id: str, fired: list[str]) -> list[str]:
    from shared.idempotency import confirm

    key = _send_key(review_id)
    print(f"\n  3 · {OPERATOR} checks the outbox and confirms the send happened")
    confirm(key, confirmed_by=OPERATOR)
    print(f"        idempotency     {key} · {_status(key)}")

    finish = _spawn(
        ["--child", "finish", "--vendor", vendor, "--review-id", review_id,
         "--fired", json.dumps(fired)]
    )
    if finish.returncode != 0:
        print(finish.stdout[-4000:], file=sys.stderr)
        raise CrashDemoFailed(f"the third worker exited {finish.returncode}")

    skips = [line for line in finish.stdout.splitlines() if "idempotency SKIP" in line]
    checkpoints = [line for line in finish.stdout.splitlines() if "checkpoint SKIP" in line]

    print("\n  4 · a third worker replays it again; the confirmed claim skips the effect")
    for line in skips:
        print(f"        {line.split('·')[-1].strip()}")
    if not skips:
        raise CrashDemoFailed("the confirmed claim was never replayed, so nothing was skipped")

    beats = _parse_beats(finish.stdout)
    if "questionnaire_sent" not in beats:
        raise CrashDemoFailed(
            "the resumed worker never confirmed that exactly one questionnaire had been sent, "
            "which is the assertion the whole crash exists to make"
        )
    print(f"\n  5 · {len(checkpoints)} further step(s) skipped by checkpoint on the way through")
    print("        the resumed worker asserted one questionnaire sent before carrying on")

    review = _review(review_id)
    versions = int(review.get("plan_version", 1))
    sent = _sent(review_id)
    print(f"\n  6 · questionnaires to the vendor: {sent}, across {versions} plan version(s)")
    print("        one per plan version and none from the crash; the re-tier's second message")
    print("        carries only the questions the first one did not")

    if sent != versions:
        raise CrashDemoFailed(
            f"{sent} questionnaire(s) were sent across {versions} plan version(s). One per plan "
            "version is the claim, and a crash must not add to it."
        )
    if review.get("state") != "decided":
        raise CrashDemoFailed(f"the review ended in {review.get('state')!r} rather than decided")

    return beats


def _spawn(args: list[str]) -> subprocess.CompletedProcess:
    """Run one worker process to completion or to its death."""
    return subprocess.run(
        [sys.executable, "-m", "scenarios.crash_demo", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def _parse_handoff(output: str) -> tuple[str, list[str]]:
    review_id = _line(output, "REVIEW ")
    if not review_id:
        raise CrashDemoFailed(
            "the first worker died before it named its review. Run `make emulators` and "
            "`make reset` first."
        )
    return review_id, _parse_beats(output)


def _parse_beats(output: str) -> list[str]:
    raw = _line(output, "BEATS ")
    return json.loads(raw) if raw else []


def _line(output: str, prefix: str) -> str:
    for line in reversed(output.splitlines()):
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _died_on(review_id: str) -> str:
    step = str(_review(review_id).get("current_step") or "")
    if step != KILL_STEP:
        raise CrashDemoFailed(
            f"the ledger records the review in step {step!r}, not {KILL_STEP!r}. The crash "
            "landed somewhere other than the send."
        )
    return step


def _send_key(review_id: str) -> str:
    from agents.questionnaire.agent import SEND_STEP_ID
    from shared.idempotency import key_for

    return key_for(review_id, int(_review(review_id).get("plan_version", 1)), SEND_STEP_ID)


def _status(idem_key: str) -> str:
    from shared.idempotency import record_status

    return str((record_status(idem_key) or {}).get("status", "never claimed"))


def _review(review_id: str) -> dict:
    return tenant.collection("reviews").document(review_id).get().to_dict() or {}


def _sent(review_id: str) -> int:
    from google.cloud.firestore_v1 import FieldFilter

    return len(
        list(
            tenant.collection("inbox")
            .where(filter=FieldFilter("review_id", "==", review_id))
            .where(filter=FieldFilter("kind", "==", "questionnaire"))
            .stream()
        )
    )


def main() -> int:
    # Every entry point adopts a tenant before it touches anything. Library code never
    # defaults one; a CLI does, and only outside cloud mode.
    with tenant.acting_for(tenant.cli_org()):
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--vendor", default="datadynamo")
        parser.add_argument("--child", choices=("open", "replay", "finish"))
        parser.add_argument("--review-id", default="")
        parser.add_argument("--fired", default="[]")
        parser.add_argument("--log-level", default="INFO")
        args = parser.parse_args()

        logging.basicConfig(
            level=args.log_level.upper(),
            format="%(asctime)s %(levelname)-7s %(name)s · %(message)s",
            datefmt="%H:%M:%S",
            stream=sys.stdout,
        )

        try:
            if args.child == "open":
                return child_open(args.vendor)
            if args.child == "replay":
                return child_replay(args.vendor, args.review_id)
            if args.child == "finish":
                return child_finish(args.vendor, args.review_id, json.loads(args.fired))
            return run(args.vendor)
        except CrashDemoFailed as exc:
            print(f"\nCRASH DEMO FAILED · {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
