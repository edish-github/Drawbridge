/**
 * The review graph, and one review's path through it.
 *
 * The topology is **not declared here.** `graph.json` is generated from `shared/graph.py` by
 * `scripts/graph_dump.py`, because the alternative to generating it is maintaining the same
 * twenty-eight nodes in two languages and discovering they disagree on a screen somebody is
 * presenting. `make graph` rewrites it and `tests/test_graph_ui.py` fails if the committed copy
 * has gone stale.
 *
 * What *is* here is the projection loop: resolving each node's declared observations against
 * this review's ledger. It mirrors `shared/graph_run.py` and that duplication is real, so it is
 * bounded and checked rather than tolerated. Bounded, because the rules being resolved live in
 * the generated file and only the resolving is written twice; checked, because the observation
 * forms this module handles are asserted against the Python resolvers by a test. A form added on
 * one side and not the other fails there rather than rendering a node as never having run.
 *
 * The reason the loop is not simply called over an API: the dashboard has no API, and the
 * absence of one is the design — a surface every reviewer can reach holds no write path and no
 * server route that can be made to do work. A read-only page reading a ledger is what it is.
 */

import graphDefinition from "./graph.json";
import { db, type Doc } from "./ledger";

export type NodeKind =
  | "event"
  | "router"
  | "agent"
  | "deterministic"
  | "join"
  | "gate"
  | "terminal";

export type GraphNode = {
  id: string;
  kind: NodeKind;
  label: string;
  identity: string;
  checkpoint: string | null;
  plan_step: string | null;
  triggered_by: string[];
  emits: string[];
  arrives_in: string[];
  advances_to: string | null;
  observed_by: string[];
  note: string;
  contract: {
    inputs: string[];
    outputs: string[];
    reads: string[];
    writes: string[];
    forbidden: string[];
    model: string | null;
  };
  failure: {
    max_attempts: number;
    on_exhaustion: string;
    park_reason: string;
    degraded_to: string | null;
  } | null;
};

export type GraphEdge = {
  source: string;
  target: string;
  kind: string;
  trigger: string;
  guard: string;
  label: string;
};

export type Graph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  routers: { id: string; node: string; monotonic: string; branches: Doc[] }[];
  joins: {
    id: string;
    node: string;
    mode: string;
    threshold: number | null;
    override: string | null;
    on_shortfall: string;
    arms: { name: string; required: boolean; describes: string }[];
  }[];
  cycles: {
    id: string;
    through: string[];
    max_iterations: number;
    counted_by: string;
    on_exhaustion: string;
    bounded_by: string;
  }[];
};

export const GRAPH = graphDefinition as unknown as Graph;

export type Status =
  | "pending"
  | "running"
  | "complete"
  | "waiting"
  | "failed"
  | "unknown";

export type NodeRun = {
  node: GraphNode;
  status: Status;
  startedAt: string;
  finishedAt: string;
  detail: string;
  evidence: string[];
};

/**
 * Observation forms this module resolves.
 *
 * Exported so a test can compare it against `shared.graph_run._RESOLVERS`. A form handled on one
 * side and not the other is the exact way two projections drift, and it drifts silently: the
 * unhandled form contributes no evidence, so a node that ran renders as one that did not.
 */
export const OBSERVATION_FORMS = [
  "step",
  "event",
  "state",
  "card",
  "collection",
  "vendor_collection",
  "field",
  "decision",
  "finding_source",
] as const;

const WATCHED = [
  "evidence_chunks",
  "followups",
  "screenings",
  "qa_responses",
  "scores",
  "memos",
  "tasks",
];

type Ledger = {
  review: Doc;
  events: Doc[];
  decisions: Doc[];
  cards: Doc[];
  findings: Doc[];
  counts: Record<string, number>;
};

async function rows(collection: string, field: string, value: string): Promise<Doc[]> {
  const snapshot = await db().collection(collection).where(field, "==", value).get();
  return snapshot.docs.map((d) => d.data() as Doc);
}

