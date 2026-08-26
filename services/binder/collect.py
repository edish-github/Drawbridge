"""Gathering the material the binder prints. Reads only, and reads only the ledger.

Eight sections, one query set. Everything here comes out of collections another component
already wrote — reviews, events, decisions, qa_responses, screenings, findings,
evidence_chunks, scores, memos, dashboard_events, subprocessors — and nothing is recomputed.
That is the property the artefact rests on: the binder shows what the fleet recorded at the
time, not what the current code would conclude if asked again today. A binder that recomputed
its score would be showing this week's rubric against a decision taken under last week's.

Nothing here calls a model, and there is no import path from this module to one. The cover says
the binder is rendered by a template rather than written by a model, and that sentence is only
worth printing if it is structurally true — a blocked payload must not be able to influence the
document that reports it.

Failure semantics: a missing section is rendered as an explicit absence rather than omitted. A
review with no findings prints "no findings recorded", because a section that silently
disappears reads as a section that never applied.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from shared import tenancy as tenant

log = logging.getLogger("drawbridge.binder")

FRAMEWORKS = (
    ("SOC 2", "CC9.2 — vendor and business partner risk assessment"),
    ("ISO/IEC 27001", "A.5.19–5.23 — supplier relationships and cloud service security"),
    ("DORA", "Article 28 — register of ICT third-party service arrangements"),
    ("NIS2", "Article 21(2)(d) — supply chain security"),
)
"""The compliance frameworks this table of contents maps onto, printed on the cover.

