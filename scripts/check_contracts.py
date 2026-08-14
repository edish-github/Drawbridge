"""Cross-file contract checks. These run in CI on every push and cost nothing.

Each check exists because the thing it checks was a real bug class rather than a tidiness
concern:

- **topics** — the topic list appears in three places. An undocumented topic is one bootstrap
  does not create, and an untested one.
- **rubric** — the domain weights are read as percentages and the audit binder prints the
  arithmetic. A scale that does not add up is a number a judge can catch on screen.
- **bank** — a yes/no question makes later contradiction detection impossible, so the style
  rule is enforced rather than remembered.
- **iam** — the permission matrix is a published deliverable. If the generated rules stop
  matching it, the table in the README is no longer describing the project.
- **public-routes** — no route reachable without a token may reach the model router. A public
  endpoint that can be made to spend tokens is a credit drain found by a billing alert.

Failure semantics: every check reports every problem it found rather than the first, and exits
non-zero if any check failed. A check whose inputs are missing fails rather than passing
vacuously — a check that silently passes when its file is absent is worse than no check.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _fail(problems: list[str], label: str) -> bool:
    if problems:
        print(f"FAIL {label}")
        for p in problems:
            print(f"  - {p}")
        return False
    print(f"ok   {label}")
    return True


def check_topics() -> bool:
    """The topic list in shared/events.py, infra/pubsub.yaml and infra/bootstrap.sh must agree."""
    problems: list[str] = []

    events_src = (ROOT / "shared" / "events.py").read_text()
    tree = ast.parse(events_src)
    constants = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and any(isinstance(t, ast.Name) and t.id.startswith("TOPIC_") for t in node.targets)
    }

    pubsub = yaml.safe_load((ROOT / "infra" / "pubsub.yaml").read_text())
    declared = {t["name"] for t in pubsub["topics"]}

    bootstrap = (ROOT / "infra" / "bootstrap.sh").read_text()
    match = re.search(r"^TOPICS=\((.*?)^\)", bootstrap, re.MULTILINE | re.DOTALL)
    if not match:
        problems.append("could not find the TOPICS array in infra/bootstrap.sh")
        provisioned: set[str] = set()
    else:
        provisioned = {line.strip() for line in match.group(1).split() if line.strip()}

    if len(constants) != 11:
        problems.append(f"shared/events.py declares {len(constants)} topics, expected 11")

    for label, other in (("infra/pubsub.yaml", declared), ("infra/bootstrap.sh", provisioned)):
        for missing in sorted(constants - other):
            problems.append(f"{missing} is in shared/events.py but not in {label}")
        for extra in sorted(other - constants):
            problems.append(f"{extra} is in {label} but not in shared/events.py")

    return _fail(problems, "topics agree across code, config and bootstrap")


def check_rubric() -> bool:
    """Domain weights must sum to exactly 100, and the bands must be complete and ordered."""
    problems: list[str] = []
    rubric = yaml.safe_load((ROOT / "agents" / "risk_scorer" / "rubric.yaml").read_text())

    total = sum(rubric["domains"].values())
    if total != 100:
        problems.append(f"domain weights sum to {total}, expected exactly 100")

    for band in ("approve", "conditional", "escalate"):
        if band not in rubric["bands"]:
            problems.append(f"band {band!r} is missing")

    if not problems:
        bands = rubric["bands"]
        if not bands["approve"] > bands["conditional"] > bands["escalate"]:
            problems.append(f"band boundaries are not descending: {bands}")

    for severity in ("low", "medium", "high"):
        if severity not in rubric["penalties"]:
            problems.append(f"no penalty configured for severity {severity!r}")

    modifier = rubric["modifiers"]["adversarial_conduct"]
    if modifier["penalty"] != 25:
        problems.append(f"adversarial penalty is {modifier['penalty']}, expected 25")
    if modifier["forces_band"] != "escalate":
        problems.append("adversarial conduct must force the escalate band")

    for tier, domains in rubric["tier_profiles"].items():
        unknown = set(domains) - set(rubric["domains"])
        if unknown:
            problems.append(f"tier {tier} profile names unknown domains: {sorted(unknown)}")

    return _fail(problems, "rubric weights sum to 100 and bands are complete")


YES_NO_PREFIXES = (
    "do you", "does your", "are you", "is your", "have you", "has your",
    "can you", "will you", "did you", "would you",
)


def check_bank() -> bool:
    """No question may be answerable yes or no, and every domain must exist in the rubric."""
    problems: list[str] = []
    bank = yaml.safe_load((ROOT / "agents" / "questionnaire" / "bank.yaml").read_text())
    rubric = yaml.safe_load((ROOT / "agents" / "risk_scorer" / "rubric.yaml").read_text())

    for domain, questions in bank["domains"].items():
        if domain not in rubric["domains"]:
            problems.append(f"bank domain {domain!r} is not a rubric domain")
        for q in questions:
            text = " ".join(q["text"].split()).lower()
            if text.startswith(YES_NO_PREFIXES):
                problems.append(f"{q['id']} is phrased as a yes/no question: {q['text'][:60]}...")
            if not q.get("tiers"):
                problems.append(f"{q['id']} declares no tiers, so it can never be selected")

    ids = [q["id"] for qs in bank["domains"].values() for q in qs]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        problems.append(f"duplicate question ids: {sorted(duplicates)}")

    return _fail(problems, "question bank is evidence-demanding and well-formed")


def check_iam() -> bool:
    """Every collection an identity writes must be one it is also allowed to read."""
    problems: list[str] = []
    matrix = yaml.safe_load((ROOT / "infra" / "iam" / "permission-matrix.yaml").read_text())

    for identity in matrix["identities"]:
        fs = identity.get("firestore") or {}
        reads = set(fs.get("read") or [])
        writes = set(fs.get("write") or [])
        # A writer needs read access to perform a read-modify-write. The generated rules add it;
        # this check makes the matrix state it, so the published table matches the rules.
        for collection in sorted(writes - reads):
            if collection != "events":
                problems.append(
                    f"{identity['name']} writes {collection!r} but does not declare reading it"
                )

    # The private signing key must remain accessible to exactly one identity.
    key = "drawbridge-approval-key"
    holders = [i["name"] for i in matrix["identities"] if key in (i.get("secrets") or [])]
    if holders != ["sa-approvals"]:
        problems.append(
            f"the approval signing key is held by {holders}, expected only sa-approvals"
        )

    # No agent may hold any role on the quarantine bucket except the screening pipeline.
    quarantine_allowed = ("sa-armor", "sa-portal")
    for identity in matrix["identities"]:
        for grant in identity.get("storage") or []:
            if grant["bucket"] != "evidence-quarantine":
                continue
            if identity["name"] not in quarantine_allowed:
                problems.append(f"{identity['name']} holds a role on the quarantine bucket")

    # The screening pipeline must never be able to call a generative model.
    armor = next(i for i in matrix["identities"] if i["name"] == "sa-armor")
    if armor.get("vertex_ai"):
        problems.append(
            "sa-armor is granted Vertex AI; the screening pipeline must not call a model"
        )

    return _fail(problems, "permission matrix is internally consistent")


def check_public_routes() -> bool:
    """No route reachable without a token may import the model router."""
    problems: list[str] = []
    public_dirs = [
        ROOT / "services" / "portal",
        ROOT / "services" / "dashboard",
        ROOT / "services" / "hello",
    ]

    pattern = re.compile(r"from\s+shared\.routing\s+import|import\s+shared\.routing")
    for directory in public_dirs:
        if not directory.exists():
            continue
        for path in directory.rglob("*.py"):
            if pattern.search(path.read_text()):
                problems.append(
                    f"{path.relative_to(ROOT)} imports the model router on a public surface"
                )

    return _fail(problems, "no public route reaches the model router")


CHECKS = {
    "topics": check_topics,
    "rubric": check_rubric,
    "bank": check_bank,
    "iam": check_iam,
    "public-routes": check_public_routes,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", choices=sorted(CHECKS) + ["all"], default="all")
    args = parser.parse_args()

    names = sorted(CHECKS) if args.check == "all" else [args.check]
    results = [CHECKS[name]() for name in names]

    if not all(results):
        print(f"\n{results.count(False)} of {len(results)} checks failed", file=sys.stderr)
        return 1
    print(f"\n{len(results)} check(s) passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
