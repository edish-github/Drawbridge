"""Measure this system's own miss rate on contradictions it was not built to catch.

    python -m scripts.miss_rate --runs 3
    python -m scripts.miss_rate --summarise-only     # report what is already recorded

**Do not run this until the quota lifts.** Six cases times three runs is eighteen live
cross-examination calls plus indexing, against a free tier that allows twenty a day. It is
written now so the day the quota lifts it is one command rather than an afternoon.

Why it exists. The test suite can prove over-flagging does not happen — a claim the evidence
supports produces no finding, and that is asserted. It cannot prove under-flagging does not
happen, because a test for a missed contradiction needs a contradiction already known to be
missed, and anything already known to be missed gets fixed instead of tested. The only honest
alternative is to measure: build a set of contradictions genuinely harder than the ones the
system was designed around, run them, and publish the rate including the ones that got through.

That is the same argument the injection corpus makes, and the two numbers belong next to each
other. One says how much hostile content the screening boundary stops. This one says how much
quiet overstatement the reviewer behind it catches. A product that publishes the first and not
the second has published the half it was more confident about.

**A miss is attributed, not just counted.** Two different failures produce the same silence:

``retrieval_miss``
    The contradicting passage was never retrieved, so the model was asked to reconcile a claim
    against evidence that did not contain the answer. The repair is retrieval — anchors,
    chunking, ``TOP_K`` — and no prompt change will touch it.
``judgement_miss``
    The passage was in the prompt and the model said there was nothing to report. The repair is
    the prompt or the anchors.

Reporting these together as one number would send every repair to the wrong place, which is
how a measurement becomes worse than no measurement.

**False positives are counted too.** ``SC06`` is a control that is genuinely in place, written
in the same register as the five that are not. A harness that only counts misses rewards a
model that flags everything, and a cross-examiner that flags everything is one nobody keeps.

**Resumable and cheap to resume.** Every result is appended to JSONL as it happens and a re-run
skips any (case, run) already recorded. The most likely reason this stops is the daily quota,
which is also the reason it cannot simply be restarted from the beginning.

Failure semantics: one failed call is recorded as a failure for that run and the harness
continues; a case that failed every run is reported as unmeasured rather than as missed,
because those are different problems. Nothing here writes a finding, a score or a review — it
seeds a throwaway review's evidence, calls the model, and writes one file.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from shared import tenancy

log = logging.getLogger("drawbridge.miss_rate")

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "synthetic-vendors" / "datadynamo" / "subtle-contradictions.json"
DEFAULT_OUT = REPO / "infra" / "MISS-RATE.jsonl"
DEFAULT_REPORT = REPO / "infra" / "MISS-RATE.md"

DEFAULT_RUNS = 3

CAUGHT = "caught"
JUDGEMENT_MISS = "judgement_miss"
RETRIEVAL_MISS = "retrieval_miss"
FALSE_POSITIVE = "false_positive"
CORRECT_SILENCE = "correct_silence"
UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class Outcome:
    """One reconciliation of one case, as it is written to the file."""

    case_id: str
    run: int
    verdict: str | None
    retrieved: bool = False
    signature: str | None = None
    error: str | None = None

    def key(self) -> tuple[str, int]:
        return (self.case_id, self.run)

    def as_json(self) -> str:
        return json.dumps(
            {
                "case_id": self.case_id,
                "run": self.run,
                "verdict": self.verdict,
                "retrieved": self.retrieved,
                "signature": self.signature,
                "error": self.error,
            }
        )


def cases() -> list[dict]:
    """Return the corpus. Read from the pack so the fixture and the harness cannot disagree."""
    return list(json.loads(CORPUS.read_text(encoding="utf-8"))["cases"])


def classify(case: dict, finding, retrieved: bool) -> str:
    """Return what this run of this case was.

    The attribution is the point. A silent run on a case whose passage was never retrieved is a
    retrieval problem wearing a model problem's clothes, and the two have no repair in common.
    """
    flagged = finding is not None
    if case["must_flag"]:
        if flagged:
            return CAUGHT
        return JUDGEMENT_MISS if retrieved else RETRIEVAL_MISS
    return FALSE_POSITIVE if flagged else CORRECT_SILENCE


# --- the run ------------------------------------------------------------------------------------


def measure(runs: int, out: Path, *, only: str | None = None) -> list[Outcome]:
    """Run every case ``runs`` times, appending as it goes."""
    corpus = [c for c in cases() if only is None or c["id"] == only]
    done = already_done(out)
    outcomes = list(load(out))
    out.parent.mkdir(parents=True, exist_ok=True)

    review_id = prepare(corpus)
    log.info("%d case(s) × %d run(s) on review=%s", len(corpus), runs, review_id)

    with out.open("a", encoding="utf-8") as fh:
        for run in range(1, runs + 1):
            for case in corpus:
                if (case["id"], run) in done:
                    continue
                outcome = reconcile_once(review_id, case, run)
                fh.write(outcome.as_json() + "\n")
                fh.flush()
                outcomes.append(outcome)
                log.info(
                    "%s run %d → %s%s",
                    case["id"],
                    run,
                    outcome.error or outcome.verdict,
                    "" if outcome.retrieved else "  (passage not retrieved)",
                )
    return outcomes


def prepare(corpus: list[dict]) -> str:
    """Seed the corpus passages as one review's evidence and index them. Returns the review id.

    Written into the clean bucket and indexed the way any evidence is, so ``reconcile_claim``
    retrieves rather than being handed its passages. That is what makes ``retrieval_miss``
    measurable at all: a harness that injected the right passage into every prompt would be
    measuring the model with retrieval's hardest job already done for it.
    """
    import uuid

    from scenarios.seed import SEEDED_TEMPLATE, _seed_stamp
    from shared import storage
    from shared.armor import index_chunks, record_screening
    from shared.config import settings

    review_id = f"missrate-{uuid.uuid4().hex[:8]}"
    body = "\n\n".join(
        f"## {case['id']} — {case['technique']}\n\n{case['passage']}" for case in corpus
    )

    ref = storage.ref_for(settings().bucket_clean, f"{review_id}/subtle-contradictions.txt")
    storage.write_object(ref, body.encode("utf-8"), content_type="text/plain")
    record_screening(review_id, _seed_stamp(ref))
    index_chunks(ref, review_id)

    log.info("seeded %d passage(s) as %s (%s)", len(corpus), ref, SEEDED_TEMPLATE)
    return review_id


def reconcile_once(review_id: str, case: dict, run: int) -> Outcome:
    """Reconcile one case once. A failure is recorded rather than raised."""
    from agents.evidence.cross_exam import Claim, reconcile_claim, retrieve_for_claim
    from shared.context import AgentContext

    ctx = AgentContext(
        org_id=tenancy.current_org(),
        review_id=review_id,
        agent="evidence",
        trace_id=f"missrate-{run}",
    )
    claim = Claim(question_id=case["id"], domain=case["domain"], text=case["claim"])

    try:
        retrieved = _passage_was_retrieved(case, review_id, ctx, retrieve_for_claim)
        finding = reconcile_claim(ctx, review_id, claim)
    except Exception as exc:  # noqa: BLE001 — one failed call never stops the measurement
        return Outcome(case["id"], run, None, error=str(exc)[:200])

    return Outcome(
        case["id"],
        run,
        classify(case, finding, retrieved),
        retrieved=retrieved,
        signature=_signature(finding),
    )


def _passage_was_retrieved(case: dict, review_id: str, ctx, retrieve) -> bool:
    """Return whether this case's own passage came back for this case's claim.

    Matched on a distinctive fragment of the passage rather than on a chunk id, because the
    chunker decides where the boundaries fall and a passage split across two chunks is still a
    passage that was retrieved.
    """
    marker = case["passage"][:60]
    return any(marker[:40] in chunk.text for chunk in retrieve(case["claim"], review_id, ctx))


def _signature(finding) -> str | None:
    if finding is None:
        return None
    kind = "contradiction" if finding.contradiction else "gap"
    return f"{finding.domain}/{finding.severity}/{kind}"


# --- reading what has already been measured -----------------------------------------------------


def load(out: Path):
    """Yield the outcomes already recorded. A malformed line is skipped, never fatal."""
    if not out.is_file():
        return
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            yield Outcome(
                row["case_id"],
                int(row["run"]),
                row.get("verdict"),
                bool(row.get("retrieved")),
                row.get("signature"),
                row.get("error"),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            log.warning("skipping an unreadable line in %s", out.name)


def already_done(out: Path) -> set[tuple[str, int]]:
    """Return the (case, run) pairs that need not be spent again."""
    return {o.key() for o in load(out) if o.error is None}


# --- the report ----------------------------------------------------------------------------------


def summarise(outcomes: list[Outcome]) -> dict:
    """Return the per-case verdicts and the rates that go next to the detection rate."""
    by_case: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        by_case[outcome.case_id].append(outcome)

    spec = {case["id"]: case for case in cases()}
    rows = []
    for case_id, runs in sorted(by_case.items()):
        verdicts = [o.verdict for o in runs if o.error is None]
        rows.append(
            {
                "case_id": case_id,
                "technique": spec.get(case_id, {}).get("technique", ""),
                "must_flag": bool(spec.get(case_id, {}).get("must_flag")),
                "runs": len(runs),
                "verdicts": sorted(set(verdicts)) or [UNMEASURED],
                "caught": verdicts.count(CAUGHT),
                "errors": sum(1 for o in runs if o.error is not None),
            }
        )

    positives = [r for r in rows if r["must_flag"]]
    attempts = sum(r["runs"] - r["errors"] for r in positives)
    caught = sum(r["caught"] for r in positives)

    negatives = [r for r in rows if not r["must_flag"]]
    false_positives = sum(
        1 for o in outcomes if o.verdict == FALSE_POSITIVE
    )

    return {
        "cases": rows,
        "attempts": attempts,
        "caught": caught,
        "detection_rate": round(100.0 * caught / attempts, 1) if attempts else None,
        "miss_rate": round(100.0 * (attempts - caught) / attempts, 1) if attempts else None,
        "judgement_misses": sum(1 for o in outcomes if o.verdict == JUDGEMENT_MISS),
        "retrieval_misses": sum(1 for o in outcomes if o.verdict == RETRIEVAL_MISS),
        "false_positives": false_positives,
        "control_runs": sum(r["runs"] for r in negatives),
    }


def report(summary: dict) -> str:
    """Render the number that goes beside the injection detection rate."""
    rate = summary["miss_rate"]
    headline = (
        f"**{rate}% miss rate** over {summary['attempts']} attempt(s): "
        f"{summary['caught']} caught, {summary['attempts'] - summary['caught']} missed."
        if rate is not None
        else "**Not yet measured.** Run `python -m scripts.miss_rate`."
    )

    lines = [
        "# Miss rate on subtle contradictions",
        "",
        headline,
        "",
        "Of the misses, "
        f"{summary['judgement_misses']} were judgement — the passage was in the prompt and the "
        f"model reported nothing — and {summary['retrieval_misses']} were retrieval, where the "
        "contradicting passage never reached the prompt at all. The two have no repair in "
        "common.",
        "",
        f"On the control case, {summary['false_positives']} false positive(s) over "
        f"{summary['control_runs']} run(s).",
        "",
        "| Case | Technique | Expected | Runs | Caught | Verdicts |",
        "|---|---|---|---|---|---|",
    ]
    for row in summary["cases"]:
        expected = "contradiction" if row["must_flag"] else "no finding"
        lines.append(
            f"| {row['case_id']} | {row['technique']} | {expected} | {row['runs']} | "
            f"{row['caught']} | {', '.join(row['verdicts'])} |"
        )

    lines += [
        "",
        "Read this next to the injection-corpus detection rate. One says how much hostile "
        "content the screening boundary stops; this says how much quiet overstatement the "
        "reviewer behind it catches. Publishing the first without the second would be "
        "publishing the half there was more confidence about.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    # Every entry point adopts a tenant before it touches anything. Library code never
    # defaults one; a CLI does, and only outside cloud mode.
    with tenancy.acting_for(tenancy.cli_org()):
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
        parser.add_argument("--case", help="run one case by id")
        parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
        parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
        parser.add_argument(
            "--summarise-only",
            action="store_true",
            help="report what is already recorded and spend nothing",
        )
        args = parser.parse_args()

        logging.basicConfig(level=logging.INFO, format="[miss-rate] %(message)s")

        outcomes = (
            list(load(args.out))
            if args.summarise_only
            else measure(args.runs, args.out, only=args.case)
        )
        summary = summarise(outcomes)

        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report(summary), encoding="utf-8")
        print(report(summary))
        print(f"wrote {args.report}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
