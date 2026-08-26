/**
 * The fleet as the graph describes it, rather than as a list somebody maintained here.
 *
 * The Agent Registry and the fleet-status panel both need to answer "which agents exist, what
 * may each one touch, and what is each one structurally forbidden". That is exactly what the
 * node contracts in `shared/graph.py` hold, and they are checked against
 * `infra/iam/permission-matrix.yaml` on every push — so deriving the registry from the graph
 * means the console cannot show a permission the matrix does not grant.
 *
 * A hand-written copy would have been quicker and would have started drifting the first time an
 * agent gained a collection.
 */

import { GRAPH, type GraphNode } from "./graph";

export type AgentSummary = {
  identity: string;
  label: string;
  kind: "agent" | "service";
  nodes: GraphNode[];
  /** Firestore collections the identity's nodes read or write, deduped. */
  reads: string[];
  writes: string[];
  /** Capabilities declared forbidden across its nodes. The half that matters. */
  forbidden: string[];
  models: string[];
  /** The review state this identity is doing work in, for a live-ish activity read. */
  activeState: string;
  blurb: string;
};

const DISPLAY: Record<string, { label: string; kind: "agent" | "service"; state: string; blurb: string }> = {
  "sa-orchestrator": {
    label: "Orchestrator",
    kind: "agent",
    state: "intake",
    blurb:
      "Turns an intake request into a tiered review plan, dispatches its steps, re-tiers upward when evidence contradicts the intake form, and owns every forward transition.",
  },
  "sa-questionnaire": {
    label: "Questionnaire",
    kind: "agent",
    state: "questionnaire_out",
    blurb:
      "Selects tier-appropriate questions from a curated bank, delivers them once a human has authorised contact, parses replies incrementally and chases on a schedule.",
  },
  "sa-evidence": {
    label: "Evidence",
    kind: "agent",
    state: "evidence_review",
    blurb:
      "Reads screened documents, extracts dated fields, retrieves the passage behind each claim and cross-examines it against the questionnaire. Holds nothing to actuate with.",
  },
  "sa-scorer": {
    label: "Risk Scorer",
    kind: "agent",
    state: "scored",
    blurb:
      "Converts findings into a Trust Score by arithmetic over rubric.yaml, then writes one memo. No model touches the number.",
  },
  "sa-watchdog": {
    label: "Watchdog",
    kind: "agent",
    state: "monitored",
    blurb:
      "Sweeps allowlisted feeds and certificate horizons across the approved portfolio, and opens a linked re-review on a confirmed signal.",
  },
  "sa-armor": {
    label: "Screening Pipeline",
    kind: "service",
    state: "",
    blurb:
      "Extracts text locally, screens every vendor-origin byte through Model Armor, and signs the clean-stamp. The one component structurally incapable of prompting anything.",
  },
  "sa-portal": {
    label: "Vendor Portal",
    kind: "service",
    state: "",
    blurb:
      "The vendor-facing surface. Writes to quarantine and cannot read it back; publishes replies and uploads onto the event backbone.",
  },
};

function unique(values: string[]): string[] {
  return [...new Set(values)].sort();
}

export const AGENT_NODES: AgentSummary[] = Object.entries(DISPLAY).map(([identity, meta]) => {
  const nodes = GRAPH.nodes.filter((n) => n.identity === identity);
  return {
    identity,
    label: meta.label,
    kind: meta.kind,
    nodes,
    reads: unique(nodes.flatMap((n) => n.contract.reads)),
    writes: unique(nodes.flatMap((n) => n.contract.writes)),
    forbidden: unique(nodes.flatMap((n) => n.contract.forbidden)),
    models: unique(nodes.map((n) => n.contract.model).filter((m): m is string => Boolean(m))),
    activeState: meta.state,
    blurb: meta.blurb,
  };
});

/** Nodes owned by nobody in `DISPLAY` — the operator boundary and the park. Shown separately. */
export const UNOWNED_NODES: GraphNode[] = GRAPH.nodes.filter((n) => !(n.identity in DISPLAY));
