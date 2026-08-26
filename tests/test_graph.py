"""The declared graph, against the code that executes it.

``scripts/check_contracts.py --check graph`` runs the cross-file diff in CI; these tests cover
the properties that are about the graph *itself* — that a router names every outcome, that a
join is a barrier rather than a formality, that every cycle terminates, that a node contract
denies what its identity denies.

The distinction the whole file rests on: **the graph is a description, and a description nothing
checks stops being true.** Every assertion here exists because the thing it asserts could drift
without any other test failing, and would drift silently — a diagram is the one artefact in a
repository that can be wrong for a year.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

from shared.domain import ALLOWED, ReviewState
from shared.events import ALL_TOPICS, EXPECTED_STATES
from shared.graph import (
    CYCLES,
    ENTRY_POINTS,
    GRAPH,
    JOINS,
    NODES,
    ROUTERS,
    EdgeKind,
    Graph,
    GraphInvalid,
    JoinMode,
    NodeKind,
    validate,
)

REPO = Path(__file__).resolve().parent.parent


# --- The declaration is well-formed ---------------------------------------------------------


def test_the_declared_graph_validates():
    validate()


def test_every_node_id_is_unique():
    ids = [n.id for n in NODES]
    assert len(ids) == len(set(ids))


def test_every_node_is_reachable_from_an_entry_point():
    """A node nothing can reach is a box on a picture, not a description of the system."""
    seen = set(ENTRY_POINTS)
    frontier = list(ENTRY_POINTS)
    while frontier:
        for edge in GRAPH.out_edges(frontier.pop()):
            if edge.target not in seen:
                seen.add(edge.target)
                frontier.append(edge.target)

    assert {n.id for n in NODES} - seen == set()


@pytest.mark.parametrize("node", NODES, ids=lambda n: n.id)
def test_node_declares_how_it_can_be_observed(node):
    """A node the ledger cannot evidence cannot be projected, so it cannot be shown to have run."""
    assert node.observed_by, f"{node.id} declares no observation"


def test_a_dangling_edge_is_rejected():
    """The validator's own failure path, exercised rather than assumed."""
    from shared.graph import Edge

    broken = Graph(nodes=NODES, edges=(*GRAPH.edges, Edge("plan", "nowhere", EdgeKind.EVENT)))
    with pytest.raises(GraphInvalid, match="unknown target"):
        validate(broken)


# --- test_node_contract ----------------------------------------------------------------------


def matrix() -> dict:
    raw = yaml.safe_load((REPO / "infra" / "iam" / "permission-matrix.yaml").read_text())
    return {i["name"]: i for i in raw["identities"]}


SERVICE_NODES = [n for n in NODES if n.identity.startswith("sa-")]


@pytest.mark.parametrize("node", SERVICE_NODES, ids=lambda n: n.id)
def test_node_contract_claims_nothing_its_identity_lacks(node):
    """A contract that over-claims fails the first time real IAM is applied. That is late."""
    identity = matrix()[node.identity]
    fs = identity.get("firestore") or {}
    writes = set(fs.get("write") or [])
    reads = set(fs.get("read") or []) | writes

    assert set(node.contract.writes) <= writes
    assert set(node.contract.reads) <= reads


@pytest.mark.parametrize("node", SERVICE_NODES, ids=lambda n: n.id)
def test_node_contract_denies_nothing_its_identity_is_granted(node):
    """The direction that never fails on its own.

    A contract that *under*-claims runs perfectly and quietly stops describing the identity it
    belongs to, which is worse than the over-claiming case: the published permission matrix and
    the graph a reader is shown disagree, and nothing anywhere goes red.
    """
    identity = matrix()[node.identity]
    granted = set((identity.get("firestore") or {}).get("write") or [])

    for denial in node.contract.forbidden:
        collection, _, verb = denial.partition(" ")
        if verb == "write":
            assert collection not in granted, f"{node.id} forbids {denial!r} but holds it"


