"""The review graph, declared once and checked against the code that executes it.

**This module describes the fleet. It never drives it.** Nothing here dispatches, schedules or
holds a position in a review, and that restraint is the entire design. A graph *runner* is a
place for the review to live, and a place for the review to live is a process whose death loses
it — the property ``make demo-crash`` exists to demonstrate. The review's position stays what it
has always been: a checkpointed row in Firestore and an unacknowledged message on a subscription.

What was missing was not an executor. It was a **written-down topology that the code is checked
against**. Before this module the graph existed only in the shape of five ``handle_event``
functions, an ``EXPECTED_STATES`` table and a transition table, and the three could disagree
without anything failing. ``scripts/check_contracts.py --check graph`` now diffs this
declaration against all three, so a node whose handler was deleted, an edge carrying a topic
nobody subscribes to, or an agent writing a collection its identity cannot write, fails in CI.

Three things follow from a graph being data rather than control flow:

- It can be **dumped** — ``scripts/graph_dump.py`` renders it as Mermaid, which is where
  ``docs/diagrams/src/23-review-graph.mmd`` comes from. The diagram set said 23 was absent
  because there was nothing to dump; there now is, and it is generated rather than drawn.
- It can be **projected onto a review** — ``shared.graph_run`` reconstructs which nodes ran, in
  what order and with what result, purely from the immutable ledger. No new collection and no
  new IAM row: the projection reads what the binder already reads.
- It can be **enforced**. Join policies, cycle budgets and failure policies declared here are
  read by the code that implements them, so the picture and the behaviour are the same object.

Vocabulary, kept distinct from the two that already exist:

- a **state** is where a review is (``shared.domain.ReviewState``) — nine of them, one per review
- a **node** is a unit of work — twenty-seven of them, several per state
- a **step** is a durable checkpoint (``shared.checkpoint``) — a node may have one, or none

A node is not a state and not a step. Cross-examination and subprocessor extraction are two
nodes inside one state under one checkpoint, and collapsing them would lose exactly the detail
this module exists to expose.

Failure semantics: ``validate`` raises ``GraphInvalid`` on a structurally broken declaration —
a dangling edge, a duplicate id, an unreachable node, a cycle through a node that does not
exist. It is called at import, so an inconsistent graph fails the process that imports it rather
than being served to a diagram.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from shared.domain import ReviewState


class GraphInvalid(Exception):
    """The declared graph is structurally broken. A bug in this file, not a runtime condition."""


class NodeKind(StrEnum):
    """What a node *is*, which decides what may be asked of it.

    The distinction that matters most is ``AGENT`` against ``DETERMINISTIC``. An agent node
    reaches a model and its output is judgement; a deterministic node is arithmetic over data
    somebody else produced, and its output is reproducible. Scoring is deterministic and the
    memo is an agent, and the fact that they sit next to each other inside one service is
    precisely why the two need different names.
    """

    EVENT = "event"
    """A boundary the fleet does not control: intake arriving, a vendor replying, a sweep firing."""

    ROUTER = "router"
    """A branch. Exactly one successor is taken and the choice is recorded with its reason."""

    AGENT = "agent"
    """Bounded reasoning. Reaches a model through ``shared.routing.generate`` and nothing else."""

    DETERMINISTIC = "deterministic"
    """Code. No model call is reachable from it, and several are asserted by import graph."""

    JOIN = "join"
    """A barrier. Continues when its arm policy is satisfied, and records which arms were not."""

    GATE = "gate"
    """A wait on a named human. The only node kind the fleet cannot satisfy by itself."""

    TERMINAL = "terminal"
    """A resting place: the decided record, the monitored portfolio, the parked review."""


class EdgeKind(StrEnum):
    """How control reaches the next node, which is the question an operator actually asks.

    ``EVENT`` edges survive a process death and ``INLINE`` edges do not, so the distinction is
    not presentational: it says exactly where a SIGKILL loses work and where it does not. An
    inline edge is inside one handler and re-runs from its triggering event on redelivery; an
    event edge has a durable message behind it.
    """

    EVENT = "event"
    INLINE = "inline"
    BRANCH = "branch"
    FAILURE = "failure"
    CYCLE = "cycle"


@dataclass(frozen=True, slots=True)
class Contract:
    """What a node may take, produce and touch.

    ``reads`` and ``writes`` are Firestore collections, and ``forbidden`` names capabilities the
    node must provably not have. Both halves are checked against
    ``infra/iam/permission-matrix.yaml`` rather than believed: a node declaring a write its
    identity cannot perform is a contract that would fail the first time real IAM was applied,
    and a node declaring no forbidden capability that the matrix denies is a contract that has
    stopped describing the identity it belongs to.
    """

    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    model: str | None = None
    """``fast``, ``deep`` or ``None``. ``None`` on an ``AGENT`` node is a contradiction and
    ``validate`` rejects it; anything other than ``None`` on a ``DETERMINISTIC`` node is the
    same contradiction from the other side."""


@dataclass(frozen=True, slots=True)
class FailurePolicy:
    """What happens when a node fails, declared rather than left to whoever wrote the handler.

    ``max_attempts`` is the Pub/Sub delivery count for a node reached by an event edge, and the
    in-process retry count for one reached inline. ``on_exhaustion`` is the important field:

    ``park``     stop the review, raise a card, name what stalled. The default, and correct
                 wherever the alternative is a number computed from partial input.
    ``degrade``  continue with less, saying so. Legitimate only where the loss is visible in
                 the output — retrieval falling back to whole-document reconciliation puts the
                 fallback in the prompt, so the memo says which one it used.
    ``skip``     continue as if the node had not been asked for. Reserved for work that informs
                 a future review rather than this one.

    The rule the three encode is the one in the failure-semantics section of the architecture:
    mandatory controls fail closed, optional controls degrade, and never the other way round.
    """

    max_attempts: int = 5
    on_exhaustion: Literal["park", "degrade", "skip"] = "park"
    park_reason: str = ""
    degraded_to: str | None = None


class JoinMode(StrEnum):
    """How a barrier decides it has waited long enough."""

    ALL_REQUIRED = "all_required"
    """Every arm marked required must have completed. No partial continuation."""

    THRESHOLD = "threshold"
    """A measured proportion of one arm crosses a line — the questionnaire's coverage rule."""

    QUORUM = "quorum"
    """Any ``n`` of the arms, where which ``n`` does not matter."""


