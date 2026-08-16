"""Backend selection: the one place ``RUNTIME_MODE`` turns into a client object.

Every other module in the kernel asks for a client here and never branches on mode itself.
That is the whole point of a single switch — a conditional repeated in eight modules is eight
chances for local and cloud to drift apart, and the drift only shows up in the mode you test
less.

Clients are constructed once and cached per process. The Google client libraries are safe to
share across threads and expensive to build.

Failure semantics: a client that cannot be constructed raises here rather than returning a
half-configured object, so the failure names the backend rather than surfacing later as a
confusing call error. In local mode the emulator host variables are already exported by
``shared.config.load_settings``, so the clients pick them up without being told.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from shared.config import settings


@lru_cache(maxsize=1)
def firestore_client():
    """Return the Firestore client for this mode.

    In local mode ``FIRESTORE_EMULATOR_HOST`` is set, and the library talks to the emulator
    with no credentials. In cloud mode it uses application default credentials.
    """
    from google.cloud import firestore

    cfg = settings()
    if cfg.is_local:
        # The emulator authenticates nothing but the library still requires a credential
        # object, so an explicit anonymous one avoids a search for credentials that are not
        # there and would fail slowly.
        from google.auth.credentials import AnonymousCredentials

        return firestore.Client(
            project=cfg.emulator_project(),
            credentials=AnonymousCredentials(),
        )
    return firestore.Client(project=cfg.project_id)


@lru_cache(maxsize=1)
def publisher_client():
    """Return the Pub/Sub publisher for this mode.

    ``settings()`` is called before the client is built and not merely for the mode value: it
    is what exports ``PUBSUB_EMULATOR_HOST`` into the environment, and the Pub/Sub library
    reads that variable at construction time. Building the client first sends every publish to
    the real API instead of the emulator, and the library gives no indication that it did.
    """
    from google.cloud import pubsub_v1

    settings()
    return pubsub_v1.PublisherClient()


@lru_cache(maxsize=1)
def subscriber_client():
    """Return the Pub/Sub subscriber for this mode. See ``publisher_client`` on ordering."""
    from google.cloud import pubsub_v1

    settings()
    return pubsub_v1.SubscriberClient()


@lru_cache(maxsize=1)
def storage_client():
    """Return the Cloud Storage client.

    Cloud mode only. There is no Storage emulator, so local mode is served by the filesystem
    backend in ``shared.storage`` and never reaches this function; calling it locally raises
    rather than quietly building a client that would talk to the real API.
    """
    cfg = settings()
    if cfg.is_local:
        raise RuntimeError(
            "local mode has no Cloud Storage backend — shared.storage writes to the "
            "filesystem. Reaching this function locally means a caller bypassed it."
        )

    from google.cloud import storage

    return storage.Client(project=cfg.project_id)


@lru_cache(maxsize=1)
def genai_client() -> Any:
    """Return the generative model client for this mode.

    One client class serves both backends: ``google-genai`` takes either an API key or a
    Vertex AI project and location, and the ``models.generate_content`` surface is identical
    either way. That is what lets ``shared.routing`` hold a single code path rather than two
    that must be kept in step.
    """
    from google import genai

    cfg = settings()
    if cfg.is_local:
        return genai.Client(api_key=cfg.gemini_api_key)
    return genai.Client(vertexai=True, project=cfg.project_id, location=cfg.region)


def topic_path(topic: str) -> str:
    """Return the fully qualified Pub/Sub topic path."""
    cfg = settings()
    return publisher_client().topic_path(cfg.emulator_project(), topic)


def subscription_path(subscription: str) -> str:
    """Return the fully qualified Pub/Sub subscription path."""
    cfg = settings()
    return subscriber_client().subscription_path(cfg.emulator_project(), subscription)


def reset_client_cache() -> None:
    """Drop every cached client. For tests that switch mode inside one process."""
    firestore_client.cache_clear()
    publisher_client.cache_clear()
    subscriber_client.cache_clear()
    storage_client.cache_clear()
    genai_client.cache_clear()