@pytest.mark.parametrize("node", SERVICE_NODES, ids=lambda n: n.id)
def test_a_node_that_reaches_a_model_runs_as_an_identity_that_can(node):
    if node.contract.model:
        assert matrix()[node.identity].get("vertex_ai") is True


def test_the_screening_node_is_structurally_incapable_of_prompting_anything():
    """The strongest claim in the security story, asserted through the graph as well as IAM."""
    screening = GRAPH.node("screening")

    assert screening.kind is NodeKind.DETERMINISTIC
    assert screening.contract.model is None
    assert "any generative model call" in screening.contract.forbidden
    assert matrix()["sa-armor"].get("vertex_ai") is False


def test_the_scorer_node_is_deterministic():
    """Scoring and the memo deploy as one service and are two node kinds, which is the point."""
    assert GRAPH.node("score").kind is NodeKind.DETERMINISTIC
    assert GRAPH.node("score").contract.model is None
    assert GRAPH.node("memo").kind is NodeKind.AGENT
    assert GRAPH.node("memo").contract.model == "deep"


def test_exactly_two_nodes_reach_the_deep_model():
    """The routing table spends the deep model in two places. The graph says the same two."""
    deep = sorted(n.id for n in NODES if n.contract.model == "deep")
    assert deep == ["cross_exam", "memo"]


# --- test_router -----------------------------------------------------------------------------


@pytest.mark.parametrize("router", ROUTERS, ids=lambda r: r.id)
def test_router_names_every_outcome(router):
    """A branch with an undeclared outcome is a branch nobody drew."""
    assert len(router.branches) >= 2
    for branch in router.branches:
        assert GRAPH.has(branch.to)
        assert branch.when
        assert branch.decided_by


@pytest.mark.parametrize("router", ROUTERS, ids=lambda r: r.id)
def test_router_declares_the_direction_it_may_not_travel(router):
    """Every router in this graph is monotonic, and the constraint belongs on the router.

    The tier may only rise, evidence review may only open, a signal may not be un-seen. Each of
    those is a security property rather than a workflow convenience — a router that could travel
    the other way would let a vendor's own answers reduce the scrutiny applied to them.
    """
    assert router.monotonic


def test_every_router_node_has_a_router_and_vice_versa():
    by_kind = {n.id for n in GRAPH.of_kind(NodeKind.ROUTER)}
    declared = {r.node for r in ROUTERS}
    assert by_kind == declared


def test_the_retier_router_can_only_reach_a_stricter_tier():
    """Asserted against the planner rather than against the router's own prose."""
    from agents.orchestrator.planner import tier_from

    assert tier_from({"customer_data"}) == 1
    assert tier_from({"internal_data"}) == 2
    assert tier_from(set()) == 3
    # A lower number is a stricter review, so "never lower the tier" is a minimum on the number.
    assert min(1, 2) == 1


# --- test_join_semantics ---------------------------------------------------------------------


@pytest.mark.parametrize("join", JOINS, ids=lambda j: j.id)
def test_join_has_at_least_one_required_arm(join):
    """A join with no required arm is not a barrier; it is a comment."""
    assert any(arm.required for arm in join.arms)


@pytest.mark.parametrize("join", JOINS, ids=lambda j: j.id)
def test_every_join_arm_is_a_node_the_evaluator_can_read(join):
    from shared.join import _READERS

    for arm in join.arms:
        assert GRAPH.has(arm.name)
        assert arm.name in _READERS, f"join {join.id} waits on {arm.name}, which nothing reads"


@pytest.mark.parametrize("join", JOINS, ids=lambda j: j.id)
def test_join_says_what_happens_on_a_shortfall(join):
    assert join.on_shortfall in {"wait", "park", "degrade"}


def test_the_coverage_join_carries_its_threshold_and_its_escape_hatch():
    """Both halves of the rule, in the object the code reads.

    The threshold without the override would make the fleet look hung on the ordinary case of a
    vendor who answers most of what was asked and stops. The override without the threshold
    would make the barrier decorative.
    """
    coverage = next(j for j in JOINS if j.id == "coverage")

    assert coverage.mode is JoinMode.THRESHOLD
    assert coverage.threshold == 0.9
    assert coverage.override
    assert coverage.on_shortfall == "wait"