@dataclass(frozen=True, slots=True)
class Arm:
    """One input a join waits on."""

    name: str
    required: bool
    describes: str


@dataclass(frozen=True, slots=True)
class Join:
    """A barrier, with the policy that decides when it opens.

    Declared here and evaluated in ``shared.join``, so the rule a diagram shows and the rule the
    Orchestrator applies are read from the same object. Before this, the coverage rule was a
    constant in the questionnaire parser and a comparison in the Orchestrator, and the picture
    of it was a sentence in a document.

    ``override`` names the escape hatch, because every real barrier has one and an undeclared
    escape hatch is the part of a design that surprises people. The questionnaire join's
    override is an analyst marking the thread complete, which is what happens when a vendor
    answers most of what was asked and simply stops.
    """

    id: str
    node: str
    mode: JoinMode
    arms: tuple[Arm, ...]
    threshold: float | None = None
    quorum: int | None = None
    override: str | None = None
    on_shortfall: str = "wait"


@dataclass(frozen=True, slots=True)
class Cycle:
    """A loop that is allowed, with the budget that stops it being a loop.

    Two cycles exist and they are not the same shape. The re-tier cycle is bounded by the tier
    floor — tier only ever rises, there are three tiers, so it can fire at most twice and the
    arithmetic bounds it. The re-review cycle has no such natural bound: a noisy feed could
    reopen a vendor indefinitely, each reopening a legitimate new review by every rule the
    system has. ``max_iterations`` is what makes the second one terminate, and
    ``on_exhaustion`` is what it does instead — which is never "stop monitoring", because a
    vendor nobody is watching is the outcome this whole component exists to prevent.
    """

    id: str
    through: tuple[str, ...]
    max_iterations: int
    counted_by: str
    on_exhaustion: str
    bounded_by: str = ""


@dataclass(frozen=True, slots=True)
class Branch:
    """One outcome of a router: where it goes, when, and what decides."""

    to: str
    when: str
    decided_by: str


@dataclass(frozen=True, slots=True)
class Router:
    """A branch point, with every outcome named.

    ``monotonic`` records a direction the router may not travel, and both routers in this graph
    have one. The tier router may raise scrutiny and never lower it; the coverage router may
    open evidence review and never close it again. A monotonic constraint is what stops a
    vendor's own answers reducing the scrutiny applied to them, so it is on the router rather
    than in the handler that happens to implement it.
    """

    id: str
    node: str
    branches: tuple[Branch, ...]
    monotonic: str = ""


@dataclass(frozen=True, slots=True)
class Node:
    """One unit of work in the review graph.

    ``observed_by`` is the field that makes ``shared.graph_run`` possible. It names how this
    node's execution is detectable in the immutable ledger — a checkpoint appearing in
    ``completed_steps``, an event of a given type existing, a review reaching a state, or a
    decision record stamped with this node's id. A node with no observation cannot be projected
    and ``validate`` rejects one, because a node the repository cannot show having run is a box
    on a diagram rather than a description of the system.
    """

    id: str
    kind: NodeKind
    label: str
    identity: str
    contract: Contract
    observed_by: tuple[str, ...]
    triggered_by: tuple[str, ...] = ()
    emits: tuple[str, ...] = ()
    checkpoint: str | None = None
    plan_step: str | None = None
    arrives_in: frozenset[ReviewState] = frozenset()
    """States in which this node's trigger is in phase.

    Checked against ``shared.events.EXPECTED_STATES``, which is the event contract's own answer
    to the same question. Two fields rather than one because *the states a node runs in* and
    *the state it leaves the review in* are different questions with different answers, and the
    first draft of this module conflated them — which produced a graph claiming the decision
    gate was triggered by an event that only ever arrives before the gate exists.
    """

    advances_to: ReviewState | None = None
    """The state this node moves the review into, when it moves one.

    Checked against ``shared.domain.ALLOWED``: the transition from every state in ``arrives_in``
    to this one must be legal, or the node is declaring a move the state machine would refuse.
    """

    failure: FailurePolicy | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class Edge:
    """A directed connection, with what carries it and what has to be true to take it."""

    source: str
    target: str
    kind: EdgeKind
    trigger: str = ""
    guard: str = ""
    label: str = ""


@dataclass(frozen=True)
class Graph:
    """The declared topology, plus the lookups everything else asks it for."""

    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    routers: tuple[Router, ...] = ()
    joins: tuple[Join, ...] = ()
    cycles: tuple[Cycle, ...] = ()
    _by_id: dict[str, Node] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {n.id: n for n in self.nodes})

    def node(self, node_id: str) -> Node:
        """Return a node by id.

        Raises:
            KeyError: on an unknown id. Never returns a placeholder — a graph lookup that
                invents a node is how a projection reports work that never happened.
        """
        return self._by_id[node_id]

    def has(self, node_id: str) -> bool:
        return node_id in self._by_id

    def of_kind(self, kind: NodeKind) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes if n.kind is kind)

    def out_edges(self, node_id: str) -> tuple[Edge, ...]:
        return tuple(e for e in self.edges if e.source == node_id)

    def in_edges(self, node_id: str) -> tuple[Edge, ...]:
        return tuple(e for e in self.edges if e.target == node_id)

    def consumers_of(self, topic: str) -> tuple[Node, ...]:
        """Nodes this topic triggers. Several nodes legitimately share one topic."""
        return tuple(n for n in self.nodes if topic in n.triggered_by)

    def publishers_of(self, topic: str) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes if topic in n.emits)

    def router(self, node_id: str) -> Router | None:
        return next((r for r in self.routers if r.node == node_id), None)

    def join(self, node_id: str) -> Join | None:
        return next((j for j in self.joins if j.node == node_id), None)

    def parallel_groups(self) -> tuple[tuple[str, ...], ...]:
        """Sets of nodes whose completion one join waits on, in declaration order.

        This is what "runs in parallel" means here, and it is worth being exact about because
        the fleet has no scheduler and therefore nothing that *makes* two nodes concurrent.
        What it has is independence: the questionnaire thread and the evidence pipeline are
        driven by different events, consume different topics and write different collections,
        so their relative order is whatever the vendor and the network produced. A join is the
        only place that independence has to end, and the arms of a join are therefore the
        honest definition of a parallel group.
        """
        return tuple(tuple(arm.name for arm in j.arms) for j in self.joins)