async function load(reviewId: string): Promise<Ledger | null> {
  const snapshot = await db().collection("reviews").doc(reviewId).get();
  if (!snapshot.exists) return null;
  const review = snapshot.data() as Doc;

  const [events, decisions, cards, findings, ...watched] = await Promise.all([
    rows("events", "review_id", reviewId),
    rows("decisions", "review_id", reviewId),
    rows("dashboard_events", "review_id", reviewId),
    rows("findings", "review_id", reviewId),
    ...WATCHED.map((c) => rows(c, "review_id", reviewId)),
    // Keyed by vendor rather than review: a subprocessor is a fact about the company and
    // survives the review that discovered it.
    rows("subprocessors", "vendor_id", String(review.vendor_id ?? "")),
  ]);

  const counts: Record<string, number> = {};
  WATCHED.forEach((name, i) => (counts[name] = watched[i].length));
  counts.subprocessors = watched[WATCHED.length].length;

  return {
    review,
    events: events.sort((a, b) => String(a.ts ?? "").localeCompare(String(b.ts ?? ""))),
    decisions,
    cards,
    findings,
    counts,
  };
}

/** Whether a recorded checkpoint is this node's, under any plan version. */
function stepMatches(declared: string, recorded: string): boolean {
  return recorded === declared || recorded.startsWith(`${declared}@plan_v`);
}

type Hit = { at: string; why: string };

function resolve(observation: string, ledger: Ledger): Hit[] {
  const [form, ...rest] = observation.split(":");
  const argument = rest.join(":");

  switch (form) {
    case "step": {
      // A checkpoint proves a node ran and deliberately contributes no timestamp: `step_started`
      // is one field overwritten by whichever step began most recently.
      const completed: string[] = ledger.review.completed_steps ?? [];
      const current = String(ledger.review.current_step ?? "");
      const hits = completed
        .filter((s) => stepMatches(argument, String(s)))
        .map((s) => ({ at: "", why: `checkpoint ${s}` }));
      if (current && stepMatches(argument, current)) {
        hits.push({ at: "", why: `checkpoint ${current} in flight` });
      }
      return hits;
    }
    case "event":
      return ledger.events
        .filter((e) => e.type === argument && !e.addendum)
        .map((e) => ({ at: String(e.ts ?? ""), why: argument }));
    case "state": {
      const [state, scope] = argument.split("/");
      const hits = ledger.events
        .filter((e) => String(e.to_state ?? "") === state)
        .map((e) => ({ at: String(e.ts ?? ""), why: `reached ${state}` }));
      if (!scope) return hits;
      // A transition event cannot say which gate: the scope is not on the transition, so both
      // gates would otherwise be timed by each other's events.
      const onReview = String(ledger.review.gate_scope ?? "") === scope;
      const scoped = ledger.cards.filter((c) => String(c.gate_scope ?? "") === scope);
      if (!onReview && scoped.length === 0) return [];
      return scoped.length
        ? scoped.map((c) => ({ at: String(c.at ?? ""), why: `${scope} gate` }))
        : [{ at: "", why: `${scope} gate, still parked` }];
    }
    case "card": {
      const [kind, scope] = argument.split("/");
      let matches = ledger.cards.filter((c) => String(c.kind ?? "") === kind);
      if (scope) matches = matches.filter((c) => String(c.gate_scope ?? "") === scope);
      return matches.map((c) => ({
        at: String(c.at ?? ""),
        why: `card ${kind}${scope ? `/${scope}` : ""}`,
      }));
    }
    case "collection":
    case "vendor_collection": {
      const count = ledger.counts[argument] ?? 0;
      return count ? [{ at: "", why: `${count} document(s) in ${argument}` }] : [];
    }
    case "field":
      return ledger.review[argument] ? [{ at: "", why: `${argument} set` }] : [];
    case "decision":
      return ledger.decisions
        .filter((d) => String(d.node ?? "") === argument)
        .map((d) => ({ at: String(d.at ?? ""), why: String(d.decision ?? "") }));
    case "finding_source": {
      const matching = ledger.findings.filter((f) => String(f.source ?? "") === argument);
      return matching.length
        ? [{ at: "", why: `${matching.length} ${argument} finding(s)` }]
        : [];
    }
    default:
      return [];
  }
}

function statusFor(node: GraphNode, ledger: Ledger, evidenced: boolean): Status {
  const parkReason = String(ledger.review.park_reason ?? "");
  if (node.failure && parkReason && parkReason === node.failure.park_reason) return "failed";

  const state = String(ledger.review.state ?? "");

  if (!evidenced) {
    // A gate or a join is reached by the review standing in a state it waits in, whether or not
    // anything has been written about it yet.
    if ((node.kind === "gate" || node.kind === "join") && node.arrives_in.includes(state)) {
      return "waiting";
    }
    return "pending";
  }

  if (node.checkpoint) {
    const completed: string[] = ledger.review.completed_steps ?? [];
    const current = String(ledger.review.current_step ?? "");
    if (current && stepMatches(node.checkpoint, current)) return "running";
    if (!completed.some((s) => stepMatches(node.checkpoint!, String(s)))) return "running";
  }

  if (node.kind === "gate" && state === "gated") return "waiting";
  if (node.kind === "join" && !joinPassed(node, ledger)) return "waiting";

  return "complete";
}

