# What a real document did to the extractor

Every document this system had been measured on until now was written by the person who wrote
the extractor. They parse well, and that is a fact about the fixtures rather than about the
product. This is the record of running the same pipeline over an independent security audit
nobody here wrote: **NARA's FY2024 FISMA audit, conducted by Sikich, published by the NARA
Office of Inspector General on 27 September 2024** — 63 pages, 83,909 characters of extracted
text.

The document is not in this repository and the reason is in `SOURCES.yaml`. Run
`python -m fixtures.public.fetch` to reproduce any of this; `tests/test_real_document.py`
asserts every number below.

**Two rounds.** The first measured the pipeline as it was — a 24,000-character prefix — and
produced the three findings below. The second ran after extraction was changed to retrieve per
fact, and is recorded in `measured.json`. Everything here is reproducible with
`python -m fixtures.public.fetch` and `DRAWBRIDGE_MEASURE_EXTRACTION=1 pytest
tests/test_real_document.py`.

---

## 1 · Seventy-one per cent of the document never reached the model — and the fields survived anyway

`MAX_DOCUMENT_CHARS` was 24,000. This document is 83,909 characters.

| | in the window | past the cut |
|---|---|---|
| "Appendix" | 12 | 13 |
| "recommendation" | 16 | 71 |

**The narrower truth, which took a second look to see.** Every *field* extraction asks for was
inside the window. This report front-loads: a table of contents listing every appendix, then an
executive summary carrying the objective, the auditor and both date ranges, all within the first
12,000 characters. A prefix got all five fields right on this document.

What a prefix could not reach was the substance. The methodology (character 31,005), the FISMA
scoring basis the conclusion rests on (69,420), and the status of sixteen prior recommendations
were all past the cut — none of which the fact extractor asks for, and all of which
cross-examination would.

So the case for changing it is not *"truncation lost a field here"*. It is that **the prefix
worked by a property of this document's layout, and nothing checked that property before relying
on it.** A report that puts its scope only in Appendix B — and plenty do — would have failed
silently, returning a confident answer drawn from whatever was in the first 24,000 characters.
That is the failure mode a prefix has and a measurement of one document cannot rule out.

### What retrieval changed, measured

Extraction now runs one query per fact against the document's own chunks.

| | prefix | retrieval |
|---|---|---|
| document seen | first 24,000 chars | all 63 chunks searchable |
| passages in the prompt | one contiguous block | 13 distinct, deduplicated, in document order |
| prompt characters | 24,000 | 18,125 |
| prompt tokens | ~6,000 | **4,593** |
| cost | — | $0.0046 |

**Six of the thirteen retrieved passages sit past the old cut** — chunks 023, 047, 048, 051, 052
and 060 — and four of those six carry appendix or recommendation text. It is smaller, cheaper,
and it reads the whole document instead of the beginning of it.

### What the live run returned

```json
{
  "auditor":           "Sikich CPA LLC",
  "opinion":           "Not Effective",
  "scope":             "NARA's information security program and practices consistent with
                        FISMA and reporting instructions that OMB and DHS issued for FY 2024.",
  "cert_expiry":       null,
  "report_period_end": "2024-07-30"
}
```

Three of these are worth reading twice.

**`opinion` is the conclusion, in the document's own words.** A FISMA audit issues no opinion at
all. The old prompt named unqualified, qualified and adverse by example, which is SOC 2's
vocabulary and had nothing to match here; it now asks for whichever the document states.

**`cert_expiry` is null, and that is the harder result.** The expiry query still returned four
passages — the nearest being the acronym glossary, because nothing in a FISMA audit is a
certificate — and the model reported no date rather than reading one out of them. That is the
behaviour the prompt was hardened for: *a date that is not in front of you is worse than no
date*, because what comes back is compared arithmetically by code that cannot tell a guess from
a reading.

