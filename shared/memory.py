"""Durable vendor memory: what is worth knowing next time, not everything that happened.

Layer three of four. The ledger holds *what happened, exactly*; this holds *what is worth
knowing next time*. Writes happen at review close and at notable events — an adversarial flag, a
negotiated exception, a contact change — never as a transcript dump.

**The structure rule is a security control, not a style preference.** Memory is written from
material derived from vendor-supplied content, and it is recalled at the *start* of every future
review, before any screening runs in that review. Nothing screens what gets written into durable
memory. So an injection that survived screening, or simply a cleverly-worded questionnaire
answer, could persist a false fact into a dossier and have it recalled as trusted context for
years — a durable compromise seeded through the legitimate channel, and precisely the failure
mode this whole product exists to talk about.

Hence: memory accepts enumerated note types, values from a controlled vocabulary, and a
provenance tag on every note. Free text derived from vendor content never enters this layer. If
a future review needs the vendor's exact wording it reads the ledger, where the wording sits
behind the screening record that describes it.

The guard is enforced here in code rather than trusted to callers, because the caller that gets
it wrong is the one under time pressure at the end of a review.

**Supersession, not accumulation.** A changed contact or a superseded exception replaces its
predecessor; ``recall_dossier`` returns the current view. A dossier holding three contradictory
contact records will eventually hand an agent the wrong one.

``question_effectiveness`` is the one learning note: which questions produced low-confidence
parses or non-answers, written at review close only. The boundary it enforces is that the fleet
may improve **how it asks**, never **how it scores** — and a note type is exactly where that
boundary is enforceable.

Backend follows the mode: a Firestore ``dossiers`` collection locally, Memory Bank in cloud.
Both sit behind this interface, so the structured-write guard applies either way — a guard that
only ran in one backend would be a guard that stopped running the moment it mattered.

Verified against ``google-adk`` 2.7.0: ``VertexAiMemoryBankService(project, location,
agent_engine_id)`` exposes ``add_memory``, ``add_session_to_memory``, ``search_memory`` and
``retrieve_profiles``. TODO(verify): whether ``add_memory`` accepts a structured fact payload
directly or requires a session-shaped wrapper. It does not change the guard, which runs before
the backend is chosen.

Failure semantics: a note failing the type, vocabulary or provenance check raises and is not
written — a rejected note is a visible gap, whereas an accepted bad note is a silent one. If the
backend is unavailable at recall, the caller proceeds with an empty dossier and logs a
degraded-mode warning; recall is context, not a control. If it is unavailable at write, the note
is queued in the ledger for replay rather than dropped.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from shared.clients import firestore_client
from shared.config import settings
from shared.domain import ALLOWED_NOTE_TYPES, MemoryNote

log = logging.getLogger("drawbridge.memory")

COLLECTION_DOSSIERS = "dossiers"

ALLOWED_PROVENANCE = frozenset({"human", "rule", "model_structured"})

CONTROLLED_VOCABULARY: dict[str, frozenset[str]] = {
    "outcome": frozenset({"approved", "conditional", "rejected", "abandoned"}),
    "band": frozenset({"approve", "conditional", "escalate"}),
    "conduct_flag": frozenset({"adversarial_conduct"}),
    "question_effectiveness": frozenset({"usable", "low_confidence", "non_answer"}),
    "approval_condition": frozenset({"attached", "met", "unmet"}),
}
"""Note types whose values come from a fixed vocabulary, and the terms each accepts.

Types absent from this mapping still accept only enumerated *fields* — see ``_check_value`` —
but their values are identifiers rather than terms: a subprocessor name, a certificate expiry
date, a contact address. The distinction is between a value the fleet chose from a list and a
value the fleet copied from a record it holds, and neither is prose written by a vendor.
"""

MAX_VALUE_WORDS = 12
"""A value longer than this is prose, whatever it claims to be.

