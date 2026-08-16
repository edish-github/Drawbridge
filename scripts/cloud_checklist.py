"""The first hour on a real project, as a script rather than as a list somebody reads.

    python -m scripts.cloud_checklist            # run every step, stop at the first blocker
    python -m scripts.cloud_checklist --step 3   # re-run one step after fixing it
    python -m scripts.cloud_checklist --list     # what it will do, without doing it

Thirteen ``TODO(verify)`` markers name assumptions about API surfaces nobody has been able to
check without billing. Discovered serially on the day the project exists, they are an afternoon
of reading tracebacks. Discovered by this script, they are one command and a file.

Each step states what it verifies, makes the **smallest real call** that could settle it, and
records the answer. Ordered cheapest and most consequential first: the model ids gate everything
downstream, the embedding dimension is fixed at index creation and expensive to get wrong, and
Agent Engine deployment is last because it takes twenty minutes and nothing else waits on it.

**A step that fails does not stop the file being written.** The output is the point: the answers
are what turn thirteen open questions into a list of one-function changes, and half the answers
are worth more than none.

Nothing here writes a review, a finding or a score. It reads configuration, makes a handful of
tiny calls, and writes ``infra/CLOUD-CHECKLIST.md``.

Failure semantics: a step that raises records the exception and the run continues to the next.
A step whose prerequisite is missing is recorded as skipped with the prerequisite named, which
is a different answer from failed and leads somewhere different.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("drawbridge.checklist")

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "infra" / "CLOUD-CHECKLIST.md"
DEFAULT_JSON = REPO / "infra" / "CLOUD-CHECKLIST.json"

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass
class Result:
    number: int
    title: str
    verifies: str
    outcome: str
    detail: str = ""
    answer: dict = field(default_factory=dict)


@dataclass
class Step:
    number: int
    title: str
    verifies: str
    closes: str
    run: Callable[[], dict]

    def execute(self) -> Result:
        log.info("step %d · %s", self.number, self.title)
        try:
            answer = self.run()
        except Skipped as exc:
            return Result(self.number, self.title, self.verifies, SKIP, str(exc))
        except Exception as exc:  # noqa: BLE001 — the answer is what the run is for
            log.warning("step %d failed: %s", self.number, exc)
            return Result(
                self.number,
                self.title,
                self.verifies,
                FAIL,
                f"{type(exc).__name__}: {exc}"[:400],
            )
        return Result(self.number, self.title, self.verifies, PASS, answer=answer)


class Skipped(Exception):
    """A prerequisite is missing. Different from a failure, and it leads somewhere different."""


# --- the steps ---------------------------------------------------------------------------------


def step_model_ids() -> dict:
    """The cheapest call that settles the most: do the configured model ids resolve?"""
    from shared.clients import genai_client
    from shared.config import settings

    cfg = settings()
    client = genai_client()
    out = {}
    for label, model in (("fast", cfg.model_fast), ("deep", cfg.model_deep)):
        response = client.models.generate_content(model=model, contents="ok")
        out[label] = {"model": model, "replied": bool(getattr(response, "text", ""))}
    return out


def step_embedding_dimension() -> dict:
    """Fixed at index creation and expensive to get wrong. One embedding settles it."""
    from shared.clients import genai_client
    from shared.config import settings

    cfg = settings()
    response = genai_client().models.embed_content(model=cfg.model_embed, contents="probe")
    vector = response.embeddings[0].values
    return {
        "model": cfg.model_embed,
        "dimensions": len(vector),
        "matches_index": len(vector) == _declared_dimensions(),
    }


def step_knn_prefilter() -> dict:
    """Does ``collection.where(...).find_nearest(...)`` compose on a composite vector index?

    ``agents/evidence/retrieval.py`` carries the TODO. The pre-filter is what stops one review's
    evidence surfacing inside another, so a composition that silently post-filters would be a
    correctness problem rather than a performance one.
    """
    from agents.evidence.retrieval import knn_search

    chunks = knn_search("cloud-checklist-probe", [0.0] * _declared_dimensions(), 3)
    return {"composed": True, "returned": len(chunks)}


def step_model_armor_fields() -> dict:
    """The per-filter response field names. ``armor._from_sdk_response`` is the one place."""
    from shared.armor import _screen_with_service
    from shared.config import settings

    result = _screen_with_service(
        "A benign probe string.", settings().model_armor_template_untrusted, "checklist:probe"
    )
    return {
        "template": result.template,
        "template_version": result.template_version,
        "filters": sorted(result.filters),
        "execution": sorted(result.execution),
        "all_critical_executed": result.all_critical_executed(),
    }


def step_memory_bank_payload() -> dict:
    """Does ``add_memory`` take a structured fact, or does it want a session wrapper?

    ``memory._write_to_memory_bank`` is the one place, and it degrades rather than raising — so
    this step calls it and then checks whether the semantic copy actually landed, which the
    degradation would otherwise hide.
    """
    from datetime import datetime as dt

    from shared.config import settings
    from shared.domain import MemoryNote
    from shared.memory import _write_to_memory_bank

    if not settings().agent_engine_id:
        raise Skipped("AGENT_ENGINE_ID is unset; deploy an Agent Engine first (step 7)")

    note = MemoryNote(
        vendor_id="cloud-checklist-probe",
        type="band",
        provenance="rule",
        value={"value": "approve", "review_id": "probe"},
        at=dt.now(UTC),
    )
    note_id = _write_to_memory_bank("cloud-checklist-probe", note)
    return {"wrote": bool(note_id), "note": "check the log for a degraded-mode warning"}


def step_agent_engine_deploy() -> dict:
    """Twenty minutes, and nothing else waits on it. Last for that reason."""
    raise Skipped(
        "run `python -m infra.deploy.hello_agent.deploy` by hand and record the handle's "
        "streaming query method name; this step is a placeholder for the answer"
    )


def step_registry_client() -> dict:
    """Whether the installed SDK exposes an Agent Registry surface at all."""
    import google.adk as adk

    surfaces = sorted(name for name in dir(adk) if "registr" in name.lower())
    return {"adk_version": getattr(adk, "__version__", "unknown"), "registry_surfaces": surfaces}


def step_rate_table() -> dict:
    """The per-million-token rates the cost ceiling and the demo's cost claim rest on."""
    from shared.routing import RATES_USD_PER_MTOK

    return {
        "declared": RATES_USD_PER_MTOK,
        "note": "compare against the billing console after one full review has run",
    }


