"""Measure whether the model assigns the same severity to the same evidence twice.

    python -m scripts.severity_sweep --vendors datadynamo cleancloud --runs 3

**Do not run this until the quota lifts.** Every claim in two vendor packs, three times each, is
several hundred live calls against a free tier that allows twenty a day. It is written now so
that the day the quota lifts it is one command rather than an afternoon.

Why it exists. The Trust Score is arithmetic over severities, and the severities come from a
model. That makes severity stability the input to the headline number: if the same passage
produces ``high`` on Monday and ``medium`` on Tuesday, the score moves by five points for
reasons nobody can explain, and every defence of the arithmetic downstream is defending a
number built on sand. One claim reconciled three times is a data point. Every claim in two packs
reconciled three times is a property.

What is compared is the **signature** — ``(domain, severity, contradiction)`` — rather than the
summary text. Wording is expected to vary and does not move the number; those three fields are
the entire input to the score, so those three fields are what stability means here.

**Resumable and cheap to resume.** Every result is appended to a JSONL file as it happens, and a
re-run skips any (vendor, claim, run) already in that file. A sweep interrupted at call 180 of
340 costs 160 calls to finish, not 340. That matters more than it looks: the most likely reason
this run stops is the daily quota, which is also the reason it cannot simply be started again
from the beginning.

**If it shows drift**, the repair is tighter severity anchors for the claims that drifted, not a
lower temperature. Temperature is already 0 — see ``shared.routing`` — so a wobble at zero is
the prompt being genuinely ambiguous about which anchor applies, and a knob cannot fix an
ambiguity in a definition.

Failure semantics: one failed call is recorded as a failure for that run and the sweep
continues; a claim that failed every run is reported as unmeasured rather than as unstable,
because those are different problems with different repairs. Nothing here writes a finding, a
score or a review — it reads the pack, calls the model, and writes one file.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from shared import tenancy as tenant

log = logging.getLogger("drawbridge.sweep")

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "infra" / "SEVERITY-SWEEP.jsonl"
DEFAULT_REPORT = REPO / "infra" / "SEVERITY-SWEEP.md"

DEFAULT_VENDORS = ("datadynamo", "cleancloud")
DEFAULT_RUNS = 3

STABLE = "stable"
UNSTABLE = "unstable"
UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class Attempt:
    """One reconciliation of one claim, as it is written to the file."""

    vendor: str
    review_id: str
    question_id: str
    run: int
    signature: str | None
    error: str | None = None

    def key(self) -> tuple[str, str, int]:
        return (self.vendor, self.question_id, self.run)

    def as_json(self) -> str:
        return json.dumps(
            {
                "vendor": self.vendor,
                "review_id": self.review_id,
                "question_id": self.question_id,
                "run": self.run,
                "signature": self.signature,
                "error": self.error,
            }
        )


def signature_of(finding) -> str:
    """Return the three fields that decide the number.

    Not the summary. Wording is expected to vary between runs and moves nothing; domain,
    severity and the contradiction flag are the entire input to ``compute_score``, so drift in
    them is drift in the Trust Score and drift anywhere else is prose.
    """
    if finding is None:
        return "none"
    kind = "contradiction" if finding.contradiction else "gap"
    return f"{finding.domain}/{finding.severity}/{kind}"


# --- the sweep -------------------------------------------------------------------------------


def sweep(
    vendors: list[str], runs: int, out: Path, *, limit: int | None = None
) -> list[Attempt]:
    """Run every claim in every vendor pack ``runs`` times, appending as it goes."""
    done = already_done(out)
    attempts = list(load(out))
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("a", encoding="utf-8") as fh:
        for vendor in vendors:
            review_id, claims = prepare(vendor)
            if limit:
                claims = claims[:limit]

            log.info("%s: %d claim(s) × %d run(s)", vendor, len(claims), runs)
            for run in range(1, runs + 1):
                for claim in claims:
                    if (vendor, claim.question_id, run) in done:
                        continue
                    attempt = reconcile_once(vendor, review_id, claim, run)
                    fh.write(attempt.as_json() + "\n")
                    fh.flush()
                    attempts.append(attempt)
                    log.info(
                        "%s %s run %d → %s",
                        vendor,
                        claim.question_id,
                        run,
                        attempt.error or attempt.signature,
                    )
    return attempts


def prepare(vendor: str) -> tuple[str, list]:
    """Seed one vendor's evidence and answers into a throwaway review. Returns its claims.

    The evidence has to be in the clean bucket and indexed for retrieval before a claim can be
    reconciled, so the sweep builds the same review state a run would — without running the
    fleet, publishing anything, or writing a finding.
    """
    import uuid

    from agents.evidence.agent import claims_for
    from scenarios.seed import load_vendor, seed_clean_evidence, seed_vendor

    review_id = f"sweep-{vendor}-{uuid.uuid4().hex[:8]}"
    seed_vendor(vendor)
    seed_clean_evidence(review_id, vendor)
    _seed_answers(review_id, load_vendor(vendor))
    return review_id, claims_for(review_id)


def reconcile_once(vendor: str, review_id: str, claim, run: int) -> Attempt:
    """Reconcile one claim once. A failure is recorded rather than raised."""
    from agents.evidence.cross_exam import reconcile_claim
    from shared.context import AgentContext

    ctx = AgentContext(
        org_id=tenant.current_org(),
        review_id=review_id,
        agent="evidence",
        trace_id=f"sweep-{run}",
    )
    try:
        finding = reconcile_claim(ctx, review_id, claim)
    except Exception as exc:  # noqa: BLE001 — one failed call never stops the sweep
        return Attempt(vendor, review_id, claim.question_id, run, None, error=str(exc)[:200])
    return Attempt(vendor, review_id, claim.question_id, run, signature_of(finding))


def _seed_answers(review_id: str, pack: dict) -> None:
    for question_id, entry in (pack["answers"].get("answers") or {}).items():
        tenant.collection("qa_responses").document(f"{review_id}:{question_id}").set(
            {
                "review_id": review_id,
                "question_id": question_id,
                "text": entry["text"],
                "confidence": float(entry.get("expected_confidence", 0.8)),
                "needs_human": float(entry.get("expected_confidence", 0.8)) < 0.7,
                "source_msg": "sweep",
            }
        )


# --- reading what has already been measured ---------------------------------------------------


def load(out: Path):
    """Yield the attempts already recorded. A malformed line is skipped, never fatal."""
    if not out.is_file():
        return
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            yield Attempt(
                row["vendor"],
                row.get("review_id", ""),
                row["question_id"],
                int(row["run"]),
                row.get("signature"),
                row.get("error"),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            log.warning("skipping an unreadable line in %s", out.name)


def already_done(out: Path) -> set[tuple[str, str, int]]:
    """Return the (vendor, claim, run) triples that need not be spent again."""
    return {a.key() for a in load(out) if a.error is None}


# --- the report --------------------------------------------------------------------------------


def summarise(attempts: list[Attempt]) -> dict:
    """Return per-claim stability and the overall rate."""
    by_claim: dict[tuple[str, str], list[Attempt]] = defaultdict(list)
    for attempt in attempts:
        by_claim[(attempt.vendor, attempt.question_id)].append(attempt)

    claims = []
    for (vendor, question_id), rows in sorted(by_claim.items()):
        signatures = [r.signature for r in rows if r.error is None]
        failures = [r for r in rows if r.error is not None]
        distinct = sorted(set(signatures))

        if not signatures:
            verdict = UNMEASURED
        elif len(distinct) == 1:
            verdict = STABLE
        else:
            verdict = UNSTABLE

        claims.append(
            {
                "vendor": vendor,
                "question_id": question_id,
                "runs": len(rows),
                "failures": len(failures),
                "verdict": verdict,
                "signatures": distinct,
            }
        )

    measured = [c for c in claims if c["verdict"] != UNMEASURED]
    stable = [c for c in measured if c["verdict"] == STABLE]
    return {
        "claims": claims,
        "measured": len(measured),
        "stable": len(stable),
        "unstable": len(measured) - len(stable),
        "unmeasured": len(claims) - len(measured),
        "rate": (len(stable) / len(measured)) if measured else 0.0,
    }


def report(summary: dict, runs: int) -> str:
    """Render the summary as the table that goes in the write-up."""
    lines = [
        "# Severity stability",
        "",
        f"Every claim in the pack, reconciled {runs} times, comparing "
        "`(domain, severity, contradiction)` — the three fields the Trust Score is computed "
        "from. Summary wording is expected to vary and is not compared.",
        "",
        f"**{summary['stable']} of {summary['measured']} claims stable "
        f"({summary['rate']:.0%})**"
        + (f", {summary['unmeasured']} unmeasured" if summary["unmeasured"] else ""),
        "",
        "| Vendor | Claim | Runs | Verdict | Signatures |",
        "|---|---|---|---|---|",
    ]
    for claim in summary["claims"]:
        lines.append(
            f"| {claim['vendor']} | {claim['question_id']} | {claim['runs']} | "
            f"{claim['verdict']} | {' · '.join(claim['signatures']) or '—'} |"
        )

    drifted = [c for c in summary["claims"] if c["verdict"] == UNSTABLE]
    if drifted:
        lines += [
            "",
            "## What to do about the drift",
            "",
            "Tighter severity anchors for the claims below, not a lower temperature — "
            "temperature is already 0, so a wobble at zero is the prompt being genuinely "
            "ambiguous about which anchor applies, and a knob cannot fix an ambiguity in a "
            "definition.",
            "",
        ]
        for claim in drifted:
            seen = " vs ".join(claim["signatures"])
            lines.append(f"- `{claim['vendor']}/{claim['question_id']}`: {seen}")

    return "\n".join(lines) + "\n"


def main() -> int:
    # Every entry point adopts a tenant before it touches anything. Library code never
    # defaults one; a CLI does, and only outside cloud mode.
    with tenant.acting_for(tenant.cli_org()):
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--vendors", nargs="*", default=list(DEFAULT_VENDORS))
        parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
        parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
        parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
        parser.add_argument("--limit", type=int, help="claims per vendor, for a smoke run")
        parser.add_argument(
            "--summarise-only",
            action="store_true",
            help="rebuild the report from what has already been measured, spending nothing",
        )
        args = parser.parse_args()

        logging.basicConfig(level=logging.INFO, format="[sweep] %(message)s")

        attempts = list(load(args.out)) if args.summarise_only else sweep(
            args.vendors, args.runs, args.out, limit=args.limit
        )
        if not attempts:
            print("nothing measured yet; run without --summarise-only once quota allows")
            return 1

        summary = summarise(attempts)
        args.report.write_text(report(summary, args.runs), encoding="utf-8")

        print(
            f"{summary['stable']}/{summary['measured']} claims stable "
            f"({summary['rate']:.0%}); {summary['unstable']} drifted, "
            f"{summary['unmeasured']} unmeasured"
        )
        print(f"wrote {args.report}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
