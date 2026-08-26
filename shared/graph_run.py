"""Projecting the declared graph onto one review, from the ledger and nothing else.

The question a security product has to be able to answer six months later is *why did the
system reach this conclusion*, and the honest form of the answer is the path it took. This
module reconstructs that path: which nodes ran, in what order, which were skipped and which are
still waiting, for any review, at any time, from records that were written for other reasons.

**No new collection and no new IAM row.** Everything here reads what
``services/dashboard/lib/ledger.ts`` and the audit binder already read — the review document,
the event ledger, the reasoning records, the dashboard cards and the findings. That constraint
was chosen before the code was written and it shaped the design: a projection that needed its
own write path would be a second source of truth about what happened, and the first thing a
second source of truth does is disagree with the first one.

It also means the projection is **retroactive**. Reviews that ran before this module existed
project exactly as well as reviews that ran after it, because nothing was instrumented for it.
A replay feature that only works on data recorded after the feature shipped is a feature that
cannot be demonstrated on the evidence you already have.

How a node is detected is declared on the node itself, in ``observed_by``. Seven forms:

``step:name``            a checkpoint in the review's completed steps
``event:topic``          an envelope of that type on the ledger
``state:name``           the review reached that state, per its transition events
``card:kind``            a dashboard card of that kind
``collection:name``      at least one document in that collection for this review
``field:name``           a field set on the review document
``decision:node``        a reasoning record stamped with this node's id
``finding_source:rule``  at least one finding with that provenance

The first match wins for *whether* a node ran; every match contributes to *when*. A node with
several observations is therefore more precisely timed rather than counted twice.

Failure semantics: a node whose evidence is unreadable is reported ``UNKNOWN`` rather than
``PENDING``. The two look the same on a diagram and mean opposite things — one is work that has
not happened, the other is work whose record could not be read — and a projection that collapsed
them would be least reliable at exactly the moment somebody was using it to diagnose a failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from shared.clients import firestore_client
from shared.domain import ReviewState
from shared.graph import GRAPH, Edge, Graph, Node, NodeKind

log = logging.getLogger("drawbridge.graph_run")


class NodeStatus(StrEnum):
    """Where one node stands in one review."""

    PENDING = "pending"
    """Not reached. The ordinary state of most of the graph for most of a review's life."""

    RUNNING = "running"
    """In flight when the last write happened. On a restart this is what "resumed from" names."""

    COMPLETE = "complete"

    WAITING = "waiting"
    """Reached and holding — a gate with no approval yet, a join whose arms are short."""

    FAILED = "failed"
    """The review parked with this node's declared park reason."""

    SKIPPED = "skipped"
    """Not applicable to this plan: a Tier 2 review never runs subprocessor extraction."""

    UNKNOWN = "unknown"
    """The evidence for this node could not be read. Never conflated with ``PENDING``."""


@dataclass(frozen=True, slots=True)
class NodeRun:
    """One node's execution in one review."""

    node_id: str
    status: NodeStatus
    started_at: str = ""
    finished_at: str = ""
    detail: str = ""
    plan_version: int | None = None
    evidence: tuple[str, ...] = ()
    """The ledger records that justify this status, as ``kind:identifier`` strings.

    On the record rather than recomputed on display, because the useful version of this panel is
    the one where clicking a node shows *what the claim rests on*. A status with no evidence
    behind it is the projection asserting rather than reporting.
    """

    @property
    def duration_ms(self) -> int | None:
        """Wall-clock between the first and last observation, or ``None`` when undefined.

        Wall-clock rather than compute: the gap between a questionnaire going out and a reply
        arriving is days of a vendor not answering, and calling that a node's latency would be
        a lie about the fleet in the flattering direction as often as the other one.
        """
        if not (self.started_at and self.finished_at):
            return None
        start, end = _parse(self.started_at), _parse(self.finished_at)
        if start is None or end is None:
            return None
        return max(0, int((end - start).total_seconds() * 1000))


@dataclass(frozen=True, slots=True)
class Traversal:
    """An edge that was taken, and the record that shows it was."""

    edge: Edge
    at: str
    because: str