# --- The Drawbridge review graph -----------------------------------------------------------
#
# Twenty-seven nodes. Read it as five bands: intake and planning, the questionnaire thread, the
# evidence pipeline, scoring and the decision, and monitoring. The two human gates sit between
# bands two and three and between four and five, which is the whole product in one sentence.

_ORC = "sa-orchestrator"
_QA = "sa-questionnaire"
_EV = "sa-evidence"
_RS = "sa-scorer"
_WD = "sa-watchdog"
_AR = "sa-armor"
_AP = "sa-approvals"

_LEDGER = ("events", "decisions", "dashboard_events")
"""Collections every acting identity appends to. Named once so twenty-two contracts do not
each restate the three, and so a node that does *not* write them is visibly different."""

NODES: tuple[Node, ...] = (
    # --- Band 1 · intake and planning ------------------------------------------------------
    Node(
        id="intake",
        kind=NodeKind.EVENT,
        label="Intake",
        identity="operator",
        contract=Contract(
            inputs=("vendor record", "intake form"),
            outputs=("review.intake",),
            reads=("vendors",),
            writes=("reviews", "events"),
            forbidden=("any model call",),
        ),
        observed_by=("event:review.intake",),
        emits=("review.intake",),
        arrives_in=frozenset({ReviewState.INTAKE}),
        note="Opened by a person through scripts/open_review.py, or by the Watchdog on a "
        "signal. Identity 'operator' rather than a service account, and that is a finding "
        "rather than a shorthand: no identity in the permission matrix can write the reviews "
        "collection except the Orchestrator and the Questionnaire agent, so there is today no "
        "service account that could open a review from a hosted intake form. The dashboard "
        "deliberately holds no write path and must not acquire one for this.",
    ),
    Node(
        id="recall",
        kind=NodeKind.DETERMINISTIC,
        label="Recall dossier",
        identity=_ORC,
        contract=Contract(
            inputs=("vendor_id",),
            outputs=("dossier", "carried questions"),
            reads=("dossiers", "reviews"),
            writes=_LEDGER,
            forbidden=("any write to dossiers",),
        ),
        observed_by=("decision:recall", "card:prior_review_recalled"),
        triggered_by=("review.intake",),
        arrives_in=frozenset({ReviewState.INTAKE}),
        failure=FailurePolicy(max_attempts=1, on_exhaustion="degrade", degraded_to="no dossier"),
        note="A vendor nobody has reviewed before recalls nothing, which is not a failure.",
    ),
    Node(
        id="tier_router",
        kind=NodeKind.ROUTER,
        label="Tier router",
        identity=_ORC,
        contract=Contract(
            inputs=("declared intake facts", "dossier"),
            outputs=("tier",),
            reads=("vendors", "dossiers"),
            writes=_LEDGER,
            forbidden=("lowering a tier",),
        ),
        observed_by=("decision:tier_router", "field:tier"),
        triggered_by=("review.intake",),
        arrives_in=frozenset({ReviewState.INTAKE}),
        note="Arithmetic sets a floor; the model may raise scrutiny and never lower it.",
    ),
    Node(
        id="plan",
        kind=NodeKind.AGENT,
        label="Plan review",
        identity=_ORC,
        contract=Contract(
            inputs=("vendor", "dossier", "tier floor"),
            outputs=("plan", "domains", "carried questions"),
            reads=("vendors", "dossiers", "reviews"),
            writes=("reviews", "idempotency", *_LEDGER),
            forbidden=("email", "any Storage role", "approvals write"),
            model="fast",
        ),
        observed_by=("step:plan", "event:review.plan_ready"),
        triggered_by=("review.intake",),
        emits=("review.plan_ready",),
        checkpoint="plan",
        arrives_in=frozenset({ReviewState.INTAKE}),
        advances_to=ReviewState.QUESTIONNAIRE_OUT,
        failure=FailurePolicy(on_exhaustion="park", park_reason="planning_failed"),
        note="Step names come from a closed vocabulary; work with no name parks.",
    ),
    # --- Band 2 · the questionnaire thread -------------------------------------------------
    Node(
        id="contact_gate",
        kind=NodeKind.GATE,
        label="G2 · first outbound contact",
        identity=_QA,
        contract=Contract(
            inputs=("plan", "vendor contact"),
            outputs=("approval token",),
            reads=("approvals", "approval_tokens_spent", "reviews"),
            writes=("reviews", "approval_tokens_spent", *_LEDGER),
            forbidden=("approvals write", "signing an approval"),
        ),
        observed_by=("state:gated/contact", "card:gate/contact"),
        triggered_by=("review.plan_ready",),
        emits=("review.approved",),
        arrives_in=frozenset({ReviewState.QUESTIONNAIRE_OUT}),
        advances_to=ReviewState.GATED,
        failure=FailurePolicy(max_attempts=1, on_exhaustion="park", park_reason="contact_gate"),
        note="Policy P1 at the gateway is what makes this unskippable.",
    ),
    Node(
        id="questionnaire_send",
        kind=NodeKind.AGENT,
        label="Generate and send",
        identity=_QA,
        contract=Contract(
            inputs=("plan", "question bank", "approval token"),
            outputs=("sent questions", "outbound email"),
            reads=("reviews", "vendors", "qa_responses", "approvals"),
            writes=("reviews", "qa_responses", "inbox", "idempotency", *_LEDGER),
            forbidden=("findings write", "scores write", "any Storage read"),
            model="fast",
        ),
        observed_by=("step:questionnaire_send", "field:sent_questions"),
        triggered_by=("review.plan_ready",),
        checkpoint="questionnaire_send",
        plan_step="questionnaire_send",
        arrives_in=frozenset({ReviewState.QUESTIONNAIRE_OUT}),
        failure=FailurePolicy(on_exhaustion="park", park_reason="send_failed"),
        note="A re-plan rekeys this step so added questions send and asked ones cannot resend.",
    ),
    Node(
        id="reply",
        kind=NodeKind.EVENT,
        label="Vendor replies",
        identity="sa-portal",
        contract=Contract(
            inputs=("vendor message",),
            outputs=("vendor.reply_received",),
            reads=("reviews", "qa_responses"),
            writes=("qa_responses", "events"),
            forbidden=("findings write", "scores write", "any model call"),
        ),
        observed_by=("event:vendor.reply_received",),
        emits=("vendor.reply_received",),
        arrives_in=frozenset({ReviewState.QUESTIONNAIRE_OUT, ReviewState.REPLIES_IN}),
        note="The other half of the vendor boundary. Declared as its own node for the same "
        "reason 'upload' is: the fleet does not schedule it, and a graph that showed replies "
        "as something the Questionnaire agent produced would be drawing a fleet that talks to "
        "itself.",
    ),
    Node(
        id="reply_parse",
        kind=NodeKind.AGENT,
        label="Parse reply",
        identity=_QA,
        contract=Contract(
            inputs=("vendor.reply_received", "clean stamp"),
            outputs=("answers", "coverage"),
            reads=("reviews", "qa_responses", "inbox"),
            writes=("qa_responses", "qa_responses_superseded", "inbox", *_LEDGER),
            forbidden=("findings write", "scores write"),
            model="fast",
        ),
        observed_by=("event:vendor.reply_received", "decision:reply_parse"),
        triggered_by=("vendor.reply_received",),
        plan_step="followup",
        arrives_in=frozenset({ReviewState.QUESTIONNAIRE_OUT, ReviewState.REPLIES_IN}),
        failure=FailurePolicy(on_exhaustion="park", park_reason="reply_unparseable"),
    ),
    Node(
        id="followup",
        kind=NodeKind.AGENT,
        label="Re-ask an unusable answer",
        identity=_QA,
        contract=Contract(
            inputs=("answers rated unusable",),
            outputs=("follow-up questions",),
            reads=("qa_responses", "followups"),
            writes=("followups", "qa_responses", *_LEDGER),
            forbidden=("findings write",),
            model="fast",
        ),
        observed_by=("decision:followup", "collection:followups"),
        triggered_by=("vendor.reply_received",),
        plan_step="followup",
        arrives_in=frozenset({ReviewState.QUESTIONNAIRE_OUT, ReviewState.REPLIES_IN}),
        failure=FailurePolicy(max_attempts=1, on_exhaustion="skip"),
        note="Once each. An answer re-asked twice is a chase with extra steps.",
    ),
    Node(
        id="chase",
        kind=NodeKind.AGENT,
        label="Chase",
        identity=_QA,
        contract=Contract(
            inputs=("outstanding questions", "chase round"),
            outputs=("outbound email",),
            reads=("reviews", "qa_responses", "inbox"),
            writes=("inbox", "reviews", "idempotency", *_LEDGER),
            forbidden=("findings write", "scores write"),
            model="fast",
        ),
        observed_by=("event:review.chase_due", "decision:chase"),
        triggered_by=("review.chase_due",),
        plan_step="chase",
        arrives_in=frozenset({ReviewState.QUESTIONNAIRE_OUT, ReviewState.REPLIES_IN}),
        failure=FailurePolicy(max_attempts=3, on_exhaustion="park", park_reason="no_reply"),
        note="Three rounds, then a person owns it. A fourth reminder is a loop.",
    ),
    Node(
        id="retier_router",
        kind=NodeKind.ROUTER,
        label="Re-tier router",
        identity=_ORC,
        contract=Contract(
            inputs=("classified answers", "current tier"),
            outputs=("tier", "plan version"),
            reads=("qa_responses", "data_scope_classifications", "reviews"),
            writes=("reviews", "data_scope_classifications", *_LEDGER),
            forbidden=("lowering a tier",),
            model="fast",
        ),
        observed_by=("decision:retier_router", "card:tier_change"),
        triggered_by=("vendor.reply_received",),
        emits=("review.plan_ready",),
        arrives_in=frozenset({ReviewState.REPLIES_IN}),
        advances_to=ReviewState.QUESTIONNAIRE_OUT,
        note="The one legitimate backward move inside a review, and it only ever tightens.",
    ),
    Node(
        id="coverage_join",
        kind=NodeKind.JOIN,
        label="Coverage join",
        identity=_ORC,
        contract=Contract(
            inputs=("answered proportion", "analyst override"),
            outputs=("open evidence review",),
            reads=("qa_responses", "reviews", "screenings"),
            writes=("reviews", *_LEDGER),
            forbidden=("findings write",),
        ),
        observed_by=("decision:coverage_join", "state:evidence_review"),
        triggered_by=("vendor.reply_received",),
        arrives_in=frozenset({ReviewState.REPLIES_IN}),
        advances_to=ReviewState.EVIDENCE_REVIEW,
        note="Measured against the current plan, never against one a re-tier replaced.",
    ),
    # --- Band 3 · the evidence pipeline ----------------------------------------------------
    Node(
        id="upload",
        kind=NodeKind.EVENT,
        label="Vendor uploads evidence",
        identity="sa-portal",
        contract=Contract(
            inputs=("vendor bytes",),
            outputs=("vendor.evidence_uploaded",),
            reads=("reviews",),
            writes=("events",),
            forbidden=("quarantine read", "any model call"),
        ),
        observed_by=("event:vendor.evidence_uploaded",),
        emits=("vendor.evidence_uploaded",),
        arrives_in=frozenset(
            {ReviewState.QUESTIONNAIRE_OUT, ReviewState.REPLIES_IN, ReviewState.EVIDENCE_REVIEW}
        ),
        note="Writes to quarantine and cannot read it back.",
    ),
    Node(
        id="screening",
        kind=NodeKind.DETERMINISTIC,
        label="Screen and promote",
        identity=_AR,
        contract=Contract(
            inputs=("quarantined object",),
            outputs=("clean object", "clean stamp", "verdict"),
            reads=("reviews", "screenings", "inert_excerpts"),
            writes=("screenings", "inert_excerpts", "events"),
            forbidden=("any generative model call", "email", "findings write"),
        ),
        observed_by=("event:evidence.screened", "collection:screenings"),
        triggered_by=("vendor.evidence_uploaded",),
        emits=("evidence.screened", "review.rescore"),
        arrives_in=frozenset(
            {ReviewState.QUESTIONNAIRE_OUT, ReviewState.REPLIES_IN, ReviewState.EVIDENCE_REVIEW}
        ),
        failure=FailurePolicy(on_exhaustion="park", park_reason="screening_unavailable"),
        note="Fails closed. A skipped detector is not a detector that found nothing.",
    ),
    Node(
        id="index",
        kind=NodeKind.DETERMINISTIC,
        label="Chunk and embed",
        identity=_EV,
        contract=Contract(
            inputs=("clean object",),
            outputs=("evidence chunks",),
            reads=("reviews",),
            writes=("evidence_chunks", *_LEDGER),
            forbidden=("the quarantine bucket", "any egress"),
            model="fast",
        ),
        observed_by=("collection:evidence_chunks",),
        triggered_by=("evidence.screened",),
        arrives_in=frozenset({ReviewState.EVIDENCE_REVIEW, ReviewState.REPLIES_IN}),
        failure=FailurePolicy(on_exhaustion="degrade", degraded_to="whole-document reconciliation"),
        note="An embedding call, which is why it is the Evidence agent's and not screening's.",
    ),
    Node(
        id="extract",
        kind=NodeKind.AGENT,
        label="Extract document facts",
        identity=_EV,
        contract=Contract(
            inputs=("clean object", "clean stamp"),
            outputs=("dated fields", "named entities"),
            reads=("reviews", "vendors"),
            writes=_LEDGER,
            forbidden=("email", "any egress", "scores write"),
            model="fast",
        ),
        observed_by=("decision:extract", "step:evidence_review"),
        triggered_by=("evidence.screened",),
        plan_step="evidence_extract",
        arrives_in=frozenset({ReviewState.EVIDENCE_REVIEW, ReviewState.REPLIES_IN}),
        failure=FailurePolicy(on_exhaustion="degrade", degraded_to="unreadable-document finding"),
        note="An unreadable document becomes a finding rather than a missing one.",
    ),
    Node(
        id="checks",
        kind=NodeKind.DETERMINISTIC,
        label="Deterministic checks",
        identity=_EV,
        contract=Contract(
            inputs=("extracted dated fields", "today"),
            outputs=("rule findings",),
            reads=("vendors",),
            writes=("findings", *_LEDGER),
            forbidden=("any model call",),
        ),
        observed_by=("finding_source:rule",),
        arrives_in=frozenset({ReviewState.EVIDENCE_REVIEW}),
        note="Runs before the model passes, so 'already derived, do not re-derive' is a fact.",
    ),
    Node(
        id="cross_exam",
        kind=NodeKind.AGENT,
        label="Cross-examine claims",
        identity=_EV,
        contract=Contract(
            inputs=("questionnaire claims", "retrieved passages"),
            outputs=("findings", "contradictions"),
            reads=("qa_responses", "evidence_chunks"),
            writes=("findings", *_LEDGER),
            forbidden=("email", "any egress", "scores write"),
            model="deep",
        ),
        observed_by=("decision:cross_exam", "finding_source:model"),
        plan_step="cross_examine",
        arrives_in=frozenset({ReviewState.EVIDENCE_REVIEW}),
        failure=FailurePolicy(on_exhaustion="park", park_reason="cross_exam_failed"),
        note="One of exactly two deep-model destinations in the fleet.",
    ),
    Node(
        id="subprocessors",
        kind=NodeKind.AGENT,
        label="Diff the fourth-party chain",
        identity=_EV,
        contract=Contract(
            inputs=("clean object", "approved-vendor register"),
            outputs=("subprocessors", "rule findings"),
            reads=("approved_vendors", "subprocessors"),
            writes=("subprocessors", "findings", *_LEDGER),
            forbidden=("approved_vendors write",),
            model="fast",
        ),
        observed_by=("vendor_collection:subprocessors",),
        plan_step="subprocessor_extract",
        arrives_in=frozenset({ReviewState.EVIDENCE_REVIEW}),
        failure=FailurePolicy(on_exhaustion="degrade", degraded_to="no chain extracted"),
        note="A model reads five fields; a set difference decides what they mean.",
    ),
    Node(
        id="findings_join",
        kind=NodeKind.JOIN,
        label="Findings join",
        identity=_EV,
        contract=Contract(
            inputs=("rule findings", "cross-exam findings", "chain findings"),
            outputs=("review.findings_ready",),
            reads=("findings",),
            writes=("findings", "idempotency", *_LEDGER),
            forbidden=("scores write",),
        ),
        observed_by=("step:evidence_review", "event:review.findings_ready"),
        emits=("review.findings_ready",),
        checkpoint="evidence_review",
        arrives_in=frozenset({ReviewState.EVIDENCE_REVIEW}),
        failure=FailurePolicy(on_exhaustion="park", park_reason="evidence_incomplete"),
        note="All three arms required. A partial finding set scored as complete is the "
        "failure this design exists to prevent.",
    ),
    # --- Band 4 · scoring and the decision -------------------------------------------------
    Node(
        id="score",
        kind=NodeKind.DETERMINISTIC,
        label="Trust Score",
        identity=_RS,
        contract=Contract(
            inputs=("findings", "rubric", "plan domains"),
            outputs=("score", "band", "breakdown"),
            reads=("findings", "reviews", "screenings"),
            writes=("scores", "idempotency", *_LEDGER),
            forbidden=("any model call", "external calls", "approvals write"),
        ),
        observed_by=("step:score", "collection:scores"),
        triggered_by=("review.findings_ready", "review.rescore"),
        checkpoint="score",
        plan_step="score",
        arrives_in=frozenset({ReviewState.EVIDENCE_REVIEW, ReviewState.SCORED}),
        advances_to=ReviewState.SCORED,
        failure=FailurePolicy(on_exhaustion="park", park_reason="scoring_failed"),
        note="Asserted by import graph to have no path to shared.routing.",
    ),
    Node(
        id="memo",
        kind=NodeKind.AGENT,
        label="Risk memo",
        identity=_RS,
        contract=Contract(
            inputs=("findings", "score", "dossier"),
            outputs=("memo", "review.score_ready"),
            reads=("findings", "scores", "vendors", "dossiers"),
            writes=("memos", "findings", "dossiers", "idempotency", *_LEDGER),
            forbidden=("sanitised content", "external calls"),
            model="deep",
        ),
        observed_by=("step:memo", "event:review.score_ready"),
        emits=("review.score_ready",),
        checkpoint="memo",
        plan_step="memo",
        arrives_in=frozenset({ReviewState.EVIDENCE_REVIEW, ReviewState.SCORED}),
        failure=FailurePolicy(on_exhaustion="park", park_reason="memo_failed"),
        note="Given a band rather than deciding one. The only destination sanitised content "
        "is inadmissible to.",
    ),
    Node(
        id="decision_gate",
        kind=NodeKind.GATE,
        label="G1 · risk acceptance",
        identity=_ORC,
        contract=Contract(
            inputs=("score", "band", "memo"),
            outputs=("approval token", "named identity"),
            reads=("scores", "memos"),
            writes=("reviews", *_LEDGER),
            forbidden=("approvals write", "signing an approval"),
        ),
        observed_by=("state:gated/decision", "card:gate/decision"),
        triggered_by=("review.score_ready",),
        emits=("review.approved",),
        plan_step="gate_decision",
        arrives_in=frozenset({ReviewState.SCORED}),
        advances_to=ReviewState.GATED,
        failure=FailurePolicy(max_attempts=1, on_exhaustion="park", park_reason="gate_unresolved"),
        note="A named human accepts the risk. The token is scoped and cannot be spent at G2.",
    ),
    Node(
        id="decide",
        kind=NodeKind.DETERMINISTIC,
        label="Record the decision",
        identity=_ORC,
        contract=Contract(
            inputs=("approval token", "identity"),
            outputs=("decided review", "dossier note"),
            reads=("reviews", "scores"),
            writes=("reviews", *_LEDGER),
            forbidden=("approvals write",),
        ),
        observed_by=("state:decided",),
        triggered_by=("review.approved",),
        arrives_in=frozenset({ReviewState.GATED}),
        advances_to=ReviewState.DECIDED,
        failure=FailurePolicy(on_exhaustion="park", park_reason="decision_write_failed"),
        note="What this review leaves for the next one is written after the transition, not "
        "before: a decision a person made must not block on a store for a review that has "
        "not started.",
    ),
    # --- Band 5 · monitoring ---------------------------------------------------------------
    Node(
        id="monitor",
        kind=NodeKind.TERMINAL,
        label="Monitored portfolio",
        identity=_WD,
        contract=Contract(
            inputs=("decided reviews",),
            outputs=("sweep schedule",),
            reads=("reviews", "vendors", "tasks"),
            writes=("tasks", *_LEDGER),
            forbidden=("approvals", "email", "vendor data write"),
        ),
        observed_by=("state:monitored",),
        triggered_by=("watchdog.sweep",),
        arrives_in=frozenset({ReviewState.MONITORED}),
        advances_to=ReviewState.MONITORED,
        note="Not terminal in the immutable sense — the sweep re-enters it and records that "
        "it ran.",
    ),
    Node(
        id="relevance_router",
        kind=NodeKind.ROUTER,
        label="Relevance router",
        identity=_WD,
        contract=Contract(
            inputs=("feed signal", "vendor identity"),
            outputs=("re-review", "triage card", "discard"),
            reads=("vendors", "tasks", "approved_vendors"),
            writes=("tasks", *_LEDGER),
            forbidden=("egress outside the allowlist", "approvals", "email"),
            model="fast",
        ),
        observed_by=("decision:relevance_router", "collection:tasks"),
        triggered_by=("watchdog.sweep",),
        emits=("watchdog.hit",),
        arrives_in=frozenset({ReviewState.MONITORED}),
        failure=FailurePolicy(on_exhaustion="skip"),
        note="A feed outage logs and skips. Monitoring never blocks an active review.",
    ),
    Node(
        id="rereview",
        kind=NodeKind.EVENT,
        label="Open a linked re-review",
        identity=_WD,
        contract=Contract(
            inputs=("high-confidence signal",),
            outputs=("review.intake",),
            reads=("reviews", "vendors"),
            writes=("reviews", "tasks", *_LEDGER),
            forbidden=("mutating the decided review",),
        ),
        observed_by=("field:reopened_from",),
        emits=("review.intake",),
        arrives_in=frozenset({ReviewState.DECIDED, ReviewState.MONITORED}),
        note="A new linked record. History is never mutated.",
    ),
    Node(
        id="needs_human",
        kind=NodeKind.TERMINAL,
        label="Parked · needs a person",
        identity="any",
        contract=Contract(
            inputs=("a park reason",),
            outputs=("dashboard card",),
            reads=("reviews",),
            writes=("reviews", *_LEDGER),
            forbidden=("silent correction",),
        ),
        observed_by=("state:needs_human", "card:parked"),
        arrives_in=frozenset({ReviewState.NEEDS_HUMAN}),
        note="Reachable from every node. A failure path that cannot be executed is worse "
        "than one never written down.",
    ),
)

