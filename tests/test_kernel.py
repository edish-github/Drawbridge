"""Kernel behaviour: screening ordering, gateway policies, span hygiene, memory guards.

These cover the claims the architecture rests on, at the level the kernel can prove them
without an agent. Where a claim needs the real Model Armor service, the test asserts what local
mode must do instead — which is fail closed rather than pretend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from shared.armor import (
    EXECUTION_SKIPPED,
    EXECUTION_SUCCESS,
    MATCH_FOUND,
    NO_MATCH_FOUND,
    STUB_TEMPLATE,
    ArmorSkipped,
    ScreenResult,
    _screen_with_stub,
    findings_from_verdict,
    screen_text,
    verdict_is_trustworthy,
)
from shared.domain import MemoryNote
from shared.gateway import (
    PolicyViolation,
    SigningKeyUnavailable,
    UnknownTool,
    admissible,
    call_tool,
    host_of,
    issue_approval_token,
    register_tool,
    verify_approval_token,
)
from shared.memory import NoteRejected, recall_dossier, remember
from shared.telemetry import RawContentInSpan, set_content_refs, span
from tests.conftest import emulator_required

PAYLOAD = "synthetic-vendors/injection-corpus/variant-01/payload.md"
CLEAN_DOC = "synthetic-vendors/cleancloud/evidence/soc2-report.md"


def ctx(review_id: str = "r", agent: str = "test"):
    return SimpleNamespace(review_id=review_id, agent=agent, trace_id="t")


def real_result(**overrides) -> ScreenResult:
    """A verdict shaped like the real service's, for testing policy rather than screening."""
    base = dict(
        clean=True,
        template="drawbridge-untrusted",
        template_version="3",
        filters={"pi_and_jailbreak": NO_MATCH_FOUND, "malicious_uris": NO_MATCH_FOUND,
                 "sdp": NO_MATCH_FOUND},
        execution={"pi_and_jailbreak": EXECUTION_SUCCESS, "malicious_uris": EXECUTION_SUCCESS,
                   "sdp": EXECUTION_SUCCESS},
    )
    base.update(overrides)
    return ScreenResult(**base)


# --- Screening -----------------------------------------------------------------------------


def test_stub_detects_the_corpus_baseline_payload():
    text = open(PAYLOAD).read()

    result = _screen_with_stub(text, "drawbridge-untrusted", "corpus/variant-01")

    assert result.threat_found()
    assert result.first_match() == "pi_and_jailbreak"
    assert result.excerpt


def test_stub_does_not_flag_a_clean_vendor_document():
    """A false positive on a clean pack would be worse than no stub at all."""
    result = _screen_with_stub(open(CLEAN_DOC).read(), "drawbridge-untrusted", "cleancloud/soc2")

    assert not result.threat_found()


def test_a_stub_verdict_is_labelled_and_never_trustworthy():
    """A stub verdict must not be mistakable for a real one in the ledger or the binder."""
    result = _screen_with_stub("anything", "drawbridge-untrusted", "x")

    assert result.template == STUB_TEMPLATE
    assert result.is_untrusted
    assert not verdict_is_trustworthy(result)
    assert set(result.skipped_filters()) == {"pi_and_jailbreak", "malicious_uris", "sdp"}


def test_a_skipped_detector_is_not_a_clean_verdict():
    """A detector that never ran is not a detector that found nothing."""
    result = real_result(execution={"pi_and_jailbreak": EXECUTION_SUCCESS,
                                    "malicious_uris": EXECUTION_SUCCESS,
                                    "sdp": EXECUTION_SKIPPED})

    assert not verdict_is_trustworthy(result)


def test_a_fully_executed_verdict_is_trustworthy():
    assert verdict_is_trustworthy(real_result())


@emulator_required
def test_local_screening_parks_the_review_rather_than_promoting(review_id, db):
    """Local mode fails closed: the pipeline shape runs and nothing is promoted."""
    with pytest.raises(ArmorSkipped):
        screen_text(open(PAYLOAD).read(), review_id, "corpus/variant-01")

    review = db.collection("reviews").document(review_id).get().to_dict()
    assert review["state"] == "needs_human"
    assert review["park_reason"] == "armor_detector_skipped"


