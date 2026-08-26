"""Model Armor screening, the clean-stamp, and the consequences of a verdict.

One internal screening path with three thin wrappers. Uploads, reply bodies and fleet output
share it, so an injection arriving in an email body — the likelier vector in reality — is
recorded, produces findings and can raise Adversarial Conduct exactly as one arriving in a PDF.
A defence that scores one and only blocks the other has a seam in it.

**Order is load-bearing.** Screening runs against the real extracted text, and the Sensitive
Data Protection hits then tell the local scrubber what to remove. Scrubbing first would run the
SDP filter against content whose sensitive data had already been removed: nothing would error,
and a filter would simply never fire. Raw text still reaches only a screening service and never
a generative model, which is a different trust category.

``index_chunks`` runs after the clean-stamp, never before. Only stamped content is chunked,
embedded and indexed, or retrieval becomes a way to smuggle unscreened text into a model one
passage at a time. It lives here because that ordering constraint is a kernel property and it
enforces it directly — a reference outside the clean bucket is refused — but it is *called* by
the Evidence agent rather than by the promotion path, because embedding is a model call and the
screening identity holds no role that can make one.

**Local mode uses a stub, and the stub is labelled everywhere it could be mistaken for real.**
It logs a warning on every call, sets ``template`` to ``local-stub``, and reports its critical
filters as not having executed — so ``verdict_is_trustworthy`` is ``False``, no clean-stamp is
issued, and anything that would reach a model parks the review. A stub verdict therefore cannot
silently become a real one in the ledger or the audit binder. It exists to make the pipeline
*shape* testable, not to make the defence testable; the defence is measured against the real
service and the injection corpus.

Failure semantics, and the distinction the whole product rests on: **Model Armor is a mandatory
control and fails closed.** If it is unavailable and ``ARMOR_FAIL_CLOSED`` is set, nothing is
promoted out of quarantine, no model receives external content, and the review parks in
``NEEDS_HUMAN``. If any critical filter reports an execution state other than success, no
clean-stamp is issued and the object stays in quarantine — a skipped detector is treated as
unscreened, not as clean. The optional local scrubber does the opposite: if it is unavailable
the pipeline logs a degraded-mode warning and proceeds with the screened text.

Verified against ``google-cloud-modelarmor`` 0.7.1: the client exposes ``sanitize_user_prompt``
and ``sanitize_model_response`` alongside template CRUD.
TODO(verify): the exact response field names for per-filter match state and execution state,
and whether the template version is returned on the sanitize response or must be read from
``get_template``. ``_from_sdk_response`` is the single place those names are read, so
confirming them is a one-function change rather than a sweep.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from shared import tenancy as tenant
from shared.config import settings
from shared.domain import Finding

log = logging.getLogger("drawbridge.armor")

CRITICAL_FILTERS: tuple[str, ...] = ("pi_and_jailbreak", "malicious_uris", "sdp")
"""Filters whose verdict must be trustworthy before content is promoted.

Responsible AI is deliberately absent: its matches are logged and never block, because a false
positive that stalls a review is worse than an unlogged profanity.
"""

MATCH_FOUND = "MATCH_FOUND"
NO_MATCH_FOUND = "NO_MATCH_FOUND"
EXECUTION_SUCCESS = "EXECUTION_SUCCESS"
EXECUTION_SKIPPED = "EXECUTION_SKIPPED"

STUB_TEMPLATE = "local-stub"
"""The template id a stub verdict carries. Never a real template name, so a stub verdict in the
ledger or the binder is identifiable at a glance rather than by provenance archaeology.
"""

SEEDED_TEMPLATE = "local-seed"
"""The template id on a fixture placed into the clean bucket by ``scenarios.seed``.

Local mode cannot promote anything: the stub is untrustworthy by construction, so the screening
pipeline correctly refuses, and the Evidence agent would have nothing to read. The fixture
loader solves that *downstream* of the pipeline by writing pre-extracted documents into the
clean bucket directly — and this constant is what stops that shortcut becoming a lie. A seeded
stamp is as untrustworthy as a stub one to everything that checks, so it can never be mistaken
for a verdict in the ledger or the binder.