EDGES: tuple[Edge, ...] = (
    Edge("intake", "recall", EdgeKind.EVENT, trigger="review.intake"),
    Edge("recall", "tier_router", EdgeKind.INLINE),
    Edge("tier_router", "plan", EdgeKind.BRANCH, guard="planner.tier_from", label="tier 1/2/3"),
    Edge("plan", "contact_gate", EdgeKind.EVENT, trigger="review.plan_ready"),
    Edge(
        "contact_gate",
        "questionnaire_send",
        EdgeKind.EVENT,
        trigger="review.approved",
        guard="gateway.verify_approval_token(scope=contact)",
        label="G2 released",
    ),
    Edge("questionnaire_send", "chase", EdgeKind.EVENT, trigger="review.chase_due"),
    Edge("chase", "chase", EdgeKind.CYCLE, trigger="review.chase_due", label="rounds 1–3"),
    Edge("questionnaire_send", "reply", EdgeKind.EVENT, trigger="vendor.reply_received"),
    Edge("reply", "reply_parse", EdgeKind.EVENT, trigger="vendor.reply_received"),
    Edge("reply_parse", "followup", EdgeKind.INLINE, guard="answer rated unusable"),
    Edge("followup", "reply", EdgeKind.CYCLE, trigger="vendor.reply_received", label="once each"),
    Edge("reply_parse", "retier_router", EdgeKind.INLINE),
    Edge(
        "retier_router",
        "questionnaire_send",
        EdgeKind.CYCLE,
        trigger="review.plan_ready",
        guard="tier raised",
        label="plan v+1",
    ),
    Edge("retier_router", "coverage_join", EdgeKind.BRANCH, guard="tier unchanged"),
    # The questionnaire is what asks for documents, so the upload is a response to it even
    # though the vendor decides when it happens. Declared as an edge because the alternative
    # is an evidence pipeline that floats unattached to the thread that requested it.
    Edge("questionnaire_send", "upload", EdgeKind.EVENT, trigger="vendor.evidence_uploaded"),
    Edge("upload", "screening", EdgeKind.EVENT, trigger="vendor.evidence_uploaded"),
    Edge("screening", "index", EdgeKind.EVENT, trigger="evidence.screened"),
    Edge("screening", "extract", EdgeKind.EVENT, trigger="evidence.screened"),
    Edge(
        "screening", "score", EdgeKind.EVENT, trigger="review.rescore", label="adversarial conduct"
    ),
    Edge(
        "coverage_join",
        "extract",
        EdgeKind.BRANCH,
        guard="coverage reached",
        label="open evidence review",
    ),
    Edge("coverage_join", "chase", EdgeKind.BRANCH, guard="coverage short", label="keep waiting"),
    Edge("extract", "checks", EdgeKind.INLINE),
    Edge("index", "cross_exam", EdgeKind.INLINE),
    Edge("extract", "cross_exam", EdgeKind.INLINE),
    Edge("extract", "subprocessors", EdgeKind.INLINE),
    Edge("checks", "findings_join", EdgeKind.INLINE),
    Edge("cross_exam", "findings_join", EdgeKind.INLINE),
    Edge("subprocessors", "findings_join", EdgeKind.INLINE),
    Edge("findings_join", "score", EdgeKind.EVENT, trigger="review.findings_ready"),
    Edge("score", "memo", EdgeKind.INLINE),
    Edge("memo", "decision_gate", EdgeKind.EVENT, trigger="review.score_ready"),
    Edge(
        "decision_gate",
        "decide",
        EdgeKind.EVENT,
        trigger="review.approved",
        guard="gateway.verify_approval_token(scope=decision)",
        label="G1 released",
    ),
    Edge("decide", "monitor", EdgeKind.INLINE),
    Edge("monitor", "relevance_router", EdgeKind.EVENT, trigger="watchdog.sweep"),
    Edge("relevance_router", "monitor", EdgeKind.BRANCH, guard="no signal", label="keep watching"),
    Edge(
        "relevance_router",
        "needs_human",
        EdgeKind.BRANCH,
        guard="confidence below threshold",
        label="triage",
    ),
    Edge("relevance_router", "rereview", EdgeKind.BRANCH, guard="confidence >= threshold"),
    Edge("rereview", "intake", EdgeKind.CYCLE, trigger="review.intake", label="new linked review"),
)

