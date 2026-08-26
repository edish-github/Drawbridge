"""Render the declared review graph, or one review's path through it.

``docs/diagrams/README.md`` used to say diagram 23 was deliberately absent, and the reason it
gave was right at the time: the diagram had been specified as a dump of an ADK graph workflow,
the Orchestrator is not one, and *a placeholder for a picture the repository cannot produce is
the kind of thing this set exists to not have*. What changed is not the architecture — there is
still no graph runner and the review's position is still a checkpointed row and an unacked
message. What changed is that the topology is now written down as data in ``shared/graph.py``,
so there is something to dump, and the picture is generated from the same object the contract
checks are run against rather than drawn beside it.

Three outputs, and the third is the one that earns the module:

``--format mermaid``  the graph as ``docs/diagrams/src/23-review-graph.mmd``
``--format json``     the graph as data — this is what the dashboard's graph view reads
``--review <id>``     one review's *actual* path, projected from the ledger

The last is what makes "why did the system reach this conclusion" answerable in a form somebody
can check. It colours the nodes that ran, marks the one that failed, and lists the edges taken
with the event that carried each — reconstructed from records written for other purposes, so it
works on reviews that finished before this script existed.

Failure semantics: a review id that does not exist exits non-zero with the id in the message
rather than printing an empty graph, because an all-pending projection and a review that was
never opened look identical and mean opposite things.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shared.graph import GRAPH, EdgeKind, Graph, NodeKind

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MMD = ROOT / "docs" / "diagrams" / "src" / "23-review-graph.mmd"
DEFAULT_JSON = ROOT / "services" / "dashboard" / "lib" / "graph.json"
"""Where the dashboard reads the topology from.

Generated rather than hand-kept, because the dashboard is TypeScript and ``shared/graph.py`` is
Python, and the alternative to generating it is declaring the graph twice in two languages. The
file is committed so ``npm run dev`` needs no Python step, and ``make graph`` rewrites it; a
stale copy is caught by ``tests/test_graph_ui.py``, which regenerates and diffs.
"""

_SHAPE = {
    NodeKind.EVENT: ("([", "])"),
    NodeKind.ROUTER: ("{{", "}}"),
    NodeKind.AGENT: ("[", "]"),
    NodeKind.DETERMINISTIC: ("[/", "/]"),
    NodeKind.JOIN: ("[(", ")]"),
    NodeKind.GATE: ("{", "}"),
    NodeKind.TERMINAL: ("((", "))"),
}
"""One Mermaid shape per node kind, so the kind is readable without the legend.