@dataclass
class GraphRun:
    """The declared graph, projected onto one review."""

    review_id: str
    state: str
    plan_version: int
    runs: dict[str, NodeRun] = field(default_factory=dict)
    traversals: tuple[Traversal, ...] = ()
    park_reason: str = ""

    def status(self, node_id: str) -> NodeStatus:
        run = self.runs.get(node_id)
        return run.status if run else NodeStatus.PENDING

    def path(self, graph: Graph = GRAPH) -> tuple[str, ...]:
        """Nodes that ran, oldest first. The answer to *what actually happened*.

        Timestamps order the nodes that have them; **declaration order** orders the ones that do
        not, and breaks ties among the ones that do. Several nodes are evidenced only by a
        checkpoint or by a collection having documents in it, neither of which carries a
        defensible time — and sorting those alphabetically at the end produced a path that put
        the fourth-party chain after the decision, which is not a gap in the evidence but a
        statement about the run, and a false one.
        """
        order = {node.id: i for i, node in enumerate(graph.nodes)}
        ran = [r for r in self.runs.values() if r.status is not NodeStatus.PENDING]
        return tuple(
            r.node_id
            for r in sorted(ran, key=lambda r: (r.started_at or "~", order.get(r.node_id, 0)))
        )

    def counts(self) -> dict[str, int]:
        """One count per status, for a header line that does not need the whole table."""
        out: dict[str, int] = {}
        for run in self.runs.values():
            out[str(run.status)] = out.get(str(run.status), 0) + 1
        return out

    def to_dict(self) -> dict:
        """A JSON-safe rendering, for the dashboard and for ``scripts/graph_dump.py``."""
        return {
            "review_id": self.review_id,
            "state": self.state,
            "plan_version": self.plan_version,
            "park_reason": self.park_reason,
            "counts": self.counts(),
            "path": list(self.path()),
            "nodes": {
                node_id: {
                    "status": str(run.status),
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "duration_ms": run.duration_ms,
                    "detail": run.detail,
                    "evidence": list(run.evidence),
                }
                for node_id, run in self.runs.items()
            },
            "traversals": [
                {
                    "source": t.edge.source,
                    "target": t.edge.target,
                    "kind": str(t.edge.kind),
                    "trigger": t.edge.trigger,
                    "at": t.at,
                    "because": t.because,
                }
                for t in self.traversals
            ],
        }


@dataclass(slots=True)
class _Ledger:
    """Everything the projection reads, fetched once.

    One snapshot rather than a query per observation. Twenty-seven nodes with up to three
    observations each would otherwise be eighty round trips to answer one question, and the
    dashboard renders this on a page load.
    """

    review: dict
    events: list[dict]
    decisions: list[dict]
    cards: list[dict]
    findings: list[dict]
    collections: dict[str, int]
    unreadable: set[str]


COLLECTIONS_WATCHED = (
    "evidence_chunks",
    "subprocessors",
    "followups",
    "screenings",
    "qa_responses",
    "scores",
    "memos",
    "tasks",
)
"""Collections whose mere presence for a review is evidence a node ran.

Counted rather than listed: the projection needs "did the chain get extracted", not the chain.
"""


def project(review_id: str, graph: Graph = GRAPH) -> GraphRun:
    """Reconstruct ``review_id``'s path through ``graph``.

    Raises:
        LookupError: when no such review exists. Projecting a review that was never opened
            would produce a graph of entirely pending nodes, which is indistinguishable from a
            review that has just started and is a far worse answer than saying so.
    """
    ledger = _load(review_id)
    if not ledger.review:
        raise LookupError(f"no review {review_id!r} in the ledger")

    plan_version = int(ledger.review.get("plan_version", 1) or 1)
    run = GraphRun(
        review_id=review_id,
        state=str(ledger.review.get("state", "")),
        plan_version=plan_version,
        park_reason=str(ledger.review.get("park_reason") or ""),
    )

    for node in graph.nodes:
        run.runs[node.id] = _project_node(node, ledger, run)

    run.traversals = _traversals(graph, run, ledger)
    return run