function joinPassed(node: GraphNode, ledger: Ledger): boolean {
  const reached = new Set(ledger.events.map((e) => String(e.to_state ?? "")));
  if (node.id === "coverage_join") {
    return ["evidence_review", "scored", "gated", "decided", "monitored"].some((s) =>
      reached.has(s),
    );
  }
  if (node.id === "findings_join") {
    return ledger.events.some((e) => e.type === "review.findings_ready");
  }
  return true;
}

export type Projection = {
  reviewId: string;
  state: string;
  planVersion: number;
  parkReason: string;
  runs: NodeRun[];
  path: string[];
  counts: Record<string, number>;
};

/** Project the declared graph onto one review. `null` when the review does not exist. */
export async function projectGraph(reviewId: string): Promise<Projection | null> {
  const ledger = await load(reviewId);
  if (!ledger) return null;

  const runs: NodeRun[] = GRAPH.nodes.map((node) => {
    const hits = node.observed_by.flatMap((o) => resolve(o, ledger));
    const stamps = hits.map((h) => h.at).filter(Boolean).sort();
    const status = statusFor(node, ledger, hits.length > 0);

    return {
      node,
      status,
      startedAt: stamps[0] ?? "",
      finishedAt: stamps[stamps.length - 1] ?? "",
      detail: Array.from(new Set(hits.map((h) => h.why).filter(Boolean))).join("; "),
      evidence: Array.from(new Set(hits.map((h) => h.why))),
    };
  });

  // Timestamps order the nodes that have them; declaration order orders the ones that do not,
  // and untimed nodes go last.
  //
  // Ranked explicitly rather than by sorting a sentinel string. The first version used
  // `(a.startedAt || "~").localeCompare(...)`, mirroring the Python, and produced the opposite
  // order: `localeCompare` is locale collation, which sorts punctuation *before* digits, while
  // Python's `<` compares code points, where `~` is above them. The two implementations agreed
  // on every node and disagreed on the sentinel, which is the sort of divergence that only ever
  // shows up on screen.
  const order = new Map(GRAPH.nodes.map((n, i) => [n.id, i]));
  const path = runs
    .filter((r) => r.status !== "pending")
    .sort((a, b) => {
      if (a.startedAt && b.startedAt && a.startedAt !== b.startedAt) {
        return a.startedAt < b.startedAt ? -1 : 1;
      }
      if (Boolean(a.startedAt) !== Boolean(b.startedAt)) return a.startedAt ? -1 : 1;
      return (order.get(a.node.id) ?? 0) - (order.get(b.node.id) ?? 0);
    })
    .map((r) => r.node.id);

  const counts: Record<string, number> = {};
  runs.forEach((r) => (counts[r.status] = (counts[r.status] ?? 0) + 1));

  return {
    reviewId,
    state: String(ledger.review.state ?? ""),
    planVersion: Number(ledger.review.plan_version ?? 1),
    parkReason: String(ledger.review.park_reason ?? ""),
    runs,
    path,
    counts,
  };
}

/** Milliseconds between a node's first and last observation, or null when undefined. */
export function durationMs(run: NodeRun): number | null {
  if (!run.startedAt || !run.finishedAt) return null;
  const start = Date.parse(run.startedAt);
  const end = Date.parse(run.finishedAt);
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.max(0, end - start);
}

/** The five reading bands, in the order the review moves through them. */
export const BANDS: { title: string; members: string[] }[] = [
  { title: "Intake and planning", members: ["intake", "recall", "tier_router", "plan"] },
  {
    title: "The questionnaire thread",
    members: [
      "contact_gate",
      "questionnaire_send",
      "reply",
      "reply_parse",
      "followup",
      "chase",
      "retier_router",
      "coverage_join",
    ],
  },
  {
    title: "The evidence pipeline",
    members: [
      "upload",
      "screening",
      "index",
      "extract",
      "checks",
      "cross_exam",
      "subprocessors",
      "findings_join",
    ],
  },
  {
    title: "Scoring and the decision",
    members: ["score", "memo", "decision_gate", "decide"],
  },
  { title: "Monitoring", members: ["monitor", "relevance_router", "rereview"] },
  { title: "Parked", members: ["needs_human"] },
];
