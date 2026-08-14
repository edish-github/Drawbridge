"""Cross-examination: the hero finding, and the over-flagging it must not produce.

The MFA contradiction is the beat the demo turns on. What makes it defensible rather than
impressive is that its evidence reference resolves to a real retrieved passage.
"""

import pytest


@pytest.mark.skip(reason="agents/evidence is a contract; unskip when it executes")
def test_mfa_contradiction_cites_a_retrieved_passage():
    f = run_review("datadynamo").findings

    hit = next(x for x in f if x.contradiction and x.domain == "access_control")
    chunk = resolve_chunk(hit.evidence_ref)

    assert "exception" in chunk.text.lower() and "mfa" in chunk.text.lower()
    assert hit.source == "model"
    assert hit.severity == "high"
    assert hit.claim_ref == "AC01"


@pytest.mark.skip(reason="agents/evidence is a contract; unskip when it executes")
def test_missing_evidence_is_a_gap_not_a_contradiction():
    """Over-flagging is the model's most common failure here, and it would cheapen the demo."""
    f = run_review("nimbuswrite").findings

    mfa = next(x for x in f if x.domain == "access_control")

    assert mfa.contradiction is False


@pytest.mark.skip(reason="agents/evidence is a contract; unskip when it executes")
def test_every_finding_carries_a_provenance_label():
    for finding in run_review("datadynamo").findings:
        assert finding.source in ("rule", "model")


@pytest.mark.skip(reason="agents/evidence is a contract; unskip when it executes")
def test_expired_certificate_is_a_rule_finding_not_a_model_finding():
    """A date comparison should never be a model's job."""
    f = run_review("datadynamo").findings

    cert = next(x for x in f if "expired" in x.summary.lower())

    assert cert.source == "rule"
    assert cert.domain == "compliance_posture"
    assert cert.severity == "high"


@pytest.mark.skip(reason="agents/evidence is a contract; unskip when it executes")
def test_retrieval_is_scoped_to_one_review():
    """The pre-filter is applied by the index, so no query can reach another review's evidence."""
    a = run_review("datadynamo")
    b = run_review("cleancloud")

    chunks = knn_search(a.review_id, embed("multi-factor authentication"), k=6)

    assert all(c.review_id == a.review_id for c in chunks)
    assert all(c.review_id != b.review_id for c in chunks)


@pytest.mark.skip(reason="agents/evidence is a contract; unskip when it executes")
def test_unresolvable_citation_is_rejected_not_written():
    """An unverifiable citation is worse than no citation, because the binder prints it."""
    with model_returning_finding(evidence_ref="chunk-that-does-not-exist"):
        with pytest.raises(ValueError):
            list(cross_examine(ctx(), review_id="datadynamo"))


@pytest.mark.skip(reason="agents/evidence is a contract; unskip when it executes")
def test_retrieval_unavailable_degrades_rather_than_failing():
    """Retrieval is an optional control and is never on the critical path."""
    with knn_index_unavailable():
        r = run_review("datadynamo")

    assert r.state != ReviewState.NEEDS_HUMAN
    assert r.findings
    assert degraded_mode_logged(r.review_id, control="retrieval")


@pytest.mark.skip(reason="agents/evidence is a contract; unskip when it executes")
def test_unknown_fourth_party_processing_customer_data_is_a_finding():
    f = run_review("nimbuswrite").findings

    sub = next(x for x in f if x.domain == "subprocessors")

    assert "Veritas Lumen Models" in sub.summary
    assert sub.source == "rule"
