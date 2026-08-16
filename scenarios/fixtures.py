"""Deterministic model responses, derived from the vendor pack rather than invented.

``--fixtures-only`` replaces ``shared.routing.generate`` with a function that answers each
routed task from the vendor's own fixture files. No model call is made, so a full run costs
nothing, produces the same result every time, and can execute in CI on every push.

**What this proves and what it does not.** It proves the orchestration: every agent, every
transition, every guard, every idempotency key and the whole arithmetic of the Trust Score run
exactly as they do live — only the model's answers are substituted. It proves nothing about the
model's judgement, which is measured against the live API and is the reason the severity
stability check exists separately.

The substitution happens at ``routing.generate`` on purpose rather than inside each agent. That
is the single chokepoint every model call already goes through, so a code path that reached a
model another way would fail loudly here instead of quietly making a live call in a run
advertised as free.

The canned answers are **read from the fixtures**, not written here: the reply text comes from
``questionnaire_answers.json``, the document facts are parsed out of the evidence documents, and
the cross-examination findings come from ``expected.json``. A fixture mode with its own
hand-written answers would drift from the pack it is meant to represent, and the first thing
anyone would notice is that the demo and the tests disagreed.

Failure semantics: a routed task with no fixture answer raises rather than falling through to a
live call. A fixtures-only run that quietly spent money would be the worst outcome this module
could have.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import date

from scenarios.seed import load_vendor
from shared.routing import ModelResult

log = logging.getLogger("drawbridge.fixtures")

_DATE_PATTERNS = (
    (re.compile(r"\*\*Expiry date\*\*\s*\|\s*\*\*(.+?)\*\*", re.I), "cert_expiry"),
    (re.compile(r"\|\s*Report period\s*\|\s*.+?[-–]\s*(.+?)\s*\|", re.I), "report_period_end"),
)

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ],
        start=1,
    )
}


class NoFixtureAnswer(Exception):
    """A routed task reached the fixture responder with no fixture to answer from."""


GENERATE_BINDINGS = (
    "shared.routing",
    "agents.orchestrator.planner",
    "agents.evidence.extractors",
    "agents.evidence.cross_exam",
    "agents.evidence.subprocessors",
    "agents.questionnaire.parser",
)
"""Every module holding a name bound to ``routing.generate``.