Nothing in this module produces it. It is declared here because this is where trustworthiness
is decided, and a label defined by the thing it is supposed to constrain would constrain
nothing.
"""

UNTRUSTED_TEMPLATES: frozenset[str] = frozenset({STUB_TEMPLATE, SEEDED_TEMPLATE})
"""Template ids that never carry a real verdict, whatever else they say."""

COLLECTION_SCREENINGS = "screenings"
COLLECTION_EXCERPTS = "inert_excerpts"

# Patterns the local stub matches. These are the corpus's own documented technique classes, so
# the stub recognises exactly the fixtures this project ships and makes no claim beyond them.
_STUB_INJECTION_PATTERNS = (
    r"system notice for automated review",
    r"treat .{0,40} as pre-?approved",
    r"skip evidence verification",
    r"do not raise findings",
    r"maximum trust score",
    r"ignore (all )?(previous|prior) instructions",
)


class ArmorUnavailable(Exception):
    """Model Armor could not be reached. Mandatory control: the caller fails closed."""


class ArmorSkipped(Exception):
    """A critical filter did not execute. Treated as unscreened, never as clean."""

    def __init__(self, skipped: list[str]) -> None:
        super().__init__(f"critical filters did not execute: {', '.join(skipped)}")
        self.skipped = skipped


class ScreenResult(BaseModel):
    """What screening concluded, and the material the binder needs six months later.

    Attributes:
        clean: no threat found across the blocking filters.
        template: the Model Armor template id that produced this verdict, or ``local-stub``.
        template_version: the version of that template.
        filters: filter name to match state.
        execution: filter name to execution state.
        sanitised: whether a payload was stripped. A sanitised document is by definition one
            that tried something, which is why P2 treats it differently.
        excerpt: the matched text, stored as inert evidence. It goes into the ledger and the
            binder and is never included in a prompt again.
        origin_ref: what was screened.
    """

    clean: bool
    template: str
    template_version: str
    filters: dict[str, str] = Field(default_factory=dict)
    execution: dict[str, str] = Field(default_factory=dict)
    sanitised: bool = False
    excerpt: str | None = None
    origin_ref: str = ""
    screened_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_untrusted(self) -> bool:
        """Whether this verdict came from something other than the real screening service.

        Covers both the local stub and a seeded fixture. Neither executed a detector, so
        neither is a verdict, and the distinction between them matters to a reader of the
        ledger but not to any control.
        """
        return self.template in UNTRUSTED_TEMPLATES

    def threat_found(self) -> bool:
        """Whether any blocking filter matched."""
        return any(self.filters.get(f) == MATCH_FOUND for f in CRITICAL_FILTERS)

    def first_match(self) -> str | None:
        """The first critical filter that matched, for the policy log line."""
        return next((f for f in CRITICAL_FILTERS if self.filters.get(f) == MATCH_FOUND), None)

    def skipped_filters(self) -> list[str]:
        """Critical filters that did not execute."""
        return [
            f for f in CRITICAL_FILTERS if self.execution.get(f) != EXECUTION_SUCCESS
        ]

    def summary(self) -> str:
        """The one-line form used in policy blocks and the binder."""
        match = self.first_match()
        verdict = f"{match} {MATCH_FOUND}" if match else "no match"
        return f"{self.template} {self.template_version} · {verdict}"


def verdict_is_trustworthy(result: ScreenResult) -> bool:
    """Return whether every critical filter actually executed.

    A detector that never ran is not a detector that found nothing. Some regions return a
    skipped execution state for specific detectors on specific content, and code that reads only
    the match state cannot tell the two apart.

    A stub or seeded verdict is never trustworthy. That is what keeps local mode honest: the
    pipeline shape runs, and nothing a stub screened — or a fixture loader placed — is
    admissible to a model on the strength of its stamp.
    """
    if result.is_untrusted:
        return False
    return all(result.execution.get(f) == EXECUTION_SUCCESS for f in CRITICAL_FILTERS)


def _screen(text: str, review_id: str, template: str, origin_ref: str) -> ScreenResult:
    """The single screening path. Every public wrapper calls it and nothing else does.

    Raises:
        ArmorUnavailable: when the service cannot be reached and fail-closed is configured. The
            review parks in ``NEEDS_HUMAN`` with reason ``armor_unavailable`` first.
        ArmorSkipped: when a critical filter did not execute. The review parks with reason
            ``armor_detector_skipped`` and no clean-stamp is issued.
    """
    from shared.state import park

    cfg = settings()

    try:
        result = (
            _screen_with_stub(text, template, origin_ref)
            if cfg.is_local
            else _screen_with_service(text, template, origin_ref)
        )
    except ArmorUnavailable:
        if cfg.armor_fail_closed:
            park(review_id, reason="armor_unavailable")
        raise

    record_screening(review_id, result)

    # A stub is not trustworthy by construction, so in local mode this is the branch that runs.
    # It is the correct branch: the pipeline records what it saw and the review parks rather
    # than promoting content no real detector inspected.
    if not verdict_is_trustworthy(result):
        park(review_id, reason="armor_detector_skipped")
        raise ArmorSkipped(result.skipped_filters())

    return result


def _screen_with_service(text: str, template: str, origin_ref: str) -> ScreenResult:
    """Call the real Model Armor service.

    Raises:
        ArmorUnavailable: on any transport or availability failure. A screening error is never
            downgraded to "probably fine".
    """
    from google.api_core import exceptions as gexc
    from google.cloud import modelarmor_v1

    cfg = settings()
    try:
        client = modelarmor_v1.ModelArmorClient(
            client_options={"api_endpoint": f"modelarmor.{cfg.region}.rep.googleapis.com"}
        )
        name = f"projects/{cfg.project_id}/locations/{cfg.region}/templates/{template}"
        response = client.sanitize_user_prompt(
            request={
                "name": name,
                "user_prompt_data": {"text": text},
            }
        )
    except gexc.GoogleAPIError as exc:
        raise ArmorUnavailable(f"Model Armor call failed: {exc}") from exc

    return _from_sdk_response(response, template, origin_ref)


def _from_sdk_response(response: Any, template: str, origin_ref: str) -> ScreenResult:
    """Map an SDK sanitize response onto ``ScreenResult``.

    The single place the SDK's field names are read, so the ``TODO(verify)`` at the top of this
    module is a one-function change rather than a sweep through the pipeline.
    """
    filters: dict[str, str] = {}
    execution: dict[str, str] = {}

    results = getattr(getattr(response, "sanitization_result", None), "filter_results", {}) or {}
    for name, entry in dict(results).items():
        match_state = getattr(entry, "match_state", None)
        exec_state = getattr(entry, "execution_state", None)
        filters[name] = getattr(match_state, "name", str(match_state))
        execution[name] = getattr(exec_state, "name", str(exec_state))

    threat = any(filters.get(f) == MATCH_FOUND for f in CRITICAL_FILTERS)
    return ScreenResult(
        clean=not threat,
        template=template,
        template_version=str(getattr(response, "template_version", "unknown")),
        filters=filters,
        execution=execution,
        sanitised=threat,
        excerpt=_matched_excerpt(response),
        origin_ref=origin_ref,
    )


def _matched_excerpt(response: Any) -> str | None:
    """Extract the matched text for inert storage, or ``None``."""
    return getattr(response, "matched_excerpt", None)


def _screen_with_stub(text: str, template: str, origin_ref: str) -> ScreenResult:
    """Pattern-match the known corpus payloads so the pipeline shape is testable locally.

    This is not a screening verdict and says so on every call. It recognises the technique
    classes this project's own fixtures use and claims nothing about anything else — a stub that
    implied general detection would be a worse lie than no stub at all.
    """
    log.warning(
        "ARMOR STUB — not a real screening verdict (origin=%s, requested template=%s)",
        origin_ref,
        template,
    )

    lowered = text.lower()
    hit = next(
        (p for p in _STUB_INJECTION_PATTERNS if re.search(p, lowered, re.IGNORECASE)), None
    )
    excerpt = None
    if hit:
        match = re.search(hit, lowered, re.IGNORECASE)
        if match:
            start = max(0, match.start() - 40)
            excerpt = text[start : match.end() + 200]

    return ScreenResult(
        clean=hit is None,
        template=STUB_TEMPLATE,
        template_version="0",
        filters={
            "pi_and_jailbreak": MATCH_FOUND if hit else NO_MATCH_FOUND,
            "malicious_uris": NO_MATCH_FOUND,
            "sdp": NO_MATCH_FOUND,
        },
        # Reported as skipped, not successful. A stub did not execute a detector, and saying
        # otherwise is precisely the mistake this project fails closed on elsewhere.
        execution={f: EXECUTION_SKIPPED for f in CRITICAL_FILTERS},
        sanitised=hit is not None,
        excerpt=excerpt,
        origin_ref=origin_ref,
    )


def screen_and_promote(quarantine_ref: str, review_id: str) -> ScreenResult:
    """Screen a quarantined upload and promote it to the clean bucket if it earns a stamp.

    Order: read raw bytes, extract text locally, screen the real text, record the screening,
    scrub guided by the SDP hits, write the stamped object, publish. Indexing follows on the
    Evidence agent's side of the identity boundary.

    Raises:
        ArmorUnavailable, ArmorSkipped: nothing is promoted; the object stays in quarantine and
            the review parks. The seven-day lifecycle rule deletes the object in time while the
            inert excerpt in the ledger survives, so the binder is complete after the payload is
            gone.
    """
    raw = read_quarantine_object(quarantine_ref)
    text = extract_text(raw)

    result = _screen(text, review_id, settings().model_armor_template_untrusted, quarantine_ref)

    scrubbed = scrub_pii(text, result)
    body = strip_payload(scrubbed, result) if result.threat_found() else scrubbed

    # Promotion ends here. Chunking and embedding are *not* called from this function, because
    # embedding is a model call and the screening identity holds no Vertex AI role — it is the
    # one component in the fleet that is structurally incapable of prompting anything, and
    # calling index_chunks here would quietly make the published permission matrix false. The
    # Evidence agent runs it on evidence.screened; the ordering constraint it enforces lives in
    # index_chunks itself, which refuses any reference outside the clean bucket.
    write_clean_object(quarantine_ref, body, result, review_id)
    return result


def screen_text(body: str, review_id: str, origin_ref: str) -> ScreenResult:
    """Screen a vendor reply body. Same path, same records, same consequences as an upload."""
    return _screen(body, review_id, settings().model_armor_template_untrusted, origin_ref)


def screen_output(text: str, review_id: str, origin_ref: str = "fleet_output") -> ScreenResult:
    """Screen what the fleet produces, before a human reads it or a vendor receives it.

    Applied to the risk memo and to outbound email bodies. This is the only control that assumes
    every earlier one failed: if an injected instruction ever survived into a memo — steering a
    recommendation, embedding a URL, echoing dossier content — it is caught here, at the last
    gate before a CISO acts on it.

    Raises:
        ArmorUnavailable, ArmorSkipped: the artefact is never published; the review parks.
    """
    return _screen(text, review_id, settings().model_armor_template_output, origin_ref)


def record_screening(review_id: str, result: ScreenResult) -> str:
    """Persist a screening verdict.

    Written by the screening identity, which holds no findings write.

    The pipeline records and publishes; the consuming agent writes the finding. That boundary is
    what keeps the component handling the most hostile bytes incapable of writing into the score.
    """
    doc = result.model_dump(mode="json")
    doc["review_id"] = review_id
    ref = tenant.collection(COLLECTION_SCREENINGS).document()
    ref.set(doc)
    log.info(
        "screened review=%s origin=%s verdict=%s trustworthy=%s",
        review_id,
        result.origin_ref,
        result.summary(),
        verdict_is_trustworthy(result),
    )
    return ref.id


def findings_from_verdict(review_id: str, screen: ScreenResult) -> list[Finding]:
    """Derive the everyday findings a verdict implies, all labelled ``source="rule"``.

    An SDP match becomes a ``data_protection`` finding at medium severity: a vendor who ships
    customer personal data inside an evidence pack has told you something material about their
    handling practice, and this is the most common finding in real vendor review. A
    malicious-URI match becomes a ``subprocessors`` finding at medium, with the URI stored
    inert. Responsible AI matches are logged and never scored.

    The injection consequence is not here: it is ``raise_adversarial_conduct`` in the Risk
    Scorer, because the screening identity holds no ``findings`` write.
    """
    out: list[Finding] = []

    if screen.filters.get("sdp") == MATCH_FOUND:
        out.append(
            Finding(
                finding_id=f"{review_id}:sdp:{_short(screen.origin_ref)}",
                review_id=review_id,
                domain="data_protection",
                severity="medium",
                source="rule",
                contradiction=False,
                summary=(
                    "Vendor-supplied evidence contained personal data. Flagged for their "
                    "handling practice."
                ),
                evidence_ref=screen.origin_ref,
            )
        )

    if screen.filters.get("malicious_uris") == MATCH_FOUND:
        out.append(
            Finding(
                finding_id=f"{review_id}:uri:{_short(screen.origin_ref)}",
                review_id=review_id,
                domain="subprocessors",
                severity="medium",
                source="rule",
                contradiction=False,
                summary="Flagged URI in a vendor-supplied document; recorded as inert evidence.",
                evidence_ref=store_inert_excerpt(review_id, screen.excerpt or ""),
            )
        )

    return out


def store_inert_excerpt(review_id: str, excerpt: str) -> str:
    """Persist a matched excerpt as inert evidence and return its reference.

    Inert means exactly one thing: the text is stored for the binder and is never included in a
    prompt again. Re-feeding it would defeat the point of having blocked it.
    """
    ref = tenant.collection(COLLECTION_EXCERPTS).document()
    ref.set(
        {
            "review_id": review_id,
            "excerpt": excerpt,
            "inert": True,
            "never_prompt": True,
            "stored_at": datetime.now(UTC).isoformat(),
        }
    )
    return f"excerpt:{ref.id}"


def sign_stamp(claim: dict) -> str:
    """Sign a clean-stamp claim so the gateway can verify it under P2.

    The stamp is a signed claim carrying the reference, review id, template id and version, the
    per-filter verdicts, and whether the content was sanitised — so policy can be enforced on
    *what screening said*, not merely on *whether screening happened*.

    TODO(verify): production signing uses the project's asymmetric key pair. Until the key
    material exists, this derives a deterministic tag over the claim so P2 can be exercised
    locally. It is not a signature and ``verify_stamp`` treats a locally-tagged stamp as
    unverified in cloud mode.
    """
    import json

    payload = json.dumps(claim, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"local-tag:{digest}:{payload}"


def stamp_for(review_id: str, ref: str) -> str | None:
    """Return the signed clean-stamp covering ``ref``, or ``None`` when nothing screened it.

    This is what a caller passes to ``routing.generate`` so P2 can decide whether the content
    may reach a model. The stamp is rebuilt from the screening ledger rather than read from the
    clean bucket's sidecar: the ledger is the record the binder is rendered from, and a policy
    that consulted a different artefact than the audit trail could pass on one and fail on the
    other.

    A reference is matched exactly first, then by document stem. Promotion writes the clean
    object as ``{review}/{stem}.txt`` from a quarantine object named ``{vendor}/{stem}.pdf``,
    and the screening record names the quarantine reference, so the caller holding a clean
    reference has a different string for the same document. Matching the stem is what closes
    that gap without either side having to know the other's naming.

    Returns ``None`` rather than raising: an unscreened source is a policy outcome, and the
    single place it is logged is the P2 block that follows.
    """
    from google.cloud.firestore_v1 import FieldFilter

    records = [
        d.to_dict() or {}
        for d in tenant.collection(COLLECTION_SCREENINGS)
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    ]

    wanted = _stem(ref)
    match = next(
        (r for r in records if r.get("origin_ref") == ref),
        next((r for r in records if _stem(str(r.get("origin_ref", ""))) == wanted), None),
    )
    if match is None:
        return None

    match.pop("review_id", None)
    return sign_stamp(match)


def stamps_for(review_id: str, refs: list[str] | None = None) -> list[str]:
    """Return the signed stamps for ``refs``, or for every source screened on this review.

    The no-argument form is what the memo call uses: its prompt is built from findings drawn
    from every document and every reply, so the sources it must account for are all of them.
    """
    if refs is not None:
        return [s for s in (stamp_for(review_id, ref) for ref in refs) if s]

    from google.cloud.firestore_v1 import FieldFilter

    out: list[str] = []
    for doc in (
        tenant.collection(COLLECTION_SCREENINGS)
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    ):
        record = doc.to_dict() or {}
        record.pop("review_id", None)
        out.append(sign_stamp(record))
    return out


def _stem(ref: str) -> str:
    """Return a reference's bare document name without directory or extension."""
    return Path(ref.rsplit("/", 1)[-1]).stem