FAILURE_EDGES: tuple[Edge, ...] = tuple(
    Edge(n.id, "needs_human", EdgeKind.FAILURE, guard=n.failure.park_reason or n.id, label="park")
    for n in NODES
    if n.failure is not None and n.failure.on_exhaustion == "park"
)
"""Every park, derived from the node failure policies rather than restated beside them.

Written this way because the two would otherwise be a list and a picture of a list, and the
picture is the half that stops being true. A node whose policy changes to ``degrade`` loses its
failure edge in the diagram in the same commit.
"""

ROUTERS: tuple[Router, ...] = (
    Router(
        id="tier",
        node="tier_router",
        monotonic="scrutiny never falls",
        branches=(
            Branch(
                "plan", "customer data, production access or an AI service", "planner.tier_from"
            ),
            Branch("plan", "internal non-customer data", "planner.tier_from"),
            Branch("plan", "everything else", "planner.tier_from"),
        ),
    ),
    Router(
        id="retier",
        node="retier_router",
        monotonic="tier only ever rises",
        branches=(
            Branch(
                "questionnaire_send",
                "an answer widens the declared data scope",
                "retier.reassess_tier",
            ),
            Branch("coverage_join", "the declared scope still holds", "retier.reassess_tier"),
        ),
    ),
    Router(
        id="relevance",
        node="relevance_router",
        monotonic="a signal is never un-seen",
        branches=(
            Branch("rereview", "relevant and confidence >= 0.8", "watchdog.relevance"),
            Branch("needs_human", "relevant and confidence below 0.8", "watchdog.relevance"),
            Branch("monitor", "not relevant to this vendor", "watchdog.relevance"),
        ),
    ),
)

