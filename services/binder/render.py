"""The audit binder: eight sections, rendered by a template, never by a model.

    python -m services.binder.render --review-id <id>

HTML with a print stylesheet rather than a PDF library, and the reason is schedule rather than
taste: the layout will be iterated on several times before anyone is happy with it, and an
iteration in HTML costs a browser refresh where the same change through a PDF layout API costs
an afternoon. The print stylesheet is what turns it into a document — page breaks between
sections, no navigation furniture, black on white.

**No model call happens anywhere in this module's call graph, and no import path reaches one.**
The cover says so, and the sentence is only worth printing because it is structurally true: a
blocked payload must not be able to influence the document that reports it. The one place vendor
text appears is section 4's cited passages, and those are read from the chunk store, quoted
inside a bordered block, and escaped.

Everything printed is read from the ledger as it was written. Nothing is recomputed — not the
score, not the band, not the arithmetic — because a binder that recomputed would be showing this
week's rubric against a decision taken under last week's.

Failure semantics: an absent section prints as an explicit absence. A missing review raises
rather than rendering a document about nothing.
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from services.binder.collect import FRAMEWORKS, Binder, collect, counts, passage_for

log = logging.getLogger("drawbridge.binder")

REPO = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = REPO / ".binders"

SECTIONS = (
    ("1", "Review timeline"),
    ("2", "Questionnaire and answers"),
    ("3", "Evidence inventory and screening"),
    ("4", "Findings and contradictions"),
    ("5", "Score computation"),
    ("6", "Human decisions"),
    ("7", "Reasoning trace"),
    ("8", "Post-approval monitoring"),
)

# The palette is the architecture diagrams' palette. One colour language across the diagrams,
# the dashboard and this document means a reader who studied diagram 01 already knows what a red
# rule means here.
STYLE = """
:root {
  --ink: #0f172a; --mute: #64748b; --faint: #94a3b8; --line: #e2e8f0; --shell: #f8fafc;
  --accent: #1d4ed8; --danger: #b91c1c; --warn: #b45309; --ok: #15803d; --human: #c2410c;
  --violet: #6d28d9;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--shell); color: var(--ink);
  font: 400 13.5px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Inter, sans-serif;
}
main { max-width: 940px; margin: 0 auto; padding: 28px; }
h1 { font-size: 22px; font-weight: 700; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 {
  font-size: 15px; font-weight: 700; margin: 0 0 14px;
  padding-bottom: 8px; border-bottom: 1px solid var(--line);
}
h3 { font-size: 13px; font-weight: 600; margin: 18px 0 6px; }
p { margin: 0 0 10px; }
a { color: var(--accent); }
.label {
  font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--faint);
}
.mono {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace; font-size: 11.5px;
}
section {
  background: #fff; border: 1px solid var(--line); border-radius: 12px;
  padding: 22px 24px; margin-bottom: 18px;
}
.cover { padding: 30px 32px; }
.cover h1 { font-size: 26px; }
.cover .lede { color: var(--mute); margin-bottom: 22px; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }
.fact .label { display: block; margin-bottom: 3px; }
.fact .value { font-size: 14px; font-weight: 600; }
.score-badge { font-size: 40px; font-weight: 700; line-height: 1; }
.band-approve { color: var(--ok); }
.band-conditional { color: var(--warn); }
.band-escalate { color: var(--danger); }
.rendered-by {
  margin-top: 22px; padding: 12px 14px; border-left: 3px solid var(--violet);
  background: var(--shell); color: var(--mute); font-size: 12.5px;
}
.warning-banner {
  margin: 18px 0 0; padding: 12px 14px; border: 1px solid var(--danger);
  border-left-width: 3px; border-radius: 8px; color: var(--danger); background: #fef2f2;
  font-size: 12.5px;
}
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th {
  text-align: left; font-size: 11px; font-weight: 700; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--faint); padding: 6px 10px 6px 0;
  border-bottom: 1px solid var(--line); white-space: nowrap;
}
td { padding: 8px 10px 8px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: 0; }
.table-scroll { overflow-x: auto; }
.pill {
  display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 10.5px;
  font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; border: 1px solid;
}
.pill-rule { color: var(--accent); border-color: var(--accent); }
.pill-model { color: var(--violet); border-color: var(--violet); }
.pill-high { color: var(--danger); border-color: var(--danger); }
.pill-medium { color: var(--warn); border-color: var(--warn); }
.pill-low { color: var(--mute); border-color: var(--mute); }
.pill-contradiction { color: var(--danger); border-color: var(--danger); }
.pill-untrusted { color: var(--danger); border-color: var(--danger); }
.pill-clean { color: var(--ok); border-color: var(--ok); }
/* A retrieved passage is a whole chunk, which is right for an auditor and wrong for a screen:
   four findings each quoting two thousand characters reads as a document dump. Capped and
   scrollable here, printed in full below, so the same markup serves both readings. */