**`scope` is a paraphrase, not a quotation.** "OMB and DHS issued for FY 2024" appears nowhere in
the document. The prompt asks for the document's own words and did not get them. It is recorded
as it is rather than asserted as it should be, because `checks.covers` does word-overlap against
this string to decide whether a report covers the service being bought — so a paraphrase is a
real input to a real check, and this is the next thing to tighten.

## 2 · The heading-aware chunker found no headings — fixed

`armor._HEADING` matched `^#{1,6}\s`. This document contains **zero** such matches.

Its headings are `II. SUMMARY OF RESULTS` — capitals and a Roman numeral, because it was
typeset rather than written in Markdown. The rule it fed, that a heading never ends a chunk so a
section title travels with its text, was a rule about Markdown fixtures. On every real PDF it
was a no-op, and it was a no-op that never failed, which is why it survived a milestone.

**Extended.** The pattern now also matches Roman-numeral sections, appendix and annex titles,
and short all-caps lines: **4 matches in 256 paragraphs on this document, all four genuine
headings**, and no change to any Markdown fixture in the pack.

**One pattern was tried and dropped.** Numbered sections — `3.1`, `7 `, the obvious way to catch
`3.1 Access control` — matched **33 paragraphs here and not one was a heading**. Every match was
a numbered footnote. It is not repairable by tightening the number format: a footnote marker and
a section number are the same string in the same position, and separating them needs page
geometry that text extraction has already discarded. Dropped, and `test_the_dropped_pattern_
stays_dropped` asserts it stays dropped.

Tuned for precision rather than recall, because the rule only ever *moves* a paragraph forward:
a false positive tears a real paragraph off its chunk, a false negative changes nothing.

What already held, and still does: 63 chunks, none over the 1,600-character budget, and none
beginning mid-sentence.

## 3 · The conclusion is not where an assurance report puts it

The string `Independent Auditor` does not appear in this document. There is no unqualified,
qualified or adverse opinion — a FISMA audit is a performance audit and it issues a conclusion,
not an opinion. What it says is:

> Sikich concluded that NARA's information security program was "Not Effective."

800 characters in, in a transmittal letter from the Inspector General to the Archivist, and
200 characters *before* the word "audit" appears in any heading. An extractor keyed on
headings, or on the SOC 2 vocabulary the prompt names by example — "for example: unqualified,
qualified, adverse" — has nothing to key on.

The period is prose rather than a field, and there are two of them:

> The audit covered the period October 1, 2023, through July 30, 2024. We performed our audit
> fieldwork from November 2023 to July 2024.

A currency check wants the first. **The retrieval run returned `2024-07-30` — the audit period,
not the fieldwork range.** The prompt now names the ambiguity explicitly rather than leaving the
model to pick, which it had no basis to do.

---

## What this changes

Findings 1 and 3 are fixed. Extraction retrieves per fact and the prompt asks for a conclusion
rather than for SOC 2's three opinions. What is left, in descending order of what it costs:

1. **The paraphrased scope.** `checks.covers` does word-overlap against this string to decide
   whether a report covers the service being bought, so a paraphrase is a real input to a real
   check. The prompt asks for the document's own words and did not get them.
2. **The heading rule** — finding 2 below, unresolved at the time these notes were first
   written and addressed in the milestone that followed. See `docs/architecture.md`.
3. **One document is one document.** Every number here describes a single US government audit
   report. It found things no synthetic fixture could have, and it cannot tell you what a
   vendor-issued SOC 2 Type II or a CSA CAIQ would do. The fetch script takes more entries.

## What is never done with this document

Extraction, and nothing else. No Trust Score, no finding, no review, and no adverse conclusion
about NARA or about Sikich leaves these tests — the conclusion quoted above is the auditors'
and the agency's, published by them, and it appears here as an example of formatting.
`SOURCES.yaml` states this as data and `test_a_real_document_cannot_be_run_through_a_scored_
review` asserts the path does not exist rather than merely being unused.