CHUNK_COLLECTION = "evidence_chunks"
CHARS_PER_TOKEN = 4
"""Characters per token, for sizing chunks without tokenising.

An approximation, and deliberately a cheap one: chunk size trades retrieval precision against
call count, and a tokeniser round-trip per document would cost more than the imprecision does.
"""


def index_chunks(clean_ref: str, review_id: str) -> int:
    """Chunk, embed and index a stamped document. Returns the number of chunks written.

    Only ever called after the clean-stamp exists, and it enforces that rather than assuming
    it: a reference outside the clean bucket is refused. Retrieval reading unstamped content
    would be a way to smuggle unscreened text into a model one passage at a time, which is
    exactly the shape of the attack the screening boundary exists to stop.

    In local mode the chunks and their embeddings are written to Firestore with no vector
    index; similarity search over a single review's chunks is small enough to run brute force.
    The KNN index is a cloud-mode concern, declared in ``infra/firestore/indexes.yaml`` at
    **3072** dimensions to match ``gemini-embedding-001``, not the 768 the planning documents
    assumed.

    Raises:
        Nothing on an embedding or index failure: retrieval is an optional control. It logs a
        degraded-mode warning and returns 0, and cross-examination falls back to whole-document
        context. Retrieval is never on the critical path.
    """
    from types import SimpleNamespace

    from shared import storage
    from shared.routing import embed

    cfg = settings()
    bucket, name = storage.parse_ref(clean_ref)
    if bucket != cfg.bucket_clean:
        log.error(
            "refusing to index %s: only clean-bucket content is chunked, and this is in %r",
            clean_ref,
            bucket,
        )
        return 0

    try:
        text = storage.read_object(clean_ref).decode("utf-8", errors="replace")
        chunks = chunk_text(text, cfg.chunk_tokens)
    except Exception as exc:  # noqa: BLE001 — optional control, degrades rather than blocks
        log.warning("degraded mode: could not chunk %s: %s", clean_ref, exc)
        return 0

    ctx = SimpleNamespace(review_id=review_id, agent="screening", trace_id="")
    written = 0

    for index, body in enumerate(chunks):
        chunk_id = f"{review_id}:{_short(clean_ref)}:{index:03d}"
        try:
            vector = embed(body, ctx)
            tenant.collection(CHUNK_COLLECTION).document(chunk_id).set(
                {
                    "chunk_id": chunk_id,
                    "review_id": review_id,
                    "doc_ref": clean_ref,
                    "page": 1,
                    "text": body,
                    "embedding": vector,
                }
            )
            written += 1
        except Exception as exc:  # noqa: BLE001 — see the docstring: retrieval degrades
            log.warning("degraded mode: chunk %s not indexed: %s", chunk_id, exc)

    log.info("indexed %d/%d chunks for review=%s from %s", written, len(chunks), review_id, name)
    return written


