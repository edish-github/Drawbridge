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

**What was measured and what was not.** The structural half — text extraction, the truncation
window, chunking — ran and is recorded here in full. The one live model call did not: the
Gemini credential in `.env` is rejected with `API_KEY_INVALID`, so `test_what_the_extractor_
reads_off_a_real_report` is written, wired through the real router with a real stamp, and
skipped behind `DRAWBRIDGE_MEASURE_EXTRACTION=1`. It is a one-command measurement the day the
credential is replaced. The three findings below needed no model, and they are the substantive
ones.

---

## 1 · Seventy-one per cent of the document never reaches the model

`MAX_DOCUMENT_CHARS` is 24,000. This document is 83,909 characters.

| | in the window | past the cut |
|---|---|---|
| "Appendix" | 12 | 13 |
| "recommendation" | 16 | 71 |

The four fields the deterministic checks are built on — auditor, conclusion, period, scope —
all survive, because this report states them in a covering letter and an objectives section
inside the first 12,000 characters. **Everything else does not.** Sixteen recommendations,
their detail and the appendices they live in are invisible to extraction.

The synthetic packs are a few thousand characters each, so the cap has never been exercised by
any test in this repository, and it has been sitting at a value nobody could have calibrated
without a document like this one. The brief that asked for this measurement named the risk
almost exactly: *an exception in an appendix rather than in the exception notes*. On a document
this size, the appendix is not merely a harder place to look — it is not sent.

Recorded rather than fixed. Raising the cap costs tokens on every document in every review, and
a document three times longer than this one would clear any new constant just as quietly. The
answer is section-aware selection — retrieve into the extraction window rather than truncating
into it — which is a design change and not a number.

## 2 · The heading-aware chunker finds no headings

`armor._HEADING` matches `^#{1,6}\s`. This document contains **zero** matches.

Its headings are `II. SUMMARY OF RESULTS` — capitals and a Roman numeral, because it was
typeset rather than written in Markdown. The rule built last milestone, that a heading never
ends a chunk so a section title travels with its text, is a rule about Markdown fixtures. On
every real PDF it is a no-op, silently.

What does hold: 63 chunks, none over the 1,600-character budget, and **none beginning
mid-sentence**. The paragraph splitting and the sentence fallback work on real prose. It is the
heading half that does nothing, and it does nothing without failing.

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

A currency check wants the first. The second is a different range for the same document, sitting
in the next sentence, and nothing in the prompt tells a model which is which.

---

## What this changes

Nothing yet, deliberately. All three are recorded and asserted; none is patched, because two of
them are design decisions rather than constants and the third is a measurement the credential
blocks. In descending order of what they cost:

1. **The truncation window** is a correctness problem on any document over ~25,000 characters,
   and real assurance reports routinely are. It is now the top of the list, above anything on
   the cloud-gaps list, because it needs no project to fix and no project to have found.
2. **Section-aware chunking** should recognise typeset headings, or the rule should stop being
   described as one. A guarantee that holds only for fixtures is worse than no guarantee.
3. **The facts prompt's vocabulary** is SOC 2's. It should name the shape it wants — a stated
   conclusion about effectiveness, whatever word the report uses for it — rather than three
   example opinions from one report type.

## What is never done with this document

Extraction, and nothing else. No Trust Score, no finding, no review, and no adverse conclusion
about NARA or about Sikich leaves these tests — the conclusion quoted above is the auditors'
and the agency's, published by them, and it appears here as an example of formatting.
`SOURCES.yaml` states this as data and `test_a_real_document_cannot_be_run_through_a_scored_
review` asserts the path does not exist rather than merely being unused.
