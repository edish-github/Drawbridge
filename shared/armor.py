"""Model Armor screening, the clean-stamp, and the consequences of a verdict.

One internal screening path with two thin wrappers. Uploads and reply bodies share it, so
an injection arriving in an email body — the likelier vector in reality — is recorded,
produces findings and can raise Adversarial Conduct exactly as one arriving in a PDF. A
defence that scores one and only blocks the other has a seam in it.

**Order is load-bearing.** Screening runs against the real extracted text, and the
Sensitive Data Protection hits then tell the local scrubber what to remove. Scrubbing
first would run the SDP filter against content whose sensitive data had already been
removed: nothing would error, and a filter would simply never fire. Raw text still reaches
only a screening service and never a generative model, which is a different trust category.

``index_chunks`` runs after the clean-stamp, never before. Only stamped content is chunked,
embedded and indexed, or retrieval becomes a way to smuggle unscreened text into a model one
passage at a time.

Failure semantics, and the distinction the whole product rests on: **Model Armor is a
mandatory control and fails closed.** If it is unavailable and ``ARMOR_FAIL_CLOSED`` is set,
nothing is promoted out of quarantine, no model receives external content, and the review
parks in ``NEEDS_HUMAN``. If any critical filter reports an execution state other than
success, no clean-stamp is issued and the object stays in quarantine — a skipped detector is
treated as unscreened, not as clean. The optional local scrubber does the opposite: if it is
unavailable the pipeline logs a degraded-mode warning and proceeds with the screened text.

Verified against ``google-cloud-modelarmor`` 0.7.1: the client exposes ``sanitize_user_prompt``
and ``sanitize_model_response`` alongside template CRUD. TODO(verify): the exact response
field names for per-filter match state and execution state, and whether the template version
is returned on the sanitize response or must be read from ``get_template``.
"""

from __future__ import annotations

from pydantic import BaseModel

from shared.domain import Finding

CRITICAL_FILTERS: tuple[str, ...] = ("pi_and_jailbreak", "malicious_uris", "sdp")
"""Filters whose verdict must be trustworthy before content is promoted. Responsible AI is
deliberately absent: its matches are logged and never block, because a false positive that
stalls a review is worse than an unlogged profanity.
"""


class ArmorUnavailable(Exception):
    """Model Armor could not be reached. Mandatory control: the caller fails closed."""


class ArmorSkipped(Exception):
    """A critical filter did not execute. Treated as unscreened, never as clean."""


class ScreenResult(BaseModel):
    """What screening concluded, and the material the binder needs six months later.

    Attributes:
        clean: no threat found across the blocking filters.
        template: the Model Armor template id that produced this verdict.
        template_version: the version of that template, recorded so a later reviewer knows
            which policy screened the document.
        filters: filter name to match state.
        execution: filter name to execution state.
        sanitised: whether a payload was stripped. A sanitised document is by definition
            one that tried something, which is why P2 treats it differently.
        excerpt: the matched text, stored as inert evidence. It goes into the ledger and
            the binder and is never included in a prompt again.
    """

    clean: bool
    template: str
    template_version: str
    filters: dict[str, str]
    execution: dict[str, str]
    sanitised: bool = False
    excerpt: str | None = None


def verdict_is_trustworthy(result: ScreenResult) -> bool:
    """Return whether every critical filter actually executed.

    A detector that never ran is not a detector that found nothing. Some regions return a
    skipped execution state for specific detectors on specific content, and code that reads
    only the match state cannot tell the two apart.
    """
    raise NotImplementedError


def _screen(text: str, review_id: str, template: str, origin_ref: str) -> ScreenResult:
    """The single screening path. Both public wrappers call it and nothing else does.

    Raises:
        ArmorUnavailable: when the service cannot be reached and fail-closed is configured.
            The review parks in ``NEEDS_HUMAN`` with reason ``armor_unavailable`` first.
        ArmorSkipped: when a critical filter did not execute. The review parks with reason
            ``armor_detector_skipped`` and no clean-stamp is issued.
    """
    raise NotImplementedError


def screen_and_promote(quarantine_ref: str, review_id: str) -> ScreenResult:
    """Screen a quarantined upload and promote it to the clean bucket if it earns a stamp.

    Reads the raw object, extracts text locally, screens the real text, records the
    screening, optionally scrubs guided by the SDP hits, writes the stamped object to the
    clean bucket, indexes its chunks, and publishes ``evidence.screened``.

    Raises:
        ArmorUnavailable, ArmorSkipped: nothing is promoted; the object stays in quarantine
            and the review parks. The seven-day lifecycle rule on the quarantine bucket
            deletes the object in time, while the inert excerpt in the ledger survives, so
            the binder is complete after the payload is gone.
    """
    raise NotImplementedError


def screen_text(body: str, review_id: str, origin_ref: str) -> ScreenResult:
    """Screen a vendor reply body. Same path, same records, same consequences as an upload."""
    raise NotImplementedError


def screen_output(text: str, review_id: str) -> ScreenResult:
    """Screen what the fleet produces, before a human reads it or a vendor receives it.

    Applied to the risk memo and to outbound email bodies. This is the only control that
    assumes every earlier one failed: if an injected instruction ever survived into a memo —
    steering a recommendation, embedding a URL, echoing dossier content — it is caught here,
    at the last gate before a CISO acts on it.

    Raises:
        ArmorUnavailable: the memo is never published unscreened; the review parks with
            reason ``output_screening``.
    """
    raise NotImplementedError


def findings_from_verdict(review_id: str, screen: ScreenResult) -> list[Finding]:
    """Derive the everyday findings a verdict implies, all labelled ``source="rule"``.

    An SDP match becomes a ``data_protection`` finding at medium severity: a vendor who
    ships customer personal data inside an evidence pack has told you something material
    about their handling practice, and this is the most common finding in real vendor
    review. A malicious-URI match becomes a ``subprocessors`` finding at medium, with the
    URI stored inert. Responsible AI matches are logged and never scored.

    The injection consequence is not here: it is ``raise_adversarial_conduct`` in the Risk
    Scorer, because the screening pipeline identity holds no ``findings`` write. The
    pipeline records and publishes; the consuming agent writes the finding.
    """
    raise NotImplementedError


def sign_stamp(claim: dict) -> str:
    """Sign a clean-stamp claim so the gateway can verify it under P2.

    Raises:
        SigningKeyUnavailable: propagated. An unsigned stamp is never emitted, because an
            unsigned stamp is a stamp the gateway must reject anyway.
    """
    raise NotImplementedError


def store_inert_excerpt(review_id: str, excerpt: str) -> str:
    """Persist a matched excerpt as inert evidence and return its reference.

    Inert means exactly one thing: the text is stored for the binder and is never included
    in a prompt again. Re-feeding it would defeat the point of having blocked it.
    """
    raise NotImplementedError


def index_chunks(clean_ref: str, review_id: str) -> int:
    """Chunk, embed and index a stamped document. Returns the number of chunks written.

    Only ever called after the clean-stamp exists.

    Raises:
        Nothing on an embedding or index failure: retrieval is an optional control. It logs
        a degraded-mode warning and returns 0, and cross-examination falls back to
        whole-document context. Retrieval is never on the critical path.
    """
    raise NotImplementedError
