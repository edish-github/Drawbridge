"""The fourth-party chain: your vendor's vendors, and the register they are diffed against.

This is the only place an agent queries an internal system rather than reading a vendor's
document, and the split it makes is the one the whole project argues for: the model reads the
table, arithmetic decides what the table means. Every finding here carries ``source="rule"``
and every one is reproducible without a model.

Most of these tests are about restraint. Four finding types could each fire on four
subprocessors and produce sixteen findings about one vendor, which is how a fourth-party
feature becomes the tab nobody opens. The gates — customer data only, stated absences only,
declared residency only, one finding per control rather than per company — are what these
assert.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agents.evidence.subprocessors import (
    STATUS_CURRENT,
    STATUS_EXPIRED,
    ExtractedSubprocessor,
    RegisterEntry,
    _ExtractedChain,
    approved_vendor_register,
    chain_view,
    diff,
    resolve,
)
from shared.context import AgentContext
from shared.domain import Subprocessor
from tests.conftest import emulator_required

TODAY = date(2026, 8, 16)
REPO_PACK = Path(__file__).resolve().parent.parent / "synthetic-vendors"

REGISTER = {
    "aurelius-cloud-services": RegisterEntry(
        name="Aurelius Cloud Services",
        last_review_id="rv-2024-0117",
        review_valid_until=date(2027, 3, 31),
    ),
    "wayfarer-geocoding": RegisterEntry(
        name="Wayfarer Geocoding",
        last_review_id="rv-2023-0442",
        review_valid_until=date(2025, 9, 30),
    ),
}


def extracted(**overrides) -> ExtractedSubprocessor:
    return ExtractedSubprocessor(
        **{
            "name": "Veritas Lumen Models",
            "purpose": "Foundation model inference",
            "processes_customer_data": True,
            "jurisdiction": "US (Virginia)",
            "dpa_claimed": None,
            **overrides,
        }
    )


def sub(**overrides) -> Subprocessor:
    return Subprocessor(
        **{
            "subprocessor_id": "v:s",
            "vendor_id": "v",
            "name": "Veritas Lumen Models",
            "purpose": "Foundation model inference",
            "processes_customer_data": True,
            "jurisdiction": "US (Virginia)",
            "known_to_org": False,
            "register_status": "unknown",
            **overrides,
        }
    )


def domains(findings) -> set[str]:
    return {f.domain for f in findings}


# --- Resolution against the register ----------------------------------------------------------


def test_a_company_nobody_reviewed_resolves_as_unknown():
    resolved = resolve("nimbuswrite", extracted(), REGISTER, TODAY)

    assert resolved.known_to_org is False
    assert resolved.register_status == "unknown"
    assert resolved.prior_review_id is None


def test_casing_does_not_make_a_reviewed_company_unknown():
    """A register that matched on exact casing would report an already-reviewed company as an
    unknown fourth party, which is a false positive with a real cost."""
    resolved = resolve("v", extracted(name="aurelius  cloud services"), REGISTER, TODAY)

    assert resolved.known_to_org is True
    assert resolved.prior_review_id == "rv-2024-0117"


def test_a_lapsed_review_is_computed_from_the_date_not_a_stored_flag():
    """A stored flag is only as current as the last time somebody ran a job over the register."""
    entry = REGISTER["wayfarer-geocoding"]

    assert entry.status_as_of(date(2025, 1, 1)) == STATUS_CURRENT
    assert entry.status_as_of(TODAY) == STATUS_EXPIRED


# --- The headline finding ---------------------------------------------------------------------


def test_an_unreviewed_company_processing_customer_data_is_a_finding():
    """The sentence that is uncomfortable for anyone in procurement: it receives your customer
    text, and you have never reviewed it or signed anything with it."""
    findings = diff("r", "nimbuswrite", [sub()], REGISTER)

    assert len(findings) == 1
    assert findings[0].domain == "subprocessors"
    assert findings[0].severity == "medium"
    assert findings[0].source == "rule"
    assert "Veritas Lumen Models" in findings[0].summary


def test_an_unreviewed_company_that_touches_no_customer_data_raises_nothing():
    """A telemetry provider receiving aggregate counters is not a fourth-party risk, and raising
    it at the same weight as one receiving customer text is how this becomes noise."""
    telemetry = sub(name="Pathview Telemetry", processes_customer_data=False)

    assert diff("r", "v", [telemetry], REGISTER) == []


def test_a_subprocessor_the_document_is_silent_about_raises_nothing():
    """Silence is not a yes. A list that does not say whether customer data is processed cannot
    be turned into a finding about the company it names."""
    silent = sub(name="Unstated Ltd", processes_customer_data=None)

    assert diff("r", "v", [silent], REGISTER) == []


def test_no_register_means_no_register_findings():
    """Naming an unknown fourth party the organisation has in fact already reviewed is the
    expensive false positive. With no register to check against, nothing is claimed."""
    assert diff("r", "v", [sub()], {}) == []


# --- The lapsed review ------------------------------------------------------------------------


def test_a_lapsed_review_is_lower_severity_than_never_having_reviewed():
    """Somebody did the work once and the paperwork went stale. That is not the same finding as
    a company nobody ever looked at."""
    lapsed = resolve(
        "datadynamo",
        extracted(name="Wayfarer Geocoding", jurisdiction="EU (Paris)"),
        REGISTER,
        TODAY,
    )

    findings = diff("r", "datadynamo", [lapsed], REGISTER)

    assert len(findings) == 1
    assert findings[0].severity == "low"
    assert "expired" in findings[0].summary
    assert "2025-09-30" in findings[0].summary


def test_a_current_review_raises_nothing():
    current = resolve("v", extracted(name="Aurelius Cloud Services"), REGISTER, TODAY)

    assert diff("r", "v", [current], REGISTER) == []


# --- The agreement gap ------------------------------------------------------------------------


def test_a_stated_absence_of_an_agreement_is_a_finding():
    stated = sub(known_to_org=True, register_status="current", dpa_claimed=False)

    findings = diff("r", "v", [stated], REGISTER)

    assert len(findings) == 1
    assert findings[0].severity == "low"
    assert "no data processing agreement" in findings[0].summary


def test_a_silent_list_is_not_a_stated_absence():
    """A subprocessor list that does not mention agreements is following a common convention.
    A finding raised on that would be a finding about document formatting."""
    silent = sub(known_to_org=True, register_status="current", dpa_claimed=None)

    assert diff("r", "v", [silent], REGISTER) == []


def test_the_agreement_gap_is_one_finding_however_many_companies_it_covers():
    """The control that failed is the vendor's flow-down of its obligations, and it fails once."""
    chain = [
        sub(subprocessor_id="a", name="A Ltd", known_to_org=True,
            register_status="current", dpa_claimed=False),
        sub(subprocessor_id="b", name="B Ltd", known_to_org=True,
            register_status="current", dpa_claimed=False),
    ]

    findings = diff("r", "v", chain, REGISTER)

    assert len(findings) == 1
    assert "A Ltd" in findings[0].summary and "B Ltd" in findings[0].summary


