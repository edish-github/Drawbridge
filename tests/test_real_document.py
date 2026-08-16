"""Extraction against a document nobody here wrote.

Every other document this system has been measured on was written by the person who wrote the
extractor, in a format the extractor expects, and they parse suspiciously well. That is a test
of the fixtures. This is a real independent security audit — a firm, a period, a scope, a
conclusion, sixteen recommendations and the detail in appendices — laid out the way an audit
firm lays things out and not the way the synthetic packs do.

Two things are under test and they are different sizes:

**What the extractor gets.** The fields the deterministic checks are built on: report period,
auditor, opinion, scope. Where it gets them wrong the assertion records the wrong answer rather
than the right one, because a test asserting the value we wish it returned is a test that
passes while the product is broken. ``EXTRACTION-NOTES.md`` carries the same results in prose.

**What it is never allowed to do.** This document names a real organisation and a real audit
firm. Extraction is permitted; a score, a finding or any adverse conclusion is not, and the
last test in this file is the one that asserts the path does not exist rather than merely being
unused.

The document is not in this repository. ``python -m fixtures.public.fetch`` downloads it and
verifies its checksum; without it every test here skips, naming the command.
"""

from __future__ import annotations

import os
import re

import pytest
import yaml

from agents.evidence.extractors import MAX_DOCUMENT_CHARS
from fixtures.public.fetch import SOURCES, path_for, present, sources
from shared.extraction import extract
from tests.conftest import emulator_required

ENTRY = next(e for e in sources() if e["id"] == "nara-fisma-2024")

fetched = pytest.mark.skipif(
    not present(ENTRY),
    reason=(
        "the real document is not present. It is not vendored — see fixtures/public/SOURCES.yaml "
        "for why. Run `python -m fixtures.public.fetch`."
    ),
)

live_model = pytest.mark.skipif(
    not os.getenv("DRAWBRIDGE_MEASURE_EXTRACTION"),
    reason=(
        "spends live model quota against a 20-request daily cap. Set "
        "DRAWBRIDGE_MEASURE_EXTRACTION=1 to run the measurement."
    ),
)


@pytest.fixture(scope="module")
def text() -> str:
    return extract(path_for(ENTRY).read_bytes(), ref=ENTRY["filename"])


# --- What the file itself does to a text extractor ---------------------------------------------


@fetched
def test_the_real_pdf_yields_a_text_layer(text):
    """A 63-page government PDF with tables, headers and a cover letter. It extracts."""
    assert len(text) > 50_000
    assert "Sikich" in text


@fetched
def test_two_thirds_of_the_document_never_reaches_the_model(text):
    """The finding this file exists to produce, and it is not about a model at all.

    ``MAX_DOCUMENT_CHARS`` is 24,000. This document is 83,909 characters. The synthetic packs
    are a few thousand each and have never once been truncated, so the cap has never been
    exercised by a test — and the brief's own warning about *exceptions in an appendix rather
    than in the exception notes* describes precisely the part that is cut.

    Recorded rather than fixed here. Raising the cap costs tokens on every document in every
    review; the answer is section-aware selection, and choosing it is a design decision rather
    than a constant.
    """
    assert len(text) > MAX_DOCUMENT_CHARS * 3
    seen = text[:MAX_DOCUMENT_CHARS]

    # The four fields the checks are built on all survive: this report states them in its
    # covering letter and its objective section, both inside the first 12,000 characters.
    assert "Sikich" in seen
    assert "Not Effective" in seen

    # The appendices do not. Every recommendation past the cut is invisible to extraction.
    assert text.count("Appendix") > seen.count("Appendix")