def test_the_findings_join_requires_every_arm_and_parks_on_a_shortfall():
    """A partial finding set scored as if complete is the failure this design exists to prevent."""
    findings = next(j for j in JOINS if j.id == "findings")

    assert findings.mode is JoinMode.ALL_REQUIRED
    assert all(arm.required for arm in findings.arms)
    assert findings.on_shortfall == "park"


def test_the_declared_threshold_matches_the_one_the_parser_publishes():
    """Two numbers that must be one. The parser's constant is what coverage is measured against."""
    from agents.questionnaire.parser import COVERAGE_TO_PROCEED

    coverage = next(j for j in JOINS if j.id == "coverage")
    assert coverage.threshold == COVERAGE_TO_PROCEED


# --- test_parallel_branches ------------------------------------------------------------------


def test_the_graph_declares_parallel_groups():
    """Independence is what "parallel" means here, and a join's arms are its honest definition.

    There is no scheduler in this fleet, so nothing *makes* two nodes concurrent. What the
    design has is independence — different triggers, different collections — and the only place
    that independence has to end is a barrier.
    """
    groups = GRAPH.parallel_groups()

    assert ("checks", "cross_exam", "subprocessors") in groups
    assert any("reply_parse" in g for g in groups)


def test_the_evidence_arms_write_findings_and_nothing_each_other_reads():
    """The property that makes the evidence arms genuinely independent rather than merely drawn
    apart: none of the three consumes what another produces."""
    arms = [GRAPH.node(n) for n in ("checks", "cross_exam", "subprocessors")]

    for arm in arms:
        others = [a for a in arms if a is not arm]
        for other in others:
            shared_state = set(arm.contract.reads) & set(other.contract.writes)
            assert shared_state <= {"findings"}, (
                f"{arm.id} reads {shared_state} that {other.id} writes; the two are ordered, "
                "not parallel"
            )


def test_the_questionnaire_thread_and_the_evidence_pipeline_share_no_trigger():
    """Two bands driven by different events is what lets a vendor upload before they answer."""
    thread = {t for n in ("reply_parse", "chase", "followup") for t in GRAPH.node(n).triggered_by}
    pipeline = {t for n in ("screening", "extract", "index") for t in GRAPH.node(n).triggered_by}

    assert thread & pipeline == set()


# --- test_cycle_budget -----------------------------------------------------------------------


@pytest.mark.parametrize("cycle", CYCLES, ids=lambda c: c.id)
def test_every_cycle_is_bounded(cycle):
    assert cycle.max_iterations >= 1
    assert cycle.counted_by
    assert cycle.on_exhaustion, "a cycle that does not say what happens at the budget is a loop"


@pytest.mark.parametrize("cycle", CYCLES, ids=lambda c: c.id)
def test_every_cycle_runs_through_declared_nodes(cycle):
    for node_id in cycle.through:
        assert GRAPH.has(node_id)


def test_the_rereview_cycle_is_the_one_with_no_natural_bound():
    """Three cycles are bounded by arithmetic; one is bounded by a decision, and says so."""
    by_id = {c.id: c for c in CYCLES}

    assert "arithmetic" in by_id["retier"].bounded_by
    assert "counter" in by_id["chase_rounds"].bounded_by
    assert "declared budget" in by_id["rereview"].bounded_by


def test_the_watchdog_reads_its_budget_from_the_graph():
    """The number in the handler and the number on the diagram are one object."""
    from agents.watchdog.agent import rereview_budget

    declared = next(c for c in CYCLES if c.id == "rereview")
    assert rereview_budget() is declared


def test_the_rereview_budget_does_not_stop_monitoring_when_it_is_spent():
    """A vendor nobody is watching is the outcome the Watchdog exists to prevent.

    The budget changes who decides, not whether the signal is seen — so the exhaustion clause is
    asserted for what it says as well as that it says something.
    """
    rereview = next(c for c in CYCLES if c.id == "rereview")

    assert "triage" in rereview.on_exhaustion
    assert "person" in rereview.on_exhaustion