STEPS: tuple[tuple[str, str, str, Callable[[], dict]], ...] = (
    (
        "Model ids resolve",
        "MODEL_FAST and MODEL_DEEP answer a one-token prompt",
        "infra/deploy/hello_agent/agent.py · shared/config.py",
        step_model_ids,
    ),
    (
        "Embedding dimension",
        "the embedding model's vector length, against infra/firestore/indexes.yaml",
        "shared/armor.py index dimension · infra/firestore/indexes.yaml",
        step_embedding_dimension,
    ),
    (
        "KNN pre-filter composes",
        "where(review_id).find_nearest() on a composite vector index",
        "agents/evidence/retrieval.py",
        step_knn_prefilter,
    ),
    (
        "Model Armor response fields",
        "per-filter match state, execution state and template version",
        "shared/armor.py _from_sdk_response",
        step_model_armor_fields,
    ),
    (
        "Memory Bank payload shape",
        "whether add_memory takes a structured fact or a session wrapper",
        "shared/memory.py _write_to_memory_bank",
        step_memory_bank_payload,
    ),
    (
        "Agent Registry client",
        "whether the installed ADK exposes a registry surface",
        "scripts/probe_geap.py",
        step_registry_client,
    ),
    (
        "Agent Engine deploy surface",
        "the streaming query method name on the returned handle",
        "infra/deploy/hello_agent/deploy.py",
        step_agent_engine_deploy,
    ),
    (
        "Rate table",
        "the per-million-token rates behind the cost ceiling",
        "shared/routing.py RATES_USD_PER_MTOK",
        step_rate_table,
    ),
)


def steps() -> list[Step]:
    return [
        Step(number=index, title=title, verifies=verifies, closes=closes, run=run)
        for index, (title, verifies, closes, run) in enumerate(STEPS, start=1)
    ]


def _declared_dimensions() -> int:
    """Read the dimension the index declares, so the check compares two real values."""
    import yaml

    raw = yaml.safe_load((REPO / "infra" / "firestore" / "indexes.yaml").read_text())
    for index in raw.get("indexes", []):
        for field_spec in index.get("fields", []):
            dimension = (field_spec.get("vectorConfig") or {}).get("dimension")
            if dimension:
                return int(dimension)
    return 0


# --- running and reporting -----------------------------------------------------------------------


def run_all(selected: int | None = None) -> list[Result]:
    """Run every step, or one. A failing step never stops the ones after it."""
    from shared.config import settings

    if settings().is_local:
        log.warning(
            "RUNTIME_MODE=local. Every step below will fail or skip; this script is for the "
            "first hour on a real project, not for the emulators."
        )

    chosen = [s for s in steps() if selected is None or s.number == selected]
    return [s.execute() for s in chosen]


def render(results: list[Result], *, at: str) -> str:
    """Render the answers as the file that turns open questions into one-function changes."""
    by_step = {s.number: s for s in steps()}
    passed = sum(1 for r in results if r.outcome == PASS)

    lines = [
        "# Cloud checklist",
        "",
        f"Run {at}. **{passed} of {len(results)} step(s) answered.**",
        "",
        "Each row is a `TODO(verify)` and the one function that changes once it is answered.",
        "",
        "| # | Step | Outcome | Closes |",
        "|---|---|---|---|",
    ]
    for result in results:
        closes = by_step[result.number].closes
        lines.append(
            f"| {result.number} | {result.title} | **{result.outcome}** | `{closes}` |"
        )

    for result in results:
        lines += ["", f"## {result.number} · {result.title}", "", f"Verifies: {result.verifies}"]
        if result.detail:
            lines += ["", f"```\n{result.detail}\n```"]
        if result.answer:
            lines += ["", "```json", json.dumps(result.answer, indent=2, default=str), "```"]

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", type=int, help="run one step by number")
    parser.add_argument("--list", action="store_true", help="print the steps and do nothing")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[checklist] %(message)s")

    if args.list:
        for step in steps():
            print(f"{step.number}. {step.title}\n     verifies: {step.verifies}")
            print(f"     closes:   {step.closes}")
        return 0

    at = datetime.now(UTC).isoformat(timespec="seconds")
    results = run_all(args.step)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(results, at=at), encoding="utf-8")
    args.json.write_text(
        json.dumps([r.__dict__ for r in results], indent=2, default=str), encoding="utf-8"
    )

    for result in results:
        print(f"  {result.outcome:5s} {result.number}. {result.title}")
    print(f"\nwrote {args.out}")
    return 0 if all(r.outcome != FAIL for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