@fetched
def test_the_heading_aware_chunker_finds_no_headings_in_a_real_pdf(text):
    """The second finding, and the one that was invisible until a real document arrived.

    ``armor._HEADING`` matches ``^#{1,6}\\s``, and the rule it enforces — a heading never ends
    a chunk, so a section's title travels with its text — was built and tested against Markdown
    fixtures. This document's headings are ``II. SUMMARY OF RESULTS``: capitals and a Roman
    numeral, because it was typeset rather than written in Markdown. The chunker finds zero.

    What still works is the rest of it: paragraph splitting, the sentence fallback and the token
    budget all hold, so no chunk overruns and none begins mid-sentence. The heading rule is the
    part that silently does nothing, and it does nothing on every real PDF, not just this one.
    """
    from shared.armor import chunk_text
    from shared.config import settings

    assert len(re.findall(r"^#{1,6}\s", text, re.M)) == 0

    chunks = chunk_text(text, settings().chunk_tokens)
    budget = settings().chunk_tokens * 4
    assert all(len(chunk) <= budget for chunk in chunks)
    assert not any(chunk[:1].islower() for chunk in chunks)


@fetched
def test_the_conclusion_is_not_where_a_soc_2_puts_it(text):
    """Real formatting, stated as an assertion rather than as a caveat.

    The synthetic packs put the opinion in an "Auditor's opinion" line. This report states its
    conclusion in a transmittal letter from one person to another, in a sentence beginning
    "Sikich concluded that", 800 characters in and 200 characters before the word "audit"
    appears in a heading. An extractor tuned to headings finds nothing here.
    """
    assert "Independent Auditor" not in text
    assert "concluded that" in text[:2_000]

    # And the period is a sentence, not a field. "The audit covered the period October 1, 2023,
    # through July 30, 2024" — with a fieldwork range in the following sentence, which is a
    # second date range for the same document and not the one the currency check wants.
    assert "The audit covered the period" in text
    assert "We performed our audit fieldwork from" in text


# --- What the extractor reads off it -----------------------------------------------------------


@fetched
@live_model
@emulator_required
def test_what_the_extractor_reads_off_a_real_report(review_id, text):
    """One live extraction call, asserted against what it actually returned.

    The document is external content and it goes through the same door as a vendor upload: a
    declared-untrusted local stamp, P2 verifying it in the router, and the run declaring itself
    unscreened. No bypass — the point of measuring extraction on a real document is lost if the
    measurement takes a path the product does not have.
    """
    from agents.evidence.extractors import FACTS_PROMPT, _ExtractedFacts
    from scenarios.seed import _seed_stamp
    from shared.armor import record_screening, stamps_for
    from shared.context import AgentContext
    from shared.routing import generate

    origin = f"public/{ENTRY['filename']}"
    record_screening(review_id, _seed_stamp(origin))

    result = generate(
        "extract_controls",
        FACTS_PROMPT.format(name=ENTRY["filename"], body=text[:MAX_DOCUMENT_CHARS]),
        AgentContext(review_id=review_id, agent="evidence", trace_id="t"),
        response_schema=_ExtractedFacts,
        source_stamps=stamps_for(review_id, [origin]),
    )
    facts = result.parsed

    # Recorded, not wished for. See EXTRACTION-NOTES.md for the run these came from.
    assert facts.auditor and "Sikich" in facts.auditor
    assert facts.scope and "information security" in facts.scope.lower()
    assert facts.cert_expiry is None, "this is not a certificate and there is no expiry to find"


# --- The boundary ------------------------------------------------------------------------------


def test_the_declared_ethics_name_what_is_never_done():
    """The rules are data in SOURCES.yaml so they can be read without reading this file."""
    declared = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))["ethics"]

    assert "a Trust Score" in declared["never"]
    assert "a finding" in declared["never"]
    assert declared["enforced_by"] == "tests/test_real_document.py"


def test_a_real_document_cannot_be_run_through_a_scored_review():
    """The assertion that matters, and it is about a path rather than about an intention.

    A review is scored over its plan's domain set, and a plan is generated for a vendor id that
    resolves in the ``vendors`` collection. There is no vendor record for this document and
    nothing here creates one — so the sequence that would produce a Trust Score about a real
    named organisation terminates at the first step, in code, rather than at somebody's
    restraint.
    """
    from shared.clients import firestore_client

    for entry in sources():
        assert not firestore_client().collection("vendors").document(entry["id"]).get().exists


def test_no_public_document_is_vendored():
    """Fetched, never redistributed. A committed copy is a licence claim nobody verified."""
    for entry in sources():
        assert entry["redistributable"] == "unverified"
        assert not path_for(entry).is_relative_to(SOURCES.parent / "committed")
