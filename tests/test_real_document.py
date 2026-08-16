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

import json
import os
import re

import pytest
import yaml

from agents.evidence.extractors import MAX_DOCUMENT_CHARS
from fixtures.public.fetch import SOURCES, path_for, present, sources
from shared.extraction import extract
from tests.conftest import emulator_required

ENTRY = next(e for e in sources() if e["id"] == "nara-fisma-2024")
MEASURED = SOURCES.parent / "measured.json"

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
        "spends live model quota against a 20-request daily cap. Run it with "
        "DRAWBRIDGE_MEASURE_EXTRACTION=1 and a real GEMINI_API_KEY exported — conftest sets a "
        "placeholder key for the suite, and setdefault means an exported one wins."
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
def test_the_document_is_far_larger_than_a_prefix_could_carry(text):
    """29% of this document reached the model, and this is what was on either side of the line.

    ``MAX_DOCUMENT_CHARS`` is 24,000; the document is 83,909 characters. The synthetic packs are
    a few thousand each and were never truncated, which is why no test here had ever exercised
    the cap.

    **The honest result, which is narrower than it first looked.** This report front-loads: a
    table of contents listing every appendix, then an executive summary carrying the objective,
    the auditor and both date ranges, all inside the first 12,000 characters. So every *field*
    extraction asks for was reachable by a prefix. What a prefix could not reach was the
    substance — the methodology, the scoring basis, the status of prior recommendations — which
    extraction does not currently ask for and cross-examination would.

    The case for retrieving is therefore not "it rescues a field on this document". It is that
    the prefix worked here by a property of this document's layout, and nothing checks that
    property before relying on it.
    """
    assert len(text) > MAX_DOCUMENT_CHARS * 3
    seen = text[:MAX_DOCUMENT_CHARS]

    # Reachable by a prefix, because this document puts them in its front matter.
    assert "Sikich CPA LLC" in seen
    assert "Not Effective" in seen
    assert "The audit covered the period October 1, 2023" in seen

    # Not reachable. The appendix titles are in the table of contents; their contents are not.
    assert "vulnerability assessment and penetration test" not in seen
    assert "calculated average scores of the core IG FISMA" not in seen
    assert text.count("Appendix") > seen.count("Appendix")


@fetched
def test_the_recorded_extraction_reads_the_document_rather_than_quoting_it(text):
    """What the live run returned, pinned so a later change to the queries fails loudly.

    Two of these came from the prompt rather than from retrieval, and saying which is which is
    the point of recording them at all. ``opinion`` is the performance-audit conclusion in the
    document's own words, which the previous prompt could not have produced because it named
    only SOC 2's three opinions by example. ``report_period_end`` is the audit period and not
    the fieldwork range that follows it in the very next sentence.

    ``scope`` is the one that is not a quotation. The prompt asks for the document's own words
    and the model returned a paraphrase — "OMB and DHS issued for FY 2024" appears nowhere in
    the document. Recorded as it is rather than asserted as we would like it, because a
    paraphrased scope is what ``checks.covers`` performs word-overlap against.
    """
    recorded = json.loads(MEASURED.read_text())
    facts = recorded["facts"]

    assert recorded["method"] == "retrieval"
    assert facts["auditor"] == "Sikich CPA LLC"
    assert facts["opinion"] == "Not Effective"
    assert facts["report_period_end"] == "2024-07-30"
    assert "through July 30, 2024" in text

    # Not a certificate, and no date was invented for one. The expiry query still returned its
    # four nearest passages — the nearest being the acronym glossary — and nothing was read out
    # of them.
    assert facts["cert_expiry"] is None

    # A paraphrase, recorded as such.
    assert "OMB and DHS issued for FY 2024" in facts["scope"]
    assert "OMB and DHS issued for FY 2024" not in text


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
    """One live measurement, through the real path, refreshing ``measured.json``.

    The document is external content and it goes through the same door as a vendor upload: it is
    chunked and indexed the way any evidence is, given a declared-untrusted local stamp, and P2
    verifies that stamp in the router. No bypass — the point of measuring extraction on a real
    document is lost if the measurement takes a path the product does not have.

    Costs one extraction call plus one embedding per chunk and per query. On a free tier capped
    at twenty generate requests a day, that is one of the twenty.
    """
    import json as _json

    from agents.evidence.extractors import extract_document_facts
    from scenarios.seed import _seed_stamp
    from shared import storage
    from shared.armor import index_chunks, record_screening
    from shared.config import settings
    from shared.context import AgentContext

    ref = storage.ref_for(settings().bucket_clean, f"{review_id}/{ENTRY['filename']}.txt")
    storage.write_object(ref, text.encode("utf-8"), content_type="text/plain")
    record_screening(review_id, _seed_stamp(ref))
    indexed = index_chunks(ref, review_id)

    facts = extract_document_facts(
        ref, review_id, AgentContext(review_id=review_id, agent="evidence", trace_id="t")
    )

    # Written before the assertions, so a call that was spent is a call that was recorded. The
    # first live run of this test passed and its field values were lost to the next traceback,
    # which on a twenty-a-day cap is an expensive way to learn to write the file first.
    recorded = _json.loads(MEASURED.read_text())
    recorded |= {
        "document": ENTRY["id"],
        "method": "retrieval",
        "chunks_indexed": indexed,
        "facts": facts.model_dump(mode="json", exclude={"doc_ref", "name"}),
    }
    MEASURED.write_text(_json.dumps(recorded, indent=2) + "\n", encoding="utf-8")

    # Recorded, not wished for. See EXTRACTION-NOTES.md for the run these came from.
    assert facts.auditor and "Sikich" in facts.auditor
    assert facts.scope and "information security" in facts.scope.lower()
    assert facts.cert_expiry is None, "this is not a certificate and there is no expiry to find"
    assert facts.report_period_end is not None, "the audit period is stated and reachable"


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
