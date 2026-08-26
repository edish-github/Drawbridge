"""Export the policy the fleet actually runs on, for the console's Settings screen.

Settings in most consoles is a page of toggles that write somewhere. This one writes nowhere,
and the reason is the same reason the dashboard has no approve button: a surface every reviewer
can reach must not be able to change how reviews are scored. What the screen shows instead is
the policy **as configured**, read from the files that configure it — the rubric the Trust Score
is arithmetic over, the permission matrix the generated Firestore rules come from, and the three
gateway policies with the allowlist one of them enforces.

Generated rather than restated in TypeScript, for the same reason ``graph.json`` is: the
alternative is maintaining the rubric's weights in two languages and finding out on screen that
they disagree. ``tests/test_console.py`` regenerates and diffs, so a stale copy fails CI.

Failure semantics: a missing or unparseable source file raises rather than emitting a partial
document. A Settings screen showing three of four policies, with no indication that a fourth
exists, is worse than one that fails to build.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "services" / "dashboard" / "lib" / "policy.json"

RUBRIC = ROOT / "agents" / "risk_scorer" / "rubric.yaml"
MATRIX = ROOT / "infra" / "iam" / "permission-matrix.yaml"
BANK = ROOT / "agents" / "questionnaire" / "bank.yaml"
PUBSUB = ROOT / "infra" / "pubsub.yaml"


def build() -> dict:
    """Assemble the policy document from the files that are the authority on each part."""
    rubric = yaml.safe_load(RUBRIC.read_text())
    matrix = yaml.safe_load(MATRIX.read_text())
    bank = yaml.safe_load(BANK.read_text())
    pubsub = yaml.safe_load(PUBSUB.read_text())

    from agents.orchestrator.planner import STEP_VOCABULARY
    from agents.questionnaire.parser import COVERAGE_TO_PROCEED
    from shared.armor import (
        CRITICAL_FILTERS,
        EXECUTION_SUCCESS,
        MATCH_FOUND,
        UNTRUSTED_TEMPLATES,
    )
    from shared.events import ALL_TOPICS, MAX_DELIVERY_ATTEMPTS
    from shared.gateway import DEEP_MODEL_TOOLS, FEED_ALLOWLIST, MODEL_INPUT_TOOLS
    from shared.graph import GRAPH

    questions = {
        domain: len(items) for domain, items in (bank.get("domains") or {}).items()
    }

    return {
        "scoring": {
            "scale_max": rubric["scale_max"],
            "domains": rubric["domains"],
            "penalties": rubric["penalties"],
            "bands": rubric["bands"],
            "tier_profiles": {str(k): v for k, v in rubric["tier_profiles"].items()},
            "modifiers": rubric["modifiers"],
            # Stated rather than left to be inferred from the absence of a multiplier field.
            "contradiction_multiplier": None,
        },
        "questionnaire": {
            "coverage_to_proceed": COVERAGE_TO_PROCEED,
            "questions_by_domain": questions,
            "total_questions": sum(questions.values()),
        },
        "gateway": {
            "policies": [
                {
                    "id": "P1",
                    "name": "No outbound contact without a human approval",
                    "detail": (
                        "No outbound email to a new contact without a valid, single-use, scoped "
                        "approval token. Verification only: this process holds the public key and "
                        "has no signing capability at any point in its life."
                    ),
                },
                {
                    "id": "P2",
                    "name": "No external content reaches a model unscreened",
                    "detail": (
                        "Enforced in shared/routing.py rather than at the tool gateway, because "
                        "the router is the only path to a model. Verdict-aware: sanitised content "
                        "is admissible to the Evidence agent and inadmissible to the memo."
                    ),
                },
                {
                    "id": "P3",
                    "name": "Outbound fetch is bounded by an allowlist",
                    "detail": (
                        "Four hosts, named individually rather than by wildcard. An agent with "
                        "unbounded egress is an exfiltration channel and an SSRF surface."
                    ),
                },
            ],
            "feed_allowlist": sorted(FEED_ALLOWLIST),
            "model_input_tools": sorted(MODEL_INPUT_TOOLS),
            "deep_model_tools": sorted(DEEP_MODEL_TOOLS),
        },
        "screening": {
            # The console decides what verdict to print for a document. These are the values the
            # decision actually turns on, exported rather than restated in TypeScript: a console
            # that hardcoded "local-seed" would keep calling a fixture trustworthy on the day the
            # constant changed.
            "critical_filters": sorted(CRITICAL_FILTERS),
            "untrusted_templates": sorted(UNTRUSTED_TEMPLATES),
            "execution_success": EXECUTION_SUCCESS,
            "match_found": MATCH_FOUND,
        },
        "identities": [
            {
                "name": i["name"],
                "display": i.get("display", i["name"]),
                "purpose": " ".join(str(i.get("purpose", "")).split()),
                "reads": sorted((i.get("firestore") or {}).get("read") or []),
                "writes": sorted((i.get("firestore") or {}).get("write") or []),
                "never": i.get("never") or [],
                "vertex_ai": bool(i.get("vertex_ai")),
                "secrets": i.get("secrets") or [],
            }
            for i in matrix["identities"]
        ],
        "events": {
            "topics": list(ALL_TOPICS),
            "max_delivery_attempts": MAX_DELIVERY_ATTEMPTS,
            "ack_deadline_seconds": pubsub["defaults"]["ack_deadline_seconds"],
            "out_of_phase": [
                {
                    "event": o["event"],
                    "condition": o["condition"],
                    "behaviour": " ".join(str(o["behaviour"]).split()),
                }
                for o in pubsub.get("out_of_phase", [])
            ],
        },
        "graph": {
            "nodes": len(GRAPH.nodes),
            "edges": len(GRAPH.edges),
            "routers": len(GRAPH.routers),
            "joins": len(GRAPH.joins),
            "cycles": len(GRAPH.cycles),
            "step_vocabulary": list(STEP_VOCABULARY),
        },
        "collections": sorted(
            {
                c
                for i in matrix["identities"]
                for key in ("read", "write")
                for c in ((i.get("firestore") or {}).get(key) or [])
            }
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