# --- Data residency ---------------------------------------------------------------------------


@emulator_required
def test_residency_is_only_checked_when_the_intake_declared_one(db, monkeypatch):
    """Inventing a requirement would make every vendor fail a control nobody bought."""
    from agents.evidence import subprocessors

    monkeypatch.setattr(subprocessors, "declared_residency", lambda vendor_id: [])

    assert diff("r", "v", [sub(known_to_org=True, register_status="current")], REGISTER) == []


@emulator_required
def test_customer_data_outside_the_declared_region_is_a_finding(db, monkeypatch):
    from agents.evidence import subprocessors

    monkeypatch.setattr(subprocessors, "declared_residency", lambda vendor_id: ["EU", "EEA"])

    findings = diff("r", "v", [sub(known_to_org=True, register_status="current")], REGISTER)

    assert len(findings) == 1
    assert findings[0].severity == "medium"
    assert "US (Virginia)" in findings[0].summary


@emulator_required
def test_a_region_inside_the_requirement_raises_nothing(db, monkeypatch):
    """"EU (Frankfurt)" is inside "EU". A check that needed every city enumerated would be a
    check that went out of date."""
    from agents.evidence import subprocessors

    monkeypatch.setattr(subprocessors, "declared_residency", lambda vendor_id: ["EU", "EEA"])
    inside = sub(known_to_org=True, register_status="current", jurisdiction="EU (Frankfurt)")

    assert diff("r", "v", [inside], REGISTER) == []


@emulator_required
def test_residency_is_one_finding_across_the_whole_chain(db, monkeypatch):
    from agents.evidence import subprocessors

    monkeypatch.setattr(subprocessors, "declared_residency", lambda vendor_id: ["EU"])
    chain = [
        sub(subprocessor_id="a", name="Stratos Compute", known_to_org=True,
            register_status="current", jurisdiction="US (Oregon)"),
        sub(subprocessor_id="b", name="Veritas Lumen Models", known_to_org=True,
            register_status="current", jurisdiction="US (Virginia)"),
    ]

    findings = diff("r", "v", chain, REGISTER)

    assert len(findings) == 1
    assert "Stratos Compute" in findings[0].summary
    assert "Veritas Lumen Models" in findings[0].summary