JOINS: tuple[Join, ...] = (
    Join(
        id="coverage",
        node="coverage_join",
        mode=JoinMode.THRESHOLD,
        threshold=0.9,
        override="an analyst marks the reply thread complete",
        on_shortfall="wait",
        arms=(
            Arm("reply_parse", True, "answers merged and rated"),
            Arm("followup", False, "unusable answers re-asked once"),
            Arm("chase", False, "outstanding questions chased"),
        ),
    ),
    Join(
        id="findings",
        node="findings_join",
        mode=JoinMode.ALL_REQUIRED,
        on_shortfall="park",
        arms=(
            Arm("checks", True, "arithmetic over extracted dates"),
            Arm("cross_exam", True, "claims reconciled against passages"),
            Arm("subprocessors", True, "the fourth-party chain diffed"),
        ),
    ),
)

CYCLES: tuple[Cycle, ...] = (
    Cycle(
        id="chase_rounds",
        through=("chase",),
        max_iterations=3,
        counted_by="reviews.chase_round",
        on_exhaustion="park at needs_human; a person owns the correspondence",
        bounded_by="an explicit round counter",
    ),
    Cycle(
        id="followup_once",
        through=("reply_parse", "followup"),
        max_iterations=1,
        counted_by="followups.asked",
        on_exhaustion="accept the answer as given and let it show as a gap",
        bounded_by="one re-ask per answer",
    ),
    Cycle(
        id="retier",
        through=("reply_parse", "retier_router", "questionnaire_send"),
        max_iterations=2,
        counted_by="reviews.plan_version",
        on_exhaustion="tier 1 is the ceiling; there is nothing further to raise to",
        bounded_by="arithmetic — three tiers and the tier only ever rises",
    ),
    Cycle(
        id="rereview",
        through=("monitor", "relevance_router", "rereview", "intake"),
        max_iterations=3,
        counted_by="reviews.reopened_from chain depth",
        on_exhaustion="raise a triage card and open nothing; a person decides whether a "
        "fourth automated re-review is the right answer",
        bounded_by="a declared budget — the feed provides no natural one",
    ),
)

