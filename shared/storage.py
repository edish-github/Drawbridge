"""Object storage behind one interface, with the backend selected by ``RUNTIME_MODE``.

There is no Cloud Storage emulator, so local mode writes to the filesystem. The interface is
identical either way and **references are ``gs://bucket/name`` in both modes**, so the bucket
names from configuration are the directory names locally and the cloud path is a swap rather
than a rewrite. A local-only reference format would mean every ref stored in Firestore during
development becomes unreadable the day the project moves to real buckets.

The local root is ``.local-storage/`` under the repository, git-ignored, one directory per
bucket. Nothing about the layout is load-bearing except that the bucket is the first path
segment, which is what keeps ``parse_ref`` honest across the two backends.

Failure semantics: a missing object raises ``ObjectNotFound`` rather than returning empty
bytes, because empty bytes would be extracted as an empty document and screened clean. An
object name containing a parent-directory segment raises ``InvalidReference`` — the local
backend joins names onto a filesystem path, and a name is derived from vendor-supplied upload
metadata.
"""

from __future__ import annotations

import logging
from pathlib import Path

from shared.config import settings

log = logging.getLogger("drawbridge.storage")

LOCAL_ROOT_DIRNAME = ".local-storage"


class ObjectNotFound(Exception):
    """The reference names an object that does not exist."""


class InvalidReference(Exception):
    """A reference is malformed or names a path outside its bucket."""


def ref_for(bucket: str, name: str) -> str:
    """Build the canonical reference for an object. Identical in both modes."""
    _reject_traversal(name)
    return f"gs://{bucket}/{name.lstrip('/')}"


def parse_ref(ref: str) -> tuple[str, str]:
    """Split ``gs://bucket/name`` into its bucket and object name.

    Raises:
        InvalidReference: on a reference that is not a ``gs://`` URL with both parts, or whose
            object name contains a parent-directory segment.
    """
    if not ref.startswith("gs://"):
        raise InvalidReference(
            f"{ref!r} is not a storage reference. References are gs://bucket/name in both "
            "modes so that a ref recorded locally still resolves in cloud."
        )
    bucket, _, name = ref[len("gs://") :].partition("/")
    if not bucket or not name:
        raise InvalidReference(f"{ref!r} names no object")
    _reject_traversal(name)
    return bucket, name


def read_object(ref: str) -> bytes:
    """Return the raw bytes of an object.

    Raises:
        ObjectNotFound: when the object does not exist.
        InvalidReference: on a malformed reference.
    """
    bucket, name = parse_ref(ref)

    if settings().is_local:
        path = _local_path(bucket, name)
        if not path.is_file():
            raise ObjectNotFound(f"{ref} does not exist at {path}")
        return path.read_bytes()

    from google.api_core import exceptions as gexc

    from shared.clients import storage_client

    blob = storage_client().bucket(bucket).blob(name)
    try:
        return blob.download_as_bytes()
    except gexc.NotFound as exc:
        raise ObjectNotFound(f"{ref} does not exist") from exc


def write_object(ref: str, data: bytes | str, *, content_type: str = "text/plain") -> str:
    """Write an object and return its reference.

    Raises:
        InvalidReference: on a malformed reference.
    """
    bucket, name = parse_ref(ref)
    payload = data.encode("utf-8") if isinstance(data, str) else data

    if settings().is_local:
        path = _local_path(bucket, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        log.debug("wrote %s (%d bytes)", ref, len(payload))
        return ref

    from shared.clients import storage_client

    storage_client().bucket(bucket).blob(name).upload_from_string(
        payload, content_type=content_type
    )
    return ref


def object_exists(ref: str) -> bool:
    """Return whether an object exists. Never raises on absence."""
    try:
        bucket, name = parse_ref(ref)
    except InvalidReference:
        return False

    if settings().is_local:
        return _local_path(bucket, name).is_file()

    from shared.clients import storage_client

    return storage_client().bucket(bucket).blob(name).exists()


def local_root() -> Path:
    """Return the filesystem root the local backend writes under."""
    return Path(__file__).resolve().parent.parent / LOCAL_ROOT_DIRNAME


def _local_path(bucket: str, name: str) -> Path:
    """Resolve a bucket and object name to a filesystem path inside the local root."""
    root = local_root().resolve()
    path = (root / bucket / name).resolve()
    # Belt and braces over _reject_traversal: symlinks and encoded segments are resolved
    # before the containment check, so the check describes where the write actually lands.
    if root not in path.parents:
        raise InvalidReference(f"{bucket}/{name} resolves outside the local storage root")
    return path


def _reject_traversal(name: str) -> None:
    if ".." in Path(name).parts or name.startswith("/"):
        raise InvalidReference(
            f"{name!r} contains a parent-directory segment. Object names are derived from "
            "upload metadata, which is vendor-supplied."
        )
