"""Run the local worker: pull events, dispatch them to agents, acknowledge.

    make run-local

One process, one blocking loop, no background threads. That is what makes it killable in a way
the crash tests and the demo both depend on — ``kill -9`` lands exactly where the work is, and
the next start resumes from the ledger rather than from whatever a thread pool had half done.

The ADK development UI is a separate concern and stays on its own target (``make dev-ui``):
it is for inspecting an agent interactively, and it does not consume the event backbone.

Ctrl-C stops after the message in flight. A SIGKILL stops immediately and is the interesting
case: nothing is lost that was not already checkpointed, and nothing is repeated that was
already claimed.
"""

from __future__ import annotations

import argparse
import logging
import sys

from shared.config import settings
from shared.events import ALL_TOPICS
from shared.subscriber import Runner, handlers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--topics",
        nargs="*",
        default=None,
        help="topics to consume; defaults to every topic with a registered handler",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="stop after this many passes over the subscription list; unset runs until stopped",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s · %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = settings()
    registered = sorted(handlers())
    print(f"[worker] mode={cfg.mode.value}")
    print(f"[worker] handlers registered for {len(registered)} of {len(ALL_TOPICS)} topics")
    for topic in registered:
        print(f"[worker]   {topic}")

    return 0 if Runner(args.topics).run(max_batches=args.max_batches) >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