@emulator_required
def test_a_screening_is_recorded_even_when_it_fails_closed(review_id, db):
    """The pipeline records what it saw; the consuming agent writes the finding."""
    with pytest.raises(ArmorSkipped):
        screen_text("harmless text", review_id, "reply/1")

    from google.cloud.firestore_v1 import FieldFilter

    rows = list(
        db.collection("screenings")
        .where(filter=FieldFilter("review_id", "==", review_id))
        .stream()
    )
    assert len(rows) == 1
    assert rows[0].to_dict()["template"] == STUB_TEMPLATE


@emulator_required
def test_sdp_and_uri_matches_become_rule_findings(review_id):
    result = real_result(
        filters={"pi_and_jailbreak": NO_MATCH_FOUND, "malicious_uris": MATCH_FOUND,
                 "sdp": MATCH_FOUND},
        excerpt="http://flagged.example/x",
        origin_ref="clean/doc.pdf",
    )

    findings = findings_from_verdict(review_id, result)
    domains = {f.domain for f in findings}

    assert domains == {"data_protection", "subprocessors"}
    assert all(f.source == "rule" for f in findings)
    assert all(f.severity == "medium" for f in findings)


def test_a_responsible_ai_match_produces_no_finding():
    """Logged, never blocking, never scored."""
    result = real_result(filters={"pi_and_jailbreak": NO_MATCH_FOUND,
                                  "malicious_uris": NO_MATCH_FOUND,
                                  "sdp": NO_MATCH_FOUND,
                                  "rai": MATCH_FOUND})

    assert findings_from_verdict("r", result) == []


# --- Gateway: P2 admissibility -------------------------------------------------------------


def test_clean_content_is_admissible_everywhere():
    stamp = real_result()

    assert admissible(stamp, "extract_controls")
    assert admissible(stamp, "risk_memo")


def test_sanitised_content_is_admissible_to_evidence_but_not_the_memo():
    """A sanitised document is by definition one that tried something."""
    stamp = real_result(sanitised=True, clean=False,
                        filters={"pi_and_jailbreak": MATCH_FOUND,
                                 "malicious_uris": NO_MATCH_FOUND, "sdp": NO_MATCH_FOUND})

    assert admissible(stamp, "cross_examine")
    assert not admissible(stamp, "risk_memo")


def test_an_untrustworthy_verdict_is_admissible_nowhere():
    stamp = real_result(execution={"pi_and_jailbreak": EXECUTION_SKIPPED,
                                   "malicious_uris": EXECUTION_SUCCESS,
                                   "sdp": EXECUTION_SUCCESS})

    assert not admissible(stamp, "cross_examine")
    assert not admissible(stamp, "risk_memo")


def test_a_stub_verdict_is_admissible_nowhere():
    """The property that keeps local mode from proving something it has not proved."""
    stamp = _screen_with_stub("clean text", "drawbridge-untrusted", "x")

    assert not admissible(stamp, "cross_examine")


@emulator_required
def test_p2_blocks_a_model_call_with_no_stamp(review_id):
    register_tool("cross_examine", lambda **kw: "should not run")

    with pytest.raises(PolicyViolation, match="P2"):
        call_tool("cross_examine", ctx(review_id), ref="clean/doc.pdf")


# --- Gateway: P1 and signing ---------------------------------------------------------------


@emulator_required
def test_p1_blocks_outbound_email_without_a_token(review_id):
    register_tool("send_email", lambda **kw: "should not send")

    with pytest.raises(PolicyViolation, match="P1"):
        call_tool("send_email", ctx(review_id), to="contact@vendor.example")


def test_the_gateway_cannot_mint_an_approval():
    """Verification without signing capability, at any point in this process's life."""
    with pytest.raises(SigningKeyUnavailable):
        issue_approval_token("r1", scope="decision", identity="elena")


