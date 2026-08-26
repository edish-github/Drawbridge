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
- **graph** — the declared review graph in ``shared/graph.py`` is a description of the system,
  and a description nothing checks is a description that stops being true. This diffs it against
  the four places the topology actually lives: the subscriber's handler table, the event
  contract's state expectations, the plan step vocabulary and the permission matrix. It is the
  check that stops diagram 23 becoming the thing diagram 23 was originally deleted for being.

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

    # The count is checked against ALL_TOPICS rather than against a number written here, so
    # adding a topic constant without adding it to the tuple the worker subscribes from fails
    # rather than needing this line edited to match.
    from shared.events import ALL_TOPICS

    if len(constants) != len(ALL_TOPICS):
        problems.append(
            f"shared/events.py declares {len(constants)} TOPIC_ constants but ALL_TOPICS holds "
            f"{len(ALL_TOPICS)}; a topic nothing subscribes to is a topic that does not exist"
        )

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


APPEND_ONLY = frozenset({"events", "decisions"})
"""Ledgers written by many identities and read by none of them.

``events`` is the event ledger and ``decisions`` is what the binder's reasoning appendix is
rendered from. A trace nobody who writes it can read back is the only kind worth printing.
"""


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
            # Append-only ledgers are the exception: nothing appending to one ever reads it
            # back, and granting read to satisfy a symmetry rule would widen the grant for the
            # sake of the rule.
            if collection not in APPEND_ONLY:
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


# --- graph -----------------------------------------------------------------------------------
#
# The graph is data, so everything about it is checkable, and the four checks below are the four
# ways it could quietly stop describing the fleet:
#
#   a node consumes a topic nothing publishes, or nothing consumes a topic a node emits
#   a node claims to run in a state the event contract says its trigger never arrives in
#   a node names a plan step the planner cannot emit
#   a node's contract claims access its identity does not have — or denies access it does
#
# The last is the one worth having. A node contract that over-claims fails the first time real
# IAM is applied, which is late; a node contract that *under*-claims never fails at all, and the
# published permission matrix silently stops matching the graph a reader is being shown.

GRAPH_EXEMPT_IDENTITIES = frozenset({"any", "operator"})
"""Identities in the graph that are not service accounts.

Two, and both are real rather than convenient. ``needs_human`` is reachable from every node and
belongs to whichever identity parked the review, so it has no single matrix row to check against.
``intake`` runs as ``operator`` because no service account in the matrix can write the reviews
collection — reviews are opened today by ``scripts/open_review.py`` under a developer credential,
and the dashboard, which is the obvious hosted intake surface, deliberately holds no write path.
That is a gap in the deployment story rather than a gap in this check, and it is recorded on the
node so it cannot be forgotten.

Enumerated rather than pattern-matched, because the next node that quietly acquires an identity
outside the matrix should fail this check rather than join the exemption.
"""

SCHEDULER_TOPICS = frozenset({"review.chase_due", "watchdog.sweep"})
"""Topics with no in-graph publisher because a timer fires them.

Named individually rather than skipped by pattern: a third topic that nothing publishes is a
bug, and the way to keep that true is to make adding one to this set a deliberate edit.
"""


