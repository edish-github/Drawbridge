"""Create the twelve topics, their dead-letter counterparts and subscriptions in the emulator.

The cloud equivalent is ``infra/bootstrap.sh``; this is the same topology against the Pub/Sub
emulator so local mode has the same event backbone rather than an approximation of it. Both
read their list from ``shared.events.ALL_TOPICS``, so the two cannot drift.

Idempotent: an existing topic or subscription is left alone, so this runs on every
``make emulators``.

Note one honest difference from cloud: the emulator accepts a dead-letter policy but does not
enforce ``max_delivery_attempts``, so a message that would dead-letter in cloud is redelivered
indefinitely locally. The dead-letter path is therefore covered by an explicit test rather than
by watching it happen — see the report.
"""

from __future__ import annotations

import sys

from google.api_core import exceptions as gexc

from shared.clients import publisher_client, subscriber_client, subscription_path, topic_path
from shared.events import (
    ACK_DEADLINE_SECONDS,
    ALL_TOPICS,
    MAX_DELIVERY_ATTEMPTS,
    dlq_topic,
    subscription_name,
)


def main() -> int:
    publisher = publisher_client()
    subscriber = subscriber_client()

    created_topics = 0
    created_subs = 0

    for topic in ALL_TOPICS:
        for name in (topic, dlq_topic(topic)):
            try:
                publisher.create_topic(request={"name": topic_path(name)})
                created_topics += 1
            except gexc.AlreadyExists:
                pass

        try:
            subscriber.create_subscription(
                request={
                    "name": subscription_path(subscription_name(dlq_topic(topic))),
                    "topic": topic_path(dlq_topic(topic)),
                }
            )
            created_subs += 1
        except gexc.AlreadyExists:
            pass

        try:
            subscriber.create_subscription(
                request={
                    "name": subscription_path(subscription_name(topic)),
                    "topic": topic_path(topic),
                    "ack_deadline_seconds": ACK_DEADLINE_SECONDS,
                    "dead_letter_policy": {
                        "dead_letter_topic": topic_path(dlq_topic(topic)),
                        "max_delivery_attempts": MAX_DELIVERY_ATTEMPTS,
                    },
                }
            )
            created_subs += 1
        except gexc.AlreadyExists:
            pass

    print(
        f"[topics] {len(ALL_TOPICS)} topics ready "
        f"({created_topics} created now), {created_subs} subscription(s) created now"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