Deliberately not one colour per agent. The thing a reader needs to tell apart at a glance is
*what kind of thing decides here* — a model, arithmetic, a branch, a person — and colouring by
service would group the deterministic scorer with the deep-model memo because they deploy
together.
"""

_ARROW = {
    EdgeKind.EVENT: "-->",
    EdgeKind.INLINE: "-.->",
    EdgeKind.BRANCH: "-->",
    EdgeKind.FAILURE: "-..->",
    EdgeKind.CYCLE: "==>",
}

_BAND = (
    ("Intake and planning", ("intake", "recall", "tier_router", "plan")),
    (
        "The questionnaire thread",
        ("contact_gate", "questionnaire_send", "reply", "reply_parse", "followup", "chase",
         "retier_router", "coverage_join"),
    ),
    (
        "The evidence pipeline",
        ("upload", "screening", "index", "extract", "checks", "cross_exam", "subprocessors",
         "findings_join"),
    ),
    ("Scoring and the decision", ("score", "memo", "decision_gate", "decide")),
    ("Monitoring", ("monitor", "relevance_router", "rereview")),
    ("Parked", ("needs_human",)),
)
"""Reading order. Mermaid lays a graph out by edges, which produces a correct picture and an
unreadable one on a graph this size; subgraphs give it the five bands a person reads in."""


def mermaid(graph: Graph = GRAPH, *, run=None, failure_edges: bool = False) -> str:
    """Render the graph as Mermaid. With ``run``, colour it by what actually happened.

    ``failure_edges`` is off by default and that is a cartographic decision rather than a
    tidying one. Every node with a park policy has an edge into ``needs_human``, so drawing
    them produces a dozen identical lines crossing every band, and the reader's eye spends its
    budget on the one property that is *uniform* across the graph instead of on the structure
    that is not. The property is stated on the diagram instead, where it is easier to check than
    a dozen arrows are: a note saying every node parks is falsified by one node that cannot,
    which is exactly the failure a reader should be able to catch.

    ``--failure-edges`` draws them anyway, for the times the question is specifically about the
    failure topology.
    """
    title = "Drawbridge — the review graph"
    if run is not None:
        title = f"Drawbridge — review {run.review_id} through the graph"

    lines = [
        "---",
        f'title: "{title}"',
        "---",
        "flowchart TB",
    ]

    placed: set[str] = set()
    for band, members in _BAND:
        present = [m for m in members if graph.has(m)]
        if not present:
            continue
        lines.append(f'  subgraph {_slug(band)}["{band}"]')
        lines.append("    direction TB")
        for node_id in present:
            lines.append("    " + _node_line(graph.node(node_id), run))
            placed.add(node_id)
        lines.append("  end")

    for node in graph.nodes:
        if node.id not in placed:
            lines.append("  " + _node_line(node, run))

    lines.append("")
    parks = 0
    for edge in graph.edges:
        if edge.kind is EdgeKind.FAILURE and not failure_edges:
            parks += 1
            continue
        label = edge.label or edge.trigger or ""
        arrow = _ARROW[edge.kind]
        if label:
            lines.append(f'  {edge.source} {arrow}|"{label}"| {edge.target}')
        else:
            lines.append(f"  {edge.source} {arrow} {edge.target}")

    if parks:
        lines.append("")
        lines.append(
            f'  parks["<b>{parks} park edges not drawn</b><br/>every node above with a failure '
            "policy of <i>park</i> has an edge<br/>into Parked, carrying the reason it "
            'stalled.<br/>Draw them with --failure-edges."]'
        )
        lines.append("  parks -..-> needs_human")

    lines.append("")
    lines += _classes(graph, run)
    if parks:
        lines.append("  classDef legend fill:#ffffff,stroke:#94a3b8,stroke-width:1px,color:#475569")
        lines.append("  class parks legend")
    return "\n".join(lines) + "\n"


def _node_line(node, run) -> str:
    open_shape, close_shape = _SHAPE[node.kind]
    label = f"<b>{node.label}</b><br/>{node.identity}"
    if run is not None:
        status = run.status(node.id)
        label = f"<b>{node.label}</b><br/>{status}"
        detail = run.runs.get(node.id)
        if detail is not None and detail.duration_ms is not None:
            label += f" · {detail.duration_ms} ms"
    return f"{node.id}{open_shape}\"{label}\"{close_shape}"


def _classes(graph: Graph, run) -> list[str]:
    """Class definitions and assignments, by node kind or by run status."""
    if run is None:
        out = [
            "  classDef agent fill:#dbeafe,stroke:#1d4ed8,stroke-width:2px,color:#1e3a8a",
            "  classDef det fill:#e2e8f0,stroke:#334155,stroke-width:2px,color:#0f172a",
            "  classDef router fill:#ede9fe,stroke:#6d28d9,stroke-width:2px,color:#4c1d95",
            "  classDef join fill:#ccfbf1,stroke:#0f766e,stroke-width:2px,color:#134e4a",
            "  classDef gate fill:#ffedd5,stroke:#c2410c,stroke-width:2.5px,color:#7c2d12",
            "  classDef evt fill:#fef3c7,stroke:#b45309,stroke-width:2px,color:#78350f",
            "  classDef term fill:#fee2e2,stroke:#b91c1c,stroke-width:2px,color:#7f1d1d",
        ]
        by_kind = {
            NodeKind.AGENT: "agent",
            NodeKind.DETERMINISTIC: "det",
            NodeKind.ROUTER: "router",
            NodeKind.JOIN: "join",
            NodeKind.GATE: "gate",
            NodeKind.EVENT: "evt",
            NodeKind.TERMINAL: "term",
        }
        for kind, name in by_kind.items():
            members = [n.id for n in graph.of_kind(kind)]
            if members:
                out.append(f"  class {','.join(members)} {name}")
        return out

    out = [
        "  classDef complete fill:#dcfce7,stroke:#15803d,stroke-width:2px,color:#14532d",
        "  classDef running fill:#dbeafe,stroke:#1d4ed8,stroke-width:2.5px,color:#1e3a8a",
        "  classDef waiting fill:#ffedd5,stroke:#c2410c,stroke-width:2.5px,color:#7c2d12",
        "  classDef failed fill:#fee2e2,stroke:#b91c1c,stroke-width:3px,color:#7f1d1d",
        "  classDef pending fill:#f8fafc,stroke:#cbd5e1,stroke-width:1px,color:#94a3b8",
        "  classDef unknown fill:#fef9c3,stroke:#a16207,stroke-width:2px,color:#713f12",
    ]
    buckets: dict[str, list[str]] = {}
    for node in graph.nodes:
        status = str(run.status(node.id))
        buckets.setdefault(status if status != "skipped" else "pending", []).append(node.id)
    for status, members in buckets.items():
        out.append(f"  class {','.join(members)} {status}")
    return out


def as_json(graph: Graph = GRAPH) -> dict:
    """The graph as plain data. Every field a diagram or a checker would want, and no callables."""
    return {
        "nodes": [
            {
                "id": n.id,
                "kind": str(n.kind),
                "label": n.label,
                "identity": n.identity,
                "checkpoint": n.checkpoint,
                "plan_step": n.plan_step,
                "triggered_by": list(n.triggered_by),
                "emits": list(n.emits),
                "arrives_in": sorted(str(s) for s in n.arrives_in),
                "advances_to": str(n.advances_to) if n.advances_to else None,
                "observed_by": list(n.observed_by),
                "note": n.note,
                "contract": {
                    "inputs": list(n.contract.inputs),
                    "outputs": list(n.contract.outputs),
                    "reads": list(n.contract.reads),
                    "writes": list(n.contract.writes),
                    "forbidden": list(n.contract.forbidden),
                    "model": n.contract.model,
                },
                "failure": None
                if n.failure is None
                else {
                    "max_attempts": n.failure.max_attempts,
                    "on_exhaustion": n.failure.on_exhaustion,
                    "park_reason": n.failure.park_reason,
                    "degraded_to": n.failure.degraded_to,
                },
            }
            for n in graph.nodes
        ],
        "edges": [
            {
                "source": e.source,
                "target": e.target,
                "kind": str(e.kind),
                "trigger": e.trigger,
                "guard": e.guard,
                "label": e.label,
            }
            for e in graph.edges
        ],
        "routers": [
            {
                "id": r.id,
                "node": r.node,
                "monotonic": r.monotonic,
                "branches": [
                    {"to": b.to, "when": b.when, "decided_by": b.decided_by} for b in r.branches
                ],
            }
            for r in graph.routers
        ],
        "joins": [
            {
                "id": j.id,
                "node": j.node,
                "mode": str(j.mode),
                "threshold": j.threshold,
                "quorum": j.quorum,
                "override": j.override,
                "on_shortfall": j.on_shortfall,
                "arms": [
                    {"name": a.name, "required": a.required, "describes": a.describes}
                    for a in j.arms
                ],
            }
            for j in graph.joins
        ],
        "cycles": [
            {
                "id": c.id,
                "through": list(c.through),
                "max_iterations": c.max_iterations,
                "counted_by": c.counted_by,
                "on_exhaustion": c.on_exhaustion,
                "bounded_by": c.bounded_by,
            }
            for c in graph.cycles
        ],
    }


def text(graph: Graph = GRAPH) -> str:
    """A terminal rendering, for the times a picture is more effort than the answer is worth."""
    out: list[str] = [f"{len(graph.nodes)} nodes, {len(graph.edges)} edges", ""]
    for band, members in _BAND:
        out.append(band)
        for node_id in members:
            if not graph.has(node_id):
                continue
            node = graph.node(node_id)
            model = f" · {node.contract.model} model" if node.contract.model else ""
            out.append(f"  {node.id:<20} {str(node.kind):<14} {node.identity}{model}")
        out.append("")
    out.append("Routers")
    for r in graph.routers:
        out.append(f"  {r.id} on {r.node} — {r.monotonic}")
        for b in r.branches:
            out.append(f"    -> {b.to:<20} when {b.when}")
    out.append("")
    out.append("Joins")
    for j in graph.joins:
        detail = f"threshold {j.threshold}" if j.threshold else str(j.mode)
        out.append(f"  {j.id} on {j.node} — {detail}, shortfall: {j.on_shortfall}")
        for a in j.arms:
            out.append(f"    {'required' if a.required else 'optional':<9} {a.name}")
    out.append("")
    out.append("Cycles")
    for c in graph.cycles:
        out.append(f"  {c.id} — max {c.max_iterations}, counted by {c.counted_by}")
        out.append(f"    bound: {c.bounded_by}")
        out.append(f"    spent: {c.on_exhaustion}")
    return "\n".join(out)


def run_text(run) -> str:
    """One review's path, as a table plus the edges it took."""
    out = [
        f"review {run.review_id} · state {run.state} · plan v{run.plan_version}",
        f"counts: {run.counts()}",
        "",
    ]
    if run.park_reason:
        out.append(f"parked: {run.park_reason}")
        out.append("")
    for node_id, node_run in run.runs.items():
        if str(node_run.status) == "pending":
            continue
        duration = f"{node_run.duration_ms} ms" if node_run.duration_ms is not None else "—"
        out.append(
            f"  {node_id:<20} {str(node_run.status):<10} {duration:>10}  "
            f"{node_run.detail[:70]}"
        )
    out.append("")
    out.append(f"path: {' -> '.join(run.path())}")
    return "\n".join(out)


def _slug(text_: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text_).strip("_").lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("mermaid", "json", "text"), default="text")
    parser.add_argument(
        "--failure-edges",
        action="store_true",
        help="draw every park edge rather than summarising them (mermaid only)",
    )
    parser.add_argument("--review", help="project the graph onto one review from the ledger")
    parser.add_argument(
        "--out",
        help="write to a file instead of stdout; --format mermaid defaults to "
        f"{DEFAULT_MMD.relative_to(ROOT)}",
    )
    args = parser.parse_args()

    run = None
    if args.review:
        from shared.graph_run import project

        try:
            run = project(args.review)
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if args.format == "json":
        payload = as_json() if run is None else {"graph": as_json(), "run": run.to_dict()}
        rendered = json.dumps(payload, indent=2)
    elif args.format == "mermaid":
        rendered = mermaid(run=run, failure_edges=args.failure_edges)
    else:
        rendered = text() if run is None else run_text(run)

    target = args.out
    if target is None and args.format == "mermaid" and run is None:
        target = str(DEFAULT_MMD)
    if target is None and args.format == "json" and run is None:
        target = str(DEFAULT_JSON)

    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered if rendered.endswith("\n") else rendered + "\n")
        print(f"wrote {path}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