_HEADING = re.compile(
    r"^(?:"
    r"#{1,6}\s"  # Markdown, which is what the synthetic packs use
    r"|[IVXLC]{1,7}[.)]\s+\S"  # I. EXECUTIVE SUMMARY · IV) Findings
    r"|(?:APPENDIX|ANNEX|EXHIBIT|SCHEDULE)\s+[A-Z0-9]{1,3}\b"  # APPENDIX C: ...
    r"|[A-Z][A-Z '&/-]{4,69}$"  # PERFORMANCE AUDIT REPORT
    r")"
)
"""What counts as a heading, chosen for precision rather than recall.

The rule this feeds — a heading never ends a chunk, so a section title travels with the text it
names — only ever *moves* a paragraph forward. That makes a false positive expensive and a false
negative cheap: mistaking a footnote for a heading tears a real paragraph off the end of its
chunk, while missing a heading leaves the chunker doing what it did before.

Measured against a real 63-page typeset audit report: **4 matches out of 256 paragraphs, all
four of them genuine headings**, and no change to any Markdown fixture in the pack.

**One candidate pattern was tried and dropped.** Numbered sections — ``3.1``, ``7 ``, the
obvious way to catch ``3.1 Access control`` — matched 33 paragraphs in that document and not one
of them was a heading. Every single match was a numbered *footnote*, which is what the bottom of
a typeset page is full of. It is not repairable by tightening the number format, because a
footnote marker and a section number are the same string in the same position; distinguishing
them needs page geometry, which text extraction has already discarded. Dropped, and recorded
here rather than left as a heuristic that fires wrongly.

Trailing all-caps lines are matched only when they carry no digits, which is what keeps
``SEPTEMBER 27, 2024`` on a cover page from reading as a section title. It also means a heading
like ``SECTION 3 ACCESS CONTROL`` is missed, and that is the trade taken deliberately.
"""