``from shared.routing import generate`` copies the function object, so patching only the
routing module would leave every importer calling the real one — a fixtures-only run that
quietly spent money and quota. Modules that import inside a function body resolve through
``shared.routing`` at call time and are covered by the first entry alone.
"""

EMBED_BINDINGS = ("shared.routing",)
"""Where ``embed`` is bound. Every caller imports it inside a function, so one entry covers it."""


@contextmanager
def responding_from(vendor: str):
    """Replace model calls with fixture answers for the duration of the block."""
    import importlib

    responder = FixtureResponder(vendor)
    saved: list[tuple[object, str, object]] = []

    for name in GENERATE_BINDINGS:
        module = importlib.import_module(name)
        if hasattr(module, "generate"):
            saved.append((module, "generate", module.generate))
            module.generate = responder
    for name in EMBED_BINDINGS:
        module = importlib.import_module(name)
        saved.append((module, "embed", module.embed))
        module.embed = deterministic_embedding

    log.info("fixtures-only: model calls are answered from the %s pack", vendor)
    try:
        yield responder
    finally:
        for module, attribute, original in saved:
            setattr(module, attribute, original)


def deterministic_embedding(text: str, ctx) -> list[float]:
    """A stable pseudo-embedding, so retrieval ranks the same way on every fixtures run.

    Not an approximation of semantic similarity and not pretending to be: it is a hash of the
    text's word set projected into the real dimension. Retrieval still ranks, still pre-filters
    by review, and still returns k passages — which is what the orchestration under test needs.
    Whether the ranking is *good* is a question for the live model, and answering it here with a
    fake would be the kind of fixture that flatters the system it is testing.
    """
    import hashlib

    dimensions = 3072
    vector = [0.0] * dimensions
    for word in set(re.findall(r"[a-z0-9]+", text.lower())):
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        for i in range(0, 16, 2):
            index = int.from_bytes(digest[i : i + 2], "big") % dimensions
            vector[index] += 1.0
    return vector


class FixtureResponder:
    """Answers each routed task from one vendor's fixture files."""

    def __init__(self, vendor: str) -> None:
        self.vendor = vendor
        self.pack = load_vendor(vendor)
        self.calls: list[str] = []

    def __call__(self, task, prompt, ctx, *, response_schema=None, temperature=0.0):
        self.calls.append(task)
        handler = {
            "plan_review": self._plan,
            "parse_reply": self._parse_reply,
            "extract_controls": self._extract,
            "cross_examine": self._cross_examine,
            "risk_memo": self._memo,
        }.get(task)

        if handler is None:
            raise NoFixtureAnswer(
                f"no fixture answer for task {task!r}. A fixtures-only run never falls through "
                "to a live call; add an answer or stop routing this task in this mode."
            )

        parsed = handler(prompt, response_schema)
        return ModelResult(
            text=parsed if isinstance(parsed, str) else "",
            model=f"fixtures:{self.vendor}",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0.0,
            parsed=None if isinstance(parsed, str) else parsed,
        )

    # --- per-task answers ------------------------------------------------------------------

    def _plan(self, prompt: str, schema):
        """Tier from the profile, steps from the standard plan for that tier."""
        from agents.orchestrator.planner import default_steps
        from shared.domain import ReviewPlan

        profile = self.pack["profile"]
        tier = int(profile.get("tier", 2))
        return ReviewPlan(
            tier=tier,
            reason=(
                f"{profile['name']} declared "
                f"{', '.join(profile['intake'].get('declared_data_categories', [])) or 'nothing'} "
                f"at intake, which the tiering policy places at Tier {tier}."
            ),
            steps=[s.name for s in default_steps(tier)],
        )

    def _parse_reply(self, prompt: str, schema):
        """Return the answers whose ids appear in the reply body, with fixture confidences."""
        from agents.questionnaire.parser import _Answer, _ParsedReply

        answers = self.pack["answers"].get("answers", {})
        body = prompt.split("VENDOR REPLY:", 1)[-1]

        parsed = [
            _Answer(
                question_id=qid,
                text=entry["text"],
                confidence=float(entry.get("expected_confidence", 0.8)),
            )
            for qid, entry in answers.items()
            if re.search(rf"\b{re.escape(qid)}\b", body)
        ]
        return _ParsedReply(answers=parsed)

    def _extract(self, prompt: str, schema):
        """Read dates, auditor, opinion and scope straight out of the document in the prompt."""
        from agents.evidence.extractors import _ExtractedControls, _ExtractedFacts
        from agents.evidence.subprocessors import _ExtractedChain

        if schema is _ExtractedChain:
            return _ExtractedChain(subprocessors=self._subprocessors(prompt))
        if schema is _ExtractedControls:
            return _ExtractedControls(controls=[])
        return _ExtractedFacts(**self._facts(prompt))

    def _cross_examine(self, prompt: str, schema):
        """Return the finding ``expected.json`` says this claim produces, or none."""
        from agents.evidence.cross_exam import ReconciledClaim
        from shared.domain import FindingDraft

        question_id = _between(prompt, "QUESTION ID:", "\n").strip()
        expected = {
            f["claim_ref"]: f
            for f in self.pack.get("expected", {}).get("required_findings", [])
            if f.get("claim_ref")
        }

        spec = expected.get(question_id)
        if spec is None:
            return ReconciledClaim(
                finding=None, no_finding_reason=f"{question_id} is consistent with the evidence"
            )

        chunk_id = _first_chunk_id(prompt)
        return ReconciledClaim(
            finding=FindingDraft(
                domain=spec["domain"],
                severity=spec["severity"],
                contradiction=bool(spec.get("contradiction")),
                summary=spec.get("note", f"{question_id}: recorded from the fixture pack."),
                evidence_ref=chunk_id,
                claim_ref=question_id,
            )
        )

    def _memo(self, prompt: str, schema) -> str:
        band = _between(prompt, "band ", "\n").strip() or "conditional"
        return (
            f"RECOMMENDATION: {band}.\n\n"
            "WHAT DROVE IT: the findings listed in the score breakdown above, each carrying its "
            "provenance.\n\n"
            "MITIGATIONS REQUIRED: remediation of every contradiction recorded against the "
            "vendor's own evidence, with dates.\n\n"
            "RE-CHECK IN 90 DAYS: certification currency and any exception still unremediated.\n\n"
            "(Fixture memo. No model call was made in this run.)"
        )

    # --- reading the documents --------------------------------------------------------------

    def _facts(self, prompt: str) -> dict:
        name = _between(prompt, "DOCUMENT:", "\n").strip()
        body = prompt.split("\n", 1)[-1]
        out: dict = {"auditor": None, "opinion": None, "scope": None}

        for pattern, field in _DATE_PATTERNS:
            match = pattern.search(body)
            if match:
                out[field] = _parse_date(match.group(1))

        auditor = re.search(r"\|\s*(?:Auditor|Issuing body)\s*\|\s*(.+?)\s*\|", body, re.I)
        opinion = re.search(r"\|\s*Opinion\s*\|\s*(.+?)\s*\|", body, re.I)
        scope = re.search(r"##\s*(?:1\.\s*)?Scope[^\n]*\n+(.+?)(?:\n\n|\n##)", body, re.S | re.I)

        if auditor:
            out["auditor"] = auditor.group(1).strip()
        if opinion:
            out["opinion"] = opinion.group(1).strip()
        if scope:
            out["scope"] = " ".join(scope.group(1).split())

        log.debug("fixture facts for %s: %s", name, out)
        return out

    def _subprocessors(self, prompt: str) -> list:
        """Read the subprocessor table out of the document."""
        from agents.evidence.subprocessors import ExtractedSubprocessor

        rows = []
        for line in prompt.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 4 or cells[0].lower() in ("subprocessor", "---"):
                continue
            if set(cells[0]) <= set("- "):
                continue
            processes = cells[3].lower().startswith("y") if len(cells) > 3 else None
            rows.append(
                ExtractedSubprocessor(
                    name=cells[0], purpose=cells[1], processes_customer_data=processes
                )
            )
        return rows


def _between(text: str, start: str, end: str) -> str:
    if start not in text:
        return ""
    after = text.split(start, 1)[1]
    return after.split(end, 1)[0] if end in after else after


def _first_chunk_id(prompt: str) -> str | None:
    """Return the chunk id of the first retrieved passage, so the citation resolves."""
    passages = prompt.split("RETRIEVED PASSAGES:", 1)[-1]
    match = re.search(r"\[([^\]\s]+:[^\]\s]+)\]", passages)
    return match.group(1) if match else None


def _parse_date(raw: str) -> date | None:
    """Parse the date forms the fixtures use: '14 March 2025' and ISO."""
    cleaned = raw.strip().strip("*").strip()
    match = re.match(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", cleaned)
    if match:
        day, month, year = match.groups()
        if month.lower() in _MONTHS:
            return date(int(year), _MONTHS[month.lower()], int(day))
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None