ENTRY_POINTS: tuple[str, ...] = ("intake", "upload", "reply")
"""Where a review graph can be entered from outside.

Two, not one, and the second is easy to forget. ``intake`` is the fleet being asked to start
a review; ``upload`` and ``reply`` are the vendor acting — putting a document in the
quarantine bucket, or answering — which they may do at any point after contact and which the
fleet does not schedule. Reachability is checked
from both, because checking from ``intake`` alone would have quietly accepted an evidence
pipeline connected to nothing.
"""

GRAPH = Graph(nodes=NODES, edges=EDGES + FAILURE_EDGES, routers=ROUTERS, joins=JOINS, cycles=CYCLES)


def validate(graph: Graph = GRAPH) -> None:
    """Assert the declaration is structurally sound.

    Structure only. Whether the graph matches the *code* is
    ``scripts/check_contracts.py --check graph``, which needs to import the subscriber table and
    the permission matrix and therefore cannot run at import time here.

    Raises:
        GraphInvalid: listing every problem found rather than the first, because a graph edited
            by hand usually breaks in more than one place at once.
    """
    problems: list[str] = []

    ids = [n.id for n in graph.nodes]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        problems.append(f"duplicate node ids: {duplicates}")
    known = set(ids)

    for edge in graph.edges:
        for end, label in ((edge.source, "source"), (edge.target, "target")):
            if end not in known:
                problems.append(
                    f"edge {edge.source}->{edge.target} names an unknown {label} {end!r}"
                )

    for node in graph.nodes:
        if not node.observed_by:
            problems.append(
                f"{node.id} declares no observation, so a run of it cannot be projected from "
                "the ledger"
            )
        if node.kind is NodeKind.AGENT and node.contract.model is None:
            problems.append(f"{node.id} is an agent node with no model declared")
        if node.kind is NodeKind.DETERMINISTIC and node.contract.model is not None:
            # index is the documented exception: it is code that calls an embedding endpoint,
            # which is a model call the node is honest about rather than a judgement it makes.
            if node.id != "index":
                problems.append(
                    f"{node.id} is deterministic and declares a model; a node that reaches a "
                    "model is an agent node"
                )
        if node.kind is NodeKind.JOIN and graph.join(node.id) is None:
            problems.append(f"{node.id} is a join node with no declared Join policy")
        if node.kind is NodeKind.ROUTER and graph.router(node.id) is None:
            problems.append(f"{node.id} is a router node with no declared Router")

    for router in graph.routers:
        if router.node not in known:
            problems.append(f"router {router.id} is attached to unknown node {router.node!r}")
        for branch in router.branches:
            if branch.to not in known:
                problems.append(f"router {router.id} branches to unknown node {branch.to!r}")

    for join in graph.joins:
        if join.node not in known:
            problems.append(f"join {join.id} is attached to unknown node {join.node!r}")
        for arm in join.arms:
            if arm.name not in known:
                problems.append(f"join {join.id} waits on unknown node {arm.name!r}")
        if join.mode is JoinMode.THRESHOLD and join.threshold is None:
            problems.append(f"join {join.id} is a threshold join with no threshold")
        if join.mode is JoinMode.QUORUM and join.quorum is None:
            problems.append(f"join {join.id} is a quorum join with no quorum")
        if not any(arm.required for arm in join.arms):
            problems.append(f"join {join.id} has no required arm, so it is not a barrier")

    for cycle in graph.cycles:
        for node_id in cycle.through:
            if node_id not in known:
                problems.append(f"cycle {cycle.id} runs through unknown node {node_id!r}")
        if cycle.max_iterations < 1:
            problems.append(f"cycle {cycle.id} has a budget of {cycle.max_iterations}")
        if not cycle.on_exhaustion:
            problems.append(f"cycle {cycle.id} does not say what happens when its budget runs out")

    reachable: set[str] = set()
    for entry in ENTRY_POINTS:
        if entry not in known:
            problems.append(f"entry point {entry!r} is not a node")
            continue
        reachable |= _reachable_from(entry, graph)
    orphans = sorted(known - reachable)
    if orphans:
        problems.append(
            f"unreachable from {list(ENTRY_POINTS)}: {orphans}. A node nothing can reach is "
            "a node that never runs."
        )

    if problems:
        raise GraphInvalid(
            "the declared review graph is inconsistent:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )


def _reachable_from(start: str, graph: Graph) -> set[str]:
    """Nodes reachable from ``start`` over every edge kind, failure edges included."""
    seen = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for edge in graph.out_edges(current):
            if edge.target not in seen:
                seen.add(edge.target)
                frontier.append(edge.target)
    return seen


validate()