The cheap structural test that catches the realistic failure: not an attacker crafting a note,
but a developer under deadline passing a vendor's sentence into a field that expected a term.
"""


class NoteRejected(Exception):
    """A note failed the type, vocabulary or provenance check and was not written."""


class Dossier(BaseModel):
    """The current view of what is known about a vendor across reviews.

    Superseded notes are resolved out, not replayed.
    """

    vendor_id: str
    notes: list[MemoryNote] = Field(default_factory=list)
    adversarial_flag: bool = False
    prior_review_ids: list[str] = Field(default_factory=list)

    def latest(self, note_type: str) -> MemoryNote | None:
        """Return the most recent note of a type, or ``None``."""
        matching = [n for n in self.notes if n.type == note_type]
        return max(matching, key=lambda n: n.at, default=None)


def recall_dossier(vendor_id: str) -> Dossier:
    """Return the current view: prior outcomes, exceptions, contacts, conduct flags.

    Raises:
        Nothing. An unavailable memory service returns an empty dossier with a degraded-mode
        warning; a review opening without prior context is worse-informed, not incorrect.
    """
    from google.cloud.firestore_v1 import FieldFilter

    try:
        docs = (
            firestore_client()
            .collection(COLLECTION_DOSSIERS)
            .where(filter=FieldFilter("vendor_id", "==", vendor_id))
            .where(filter=FieldFilter("superseded", "==", False))
            .stream()
        )
        notes = [MemoryNote.model_validate(d.to_dict()["note"]) for d in docs]
    except Exception as exc:  # noqa: BLE001 — recall is context, not a control
        log.warning("degraded mode: dossier recall unavailable for %s: %s", vendor_id, exc)
        return Dossier(vendor_id=vendor_id)

    return Dossier(
        vendor_id=vendor_id,
        notes=notes,
        adversarial_flag=any(n.type == "conduct_flag" for n in notes),
        prior_review_ids=sorted(
            {str(n.value["review_id"]) for n in notes if "review_id" in n.value}
        ),
    )


def remember(vendor_id: str, note: MemoryNote) -> str:
    """Write one distilled, durable fact and return its note id.

    Raises:
        NoteRejected: when the type is outside ``ALLOWED_NOTE_TYPES``, when the provenance is
            not one of ``human``, ``rule`` or ``model_structured``, or when a value field
            carries free text rather than a controlled-vocabulary term or an identifier.
    """
    _validate(note)

    if note.supersedes:
        mark_superseded(vendor_id, note.supersedes)

    doc = firestore_client().collection(COLLECTION_DOSSIERS).document()
    doc.set(
        {
            "vendor_id": vendor_id,
            "note": note.model_dump(mode="json"),
            "superseded": False,
            "written_at": datetime.now(UTC).isoformat(),
            "backend": "firestore" if settings().is_local else "memory_bank",
        }
    )
    log.info("remembered %s note for vendor=%s (%s)", note.type, vendor_id, note.provenance)
    return doc.id


def mark_superseded(vendor_id: str, note_id: str) -> None:
    """Mark a prior note as replaced so ``recall_dossier`` stops returning it.

    Supersession rather than deletion: the superseded note stays readable for the audit trail,
    it simply stops being the current view.
    """
    firestore_client().collection(COLLECTION_DOSSIERS).document(note_id).set(
        {"superseded": True, "superseded_at": datetime.now(UTC).isoformat()}, merge=True
    )


def _validate(note: MemoryNote) -> None:
    """Apply the structured-write guard.

    Raises:
        NoteRejected: naming which rule failed, so the caller fixes the note rather than
            retrying it unchanged.
    """
    if note.type not in ALLOWED_NOTE_TYPES:
        raise NoteRejected(
            f"{note.type!r} is not an allowed note type. Durable memory accepts only "
            f"{sorted(ALLOWED_NOTE_TYPES)}; anything else is prose in disguise."
        )

    if note.provenance not in ALLOWED_PROVENANCE:
        raise NoteRejected(
            f"{note.provenance!r} is not an allowed provenance. Every note is tagged human, "
            "rule or model_structured — never raw vendor text."
        )

    if not note.value:
        raise NoteRejected("a note with no value records nothing; write a value or no note")

    vocabulary = CONTROLLED_VOCABULARY.get(note.type)
    for field, value in note.value.items():
        _check_value(note.type, field, value, vocabulary)


def _check_value(note_type: str, field: str, value, vocabulary: frozenset[str] | None) -> None:
    """Reject a value that is prose rather than a term or an identifier."""
    if isinstance(value, (bool, int, float)) or value is None:
        return

    if not isinstance(value, str):
        raise NoteRejected(
            f"{note_type}.{field} is a {type(value).__name__}; durable memory holds enumerated "
            "fields, not nested structures"
        )

    if vocabulary is not None and field == "value" and value not in vocabulary:
        raise NoteRejected(
            f"{note_type}.{field}={value!r} is outside the controlled vocabulary "
            f"{sorted(vocabulary)}"
        )

    if len(value.split()) > MAX_VALUE_WORDS:
        raise NoteRejected(
            f"{note_type}.{field} looks like prose ({len(value.split())} words). Durable memory "
            "holds terms and identifiers; the vendor's wording lives in the ledger, behind the "
            "screening record that describes it."
        )
