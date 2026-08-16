"""The storage backend, and the reference format that has to survive the move to cloud.

References are ``gs://bucket/name`` in both modes, so a reference recorded in Firestore during
local development still resolves once real buckets exist. A local-only format would mean every
screening record written before the migration became unreadable after it.

No emulator needed: the local backend is the filesystem.
"""

from __future__ import annotations

import pytest

from shared import storage
from shared.config import settings


@pytest.fixture
def quarantine_ref(review_id):
    return storage.ref_for(settings().bucket_quarantine, f"{review_id}/overview.md")


def test_a_written_object_reads_back_byte_identical(quarantine_ref):
    storage.write_object(quarantine_ref, b"MFA is enforced.\n")

    assert storage.read_object(quarantine_ref) == b"MFA is enforced.\n"


def test_references_are_gs_urls_in_local_mode(quarantine_ref):
    """The format is the contract; the backend behind it is not."""
    assert quarantine_ref.startswith("gs://")
    assert storage.parse_ref(quarantine_ref)[0] == settings().bucket_quarantine


def test_the_bucket_name_from_config_is_the_directory_name(quarantine_ref):
    storage.write_object(quarantine_ref, "x")

    bucket, name = storage.parse_ref(quarantine_ref)

    assert (storage.local_root() / bucket / name).is_file()


def test_a_missing_object_raises_rather_than_returning_empty_bytes():
    """Empty bytes would be extracted as an empty document and screened clean."""
    with pytest.raises(storage.ObjectNotFound):
        storage.read_object(storage.ref_for(settings().bucket_clean, "nothing/here.txt"))


def test_object_exists_is_false_for_a_missing_object_and_never_raises():
    assert storage.object_exists("not-a-reference") is False
    assert storage.object_exists(storage.ref_for("b", "missing")) is False


# --- Reference hygiene -----------------------------------------------------------------------
# Object names come from upload metadata, which is vendor-supplied.


@pytest.mark.parametrize(
    "name",
    ["../../etc/passwd", "a/../../b", "/absolute/path"],
)
def test_a_parent_directory_segment_is_refused(name):
    with pytest.raises(storage.InvalidReference):
        storage.ref_for("evidence-quarantine", name)


@pytest.mark.parametrize(
    "ref",
    ["/tmp/evidence.md", "http://example.test/x", "gs://bucket", "gs://", ""],
)
def test_a_malformed_reference_is_refused(ref):
    with pytest.raises(storage.InvalidReference):
        storage.parse_ref(ref)