def test_an_absent_token_never_verifies():
    assert verify_approval_token("r1", "contact@vendor.example", None) is False


# --- Gateway: P3 ---------------------------------------------------------------------------


def test_host_extraction_rejects_userinfo_disguise():
    """https://feeds.allowed.example@evil.example/ reads as allowed and resolves to evil."""
    with pytest.raises(PolicyViolation, match="P3"):
        host_of("https://feeds.allowed.example@evil.example/data")


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "gopher://x/", "https://", "not-a-url"],
)
def test_unparseable_urls_are_blocks_not_bypasses(url):
    with pytest.raises(PolicyViolation):
        host_of(url)


def test_host_extraction_lowercases():
    assert host_of("https://Feeds.Example.COM/rss") == "feeds.example.com"


@emulator_required
def test_p3_blocks_a_fetch_outside_the_allowlist(review_id):
    """Against the real registered tool rather than a stub: a stub would prove that a function
    the test wrote was not called, which is not the claim."""
    from agents.watchdog.fetch import TOOL_FETCH_URL

    with pytest.raises(PolicyViolation, match="P3"):
        call_tool(TOOL_FETCH_URL, ctx(review_id), url="https://not-a-feed.example/data")


def test_the_feed_allowlist_names_hosts_rather_than_patterns():
    """A wildcard on a hosting provider allowlists everyone who bought a subdomain there."""
    from shared.gateway import FEED_ALLOWLIST

    assert FEED_ALLOWLIST, "an empty allowlist means the Watchdog can fetch nothing"
    for host in FEED_ALLOWLIST:
        assert "*" not in host and "/" not in host
        assert host == host.lower()


@emulator_required
def test_p3_admits_a_host_on_the_allowlist(review_id):
    """The allowlist has to admit something, or P3 is indistinguishable from egress being off.

    Local mode makes no outbound request, so what is asserted here is that the policy let the
    call through to the tool — not that the network was reached."""
    from agents.watchdog.fetch import TOOL_FETCH_URL
    from shared.gateway import FEED_ALLOWLIST

    host = sorted(FEED_ALLOWLIST)[0]

    result = call_tool(TOOL_FETCH_URL, ctx(review_id), url=f"https://{host}/feed")

    assert result["url"] == f"https://{host}/feed"


@emulator_required
def test_an_unregistered_tool_is_never_dispatched(review_id):
    with pytest.raises(UnknownTool):
        call_tool("rm_rf", ctx(review_id))


# --- Telemetry -----------------------------------------------------------------------------


def test_a_span_accepts_refs_hashes_and_verdicts():
    with span("test", ctx()) as s:
        set_content_refs(
            s,
            ref="quarantine/nimbuswrite/security-overview.pdf",
            sha256="a" * 64,
            verdict="pi_and_jailbreak:MATCH_FOUND",
        )


def test_a_span_rejects_raw_vendor_text():
    """The wrong version of this call is the one a hurry would write."""
    with span("test", ctx()) as s:
        with pytest.raises(RawContentInSpan):
            set_content_refs(
                s,
                ref="We enforce MFA across the organisation with no exemptions",
                sha256="a" * 64,
                verdict="sdp:NO_MATCH",
            )


def test_a_span_rejects_a_free_text_verdict():
    with span("test", ctx()) as s:
        with pytest.raises(RawContentInSpan):
            set_content_refs(s, ref="chunk:abc", sha256="b" * 64, verdict="looks fine to me")


def test_a_span_records_an_exception_and_re_raises():
    with pytest.raises(ValueError):
        with span("test", ctx()):
            raise ValueError("boom")


# --- Memory --------------------------------------------------------------------------------


def note(**overrides) -> MemoryNote:
    base = dict(
        vendor_id="cleancloud",
        type="band",
        provenance="rule",
        value={"value": "approve", "review_id": "r1"},
        at=datetime.now(UTC),
    )
    base.update(overrides)
    return MemoryNote(**base)


