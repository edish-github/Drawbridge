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
    yield
    # The console span exporter runs on a background thread. Without an explicit shutdown it
    # flushes after pytest has closed stdout and prints a traceback that reads like a failure.
    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()


@pytest.fixture
def review_id() -> str:
    """A unique review id per test, so emulator state cannot leak between tests."""
    return f"t{uuid.uuid4().hex[:12]}"


@pytest.fixture
def db():
    from shared.clients import firestore_client

    return firestore_client()