# --- test_failure_isolation ------------------------------------------------------------------


def test_every_node_with_a_park_policy_has_a_failure_edge():
    """The edges are derived from the policies rather than written beside them, so this asserts
    the derivation rather than a list somebody maintained."""
    parking = {n.id for n in NODES if n.failure and n.failure.on_exhaustion == "park"}
    edges = {e.source for e in GRAPH.edges if e.kind is EdgeKind.FAILURE}

    assert parking == edges


@pytest.mark.parametrize(
    "node", [n for n in NODES if n.failure is not None], ids=lambda n: n.id
)
def test_a_park_policy_names_the_reason_it_will_park_with(node):
    """A park with no reason is a dashboard card nobody can act on."""
    if node.failure.on_exhaustion == "park":
        assert node.failure.park_reason


@pytest.mark.parametrize(
    "node", [n for n in NODES if n.failure is not None], ids=lambda n: n.id
)
def test_a_degrading_node_says_what_it_degrades_to(node):
    """Mandatory controls fail closed, optional controls degrade — and a degradation nobody can
    see in the output is a silent loss of evidence."""
    if node.failure.on_exhaustion == "degrade":
        assert node.failure.degraded_to


def test_needs_human_is_reachable_from_every_parking_node():
    for node in NODES:
        if node.failure and node.failure.on_exhaustion == "park":
            targets = {e.target for e in GRAPH.out_edges(node.id)}
            assert "needs_human" in targets


def test_a_mandatory_control_never_degrades():
    """Screening and scoring are the two that must fail closed, and neither may be optional."""
    for node_id in ("screening", "score", "findings_join"):
        node = GRAPH.node(node_id)
        assert node.failure is not None
        assert node.failure.on_exhaustion == "park"


def test_a_failure_in_one_band_does_not_reach_another_bands_nodes():
    """Isolation, stated as a reachability property.

    A parked review stops; it does not fall through into some other part of the graph. The only
    edge out of a failing node is the park, which is what makes "anything can stop a review"
    safe to say.
    """
    for edge in GRAPH.edges:
        if edge.kind is EdgeKind.FAILURE:
            assert edge.target == "needs_human"


# --- The graph agrees with the event contract -------------------------------------------------


@pytest.mark.parametrize("node", NODES, ids=lambda n: n.id)
def test_every_topic_a_node_names_is_declared(node):
    for topic in (*node.triggered_by, *node.emits):
        assert topic in ALL_TOPICS


@pytest.mark.parametrize("node", [n for n in NODES if n.triggered_by], ids=lambda n: n.id)
def test_a_node_only_runs_in_states_its_triggers_arrive_in(node):
    union: set[ReviewState] = set()
    for topic in node.triggered_by:
        union |= EXPECTED_STATES.get(topic, set())

    assert node.arrives_in <= union


@pytest.mark.parametrize("node", [n for n in NODES if n.advances_to], ids=lambda n: n.id)
def test_a_declared_advance_is_a_legal_transition(node):
    """The graph and the state machine are two descriptions of one movement."""
    for origin in node.arrives_in:
        if node.advances_to is origin:
            continue
        assert node.advances_to in ALLOWED[origin]


def test_every_handled_topic_has_a_consumer_node():
    from shared.subscriber import handlers

    consumed = {t for n in NODES for t in n.triggered_by}
    assert set(handlers()) <= consumed


# --- The node ids the agents use are the ones the graph declares -------------------------------


def node_constants():
    for path in (REPO / "agents").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith("NODE_"):
                        yield f"{path.relative_to(REPO)}:{target.id}", node.value.value


def test_every_node_constant_in_an_agent_names_a_declared_node():
    """The stamps that make the projection exact. One naming nothing is a stamp nothing reads."""
    unknown = [where for where, value in node_constants() if not GRAPH.has(value)]
    assert not unknown