def _project_node(node: Node, ledger: _Ledger, run: GraphRun) -> NodeRun:
    """Resolve one node's status from its declared observations."""
    stamps: list[str] = []
    evidence: list[str] = []
    details: list[str] = []
    unknown = False

    for observation in node.observed_by:
        kind, _, argument = observation.partition(":")
        resolver = _RESOLVERS.get(kind)
        if resolver is None:
            log.warning("node %s declares an observation form %r nothing resolves", node.id, kind)
            continue
        try:
            hits = resolver(argument, ledger, node, run)
        except Exception as exc:  # noqa: BLE001 — unreadable evidence is reported, not assumed
            log.warning("could not resolve %s for node %s: %s", observation, node.id, exc)
            unknown = True
            continue
        for at, why in hits:
            if at:
                stamps.append(at)
            evidence.append(f"{observation}")
            details.append(why)

    # The park reason is checked before the evidence, not after it. A node that failed usually
    # has *less* evidence than one that succeeded — no checkpoint recorded, no event published,
    # no collection written — so requiring evidence first would report the node that stopped the
    # review as never having been reached, on exactly the review somebody is trying to diagnose.
    if node.failure is not None and run.park_reason and run.park_reason == node.failure.park_reason:
        return NodeRun(
            node.id,
            NodeStatus.FAILED,
            started_at=min(stamps) if stamps else "",
            finished_at=max(stamps) if stamps else "",
            detail=f"parked: {run.park_reason}",
            plan_version=run.plan_version,
            evidence=(*dict.fromkeys(evidence), f"park:{run.park_reason}"),
        )

    # A gate or a join is *reached* by the review standing in a state it waits in, whether or not
    # anything has been written about it yet. Every other node kind leaves a trace when it runs;
    # these two leave one when they finish, and a barrier that only appears once it has opened is
    # a barrier the projection cannot show anybody waiting at.
    if not evidence and node.kind in (NodeKind.GATE, NodeKind.JOIN):
        try:
            standing = ReviewState(str(ledger.review.get("state", "")))
        except ValueError:
            standing = None
        if standing is not None and standing in node.arrives_in:
            return NodeRun(
                node.id,
                NodeStatus.WAITING,
                detail=f"reached; the review is standing in {standing.value}",
                plan_version=run.plan_version,
                evidence=(f"state_now:{standing.value}",),
            )

    if not evidence:
        if unknown:
            return NodeRun(node.id, NodeStatus.UNKNOWN, detail="evidence could not be read")
        return NodeRun(node.id, NodeStatus.PENDING)

    stamps.sort()
    status = _status_for(node, ledger, run, ran=True)
    return NodeRun(
        node_id=node.id,
        status=status,
        started_at=stamps[0] if stamps else "",
        finished_at=stamps[-1] if stamps else "",
        detail="; ".join(dict.fromkeys(d for d in details if d)),
        plan_version=run.plan_version,
        evidence=tuple(dict.fromkeys(evidence)),
    )


def _status_for(node: Node, ledger: _Ledger, run: GraphRun, *, ran: bool) -> NodeStatus:
    """Decide a node's status once it is known to have been reached.

    Order matters and is not arbitrary. A parked review's failing node is failed even though it
    also has a checkpoint in flight; a gate that has evidence of being reached but no approval
    is waiting rather than complete; a step recorded as current rather than completed is running.
    """
    if not ran:
        return NodeStatus.PENDING

    if node.checkpoint:
        completed = set(ledger.review.get("completed_steps") or [])
        current = ledger.review.get("current_step")
        if current and _step_matches(node.checkpoint, str(current)):
            return NodeStatus.RUNNING
        if not any(_step_matches(node.checkpoint, s) for s in completed):
            return NodeStatus.RUNNING

    if node.kind is NodeKind.GATE and str(ledger.review.get("state", "")) == "gated":
        return NodeStatus.WAITING
    if node.kind is NodeKind.JOIN and not _join_passed(node, ledger):
        return NodeStatus.WAITING

    return NodeStatus.COMPLETE


def _join_passed(node: Node, ledger: _Ledger) -> bool:
    """Whether the review moved past this join, judged by the state it opened onto."""
    reached = {str(e.get("to_state") or "") for e in ledger.events if e.get("to_state")}
    if node.id == "coverage_join":
        return bool(reached & {"evidence_review", "scored", "gated", "decided", "monitored"})
    if node.id == "findings_join":
        return any(e.get("type") == "review.findings_ready" for e in ledger.events)
    return True


