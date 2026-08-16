"""The risk memo, and the Adversarial Conduct signal.

One deep-model call per review. Input: findings, the score breakdown, vendor context and the
prior dossier. Output: a one-page memo written for a CISO with a fixed structure — the
recommendation, the three things that drove it, the mitigations required for conditional
approval, and what to re-check in 90 days. This is the single artefact a human reads before
approving, which is why it gets the expensive model.

The memo is screened before a human reads it. It is the only control in the system that
assumes every earlier one failed: if an injected instruction ever survived into a memo —
steering the recommendation, embedding a URL, echoing dossier content — this catches it at the
last gate before a CISO acts on it.

Adversarial Conduct is raised here rather than in the screening pipeline, because the
screening identity holds no ``findings`` write. The pipeline records the screening and
publishes; the consumer that holds the write records the consequence. Three things happen at
once: the Trust Score drops 25 points, the band is forced to escalate regardless of the
arithmetic, and the vendor record carries the flag into every future review.

Failure semantics: a memo that fails output screening is never published; the review parks in
``NEEDS_HUMAN`` with reason ``output_screening``. A memo generation failure parks the review
rather than presenting a score with no reasoning behind it. Raising Adversarial Conduct is
idempotent on the review: a second screening verdict for the same origin reference does not
apply a second 25-point penalty.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from shared.armor import MATCH_FOUND, ScreenResult
from shared.clients import firestore_client
from shared.domain import Finding, ScoreResult

log = logging.getLogger("drawbridge.memo")

ADVERSARIAL_PENALTY = 25

MEMO_PROMPT = """\
Write a one-page vendor risk memo for a CISO.

Structure, fixed:
1. The recommendation.
2. The three findings that drove it, each with its provenance (rule or model).
3. The mitigations required for conditional approval.
4. What to re-check in 90 days.

You do not compute or adjust the Trust Score, and you do not change any severity.
Report what the findings say.

