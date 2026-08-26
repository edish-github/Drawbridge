/**
 * One review's path through the review graph. What the fleet did, as a shape rather than a list.
 *
 * The timeline answers *what happened, in order*. This answers *what was supposed to happen, and
 * which of it did* — a different question, and the one somebody asks when a review is stalled. A
 * stream shows the eleven things that occurred; the graph shows those eleven against the
 * twenty-eight that were declared, and the gap is the answer.
 *
 * Every node expands to its contract: what it takes, what it produces, what its identity may
 * read and write, and what it is structurally forbidden. That expansion is the argument the
 * whole product rests on — an agent's boundaries are not a paragraph in a document, they are
 * rows in the permission matrix, and this is where a reader can see the two are the same thing.
 */

import Link from "next/link";
import {
  BANDS as GRAPH_BANDS,
  GRAPH,
  durationMs,
  projectGraph,
  type NodeRun,
  type Status,
} from "../../../../../lib/graph";
import { Empty, KeyValue, LoadError, PageHead, Pill } from "../../../../components";
import type { Tone } from "../../../../../lib/ledger";
import { requirePrincipal } from "../../../../../lib/guard";

export const dynamic = "force-dynamic";

const STATUS: Record<Status, { label: string; tone: Tone }> = {
  complete: { label: "complete", tone: "green" },
  running: { label: "running", tone: "blue" },
  waiting: { label: "waiting", tone: "amber" },
  failed: { label: "failed", tone: "red" },
  pending: { label: "not reached", tone: "gray" },
  unknown: { label: "unreadable", tone: "amber" },
};

