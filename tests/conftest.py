"""Test fixtures.

Tests that touch the emulators are skipped when the emulators are not running, so a clean
checkout still gets a green run from the pure-logic tests. ``make emulators`` turns them on.
"""

from __future__ import annotations

import os
import socket
import uuid

import pytest

FIRESTORE_HOST = os.environ.get("FIRESTORE_EMULATOR_HOST", "localhost:8080")
PUBSUB_HOST = os.environ.get("PUBSUB_EMULATOR_HOST", "localhost:8085")


def _reachable(host_port: str) -> bool:
    host, _, port = host_port.rpartition(":")
    try:
        with socket.create_connection((host or "localhost", int(port)), timeout=0.5):
            return True
    except OSError:
        return False


emulator_required = pytest.mark.skipif(
    not _reachable(FIRESTORE_HOST),
    reason=f"Firestore emulator not reachable at {FIRESTORE_HOST}; run `make emulators`",
)

pubsub_required = pytest.mark.skipif(
    not _reachable(PUBSUB_HOST),
    reason=f"Pub/Sub emulator not reachable at {PUBSUB_HOST}; run `make emulators`",
)


@pytest.fixture(autouse=True, scope="session")
def _local_mode_env():
    """Ensure the test process runs in local mode against the emulators.

    ``GEMINI_API_KEY`` is set to a placeholder: no test in this file makes a model call, and
    configuration must still validate. A test that needs a real key states so itself.
    """
    os.environ.setdefault("RUNTIME_MODE", "local")
    os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used")
    os.environ.setdefault("FIRESTORE_EMULATOR_HOST", FIRESTORE_HOST)
    os.environ.setdefault("PUBSUB_EMULATOR_HOST", PUBSUB_HOST)
    # The suite runs on seeded fixtures, which P2 correctly refuses. Set rather than defaulted,
    # so the value is the same whatever the developer's shell holds. Tests that exercise the
    # refusal itself unset it for their own duration.
    os.environ["DRAWBRIDGE_ALLOW_UNSCREENED"] = "1"
    yield
    # The console span exporter runs on a background thread. Without an explicit shutdown it
    # flushes after pytest has closed stdout and prints a traceback that reads like a failure.
    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()


@pytest.fixture(autouse=True, scope="session")
def _drain_subscriptions(_local_mode_env):
    """Leave the emulator's subscriptions empty when the run finishes.

    Tests publish real events, and the emulator keeps them until something acknowledges them.
    Without this, ``make run-local`` after a test run consumes a backlog of test reviews and
    reports policy blocks for reviews that no longer mean anything — which reads as a product
    failure and is nothing of the kind.
    """
    yield

    if not _reachable(PUBSUB_HOST):
        return

    from google.api_core import exceptions as gexc

    from shared.clients import subscriber_client, subscription_path
    from shared.events import ALL_TOPICS, subscription_name

    client = subscriber_client()
    for topic in ALL_TOPICS:
        path = subscription_path(subscription_name(topic))
        while True:
            try:
                pulled = client.pull(
                    request={"subscription": path, "max_messages": 100}, timeout=0.5
                )
            except (gexc.DeadlineExceeded, gexc.NotFound):
                break
            if not pulled.received_messages:
                break
            client.acknowledge(
                request={
                    "subscription": path,
                    "ack_ids": [m.ack_id for m in pulled.received_messages],
                }
            )


TEST_ORG = "test-org"
"""The tenant every test runs under.

One shared org rather than one per test, deliberately: tests already isolate on ``review_id``,
and a fresh org per test would make the emulator's ``orgs`` collection grow by one document per
test run without testing anything the shared org does not. Tests that are *about* isolation
create their own orgs and say so.
"""


@pytest.fixture(autouse=True, scope="session")
def _tenant():
    """Run every test on behalf of one organisation.

    Autouse and **session-scoped**. Function scope was the first attempt and it left every
    module- and session-scoped fixture running outside a tenant — including the demo-flow
    fixture that drives a whole review — so the scope has to be the outermost one. A test that
    wants a different org nests its own ``acting_for``, which restores cleanly on exit.
    """
    from shared import tenancy

    with tenancy.acting_for(TEST_ORG):
        yield TEST_ORG


@pytest.fixture
def review_id() -> str:
    """A unique review id per test, so emulator state cannot leak between tests."""
    return f"t{uuid.uuid4().hex[:12]}"


@pytest.fixture
def db():
    """A tenant-scoped stand-in for the raw Firestore client.

    Tests were written against ``db.collection("reviews")`` and there are hundreds of those
    calls. Rather than rewrite each one, the fixture returns an object whose ``collection`` is
    the tenant-scoped one — so a test reaches the same rows the fleet does, through the same
    path, without knowing the path exists.

    ``collection_group`` is exposed too, for the handful of tests that legitimately read across
    tenants.
    """
    from shared import tenancy
    from shared.clients import firestore_client

    class ScopedDb:
        def collection(self, name: str):
            return tenancy.collection(name)

        def collection_group(self, name: str):
            return firestore_client().collection_group(name)

        def transaction(self):
            return firestore_client().transaction()

        @property
        def raw(self):
            """The unscoped client, for tests that assert on platform collections."""
            return firestore_client()

    return ScopedDb()