def _step_matches(declared: str, recorded: str) -> bool:
    """Whether a recorded checkpoint is this node's, under any plan version.

    ``questionnaire_send`` and ``questionnaire_send@plan_v2`` are the same node at two plan
    versions, and a projection that treated the second as an unknown step would show the node
    that a re-tier re-ran as never having run at all.
    """
    return recorded == declared or recorded.startswith(f"{declared}@plan_v")


# --- Observation resolvers -------------------------------------------------------------------
#
# Each returns a list of ``(timestamp, why)`` pairs — empty when the node was not observed this
# way. None of them raises on absence: absence is the answer.


def _by_step(name: str, ledger: _Ledger, node: Node, run: GraphRun):
    """A checkpoint proves a node ran. It deliberately contributes no timestamp.

    ``step_started`` is a single field on the review, overwritten by whichever step started most
    recently, so reading it as *this* step's start time yields a number that is right for the
    last step and wrong for every earlier one. The first version of this function did exactly
    that and reported the questionnaire send as taking zero milliseconds because the memo had
    started since. A node with no defensible duration reports none; the event-edge observations
    supply real timings where real timings exist.
    """
    completed = list(ledger.review.get("completed_steps") or [])
    current = str(ledger.review.get("current_step") or "")
    hits = [
        ("", f"checkpoint {recorded}")
        for recorded in completed
        if _step_matches(name, str(recorded))
    ]
    if current and _step_matches(name, current):
        hits.append(("", f"checkpoint {current} in flight"))
    return hits


def _by_event(topic: str, ledger: _Ledger, node: Node, run: GraphRun):
    return [
        (str(e.get("ts") or ""), f"{topic}")
        for e in ledger.events
        if e.get("type") == topic and not e.get("addendum")
    ]


def _by_state(name: str, ledger: _Ledger, node: Node, run: GraphRun):
    """Transition events reaching a state, narrowed by gate scope where one is named.

    A scoped observation needs the scope to have been *this* one, and the scope lives in two
    places for two reasons: on the review while it is parked, and on the card afterwards. Both
    are consulted, because a released gate keeps its card and loses its field, and a gate that
    only read the field would show every closed review as having passed neither gate.
    """
    state, _, scope = name.partition("/")
    hits = [
        (str(e.get("ts") or ""), f"reached {state}")
        for e in ledger.events
        if str(e.get("to_state") or "") == state
    ]
    if not scope:
        return hits

    # A transition event says the review reached GATED and cannot say which gate: the scope is
    # not on the transition. Both gates therefore match every ``to_state == gated`` event, and
    # a review that passed both would time each gate by the other's timestamps. Where a scope is
    # named, only the scoped records count — the card, and the field while the review is still
    # parked — so the two gates are timed by their own evidence or by none.
    on_review = str(ledger.review.get("gate_scope") or "") == scope
    cards = [c for c in ledger.cards if str(c.get("gate_scope") or "") == scope]
    if not on_review and not cards:
        return []
    scoped = [(str(c.get("at") or ""), f"{scope} gate") for c in cards]
    return scoped or [("", f"{scope} gate, still parked")]


def _by_card(kind: str, ledger: _Ledger, node: Node, run: GraphRun):
    """Cards of a kind, optionally narrowed to one gate scope by ``card:gate/contact``.

    The scope is not decoration. ``GATED`` is one state with two meanings, and a card observation
    that ignored the scope would light the decision gate for a review waiting on permission to
    send its first email — showing a vendor as awaiting risk acceptance before anyone had asked
    them a question.
    """
    kind, _, scope = kind.partition("/")
    matches = [c for c in ledger.cards if str(c.get("kind") or "") == kind]
    if scope:
        matches = [c for c in matches if str(c.get("gate_scope") or "") == scope]
    label = f"card {kind}" + (f"/{scope}" if scope else "")
    return [(str(c.get("at") or ""), label) for c in matches]


