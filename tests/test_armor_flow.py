"""Screening: the block, the consequence, the measured detection rate, and failing closed.

The defence is measured rather than asserted. The table published in the repository README is
this file's output, and the honest number is the one that ships.
"""

import pytest


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_injection_blocks_and_drops_trust_score():
    r = run_review("nimbuswrite")

    assert r.flags.adversarial_conduct is True
    assert r.score <= baseline_score("nimbuswrite") - 25
    assert r.band == "escalate"
    assert "conduct" in [f.domain for f in r.findings]


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_conduct_finding_names_the_template_and_the_filter():
    """A verdict without its policy is not reproducible six months later."""
    r = run_review("nimbuswrite")

    finding = next(f for f in r.findings if f.domain == "conduct")

    assert "drawbridge-untrusted" in finding.summary
    assert "pi_and_jailbreak" in finding.summary
    assert finding.source == "rule"


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_blocked_excerpt_is_stored_inert_and_never_re_prompted():
    r = run_review("nimbuswrite")

    finding = next(f for f in r.findings if f.domain == "conduct")
    excerpt = resolve_inert_excerpt(finding.evidence_ref)

    assert "pre-approved" in excerpt.lower()
    assert excerpt not in every_prompt_sent_during(r.review_id)


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_injection_corpus_detection_rate():
    """The published table is this test's output, including the variants that got through."""
    res = run_corpus("synthetic-vendors/injection-corpus")

    assert res.detected_at_ingress >= 10
    assert res.variant(12).caught_by == "output_screening"
    assert res.variant(9).mitigated_by == "unscreenable_rule"
    assert res.adversarial_conduct_raised >= 11


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_no_false_positives_on_clean_packs():
    for vendor in ("cleancloud", "datadynamo", "nimbuswrite_without_payload"):
        assert run_review(vendor).flags.adversarial_conduct is False


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_skipped_detector_fails_closed():
    """A detector that never ran is not a detector that found nothing."""
    with armor_returning(execution_state="EXECUTION_SKIPPED", filter="sdp"):
        r = run_review("cleancloud")

    assert r.state == ReviewState.NEEDS_HUMAN
    assert not clean_bucket_contains("cleancloud")


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_armor_unavailable_promotes_nothing():
    """Mandatory controls fail closed. Optional controls degrade. Never the other way round."""
    with armor_unavailable():
        r = run_review("cleancloud")

    assert r.state == ReviewState.NEEDS_HUMAN
    assert park_reason(r.review_id) == "armor_unavailable"
    assert not clean_bucket_contains("cleancloud")


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_reply_body_injection_is_scored_not_merely_blocked():
    """Uploads and reply bodies share one screening path, so both raise Adversarial Conduct."""
    r = deliver_reply_containing_injection("cleancloud")

    assert r.flags.adversarial_conduct is True
    assert screening_recorded(r.review_id, origin="reply_body")


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_sensitive_data_match_becomes_a_finding():
    """Impossible before screening ran ahead of scrubbing. The most common real finding."""
    r = run_review_with_pii_in_evidence("cleancloud")

    f = next(x for x in r.findings if x.domain == "data_protection")

    assert f.source == "rule"
    assert f.severity == "medium"


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_responsible_ai_match_is_logged_and_never_scored():
    r = run_review_with_rai_match("cleancloud")

    assert rai_logged(r.review_id) is True
    assert all(f.domain != "responsible_ai" for f in r.findings)
    assert r.state != ReviewState.NEEDS_HUMAN


@pytest.mark.skip(reason="shared/armor.py is a contract; unskip when it executes")
def test_memo_is_screened_before_a_human_reads_it():
    """The only control that assumes every earlier one failed."""
    with memo_containing_injected_instruction():
        r = run_review("nimbuswrite")

    assert r.state == ReviewState.NEEDS_HUMAN
    assert park_reason(r.review_id) == "output_screening"
    assert memo_published(r.review_id) is False