def chunk_text(text: str, chunk_tokens: int) -> list[str]:
    """Split text into chunks of at most ``chunk_tokens``, breaking on paragraphs.

    Paragraph boundaries rather than a fixed character stride, so a retrieved passage is a
    passage rather than a sentence cut in half — a chunk that ends mid-claim reads as a gap to
    the cross-examination prompt.

    Two refinements on top of that, and both are retrieval quality rather than tidiness:

    A heading never ends a chunk. ``### Exception 3.2 — Multi-factor authentication coverage``
    is its own paragraph, so a naive split strands it at the tail of the preceding chunk and
    starts the next one mid-finding. The heading is the most searchable line in the section and
    it belongs with the text it names, so any run of trailing headings moves into the new chunk
    instead of closing the old one. ``_HEADING`` recognises typeset headings as well as Markdown
    ones — a rule that only fired on fixtures was worse than no rule, because it never failed.

    The budget is a cap rather than a target. A single paragraph longer than the budget used to
    become an oversized chunk of its own, which is how a "400-token" setting quietly produces a
    passage nobody wants to read in the binder; one that long is split on sentence boundaries.
    """
    budget = max(1, chunk_tokens) * CHARS_PER_TOKEN
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    for paragraph in _paragraphs(text, budget):
        if current and size + len(paragraph) > budget:
            carried = _trailing_headings(current)
            body = current[: len(current) - len(carried)]
            if body:
                chunks.append("\n\n".join(body))
            current = carried
            size = sum(len(p) for p in carried)
        current.append(paragraph)
        size += len(paragraph)

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _paragraphs(text: str, budget: int):
    """Yield the document's paragraphs, splitting any that exceed the budget on their own."""
    for raw in re.split(r"\n\s*\n", text):
        paragraph = raw.strip()
        if not paragraph:
            continue
        if len(paragraph) <= budget:
            yield paragraph
        else:
            yield from _split_sentences(paragraph, budget)