def _by_vendor_collection(name: str, ledger: _Ledger, node: Node, run: GraphRun):
    """A collection keyed by vendor rather than by review.

    ``subprocessors`` is the one, and the reason is in the domain rather than in the schema: a
    subprocessor is a fact about the company and survives the review that discovered it. A
    projection that looked for it under ``review_id`` would find nothing on every review, and
    report the fourth-party chain as never extracted on the reviews that extracted it.
    """
    from google.cloud.firestore_v1 import FieldFilter

    vendor_id = str(ledger.review.get("vendor_id") or "")
    if not vendor_id:
        return []
    count = sum(
        1
        for _ in firestore_client()
        .collection(name)
        .where(filter=FieldFilter("vendor_id", "==", vendor_id))
        .stream()
    )
    if not count:
        return []
    return [("", f"{count} document(s) in {name} for {vendor_id}")]


def _by_collection(name: str, ledger: _Ledger, node: Node, run: GraphRun):
    count = ledger.collections.get(name, 0)
    if name in ledger.unreadable:
        raise RuntimeError(f"{name} could not be read")
    if not count:
        return []
    return [("", f"{count} document(s) in {name}")]


def _by_field(name: str, ledger: _Ledger, node: Node, run: GraphRun):
    value = ledger.review.get(name)
    if not value:
        return []
    return [("", f"{name} set")]


def _by_decision(node_id: str, ledger: _Ledger, node: Node, run: GraphRun):
    return [
        (str(d.get("at") or ""), str(d.get("decision") or ""))
        for d in ledger.decisions
        if str(d.get("node") or "") == node_id
    ]


def _by_finding_source(source: str, ledger: _Ledger, node: Node, run: GraphRun):
    matching = [f for f in ledger.findings if str(f.get("source") or "") == source]
    if not matching:
        return []
    return [("", f"{len(matching)} {source} finding(s)")]


_RESOLVERS = {
    "step": _by_step,
    "event": _by_event,
    "state": _by_state,
    "card": _by_card,
    "collection": _by_collection,
    "vendor_collection": _by_vendor_collection,
    "field": _by_field,
    "decision": _by_decision,
    "finding_source": _by_finding_source,
}


def _traversals(graph: Graph, run: GraphRun, ledger: _Ledger) -> tuple[Traversal, ...]:
    """Edges both of whose ends ran, timed by the target.

    Deliberately weaker than it could be. A stronger version would match each traversal to the
    specific envelope that carried it, and for event edges the ``idem_key`` makes that possible;
    for inline edges there is nothing on the ledger to match, because an inline edge is a
    function call. Reporting only the edges that can be evidenced from both ends keeps the two
    kinds honest rather than inventing precision for one of them.
    """
    taken: list[Traversal] = []
    for edge in graph.edges:
        source = run.runs.get(edge.source)
        target = run.runs.get(edge.target)
        if not source or not target:
            continue
        if source.status in (NodeStatus.PENDING, NodeStatus.UNKNOWN):
            continue
        if target.status in (NodeStatus.PENDING, NodeStatus.UNKNOWN):
            continue
        because = edge.trigger or edge.guard or str(edge.kind)
        taken.append(Traversal(edge=edge, at=target.started_at, because=because))
    return tuple(sorted(taken, key=lambda t: (t.at or "~", t.edge.source, t.edge.target)))


def _load(review_id: str) -> _Ledger:
    """Fetch the review's ledger once. Never raises on a collection it cannot read."""
    from google.cloud.firestore_v1 import FieldFilter

    db = firestore_client()
    unreadable: set[str] = set()

    def rows(collection: str) -> list[dict]:
        try:
            return [
                d.to_dict() or {}
                for d in db.collection(collection)
                .where(filter=FieldFilter("review_id", "==", review_id))
                .stream()
            ]
        except Exception as exc:  # noqa: BLE001 — an unreadable collection is reported as such
            log.warning("graph projection could not read %s: %s", collection, exc)
            unreadable.add(collection)
            return []

    review = db.collection("reviews").document(review_id).get().to_dict() or {}
    counts: dict[str, int] = {}
    for name in COLLECTIONS_WATCHED:
        counts[name] = len(rows(name))

    return _Ledger(
        review=review,
        events=sorted(rows("events"), key=lambda e: str(e.get("ts") or "")),
        decisions=sorted(rows("decisions"), key=lambda d: str(d.get("at") or "")),
        cards=sorted(rows("dashboard_events"), key=lambda c: str(c.get("at") or "")),
        findings=rows("findings"),
        collections=counts,
        unreadable=unreadable,
    )


def _parse(stamp: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