# --- End to end over the real pack ------------------------------------------------------------


@emulator_required
def test_the_nimbuswrite_chain_names_its_model_provider(review_id, db):
    """The showcase, run over the real document with the real register. NimbusWrite cannot
    complete a local review — its evidence must go through real screening — so the chain is
    extracted and diffed directly here rather than through a run."""
    from scenarios.fixtures import responding_from
    from scenarios.seed import APPROVED_VENDOR_REGISTER, _seed_stamp, seed_register
    from shared.armor import record_screening, stamps_for

    seed_register()
    origin = "nimbuswrite/subprocessor-list.md"
    body = (REPO_PACK / "nimbuswrite" / "evidence" / "subprocessor-list.md").read_text()

    # Stamped as a seeded fixture, exactly as the demo path stamps evidence: local mode has no
    # detector, so the honest stamp says local-seed and P2 refuses it unless the run declares
    # itself unscreened. A test that passed no stamp at all would be testing a code path the
    # product does not have.
    record_screening(review_id, _seed_stamp(origin))

    with responding_from("nimbuswrite") as responder:
        chain = responder(
            "extract_controls",
            f"DOCUMENT: subprocessor-list.md\n{body}",
            _ctx(review_id),
            response_schema=_ExtractedChain,
            source_stamps=stamps_for(review_id, [origin]),
        )

    register = _register()
    resolved = [resolve("nimbuswrite", e, register, TODAY) for e in chain.parsed.subprocessors]
    findings = diff(review_id, "nimbuswrite", resolved, register)
    summaries = " ".join(f.summary for f in findings)

    assert {s.name for s in resolved} == {
        "Stratos Compute",
        "Veritas Lumen Models",
        "Cadence Mail",
        "Pathview Telemetry",
    }
    assert "Veritas Lumen Models" in summaries
    assert "never been reviewed" in summaries
    assert "no data processing agreement" in summaries
    assert all(f.source == "rule" for f in findings)

    # The absences are the fixture. A register that quietly grew one of these names would turn
    # the headline finding into no finding at all, and every assertion above would still pass.
    listed = {entry["name"] for entry in APPROVED_VENDOR_REGISTER}
    assert "Veritas Lumen Models" not in listed
    assert "Sendline Notifications" not in listed


@emulator_required
def test_the_structural_view_marks_the_unknown_nodes(review_id, db):
    from agents.evidence.subprocessors import save_subprocessor

    known = sub(
        subprocessor_id="viewv:a",
        vendor_id="viewv",
        name="Known Ltd",
        known_to_org=True,
        register_status="current",
    )
    for entry in (known, sub(subprocessor_id="viewv:b", vendor_id="viewv", name="Unknown Ltd")):
        save_subprocessor("viewv", entry)
    db.collection("vendors").document("viewv").set(
        {
            "vendor_id": "viewv",
            "name": "View Vendor",
            "category": "Analytics",
            "intake": {"data_residency_required": ["EU"]},
        }
    )

    view = chain_view("viewv")

    assert view["vendor"]["name"] == "View Vendor"
    assert view["vendor"]["residency_required"] == ["EU"]
    assert [n["known_to_org"] for n in view["subprocessors"]] == [True, False]


def test_the_binder_builds_the_chain_without_importing_the_router():
    """The binder promises it makes no model call, asserted by import graph. Building the same
    view from the agent module would import shared.routing transitively and break the promise."""
    import ast
    import inspect

    from services.binder import collect

    tree = ast.parse(inspect.getsource(collect))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert not any(name.startswith("agents.") for name in imported), sorted(imported)


# --- fixtures ---------------------------------------------------------------------------------


def _ctx(review_id: str) -> AgentContext:
    return AgentContext(review_id=review_id, agent="evidence", trace_id="t")


def _register():
    return approved_vendor_register()


@pytest.fixture(autouse=True)
def _no_residency_by_default(monkeypatch):
    """Most tests here are about the register, not about residency.

    Stubbed rather than left to read Firestore, so a pure-logic test stays pure and does not
    quietly need an emulator to assert something about a set difference.
    """
    from agents.evidence import subprocessors

    monkeypatch.setattr(subprocessors, "declared_residency", lambda vendor_id: [], raising=True)