def _split_sentences(paragraph: str, budget: int) -> list[str]:
    """Break one over-long paragraph at sentence boundaries."""
    parts: list[str] = []
    current = ""
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        if current and len(current) + len(sentence) + 1 > budget:
            parts.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return parts


def _trailing_headings(block: list[str]) -> list[str]:
    """Return the run of headings at the end of a chunk, which belong to the next one."""
    carried: list[str] = []
    for paragraph in reversed(block):
        if not _HEADING.match(paragraph):
            break
        carried.insert(0, paragraph)
    return carried


# --- Storage seams -------------------------------------------------------------------------
# These are the only places the pipeline touches object storage. They are separate functions so
# the screening path above can be tested without a storage backend, and so the quarantine read
# has exactly one call site to audit.


def read_quarantine_object(quarantine_ref: str) -> bytes:
    """Read raw bytes from quarantine. The only quarantine read in the system.

    Raises:
        ObjectNotFound, InvalidReference: propagated from ``shared.storage``. Nothing is
            promoted from a reference that does not resolve.
    """
    from shared import storage

    return storage.read_object(quarantine_ref)


def extract_text(raw: bytes) -> str:
    """Extract text locally. Raw bytes never reach a generative model.

    A document with no extractable text alongside embedded images is flagged for human review
    rather than silently passed — the known blind spot, bounded rather than denied.

    Raises:
        UnextractableDocument: when nothing could be extracted. The caller does not promote it.
    """
    from shared.extraction import extract

    return extract(raw)


