"""Durable vendor memory: what is worth knowing next time, not everything that happened.

Layer three of four. The ledger holds *what happened, exactly*; this holds *what is worth
knowing next time*. Writes happen at review close and at notable events — an adversarial
flag, a negotiated exception, a contact change — never as a transcript dump.

**The structure rule is a security control, not a style preference.** Memory is written from
material derived from vendor-supplied content, and it is recalled at the *start* of every
future review, before any screening runs in that review. Nothing screens what gets written
into durable memory. So an injection that survived screening, or simply a cleverly-worded
questionnaire answer, could persist a false fact into a dossier and have it recalled as
trusted context for years — a durable compromise seeded through the legitimate channel.
Hence: memory accepts enumerated note types, values from a controlled vocabulary, and a
provenance tag on every note. Free text derived from vendor content never enters this layer.
If a future review needs the vendor's exact wording it reads the ledger, where the wording
sits behind the screening record that describes it.

**Supersession, not accumulation.** A changed contact or a superseded exception replaces its
predecessor; ``recall_dossier`` returns the current view. A dossier holding three
contradictory contact records will eventually hand an agent the wrong one.

``question_effectiveness`` is the one learning note: which questions produced low-confidence
parses or non-answers, written at review close only. The boundary it enforces is that the
fleet may improve **how it asks**, never **how it scores** — and a note type is exactly where
that boundary is enforceable.

Verified against ``google-adk`` 2.7.0: ``VertexAiMemoryBankService(project, location,
agent_engine_id)`` exposes ``add_memory``, ``add_session_to_memory``, ``search_memory`` and
``retrieve_profiles``. TODO(verify): whether ``add_memory`` accepts a structured fact payload
directly or requires a session-shaped wrapper, which determines whether the structured-write
guard below is enforced before the call or by a schema on it.

Failure semantics: a note failing the type, vocabulary or provenance check raises and is not
written — a rejected note is a visible gap, whereas an accepted bad note is a silent one. If
Memory Bank is unavailable at recall, the caller proceeds with an empty dossier and logs a
degraded-mode warning; recall is context, not a control. If it is unavailable at write, the
note is queued in the ledger for replay rather than dropped.
"""

from __future__ import annotations

from pydantic import BaseModel

from shared.domain import MemoryNote


class NoteRejected(Exception):
    """A note failed the type, vocabulary or provenance check and was not written."""


class Dossier(BaseModel):
    """The current view of what is known about a vendor across reviews.

    Superseded notes are resolved out, not replayed.
    """

    vendor_id: str
    notes: list[MemoryNote]
    adversarial_flag: bool = False
    prior_review_ids: list[str] = []


def recall_dossier(vendor_id: str) -> Dossier:
    """Return the current view: prior outcomes, exceptions, contacts, conduct flags.

    Raises:
        Nothing. An unavailable memory service returns an empty dossier with a degraded-mode
        warning; a review opening without prior context is worse-informed, not incorrect.
    """
    raise NotImplementedError


def remember(vendor_id: str, note: MemoryNote) -> str:
    """Write one distilled, durable fact and return its note id.

    Raises:
        NoteRejected: when the type is outside ``ALLOWED_NOTE_TYPES``, when the provenance
            is not one of ``human``, ``rule`` or ``model_structured``, or when a value field
            carries free text rather than a controlled-vocabulary term.
    """
    raise NotImplementedError


def mark_superseded(vendor_id: str, note_id: str) -> None:
    """Mark a prior note as replaced so ``recall_dossier`` stops returning it."""
    raise NotImplementedError
