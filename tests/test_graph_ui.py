"""The dashboard's copy of the graph, against the one the fleet runs on.

The dashboard is TypeScript and ``shared/graph.py`` is Python, so the topology reaches the
screen through a generated file. That is the cheaper of the two bad options — the other is
declaring twenty-eight nodes twice and finding out they disagree on a screen somebody is
presenting — but generation is only safe if the committed artefact is checked against its source.
So: regenerate, and diff.

The second half is the harder problem. ``services/dashboard/lib/graph.ts`` reimplements the
projection loop, because the dashboard has no API and the absence of one is the design. The
duplication is bounded — the *rules* live in the generated file and only the *resolving* is
written twice — and this is where the bound is enforced: an observation form handled on one side
and not the other renders a node that ran as one that did not, silently, on exactly the screen
that exists to say what ran.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GRAPH_JSON = REPO / "services" / "dashboard" / "lib" / "graph.json"
GRAPH_TS = REPO / "services" / "dashboard" / "lib" / "graph.ts"


def test_the_committed_graph_json_is_not_stale():
    """``make graph`` regenerates it; this fails when somebody edited the graph and forgot."""
    from scripts.graph_dump import as_json

    committed = json.loads(GRAPH_JSON.read_text())
    assert committed == json.loads(json.dumps(as_json())), (
        "services/dashboard/lib/graph.json no longer matches shared/graph.py. "
        "Run `make graph` and commit the result."
    )


def test_the_dashboard_resolves_every_observation_form_the_fleet_declares():
    """A form the dashboard cannot resolve contributes no evidence and shows a node as pending."""
    from shared.graph_run import _RESOLVERS

    declared = set(_RESOLVERS)
    handled = set(_ts_observation_forms())

    assert declared == handled, (
        f"python resolves {sorted(declared)} and the dashboard resolves {sorted(handled)}; "
        "a form on one side only renders a node that ran as one that did not"
    )


def test_every_observation_form_used_in_the_graph_is_resolved_by_both():
    from shared.graph import GRAPH
    from shared.graph_run import _RESOLVERS

    used = {o.partition(":")[0] for n in GRAPH.nodes for o in n.observed_by}
    handled = set(_ts_observation_forms())

    assert used <= set(_RESOLVERS)
    assert used <= handled


def test_every_node_the_graph_declares_is_placed_in_a_reading_band():
    """A node in no band renders nowhere. The page iterates bands, not nodes."""
    from shared.graph import GRAPH

    banded = set()
    for members in re.findall(r"members:\s*\[([^\]]*)\]", GRAPH_TS.read_text(), re.DOTALL):
        banded |= {m.strip().strip('",') for m in members.split(",") if m.strip()}

    missing = {n.id for n in GRAPH.nodes} - banded
    assert not missing, f"these nodes are in no band and would not render: {sorted(missing)}"


def test_the_dashboard_graph_module_holds_no_write_path():
    """A surface every reviewer can reach must not be one that can act.

    The same rule ``lib/ledger.ts`` follows, asserted here because a projection is exactly the
    sort of module somebody would be tempted to have cache its results.
    """
    source = GRAPH_TS.read_text()

    for forbidden in (".set(", ".add(", ".update(", ".delete("):
        assert forbidden not in source, f"the dashboard graph module calls {forbidden}"


def _ts_observation_forms() -> list[str]:
    """The forms ``lib/graph.ts`` says it resolves, read out of its exported list."""
    source = GRAPH_TS.read_text()
    block = re.search(r"OBSERVATION_FORMS = \[(.*?)\] as const", source, re.DOTALL)
    assert block, "lib/graph.ts no longer exports OBSERVATION_FORMS"
    return [m.strip().strip('",') for m in block.group(1).split(",") if m.strip()]