def check_graph() -> bool:
    """The declared review graph must agree with the code that executes it."""
    problems: list[str] = []

    from shared.events import ALL_TOPICS, EXPECTED_STATES
    from shared.graph import GRAPH, EdgeKind, GraphInvalid, NodeKind, validate

    try:
        validate()
    except GraphInvalid as exc:
        problems.append(str(exc))
        return _fail(problems, "the review graph matches the code that executes it")

    # --- topics ---------------------------------------------------------------------------
    for node in GRAPH.nodes:
        for topic in (*node.triggered_by, *node.emits):
            if topic not in ALL_TOPICS:
                problems.append(f"node {node.id} names {topic!r}, which is not a declared topic")

    from shared.subscriber import handlers

    consumed = {t for n in GRAPH.nodes for t in n.triggered_by}
    published = {t for n in GRAPH.nodes for t in n.emits}
    registered = set(handlers())

    for topic in sorted(registered - consumed):
        problems.append(
            f"{topic} has a registered handler but no node consumes it; the graph is missing a "
            "node or the handler is dead"
        )
    for topic in sorted(consumed - registered):
        problems.append(
            f"{topic} is consumed by a node but has no handler in shared.subscriber.handlers"
        )
    for topic in sorted(consumed - published - SCHEDULER_TOPICS):
        problems.append(f"{topic} is consumed by a node and emitted by none")

    # --- states ---------------------------------------------------------------------------
    # Checked against the *union* over a node's triggers, not against each one. A node with two
    # triggers legitimately arrives in the union of their in-phase states: the scorer is reached
    # by review.findings_ready from EVIDENCE_REVIEW and by review.rescore from SCORED, and
    # requiring every state to be valid for every trigger would reject a correct declaration.
    # The second loop is what stops the union from hiding a trigger that can never fire here.
    for node in GRAPH.nodes:
        if not node.triggered_by:
            continue
        union: set = set()
        for topic in node.triggered_by:
            union |= EXPECTED_STATES.get(topic, set())
        stray = sorted(str(s) for s in node.arrives_in - union)
        if stray:
            problems.append(
                f"node {node.id} declares state(s) {stray} that none of its triggers "
                f"{list(node.triggered_by)} ever arrive in"
            )
        for topic in node.triggered_by:
            expected = EXPECTED_STATES.get(topic)
            if expected is not None and node.arrives_in and not (node.arrives_in & expected):
                problems.append(
                    f"node {node.id} is triggered by {topic}, which never arrives in any state "
                    f"the node declares running in"
                )

    # --- declared advances must be legal transitions ----------------------------------------
    #
    # The graph and the state machine are two descriptions of the same movement, and this is the
    # seam between them. A node that says it advances a review somewhere the transition table
    # forbids is a picture of a system that would raise InvalidTransition the first time it ran.
    from shared.domain import ALLOWED

    for node in GRAPH.nodes:
        if node.advances_to is None:
            continue
        for origin in node.arrives_in:
            if node.advances_to is origin:
                continue
            if node.advances_to not in ALLOWED.get(origin, set()):
                problems.append(
                    f"node {node.id} declares it advances {origin} -> {node.advances_to}, which "
                    "the transition table forbids"
                )

    # --- plan steps and checkpoints --------------------------------------------------------
    from agents.orchestrator.planner import STEP_VOCABULARY

    for node in GRAPH.nodes:
        if node.plan_step and node.plan_step not in STEP_VOCABULARY:
            problems.append(
                f"node {node.id} names plan step {node.plan_step!r}, which is not in "
                "STEP_VOCABULARY and so can never appear in a plan"
            )

    checkpoints = _checkpoint_constants()
    for node in GRAPH.nodes:
        if node.checkpoint and node.checkpoint not in checkpoints:
            problems.append(
                f"node {node.id} declares checkpoint {node.checkpoint!r}, which no agent module "
                f"records. Known checkpoints: {sorted(checkpoints)}"
            )

    # --- node contracts against the permission matrix --------------------------------------
    matrix = yaml.safe_load((ROOT / "infra" / "iam" / "permission-matrix.yaml").read_text())
    by_name = {i["name"]: i for i in matrix["identities"]}

    for node in GRAPH.nodes:
        if node.identity in GRAPH_EXEMPT_IDENTITIES:
            continue
        identity = by_name.get(node.identity)
        if identity is None:
            problems.append(f"node {node.id} runs as {node.identity!r}, which has no matrix row")
            continue

        fs = identity.get("firestore") or {}
        granted_reads = set(fs.get("read") or []) | set(fs.get("write") or [])
        granted_writes = set(fs.get("write") or [])

        for collection in node.contract.writes:
            if collection not in granted_writes:
                problems.append(
                    f"node {node.id} declares writing {collection!r}, which {node.identity} "
                    "cannot write"
                )
        for collection in node.contract.reads:
            if collection not in granted_reads:
                problems.append(
                    f"node {node.id} declares reading {collection!r}, which {node.identity} "
                    "cannot read"
                )

        # A forbidden capability naming a collection must genuinely be ungranted. This catches
        # the contract that still says "no findings write" after somebody added one.
        for denial in node.contract.forbidden:
            collection, _, verb = denial.partition(" ")
            if verb == "write" and collection in granted_writes:
                problems.append(
                    f"node {node.id} forbids {denial!r} but {node.identity} is granted it"
                )

        if node.contract.model and not identity.get("vertex_ai"):
            problems.append(
                f"node {node.id} declares a {node.contract.model} model but {node.identity} "
                "holds no Vertex AI grant"
            )

    # --- joins have readers -----------------------------------------------------------------
    from shared.join import _READERS

    for join in GRAPH.joins:
        for arm in join.arms:
            if arm.name not in _READERS:
                problems.append(
                    f"join {join.id} waits on arm {arm.name!r} that shared.join cannot read"
                )

    # --- event edges carry a topic ----------------------------------------------------------
    for edge in GRAPH.edges:
        if edge.kind is EdgeKind.EVENT and edge.trigger not in ALL_TOPICS:
            problems.append(
                f"event edge {edge.source}->{edge.target} carries {edge.trigger!r}, which is "
                "not a declared topic"
            )

    # --- the node ids agents refer to are declared -------------------------------------------
    for path, name, value in _node_constants():
        if not GRAPH.has(value):
            problems.append(f"{path} defines {name} = {value!r}, which is not a graph node")

    # --- every agent identity owns at least one node -----------------------------------------
    owners = {n.identity for n in GRAPH.nodes}
    for name in ("sa-orchestrator", "sa-questionnaire", "sa-evidence", "sa-scorer", "sa-watchdog"):
        if name not in owners:
            problems.append(f"{name} owns no node; an agent absent from the graph is not drawn")

    kinds = {n.kind for n in GRAPH.nodes}
    for required in (NodeKind.ROUTER, NodeKind.JOIN, NodeKind.GATE):
        if required not in kinds:
            problems.append(f"the graph declares no {required} node")

    return _fail(problems, "the review graph matches the code that executes it")


def _checkpoint_constants() -> set[str]:
    """Checkpoint names recorded anywhere in ``agents/``.

    Read out of the source rather than imported, so this check does not depend on every agent
    module being importable — a broken agent should fail as a broken agent, not as a graph
    problem.
    """
    found: set[str] = set()
    for path in (ROOT / "agents").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith(("STEP_", "SEND_STEP")):
                    found.add(node.value.value)
    return found


def _node_constants():
    """Every ``NODE_x = "y"`` in ``agents/``, as (path, name, value)."""
    for path in (ROOT / "agents").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("NODE_"):
                    yield str(path.relative_to(ROOT)), target.id, node.value.value


CHECKS = {
    "topics": check_topics,
    "rubric": check_rubric,
    "bank": check_bank,
    "iam": check_iam,
    "public-routes": check_public_routes,
    "graph": check_graph,
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