Named with the specific clause rather than the standard alone. "Maps to SOC 2" is a claim
anybody can make; naming CC9.2 is a claim an auditor can check in about a minute, which is the
difference between a compliance line and a compliance mapping.
"""


class ReviewNotFound(Exception):
    """No review with that id. A binder is never rendered from a partial reconstruction."""


@dataclass
class Binder:
    """Everything the eight sections need, collected once."""

    review: dict
    vendor: dict
    timeline: list[dict] = field(default_factory=list)
    questionnaire: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    chunks: dict[str, dict] = field(default_factory=dict)
    score: dict = field(default_factory=dict)
    memo: str = ""
    decisions: list[dict] = field(default_factory=list)
    reasoning: list[dict] = field(default_factory=list)
    monitoring: list[dict] = field(default_factory=list)
    subprocessors: list[dict] = field(default_factory=list)
    followups: dict[str, int] = field(default_factory=dict)
    chain: dict = field(default_factory=dict)

    @property
    def review_id(self) -> str:
        return str(self.review.get("review_id", ""))

    @property
    def unscreened(self) -> bool:
        """Whether this review was built on fixtures no detector inspected.

        Printed on the cover when true. An artefact that could not be told apart from a real one
        would be the most damaging thing this repository could produce.
        """
        return bool(self.review.get("unscreened_fixtures"))

    @property
    def approver(self) -> str:
        """The named human who accepted the risk, or a statement that nobody has."""
        return str(self.review.get("gate_released_by") or "not yet accepted")


def collect(review_id: str) -> Binder:
    """Gather everything the binder prints for one review.

    Raises:
        ReviewNotFound: when the review does not exist. A binder assembled from whatever
            happened to be in the collections would be a document about nothing.
    """

    review = (tenant.collection("reviews").document(review_id).get().to_dict()) or {}
    if not review:
        raise ReviewNotFound(f"no review {review_id!r}; there is nothing to render")

    vendor = (
        tenant.collection("vendors").document(str(review.get("vendor_id", ""))).get().to_dict()
    ) or {}

    binder = Binder(review=review, vendor=vendor)
    binder.timeline = _timeline(review_id)
    binder.questionnaire = _sorted(_by_review("qa_responses", review_id), "question_id")
    binder.evidence = _sorted(_by_review("screenings", review_id), "origin_ref")
    binder.findings = _sorted(_by_review("findings", review_id), "finding_id")
    binder.chunks = {
        str(c.get("chunk_id")): c for c in _by_review("evidence_chunks", review_id)
    }
    binder.score = (tenant.collection("scores").document(review_id).get().to_dict()) or {}
    binder.memo = str(
        ((tenant.collection("memos").document(review_id).get().to_dict()) or {}).get("text", "")
    )
    binder.decisions = _sorted(_by_review("dashboard_events", review_id), "at")
    binder.reasoning = _sorted(_by_review("decisions", review_id), "at")
    binder.monitoring = [
        e for e in binder.timeline if str(e.get("type", "")).startswith("watchdog.")
    ]
    binder.subprocessors = _sorted(
        _by_field("subprocessors", "vendor_id", str(review.get("vendor_id", ""))),
        "subprocessor_id",
    )
    binder.followups = {
        str(f.get("question_id")): int(f.get("count", 0))
        for f in _by_review("followups", review_id)
    }
    binder.chain = _chain_view(str(review.get("vendor_id", "")))

    log.info(
        "collected binder material for review=%s: %d timeline, %d answers, %d documents, "
        "%d findings, %d reasoning entries",
        review_id,
        len(binder.timeline),
        len(binder.questionnaire),
        len(binder.evidence),
        len(binder.findings),
        len(binder.reasoning),
    )
    return binder


def passage_for(binder: Binder, finding: dict) -> dict | None:
    """Return the chunk a finding cites, with its document and page.

    Section 4 carries retrieval provenance, and provenance means the passage plus where it came
    from. A citation that cannot be resolved is returned as ``None`` and printed as an
    unresolved reference rather than quietly dropped — the reader needs to know the difference
    between a finding with no citation and a citation that no longer resolves.
    """
    ref = finding.get("evidence_ref")
    if not ref:
        return None
    return binder.chunks.get(str(ref))


def _timeline(review_id: str) -> list[dict]:
    """Every event recorded against this review, oldest first.

    Transitions, published events and tier changes together, because the audit question is what
    happened to this review in order, and splitting them by which collection they landed in
    would answer a question nobody asked.
    """
    events = _by_review("events", review_id)
    for event in events:
        event.setdefault("ts", event.get("at", ""))
    return sorted(events, key=lambda e: str(e.get("ts", "")))


def _chain_view(vendor_id: str) -> dict:
    """Return the fourth-party chain: you, the vendor, and the companies behind the vendor.

    Read here rather than imported from ``agents.evidence.subprocessors``, which builds the same
    shape. That module imports ``shared.routing``, and the binder's promise that it makes no
    model call is asserted by an import graph — a promise that has to be true transitively or it
    is not a promise. The duplication is fifteen lines of Firestore reads and the alternative is
    a document that could be steered by the content it reports on.
    """
    if not vendor_id:
        return {}

    vendor = tenant.collection("vendors").document(vendor_id).get().to_dict() or {}
    chain = _sorted(_by_field("subprocessors", "vendor_id", vendor_id), "name")
    if not chain:
        return {}

    return {
        "organisation": "This organisation",
        "vendor": {
            "name": vendor.get("name", vendor_id),
            "vendor_id": vendor_id,
            "residency_required": (vendor.get("intake") or {}).get("data_residency_required")
            or [],
        },
        "subprocessors": chain,
    }


def _by_review(collection: str, review_id: str) -> list[dict]:
    return _by_field(collection, "review_id", review_id)


def _by_field(collection: str, field_name: str, value: str) -> list[dict]:
    from google.cloud.firestore_v1 import FieldFilter

    if not value:
        return []
    return [
        d.to_dict() or {}
        for d in tenant
        .collection(collection)
        .where(filter=FieldFilter(field_name, "==", value))
        .stream()
    ]


def _sorted(rows: list[dict], key: str) -> list[dict]:
    return sorted(rows, key=lambda r: str(r.get(key, "")))


def counts(binder: Binder) -> dict[str, Any]:
    """Return the per-section counts printed in the table of contents.

    Counts rather than page numbers, because page numbers are a property of the printed PDF and
    the reader is looking at HTML until they press print. A contents list with numbers that
    proved wrong on paper would be worse than one with none.
    """
    return {
        "1": len(binder.timeline),
        "2": len(binder.questionnaire),
        "3": len(binder.evidence),
        "4": len(binder.findings),
        "5": len(binder.score.get("arithmetic", [])),
        "6": len([d for d in binder.decisions if d.get("kind") in ("gate", "tier_change")]),
        "7": len(binder.reasoning),
        "8": len(binder.monitoring),
    }
