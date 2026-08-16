"""Cross-examination: the hero finding, and the over-flagging it must not produce.

The MFA contradiction is the beat the demo turns on. What makes it defensible rather than
impressive is that its evidence reference resolves to a real retrieved passage.

These run against fixture answers rather than the live model. That is a real limit and it is
stated rather than hidden: what is asserted here is that *when* the model reports the
contradiction, the pipeline carries it end to end with a citation that resolves, the right
provenance, and no way to write an unverifiable one. Whether the model finds it is measured
against the live API, and the free tier's daily request cap is what stopped that measurement
being part of this run.
"""

from __future__ import annotations

import pytest

from agents.evidence.cross_exam import Claim, persist_shape, reconcile_claim
from agents.evidence.retrieval import resolve_chunk
from scenarios.fixtures import responding_from
from scenarios.seed import seed_clean_evidence
from shared.context import AgentContext
from shared.domain import FindingDraft
from tests.conftest import emulator_required

CLAIM_AC01 = Claim(
    question_id="AC01",
    domain="access_control",
    text=(
        "Multi-factor authentication is enforced organisation-wide. Every user account across "
        "every environment requires MFA, including administrative access."
    ),
)

CLAIM_CP01 = Claim(
    question_id="CP01",
    domain="compliance_posture",
    text="Our most recent SOC 2 Type II report is attached, covering 1 July 2024 to 30 June 2025.",
)


@pytest.fixture
def datadynamo(review_id):
    """A review holding DataDynamo's evidence, chunked and indexed."""
    with responding_from("datadynamo"):
        seed_clean_evidence(review_id, "datadynamo")
        yield review_id


def ctx(review_id: str) -> AgentContext:
    return AgentContext(review_id=review_id, agent="evidence", trace_id="t")


# --- The hero finding -------------------------------------------------------------------------


@emulator_required
def test_mfa_contradiction_cites_a_retrieved_passage(datadynamo):
    """The demo beat, asserted end to end: the finding exists and its citation resolves."""
    with responding_from("datadynamo"):
        finding = reconcile_claim(ctx(datadynamo), datadynamo, CLAIM_AC01)

    assert finding is not None
    assert finding.domain == "access_control"
    assert finding.severity == "high"
    assert finding.contradiction is True
    assert finding.source == "model"
    assert finding.claim_ref == "AC01"

    chunk = resolve_chunk(finding.evidence_ref)
    assert chunk is not None, "an unverifiable citation is worse than no citation"
    assert chunk.review_id == datadynamo


@emulator_required
def test_the_cited_chunk_holds_the_exception_language(datadynamo):
    """The passage the finding points at is the one a human would want to read."""
    with responding_from("datadynamo"):
        finding = reconcile_claim(ctx(datadynamo), datadynamo, CLAIM_AC01)

    text = resolve_chunk(finding.evidence_ref).text.lower()

    assert "exception" in text
    assert "multi-factor authentication" in text or "mfa" in text


@emulator_required
def test_every_finding_carries_a_provenance_label(datadynamo):
    with responding_from("datadynamo"):
        findings = [
            reconcile_claim(ctx(datadynamo), datadynamo, claim)
            for claim in (CLAIM_AC01, CLAIM_CP01)
        ]

    for finding in [f for f in findings if f is not None]:
        assert finding.source in ("rule", "model")


@emulator_required
def test_a_claim_the_evidence_supports_produces_no_contradiction(datadynamo):
    """Over-flagging is the model's most common failure here, and it would cheapen the demo."""
    with responding_from("datadynamo"):
        finding = reconcile_claim(ctx(datadynamo), datadynamo, CLAIM_CP01)

    assert finding is None or finding.contradiction is False


# --- Chunking is a retrieval control, not a formatting one ---------------------------------------


def test_no_chunk_exceeds_the_configured_budget():
    """A chunk over the cap is a passage nobody reads in the binder and a diluted vector in the
    index. The setting is a cap, so a single long paragraph is split rather than let through."""
    from shared.armor import CHARS_PER_TOKEN, chunk_text

    budget = 400 * CHARS_PER_TOKEN
    document = "\n\n".join(f"Paragraph {n}. " + "word " * 200 for n in range(6))

    assert all(len(chunk) <= budget for chunk in chunk_text(document, 400))