@emulator_required
def test_a_structured_note_is_accepted(db):
    assert remember("cleancloud", note())


def test_an_unlisted_note_type_is_rejected():
    with pytest.raises(NoteRejected, match="not an allowed note type"):
        remember("cleancloud", note(type="freeform_summary"))


def test_an_unlisted_provenance_is_rejected_by_the_type():
    """Defence in depth: the type rejects it before the guard is reached."""
    with pytest.raises(ValidationError):
        note(provenance="vendor")


def test_the_guard_also_rejects_an_unlisted_provenance():
    """And if the type is bypassed, the guard still refuses the write."""
    bad = MemoryNote.model_construct(
        vendor_id="cleancloud",
        type="band",
        provenance="vendor",
        value={"value": "approve"},
        at=datetime.now(UTC),
    )

    with pytest.raises(NoteRejected, match="provenance"):
        remember("cleancloud", bad)


def test_a_value_outside_the_controlled_vocabulary_is_rejected():
    with pytest.raises(NoteRejected, match="controlled vocabulary"):
        remember("cleancloud", note(type="band", value={"value": "probably fine"}))


def test_prose_derived_from_vendor_content_is_rejected():
    """The realistic failure: a developer passes a vendor's sentence into a term field."""
    with pytest.raises(NoteRejected, match="prose"):
        remember(
            "cleancloud",
            note(
                type="negotiated_exception",
                value={
                    "detail": (
                        "The vendor stated that they enforce multi-factor authentication "
                        "across the whole organisation with no exemptions whatsoever"
                    )
                },
            ),
        )


def test_a_nested_structure_is_rejected():
    with pytest.raises(NoteRejected, match="nested structures"):
        remember("cleancloud", note(type="subprocessor", value={"chain": ["a", "b"]}))


def test_an_empty_note_is_rejected():
    with pytest.raises(NoteRejected, match="records nothing"):
        remember("cleancloud", note(value={}))


@emulator_required
def test_notes_supersede_rather_than_accumulate(review_id):
    vendor = f"v{review_id}"
    first = remember(vendor, note(vendor_id=vendor, type="contact_change",
                                  value={"email": "old@vendor.example"}))
    remember(vendor, note(vendor_id=vendor, type="contact_change",
                          value={"email": "new@vendor.example"}, supersedes=first))

    dossier = recall_dossier(vendor)

    assert len(dossier.notes) == 1
    assert dossier.notes[0].value["email"] == "new@vendor.example"


@emulator_required
def test_a_conduct_flag_surfaces_on_the_dossier(review_id):
    vendor = f"v{review_id}"
    remember(vendor, note(vendor_id=vendor, type="conduct_flag",
                          value={"value": "adversarial_conduct", "review_id": review_id}))

    assert recall_dossier(vendor).adversarial_flag is True


# --- Model output schemas -------------------------------------------------------------------


def test_a_finding_draft_must_use_a_rubric_domain_key():
    """Measured, not hypothetical: asked for a free string the model returned "Access Control",
    which compute_score would have raised on at the last step of every review.
    """
    from shared.domain import FindingDraft

    with pytest.raises(ValidationError):
        FindingDraft(
            domain="Access Control",
            severity="high",
            contradiction=True,
            summary="x",
        )

    assert FindingDraft(
        domain="access_control", severity="high", contradiction=True, summary="x"
    ).domain == "access_control"


def test_a_finding_draft_cannot_claim_its_own_provenance():
    """The model has no field in which to label its judgement as a rule."""
    from shared.domain import Finding, FindingDraft

    assert "source" not in FindingDraft.model_fields
    assert "source" in Finding.model_fields


def test_the_rubric_domain_type_matches_the_domain_tuple():
    """Two lists that must never drift; the schema and the rubric are read by different code."""
    import typing

    from shared.domain import RUBRIC_DOMAINS, RubricDomain

    assert set(typing.get_args(RubricDomain)) == set(RUBRIC_DOMAINS)