def write_clean_object(
    quarantine_ref: str, body: str, result: ScreenResult, review_id: str
) -> str:
    """Write the stamped object to the clean bucket and return its reference.

    Two objects are written: the extracted, screened text, and a sidecar carrying the signed
    stamp. The sidecar means the clean bucket is self-describing — an object there can be traced
    back to the template and per-filter verdicts that admitted it, without a Firestore read.
    """
    from shared import storage

    cfg = settings()
    _, name = storage.parse_ref(quarantine_ref)
    base = f"{review_id}/{Path(name).stem}"

    clean_ref = storage.ref_for(cfg.bucket_clean, f"{base}.txt")
    storage.write_object(clean_ref, body)

    stamp = sign_stamp(result.model_dump(mode="json"))
    storage.write_object(
        storage.ref_for(cfg.bucket_clean, f"{base}.stamp"),
        stamp,
        content_type="application/json",
    )

    log.info("promoted %s -> %s (%s)", quarantine_ref, clean_ref, result.summary())
    return clean_ref


def scrub_pii(text: str, result: ScreenResult) -> str:
    """Remove personal data, guided by the SDP hits screening just produced.

    Optional control: if the scrubber is unavailable this logs a degraded-mode warning and
    returns the screened text unchanged. It never blocks the pipeline, which is the deliberate
    asymmetry with Model Armor.
    """
    if result.filters.get("sdp") != MATCH_FOUND:
        return text
    log.warning("degraded mode: PII scrubber not available, proceeding with screened text")
    return text


def strip_payload(text: str, result: ScreenResult) -> str:
    """Remove the matched payload so the legitimate content is still reviewable."""
    if not result.excerpt:
        return text
    return text.replace(result.excerpt, "[REMOVED BY SCREENING]")


def _short(ref: str) -> str:
    return hashlib.sha256(ref.encode("utf-8")).hexdigest()[:12]