VENDOR: {vendor}
SCORE BREAKDOWN: {breakdown}
FINDINGS: {findings}
PRIOR DOSSIER: {dossier}
"""


COLLECTION_MEMOS = "memos"


def write_memo(
    ctx,
    review_id: str,
    *,
    findings: list[Finding],
    score: ScoreResult,
    vendor: dict,
    dossier=None,
) -> str:
    """Generate the memo, record it, and return its reference.

    The one deep-model call per review, and the artefact a human actually reads. The band is
    passed in rather than described, because the instruction that matters — the recommendation
    must match the band — is only checkable if the band is a value rather than a paraphrase.

    Output screening is deliberately **not** wired into this path yet, for the same reason it is
    not on the outbound questionnaire: the local Model Armor stub is untrustworthy by
    construction, so screening a fleet-authored memo here would park every local review before
    the memo was ever written. It lands with the real service, and it is the control that
    assumes every earlier one failed.

    Every source screened on the review is named on this call, because the memo is written from
    findings drawn from all of them. That is where P2's sharpest rule lands: a sanitised
    document is admissible to the Evidence agent and inadmissible here, so a vendor who planted
    something cannot have the resulting document quoted into the artefact a CISO acts on.

    Raises:
        Exception: a generation failure propagates and the caller parks the review. A score
            with no reasoning behind it is not something to approve against.
        PolicyViolation: naming P2, when any source on the review is inadmissible to the memo.
    """
    from shared.armor import stamps_for
    from shared.routing import generate

    prompt = MEMO_PROMPT.format(
        vendor=f"{vendor.get('name', '?')} — {vendor.get('category', '?')}",
        breakdown=_render_breakdown(score),
        findings=_render_findings(findings),
        dossier=_render_dossier(dossier),
    )

    result = generate("risk_memo", prompt, ctx, source_stamps=stamps_for(review_id))
    text = result.text.strip()
    if not text:
        raise ValueError(f"the memo call returned nothing for review {review_id}")

    ref = f"memo:{review_id}"
    firestore_client().collection(COLLECTION_MEMOS).document(review_id).set(
        {
            "review_id": review_id,
            "text": text,
            "score": score.score,
            "band": score.band,
            "model": result.model,
            "written_at": datetime.now(UTC).isoformat(),
            # Recorded rather than assumed: the binder prints whether the artefact a human read
            # was screened, and in local mode the honest answer is no.
            "output_screened": False,
        }
    )
    log.info("memo written for review=%s (%d chars, band %s)", review_id, len(text), score.band)
    return ref


def read_memo(review_id: str) -> str:
    """Return the memo text, or an empty string when none has been written."""
    snap = firestore_client().collection(COLLECTION_MEMOS).document(review_id).get()
    return str((snap.to_dict() or {}).get("text", ""))


def raise_adversarial_conduct(review_id: str, screen: ScreenResult) -> None:
    """Apply the Adversarial Conduct consequence for a prompt-injection verdict.

    Sets the review flag and the vendor flag, writes a ``conduct`` finding at high severity
    labelled ``source="rule"`` and carrying the blocked excerpt as inert evidence, publishes
    ``review.rescore``, and raises the dashboard banner.

    The finding's summary names the template and version that fired, because a verdict
    without its policy is not reproducible six months later.

    Idempotent on the origin reference: the finding id is derived from it, so a second verdict
    for the same document rewrites one document rather than applying a second penalty.
    """
    from shared.armor import store_inert_excerpt
    from shared.context import AgentContext
    from shared.events import TOPIC_REVIEW_RESCORE, publish

    db = firestore_client()
    review = db.collection("reviews").document(review_id).get().to_dict() or {}
    vendor_id = review.get("vendor_id", "")

    finding = Finding(
        finding_id=f"{review_id}:conduct:{_short(screen.origin_ref)}",
        review_id=review_id,
        domain="conduct",
        severity="high",
        source="rule",
        contradiction=False,
        summary=(
            f"Attempted manipulation of the review process. {screen.template} "
            f"{screen.template_version} recorded {screen.first_match()} {MATCH_FOUND} on "
            f"{screen.origin_ref}. The Trust Score falls {ADVERSARIAL_PENALTY} points and the "
            "band is escalate regardless of the arithmetic."
        ),
        evidence_ref=store_inert_excerpt(review_id, screen.excerpt or ""),
    )
    db.collection("findings").document(finding.finding_id).set(finding.model_dump(mode="json"))

    db.collection("reviews").document(review_id).set({"adversarial_conduct": True}, merge=True)
    if vendor_id:
        db.collection("vendors").document(vendor_id).set({"adversarial_flag": True}, merge=True)

    ctx = AgentContext(review_id=review_id, agent="risk_scorer", trace_id="")
    publish(TOPIC_REVIEW_RESCORE, review_id, {"reason": "adversarial_conduct"}, ctx=ctx)

    log.warning(
        "ADVERSARIAL CONDUCT raised for review=%s vendor=%s · %s",
        review_id,
        vendor_id,
        screen.summary(),
    )


def _render_breakdown(score: ScoreResult) -> str:
    lines = [f"  {name}: {value}" for name, value in sorted(score.breakdown.items())]
    lines.append(f"  TRUST SCORE: {score.score} / 100 · band {score.band}")
    if score.adversarial_applied:
        lines.append(f"  adversarial conduct: -{ADVERSARIAL_PENALTY}, band forced to escalate")
    return "\n".join(lines)


def _render_findings(findings: list[Finding]) -> str:
    if not findings:
        return "  none recorded"
    return "\n".join(
        f"  [{f.source}] {f.domain} · {f.severity}"
        f"{' · contradiction' if f.contradiction else ''} — {f.summary}"
        for f in findings
    )


def _render_dossier(dossier) -> str:
    """Render prior context as enumerated facts. Never recalled prose into a prompt."""
    if dossier is None or not getattr(dossier, "notes", None):
        return "  no prior reviews of this vendor"
    lines = [f"  adversarial conduct flag: {dossier.adversarial_flag}"]
    lines += [f"  {note.type}: {note.value}" for note in dossier.notes]
    return "\n".join(lines)


def _short(ref: str) -> str:
    import hashlib

    return hashlib.sha256(ref.encode("utf-8")).hexdigest()[:12]