def test_a_heading_never_ends_a_chunk():
    """``### Exception 3.2 — Multi-factor authentication coverage`` is the most searchable line
    in the section it names. Stranded at the tail of the previous chunk it retrieves the wrong
    passage and starts the finding's citation mid-sentence."""
    from shared.armor import chunk_text

    document = "\n\n".join(
        ["## Section one", "x" * 1400, "### Exception 3.2 — MFA coverage", "y" * 400]
    )

    chunks = chunk_text(document, 400)

    assert len(chunks) > 1
    assert not chunks[0].rstrip().endswith("MFA coverage")
    assert chunks[1].startswith("### Exception 3.2")


@emulator_required
def test_the_cited_passage_starts_where_a_reader_would_start(datadynamo):
    """Binder section 4 prints the cited chunk in full. It reads as a section of the report
    rather than as the tail of one and the head of another because of where it starts."""
    with responding_from("datadynamo"):
        finding = reconcile_claim(ctx(datadynamo), datadynamo, CLAIM_AC01)

    text = resolve_chunk(finding.evidence_ref).text

    assert text.lstrip().startswith("#"), text[:80]


# --- Provenance cannot be claimed by the model --------------------------------------------------


def test_a_model_cannot_label_its_own_finding_as_a_rule():
    """``FindingDraft`` has no ``source`` field, so the schema makes the mislabel unexpressible."""
    assert "source" not in FindingDraft.model_fields


@emulator_required
def test_an_unresolvable_citation_is_rejected_not_written(datadynamo):
    """The binder prints citations, so one that does not resolve is worse than none."""
    draft = FindingDraft(
        domain="access_control",
        severity="high",
        contradiction=True,
        summary="fabricated",
        evidence_ref="chunk-that-does-not-exist",
    )

    with pytest.raises(ValueError, match="does not resolve"):
        persist_shape(datadynamo, CLAIM_AC01, draft, retrieved=[], degraded=False)


@emulator_required
def test_a_contradiction_with_no_citation_is_rejected(datadynamo):
    """A contradiction requires a specific contradicting passage. No passage, no contradiction."""
    draft = FindingDraft(
        domain="access_control",
        severity="high",
        contradiction=True,
        summary="no citation offered",
        evidence_ref=None,
    )

    with pytest.raises(ValueError, match="citing no chunk"):
        persist_shape(datadynamo, CLAIM_AC01, draft, retrieved=[], degraded=False)


@emulator_required
def test_a_contradiction_found_in_degraded_mode_is_rejected(datadynamo):
    """With no retrieval there is no retrieved passage, so a contradiction cannot be evidenced."""
    draft = FindingDraft(
        domain="access_control",
        severity="high",
        contradiction=True,
        summary="found without retrieval",
        evidence_ref=None,
    )

    with pytest.raises(ValueError, match="cannot be evidenced"):
        persist_shape(datadynamo, CLAIM_AC01, draft, retrieved=[], degraded=True)


# --- Retrieval scoping --------------------------------------------------------------------------


@emulator_required
def test_retrieval_is_scoped_to_one_review(datadynamo):
    """The pre-filter means no query can reach another review's evidence."""
    import uuid

    from agents.evidence.retrieval import retrieve_for_claim

    with responding_from("datadynamo"):
        other = f"other{uuid.uuid4().hex[:10]}"
        seed_clean_evidence(other, "cleancloud")
        chunks = retrieve_for_claim(CLAIM_AC01.text, datadynamo, ctx(datadynamo))

    assert chunks, "retrieval returned nothing for a review that has evidence"
    assert all(c.review_id == datadynamo for c in chunks)
    assert all(c.review_id != other for c in chunks)


@emulator_required
def test_retrieval_unavailable_degrades_rather_than_failing(datadynamo, monkeypatch):
    """Retrieval is an optional control and is never on the critical path."""
    monkeypatch.setattr("agents.evidence.retrieval.knn_search", lambda *a, **k: [])

    with responding_from("datadynamo"):
        finding = reconcile_claim(ctx(datadynamo), datadynamo, CLAIM_CP01)

    # It still ran, against whole-document context, and produced either a gap or nothing.
    assert finding is None or finding.contradiction is False


@pytest.mark.skip(reason="the injection corpus lands with the real Model Armor service")
def test_unknown_fourth_party_processing_customer_data_is_a_finding():
    f = run_review("nimbuswrite").findings

    sub = next(x for x in f if x.domain == "subprocessors")

    assert "Veritas Lumen Models" in sub.summary
    assert sub.source == "rule"