.passage {
  margin: 8px 0 0; padding: 10px 12px; border-left: 3px solid var(--line);
  background: var(--shell); color: var(--mute); font-size: 12px; white-space: pre-wrap;
  max-height: 16em; overflow-y: auto;
}
.provenance { margin-top: 6px; color: var(--faint); }
.arithmetic {
  margin: 0; padding: 14px 16px; background: var(--shell); border: 1px solid var(--line);
  border-radius: 8px; white-space: pre; overflow-x: auto;
}
.memo { white-space: pre-wrap; }
.empty { color: var(--faint); font-style: italic; }
.entry { padding: 10px 0; border-bottom: 1px solid var(--line); }
.entry:last-child { border-bottom: 0; }
.entry .goal { font-weight: 600; }
.entry .decision { color: var(--mute); }
.entry .meta { margin-top: 3px; color: var(--faint); }
.contents { display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; font-size: 13px; }
.contents .count { color: var(--faint); font-variant-numeric: tabular-nums; }
footer { color: var(--faint); font-size: 11.5px; padding: 6px 0 28px; text-align: center; }

@media print {
  body { background: #fff; font-size: 10.5pt; }
  main { max-width: none; padding: 0; }
  section {
    border: 0; border-radius: 0; padding: 0 0 12pt; margin: 0 0 12pt;
    break-inside: auto; page-break-inside: auto;
  }
  section + section { break-before: page; page-break-before: always; }
  .cover { break-after: page; page-break-after: always; }
  h2 { border-bottom: 1pt solid #000; }
  .passage, .arithmetic, .rendered-by { background: #fff; border-color: #999; }
  .passage { max-height: none; overflow: visible; }
  tr { break-inside: avoid; page-break-inside: avoid; }
  footer { display: none; }
}
"""


def render(review_id: str) -> str:
    """Return the binder for ``review_id`` as a complete HTML document.

    Raises:
        ReviewNotFound: propagated from ``collect``.
    """
    binder = collect(review_id)
    body = "\n".join(
        [
            _cover(binder),
            _section_1_timeline(binder),
            _section_2_questionnaire(binder),
            _section_3_evidence(binder),
            _section_4_findings(binder),
            _section_5_score(binder),
            _section_6_decisions(binder),
            _section_7_reasoning(binder),
            _section_8_monitoring(binder),
        ]
    )
    return _document(binder, body)


def write(review_id: str, path: Path | None = None) -> Path:
    """Render the binder and write it to disk. Returns the path written."""
    target = path or (OUTPUT_DIR / f"{review_id}.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(review_id), encoding="utf-8")
    log.info("binder written to %s", target)
    return target


# --- the document ----------------------------------------------------------------------------


def _document(binder: Binder, body: str) -> str:
    vendor = _text(binder.vendor.get("name") or binder.review.get("vendor_id"))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Audit binder — {vendor} — {_text(binder.review_id)}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
{body}
<footer>Drawbridge audit binder · rendered {_now()} · one page per section when printed</footer>
</main>
</body>
</html>
"""


def _cover(binder: Binder) -> str:
    review, vendor = binder.review, binder.vendor
    band = str(binder.score.get("band", "")) or "not scored"
    score = _text(binder.score.get("score", "—"))
    tier = review.get("tier")
    prior = _prior_tier(review)

    contents = "".join(
        f'<div>{number}. {_text(title)}</div>'
        f'<div class="count mono">{counts(binder).get(number, 0)}</div>'
        for number, title in SECTIONS
    )

    frameworks = "".join(
        f'<div class="fact"><span class="label">{_text(name)}</span>'
        f'<span class="value" style="font-size:12.5px">{_text(clause)}</span></div>'
        for name, clause in FRAMEWORKS
    )

    banner = (
        '<div class="warning-banner"><strong>Built on unscreened fixtures.</strong> The '
        "evidence in this review was seeded into the clean bucket by a fixture loader and was "
        "not inspected by any detector. The screening verdicts in section 3 carry the template "
        "<span class=\"mono\">local-seed</span> and are not verdicts. This binder documents a "
        "development run, not a vendor assessment.</div>"
        if binder.unscreened
        else ""
    )

    return f"""<section class="cover">
  <span class="label">Vendor security review · audit binder</span>
  <h1>{_text(vendor.get("name") or review.get("vendor_id"))}</h1>
  <p class="lede">{_text(vendor.get("legal_entity_name") or vendor.get("category") or "")}</p>

  <div class="facts">
    <div class="fact"><span class="label">Review</span>
      <span class="value mono">{_text(binder.review_id)}</span></div>
    <div class="fact"><span class="label">Tier</span>
      <span class="value">{_text(tier)}{prior}</span></div>
    <div class="fact"><span class="label">Opened</span>
      <span class="value">{_date(review.get("opened_at"))}</span></div>
    <div class="fact"><span class="label">Decided</span>
      <span class="value">{_date(review.get("decided_at"))}</span></div>
    <div class="fact"><span class="label">Outcome</span>
      <span class="value">{_text(review.get("state", "in flight"))}</span></div>
    <div class="fact"><span class="label">Approved by</span>
      <span class="value">{_text(binder.approver)}</span></div>
    <div class="fact"><span class="label">Trust Score</span>
      <span class="score-badge band-{_text(band)}">{score}</span>
      <span class="value band-{_text(band)}">{_text(band)}</span></div>
    <div class="fact"><span class="label">Model spend</span>
      <span class="value mono">${float(review.get("cost_usd", 0.0)):.4f}</span></div>
  </div>
  {banner}

  <h3>Contents</h3>
  <div class="contents">{contents}</div>

  <h3>Compliance mapping</h3>
  <div class="facts">{frameworks}</div>

  <div class="rendered-by">
    <strong>This binder is rendered by a template, not written by a model.</strong> Every figure
    and every line below is read from the review ledger as it was recorded at the time. Nothing
    is recomputed and no generative model is called anywhere in the rendering path, which
    forecloses the question of whether blocked content could influence the document that
    reports it.
  </div>
</section>"""


# --- the eight sections -----------------------------------------------------------------------


def _section_1_timeline(binder: Binder) -> str:
    rows = "".join(
        f"<tr><td class='mono'>{_date(e.get('ts'))}</td>"
        f"<td class='mono'>{_text(e.get('type'))}</td>"
        f"<td>{_transition(e)}</td>"
        f"<td>{_text(e.get('reason') or e.get('addendum_reason') or '')}</td></tr>"
        for e in binder.timeline
    )
    changes = "".join(
        f"<tr><td class='mono'>{_date(c.get('at'))}</td>"
        f"<td>Tier {_text(c.get('from_tier'))} &rarr; {_text(c.get('to_tier'))}</td>"
        f"<td class='mono'>{_text(c.get('source_ref'))}</td>"
        f"<td>{_text(c.get('reason'))}</td></tr>"
        for c in binder.review.get("tier_history", [])
    )

    tier_block = (
        "<h3>Tier changes and the evidence that caused them</h3>"
        "<div class='table-scroll'><table>"
        "<tr><th>When</th><th>Change</th><th>Source</th><th>Reason</th></tr>"
        f"{changes}</table></div>"
        if changes
        else "<h3>Tier changes</h3><p class='empty'>The tier set at intake was never corrected "
        "by evidence.</p>"
    )

    return f"""<section>
  <h2>1 · Review timeline</h2>
  {tier_block}
  <h3>Every event, in order</h3>
  {_table(rows, "When", "Event", "Transition", "Reason") if rows else _empty("No events recorded.")}
</section>"""


def _section_2_questionnaire(binder: Binder) -> str:
    rows = "".join(
        f"<tr><td class='mono'>{_text(a.get('question_id'))}</td>"
        f"<td>{_text(a.get('text'))}</td>"
        f"<td class='mono'>{float(a.get('confidence', 0)):.2f}</td>"
        f"<td>{_confidence_pill(a)}</td>"
        f"<td class='mono'>{_text(a.get('source_msg'))}</td></tr>"
        for a in binder.questionnaire
    )
    return f"""<section>
  <h2>2 · Questionnaire and answers</h2>
  <p>Every answer as the vendor gave it, with the confidence the parser assigned and the message
  it arrived in. An answer below the confidence threshold was recorded as needing a human rather
  than counted toward coverage.</p>
  {_table(rows, "Question", "Answer", "Confidence", "Parse", "Source message")
   if rows else _empty("No answers were recorded.")}
</section>"""


def _section_3_evidence(binder: Binder) -> str:
    rows = ""
    for record in binder.evidence:
        filters = record.get("filters") or {}
        execution = record.get("execution") or {}
        verdicts = "<br>".join(
            f"{_text(name)}: {_text(state)}"
            f"<span class='provenance'> ({_text(execution.get(name, 'unknown'))})</span>"
            for name, state in sorted(filters.items())
        )
        rows += (
            f"<tr><td class='mono'>{_text(record.get('origin_ref'))}</td>"
            f"<td class='mono'>{_text(record.get('template'))} "
            f"v{_text(record.get('template_version'))}</td>"
            f"<td>{verdicts or '<span class=empty>none recorded</span>'}</td>"
            f"<td>{_trust_pill(record)}</td>"
            f"<td>{_excerpt(record.get('excerpt'))}</td></tr>"
        )

    subs = "".join(
        f"<tr><td>{_text(s.get('name'))}</td><td>{_text(s.get('purpose'))}</td>"
        f"<td>{'yes' if s.get('processes_customer_data') else 'no'}</td>"
        f"<td>{'yes' if s.get('known_to_org') else 'no'}</td></tr>"
        for s in binder.subprocessors
    )

    return f"""<section>
  <h2>3 · Evidence inventory and screening</h2>
  <p>Per document, per filter. The template id and version that produced each verdict are
  printed beside it, because a verdict without its policy is not reproducible six months later.
  Any matched excerpt is reproduced verbatim and inert — it is stored for this document and is
  never re-entered into a prompt.</p>
  {_table(rows, "Document", "Template", "Per-filter verdict", "Trust", "Inert excerpt")
   if rows else _empty("No documents were screened for this review.")}
  <h3>Subprocessors extracted</h3>
  {_table(subs, "Subprocessor", "Purpose", "Processes customer data", "Known to us")
   if subs else _empty("No subprocessor chain was extracted.")}
</section>"""


def _section_4_findings(binder: Binder) -> str:
    blocks = []
    for finding in binder.findings:
        chunk = passage_for(binder, finding)
        if chunk:
            provenance = (
                f"<div class='provenance mono'>chunk {_text(chunk.get('chunk_id'))} · page "
                f"{_text(chunk.get('page'))} · {_text(chunk.get('doc_ref'))}</div>"
                f"<div class='passage'>{_text(chunk.get('text'))}</div>"
            )
        elif finding.get("evidence_ref"):
            provenance = (
                f"<div class='provenance mono'>reference "
                f"{_text(finding.get('evidence_ref'))} — not a retrievable passage</div>"
            )
        else:
            provenance = "<div class='provenance'>No passage cited.</div>"

        blocks.append(
            f"<div class='entry'>"
            f"<div class='goal'>{_text(finding.get('summary'))}</div>"
            f"<div class='meta mono'>{_text(finding.get('domain'))} · "
            f"{_severity_pill(finding)} {_source_pill(finding)}"
            f"{_contradiction_pill(finding)} · "
            f"claim {_text(finding.get('claim_ref') or '—')}</div>"
            f"{provenance}</div>"
        )

    return f"""<section>
  <h2>4 · Findings and contradictions</h2>
  <p>Every finding carries a provenance label. <span class="pill pill-rule">rule</span> means
  the conclusion is arithmetic — a date comparison, a set difference — and
  <span class="pill pill-model">model</span> means it is judgement over a retrieved passage. A
  contradiction cites the passage that refutes the claim, with the chunk and page it came
  from.</p>
  {"".join(blocks) if blocks else _empty("No findings were recorded for this review.")}
</section>"""


def _section_5_score(binder: Binder) -> str:
    arithmetic = "\n".join(str(line) for line in binder.score.get("arithmetic", []))
    modifier = (
        "<p>The Adversarial Conduct modifier was applied: 25 points, and the band is escalate "
        "regardless of the arithmetic above.</p>"
        if binder.score.get("adversarial_applied")
        else ""
    )
    return f"""<section>
  <h2>5 · Score computation</h2>
  <p>The Trust Score is 0–100 and higher is safer. Each domain starts at its maximum for this
  review and loses points per finding by severity; a contradiction costs what its severity costs
  and nothing more, because the severity anchors already price it. The arithmetic below is
  printed as it was computed, so it can be re-done by hand.</p>
  {f'<pre class="arithmetic mono">{_text(arithmetic)}</pre>' if arithmetic
   else _empty("No score was computed for this review.")}
  {modifier}
  <h3>Risk memo</h3>
  {f'<div class="memo">{_text(binder.memo)}</div>' if binder.memo
   else _empty("No memo was written.")}
</section>"""


def _section_6_decisions(binder: Binder) -> str:
    rows = ""
    for card in binder.decisions:
        kind = str(card.get("kind", ""))
        if kind not in ("gate", "tier_change", "policy_block", "parked"):
            continue
        who = (
            binder.approver
            if kind == "gate" and binder.review.get("gate_released_by")
            else "the fleet"
        )
        rows += (
            f"<tr><td class='mono'>{_date(card.get('at'))}</td>"
            f"<td class='mono'>{_text(kind)}</td>"
            f"<td>{_text(card.get('gate_scope') or card.get('policy') or '')}</td>"
            f"<td>{_text(card.get('reason') or card.get('line') or '')}</td>"
            f"<td>{_text(who)}</td></tr>"
        )

    return f"""<section>
  <h2>6 · Human decisions</h2>
  <p>Every gate this review stopped at, every policy refusal that stopped it, and who released
  it. The decision gate cannot be released without a signed, single-use approval token: no code
  path sets a review to decided without one.</p>
  {_table(rows, "When", "Kind", "Scope", "What happened", "Who")
   if rows else _empty("No gates or refusals were recorded.")}
</section>"""


def _section_7_reasoning(binder: Binder) -> str:
    entries = "".join(
        f"<div class='entry'>"
        f"<div class='goal'>{_text(r.get('goal'))}</div>"
        f"<div class='decision'>{_text(r.get('decision'))}</div>"
        f"<div class='meta mono'>{_text(r.get('agent'))} · {_date(r.get('at'))} · trace "
        f"{_text(r.get('trace_id') or '—')}"
        f"{' · ' + _text(r.get('idem_key')) if r.get('idem_key') else ''}</div>"
        f"</div>"
        for r in binder.reasoning
    )
    return f"""<section>
  <h2>7 · Reasoning trace</h2>
  <p>What each step was trying to do and what it concluded, in the fleet's own words. These
  entries carry references, hashes and enumerated verdicts and never vendor-authored text — a
  span becomes a binder section, so hostile content reaching the trace would have escaped the
  quarantine boundary by a side door.</p>
  {entries if entries else _empty("No reasoning entries were recorded.")}
</section>"""


def _section_8_monitoring(binder: Binder) -> str:
    rows = "".join(
        f"<tr><td class='mono'>{_date(e.get('ts'))}</td>"
        f"<td class='mono'>{_text(e.get('type'))}</td>"
        f"<td>{_text((e.get('payload') or {}).get('reason', ''))}</td></tr>"
        for e in binder.monitoring
    )
    return f"""<section>
  <h2>8 · Post-approval monitoring</h2>
  <p>Signals raised against this vendor after the decision was taken. A vendor's posture on the
  day of approval is not their posture six months later, and a review that ends at the signature
  is a snapshot.</p>
  {_table(rows, "When", "Signal", "Detail")
   if rows else _empty("No monitoring signals have been raised since the decision.")}
</section>"""


# --- small renderers ---------------------------------------------------------------------------


def _table(rows: str, *headers: str) -> str:
    head = "".join(f"<th>{_text(h)}</th>" for h in headers)
    return f"<div class='table-scroll'><table><tr>{head}</tr>{rows}</table></div>"


def _empty(message: str) -> str:
    return f'<p class="empty">{_text(message)}</p>'


def _transition(event: dict) -> str:
    before, after = event.get("from_state"), event.get("to_state")
    if not after:
        return ""
    return f"{_text(before or 'new')} &rarr; {_text(after)}"


def _confidence_pill(answer: dict) -> str:
    if answer.get("needs_human"):
        return '<span class="pill pill-medium">needs human</span>'
    return '<span class="pill pill-clean">recorded</span>'


def _trust_pill(record: dict) -> str:
    from shared.armor import UNTRUSTED_TEMPLATES

    if str(record.get("template")) in UNTRUSTED_TEMPLATES:
        return '<span class="pill pill-untrusted">not a verdict</span>'
    if any(state != "EXECUTION_SUCCESS" for state in (record.get("execution") or {}).values()):
        return '<span class="pill pill-untrusted">detector skipped</span>'
    return '<span class="pill pill-clean">verdict</span>'


def _severity_pill(finding: dict) -> str:
    severity = str(finding.get("severity", "low"))
    return f'<span class="pill pill-{_text(severity)}">{_text(severity)}</span>'


def _source_pill(finding: dict) -> str:
    source = str(finding.get("source", "model"))
    return f'<span class="pill pill-{_text(source)}">{_text(source)}</span>'


def _contradiction_pill(finding: dict) -> str:
    if not finding.get("contradiction"):
        return ""
    return ' <span class="pill pill-contradiction">contradiction</span>'


def _excerpt(excerpt) -> str:
    if not excerpt:
        return '<span class="empty">none</span>'
    return f'<div class="passage mono">{_text(excerpt)}</div>'


def _text(value) -> str:
    """Escape anything that reaches the page.

    Section 3 prints a blocked payload verbatim and section 4 prints vendor passages, so this is
    not a formality: unescaped vendor text in an audit document is a stored cross-site scripting
    hole in the artefact that exists to describe an injection attempt.
    """
    return html.escape("" if value is None else str(value), quote=True)


def _date(value) -> str:
    if not value:
        return "—"
    text = str(value)
    return text[:19].replace("T", " ")


def _prior_tier(review: dict) -> str:
    history = review.get("tier_history") or []
    if not history:
        return ""
    return f' <span class="label">was {_text(history[0].get("from_tier"))}</span>'


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a review's audit binder.")
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[binder] %(message)s")

    started = datetime.now(UTC)
    path = write(args.review_id, args.out)
    elapsed = (datetime.now(UTC) - started).total_seconds()

    print(f"{path}  ({elapsed:.2f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
