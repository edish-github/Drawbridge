"""What each provenance label proves, and what it does not.

``source="rule"`` has been load-bearing for this project since the scoring split was made: it
is the sentence "the model judged severity, the code computed the score" written onto every
finding. It describes the *conclusion*. On a date comparison the conclusion is arithmetic and
the input is a date a model read off a page, and a single label covering both reads as a
stronger claim than the evidence supports — in the binder, in front of an auditor.

``date_source`` is the second half of that attribution. These tests pin which label goes where
and, more importantly, pin that a finding turning on no date carries no date label at all: an
axis printed on everything is an axis nobody reads.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.evidence.checks import DATE_SOURCES, deterministic_checks, rule_finding
from agents.evidence.extractors import DocumentFacts
from agents.evidence.subprocessors import RegisterEntry, diff
from shared.domain import Subprocessor

TODAY = date(2025, 11, 1)


def facts(**overrides) -> DocumentFacts:
    base = {
        "doc_ref": "clean/soc2-report.md",
        "name": "soc2-report.md",
        "auditor": "Marlow & Finch LLP",
        "opinion": "unqualified",
        "scope": "the Routing Platform",
    }
    return DocumentFacts(**{**base, **overrides})


def sub(**overrides) -> Subprocessor:
    base = {
        "subprocessor_id": "v:x",
        "vendor_id": "v",
        "name": "Example Ltd",
        "purpose": "Hosting",
        "processes_customer_data": True,
    }
    return Subprocessor(**{**base, **overrides})


def only(findings, needle: str):
    matched = [f for f in findings if needle in f.summary]
    assert len(matched) == 1, f"expected one finding mentioning {needle!r}, got {len(matched)}"
    return matched[0]


# --- A date a model read off a page ---------------------------------------------------------


def test_an_expired_certificate_says_the_date_was_extracted():
    """The conclusion is arithmetic. The date it compared is not."""
    findings = deterministic_checks(
        "rv1", [facts(name="iso-27001.md", cert_expiry=date(2025, 3, 14))], today=TODAY
    )
    finding = only(findings, "expired on")

    assert finding.source == "rule"
    assert finding.date_source == "extracted"


def test_a_stale_report_period_says_the_date_was_extracted():
    findings = deterministic_checks(
        "rv1", [facts(report_period_end=date(2024, 6, 30))], today=TODAY
    )
    assert only(findings, "currency threshold").date_source == "extracted"


def test_a_certificate_with_no_readable_expiry_still_attributes_the_extractor():
    """The finding is about what extraction returned, so extraction is what is attributed."""
    findings = deterministic_checks("rv1", [facts(name="iso-27001.md")], today=TODAY)
    assert only(findings, "No expiry date").date_source == "extracted"


# --- A date this system worked out, and a date somebody typed -------------------------------


def test_a_lapsed_register_review_says_the_lapse_was_computed():
    """Nobody marked Wayfarer expired. The code compared two dates and concluded it.

    This is the distinction the label exists for: an auditor asking whether a status was
    reached or recorded is asking a question the register alone cannot answer, because
    ``status_as_of`` derives it on every read.
    """
    register = {
        "example-ltd": RegisterEntry(
            name="Example Ltd", review_valid_until=date(2025, 9, 30)
        )
    }
    chain = [
        sub(
            known_to_org=True,
            register_status="expired",
            review_valid_until=date(2025, 9, 30),
        )
    ]

    finding = only(diff("rv1", "v", chain, register), "Example Ltd")
    assert finding.source == "rule"
    assert finding.date_source == "computed"


def test_a_register_row_marked_incomplete_by_hand_says_declared():
    """No date was compared. Somebody set a flag, and a flag is only as current as they were."""
    register = {"example-ltd": RegisterEntry(name="Example Ltd", review_status="incomplete")}
    chain = [sub(known_to_org=True, register_status="incomplete")]

    assert only(diff("rv1", "v", chain, register), "Example Ltd").date_source == "declared"


# --- What carries no date label at all ------------------------------------------------------


def test_an_unknown_fourth_party_turns_on_no_date_and_says_nothing_about_one():
    """A set difference. Printing "date: n/a" here would bury the four places it matters."""
    chain = [sub(name="Unheard Of Ltd", known_to_org=False, register_status="unknown")]

    finding = only(diff("rv1", "v", chain, {"other": RegisterEntry(name="Other")}), "Unheard Of")
    assert finding.source == "rule"
    assert finding.date_source is None


def test_a_missing_auditor_carries_no_date_label():
    findings = deterministic_checks(
        "rv1", [facts(auditor=None, report_period_end=TODAY)], today=TODAY
    )
    assert only(findings, "names no auditor").date_source is None


# --- The vocabulary is closed ---------------------------------------------------------------


def test_an_invented_date_source_raises_rather_than_being_written():
    """Same discipline as the domain and severity checks above it in the same function."""
    with pytest.raises(ValueError, match="not a date source"):
        rule_finding("rv1", "compliance_posture", "low", "x", date_source="guessed")


def test_the_three_values_are_the_three_the_binder_explains():
    assert DATE_SOURCES == ("extracted", "computed", "declared")


def test_the_binder_prints_the_label_beside_the_provenance_label():
    """Section 4's whole job is letting a reader re-do the reasoning. Both halves or neither."""
    from services.binder.render import _date_source_pill

    assert "extracted" in _date_source_pill({"date_source": "extracted"})
    assert _date_source_pill({"source": "rule"}) == ""