export default async function ReviewGraph({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const principal = await requirePrincipal();
  const orgId = principal.orgId;

  let projection: Awaited<ReturnType<typeof projectGraph>>;
  try {
    projection = await projectGraph(id);
  } catch (error) {
    return (
      <>
        <PageHead crumb="Reviews / Graph" title="Review graph" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  if (!projection) {
    return (
      <>
        <PageHead crumb="Reviews / Graph" title="Review not found" subtitle={`No review with id ${id}.`} />
        <div className="scroll-area" />
      </>
    );
  }

  const byId = new Map(projection.runs.map((r) => [r.node.id, r]));
  const reached = projection.runs.filter((r) => r.status !== "pending").length;

  return (
    <>
      <PageHead
        crumb={`Reviews / ${id} / Graph`}
        title="Review graph"
        subtitle={`${reached} of ${GRAPH.nodes.length} declared nodes reached · plan v${projection.planVersion} · reconstructed from the ledger alone`}
        actions={
          <>
            <Link href={`/reviews/${id}`} className="filter">
              Timeline
            </Link>
            <Link href={`/reviews/${id}/gate`} className="filter">
              Gate card
            </Link>
          </>
        }
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        {projection.parkReason ? (
          <div className="banner">
            <strong>Parked.</strong> The review stopped with reason{" "}
            <span className="mono">{projection.parkReason}</span>. The node it stopped at is
            marked below; every other node is left exactly as it was.
          </div>
        ) : null}

        <div className="filters">
          {Object.entries(projection.counts)
            .sort()
            .map(([status, count]) => (
              <span key={status} className="filter" data-active={status === "failed" ? "true" : undefined}>
                {STATUS[status as Status]?.label ?? status} · {count}
              </span>
            ))}
        </div>

        <div className="stack">
          {GRAPH_BANDS.map((band) => {
            const members = band.members.map((m) => byId.get(m)).filter(Boolean) as NodeRun[];
            if (members.length === 0) return null;
            return (
              <div className="card" key={band.title}>
                <span className="label">{band.title}</span>
                <div style={{ marginTop: 10 }}>
                  {members.map((run) => (
                    <NodeCard key={run.node.id} run={run} />
                  ))}
                </div>
              </div>
            );
          })}

          <div className="card">
            <span className="label">Path taken</span>
            {projection.path.length === 0 ? (
              <Empty>Nothing has run against this review yet.</Empty>
            ) : (
              <p className="mono small" style={{ lineHeight: 2, marginTop: 10, color: "var(--body)" }}>
                {projection.path.join(" → ")}
              </p>
            )}
          </div>

          <div className="grid grid-2">
            <div className="card">
              <span className="label">Barriers</span>
              <p className="card-note" style={{ marginTop: 6 }}>
                Where independent work has to stop being independent.
              </p>
              {GRAPH.joins.map((join) => (
                <div key={join.id} style={{ marginTop: 14, borderTop: "1px solid var(--divider)", paddingTop: 12 }}>
                  <div className="row-between">
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{join.id} join</span>
                    <Pill tone={join.on_shortfall === "park" ? "red" : "amber"}>
                      shortfall: {join.on_shortfall}
                    </Pill>
                  </div>
                  <div className="small muted" style={{ marginTop: 5 }}>
                    {join.mode.replace(/_/g, " ")}
                    {join.threshold !== null ? ` at ${Math.round(join.threshold * 100)}%` : ""}
                  </div>
                  <div className="mono small faint" style={{ marginTop: 5 }}>
                    {join.arms.map((a) => `${a.name} (${a.required ? "required" : "optional"})`).join(" · ")}
                  </div>
                  {join.override ? (
                    <div className="small faint" style={{ marginTop: 4 }}>
                      Override: {join.override}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>

            <div className="card">
              <span className="label">Cycle budgets</span>
              <p className="card-note" style={{ marginTop: 6 }}>
                Where a loop is allowed to run, and what stops it being a loop.
              </p>
              {GRAPH.cycles.map((cycle) => (
                <div key={cycle.id} style={{ marginTop: 14, borderTop: "1px solid var(--divider)", paddingTop: 12 }}>
                  <div className="row-between">
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{cycle.id}</span>
                    <span className="mono small">max {cycle.max_iterations}</span>
                  </div>
                  <div className="small muted" style={{ marginTop: 5 }}>
                    Bound: {cycle.bounded_by}
                  </div>
                  <div className="small faint" style={{ marginTop: 3 }}>
                    Spent: {cycle.on_exhaustion}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

/** One node, with its status, and its contract behind a disclosure. */
function NodeCard({ run }: { run: NodeRun }) {
  const { node } = run;
  const ms = durationMs(run);
  const status = STATUS[run.status];

  return (
    <details className="entry">
      <summary>
        <Pill tone={status.tone}>{status.label}</Pill>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ fontWeight: 600 }}>{node.label}</span>{" "}
          <span className="faint small">
            {node.kind}
            {node.contract.model ? ` · ${node.contract.model} model` : ""} · {node.identity}
          </span>
        </span>
        {ms !== null ? <span className="mono small faint">{ms} ms</span> : null}
      </summary>
      <div>
        {node.note ? (
          <p className="small" style={{ marginTop: 0, color: "var(--body)" }}>
            {node.note}
          </p>
        ) : null}
        {run.detail ? (
          <p className="mono small faint" style={{ marginTop: 6 }}>
            {run.detail}
          </p>
        ) : null}

        <div style={{ marginTop: 12 }}>
          <KeyValue
            rows={[
              ["Takes", node.contract.inputs.join(", ") || "—"],
              ["Produces", node.contract.outputs.join(", ") || "—"],
              ["Reads", <span className="mono small">{node.contract.reads.join(", ") || "—"}</span>],
              ["Writes", <span className="mono small">{node.contract.writes.join(", ") || "—"}</span>],
              [
                "Cannot",
                <span style={{ color: "var(--red)" }}>✗ {node.contract.forbidden.join(", ") || "—"}</span>,
              ],
              ...(node.failure
                ? ([
                    [
                      "On failure",
                      `${node.failure.on_exhaustion}${node.failure.park_reason ? ` (${node.failure.park_reason})` : ""}${
                        node.failure.degraded_to ? ` → ${node.failure.degraded_to}` : ""
                      } after ${node.failure.max_attempts} attempt(s)`,
                    ],
                  ] as [string, React.ReactNode][])
                : []),
            ]}
          />
        </div>
      </div>
    </details>
  );
}
