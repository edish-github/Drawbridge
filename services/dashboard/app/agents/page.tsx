/**
 * Agent Registry — the fleet, its live state, and its least-privilege boundaries.
 *
 * **Nothing on this page is hand-maintained.** Every agent, every collection it may read or
 * write, and every capability it is denied is derived from the node contracts in
 * `shared/graph.py`, which `scripts/check_contracts.py --check graph` diffs against
 * `infra/iam/permission-matrix.yaml` on every push. So the boundaries printed here cannot claim
 * a permission the matrix does not grant, and cannot deny one it does.
 *
 * That is the whole reason the page is worth having. A registry typed out by hand is a
 * screenshot of an intention; this one is a rendering of the thing that is enforced.
 *
 * The **Cannot** row is the important half. Anyone can list what a service is allowed to do; the
 * claim that makes an autonomous fleet safe to run unattended is the list of what it structurally
 * cannot, and that list is checked in both directions.
 */

import Link from "next/link";
import { overview, type Doc } from "../../lib/ledger";
import { AGENT_NODES, UNOWNED_NODES } from "../../lib/graph-summary";
import { GRAPH } from "../../lib/graph";
import { Empty, KeyValue, LoadError, PageHead, Pill } from "../components";

export const dynamic = "force-dynamic";

export default async function Agents() {
  let data: Awaited<ReturnType<typeof overview>>;
  try {
    data = await overview();
  } catch (error) {
    return (
      <>
        <PageHead crumb="Governance / Agent Registry" title="Agent Registry" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  const inState = (state: string) => data.reviews.filter((r: Doc) => r.state === state).length;

  return (
    <>
      <PageHead
        crumb="Governance / Agent Registry"
        title="Agent Registry"
        subtitle="The autonomous review fleet, its live state, and the boundaries it is structurally incapable of crossing."
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <div className="notice" style={{ marginBottom: 18 }}>
          Every row below is derived from the node contracts in{" "}
          <code className="mono">shared/graph.py</code>, which CI diffs against{" "}
          <code className="mono">infra/iam/permission-matrix.yaml</code>. A permission shown here
          that the matrix does not grant fails the build — so this is a rendering of what is
          enforced rather than a description of what was intended.
        </div>

        <div className="grid grid-4" style={{ marginBottom: 18 }}>
          <div className="card">
            <div className="stat-value">{AGENT_NODES.filter((a) => a.kind === "agent").length}</div>
            <div className="stat-label">Agents</div>
          </div>
          <div className="card">
            <div className="stat-value">{GRAPH.nodes.length}</div>
            <div className="stat-label">Declared graph nodes</div>
          </div>
          <div className="card">
            <div className="stat-value">{GRAPH.edges.length}</div>
            <div className="stat-label">Edges</div>
          </div>
          <div className="card">
            <div className="stat-value">
              {new Set(GRAPH.nodes.flatMap((n) => n.contract.forbidden)).size}
            </div>
            <div className="stat-label">Distinct denied capabilities</div>
          </div>
        </div>

        <div className="stack">
          {AGENT_NODES.map((a) => {
            const busy = a.activeState ? inState(a.activeState) : 0;
            return (
              <div className="card" key={a.identity}>
                <div className="row-between" style={{ alignItems: "flex-start", marginBottom: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 11, minWidth: 0 }}>
                    <span className="live" style={{ color: busy ? "var(--green)" : "var(--gray)" }}>
                      <i />
                      {busy ? <i /> : null}
                    </span>
                    <div>
                      <div style={{ fontSize: 15, fontWeight: 600 }}>{a.label}</div>
                      <div className="mono small faint">{a.identity}</div>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
                    <Pill tone={a.kind === "agent" ? "blue" : "gray"}>{a.kind}</Pill>
                    {a.models.map((m) => (
                      <Pill key={m} tone={m === "deep" ? "purple" : "blue"}>
                        {m} model
                      </Pill>
                    ))}
                    <Pill tone={busy ? "green" : "gray"} dot>
                      {busy ? `${busy} in flight` : "idle"}
                    </Pill>
                  </div>
                </div>

                <p className="body-text" style={{ marginTop: 0 }}>
                  {a.blurb}
                </p>

                <div style={{ marginTop: 14 }}>
                  <KeyValue
                    rows={[
                      [
                        "Graph nodes",
                        <span className="mono small">{a.nodes.map((n) => n.id).join(", ") || "—"}</span>,
                      ],
                      ["Reads", <span className="mono small">{a.reads.join(", ") || "—"}</span>],
                      ["Writes", <span className="mono small">{a.writes.join(", ") || "—"}</span>],
                      [
                        "Cannot",
                        <span style={{ color: "var(--red)", fontWeight: 500 }}>
                          ✗ {a.forbidden.join(" · ") || "—"}
                        </span>,
                      ],
                    ]}
                  />
                </div>

                <details className="entry" style={{ marginTop: 12 }}>
                  <summary>What each of its nodes does</summary>
                  <div>
                    {a.nodes.map((n) => (
                      <div key={n.id} style={{ borderTop: "1px solid var(--divider)", padding: "10px 0" }}>
                        <div className="row-between" style={{ marginBottom: 4 }}>
                          <span style={{ fontWeight: 600, fontSize: 12.8 }}>{n.label}</span>
                          <span className="mono small faint">
                            {n.kind}
                            {n.checkpoint ? ` · checkpoint ${n.checkpoint}` : ""}
                          </span>
                        </div>
                        {n.note ? (
                          <p className="small" style={{ margin: 0, color: "var(--mute)" }}>
                            {n.note}
                          </p>
                        ) : null}
                        {n.failure ? (
                          <div className="mono small faint" style={{ marginTop: 4 }}>
                            on failure: {n.failure.on_exhaustion}
                            {n.failure.park_reason ? ` (${n.failure.park_reason})` : ""}
                            {n.failure.degraded_to ? ` → ${n.failure.degraded_to}` : ""}
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </details>
              </div>
            );
          })}
        </div>

        <div className="grid grid-2" style={{ marginTop: 14 }}>
          <div className="card">
            <span className="label">Routers</span>
            <p className="card-note" style={{ marginTop: 6, marginBottom: 12 }}>
              Every branch in the graph, and the direction it may not travel. A monotonic
              constraint is a security property, not a workflow convenience — a router that could
              go the other way would let a vendor&rsquo;s own answers reduce the scrutiny applied
              to them.
            </p>
            {GRAPH.routers.map((r) => (
              <div key={r.id} style={{ borderTop: "1px solid var(--divider)", padding: "11px 0" }}>
                <div className="row-between">
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{r.id}</span>
                  <Pill tone="purple">{r.monotonic}</Pill>
                </div>
                <div style={{ marginTop: 6 }}>
                  {r.branches.map((b: Doc, i: number) => (
                    <div key={i} className="small faint" style={{ marginTop: 3 }}>
                      → <span className="mono">{b.to}</span> when {b.when}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="card">
            <span className="label">Nodes owned by nobody</span>
            <p className="card-note" style={{ marginTop: 6, marginBottom: 12 }}>
              Two, and both are honest gaps rather than shorthand.
            </p>
            {UNOWNED_NODES.length === 0 ? (
              <Empty>None.</Empty>
            ) : (
              UNOWNED_NODES.map((n) => (
                <div key={n.id} style={{ borderTop: "1px solid var(--divider)", padding: "11px 0" }}>
                  <div className="row-between" style={{ marginBottom: 4 }}>
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{n.label}</span>
                    <span className="mono small faint">{n.identity}</span>
                  </div>
                  <p className="small" style={{ margin: 0, color: "var(--mute)" }}>
                    {n.note}
                  </p>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="card" style={{ marginTop: 14 }}>
          <span className="label">See the graph on a real review</span>
          <p className="body-text" style={{ marginTop: 8 }}>
            Any review page carries a <strong>Graph</strong> tab that projects these nodes onto
            what that review actually did — reconstructed from the ledger, with no
            instrumentation, which is why it works on reviews recorded before the projection
            existed.
          </p>
          {data.recent[0] ? (
            <Link href={`/reviews/${data.recent[0].review_id}/graph`} className="filter" style={{ marginTop: 10, display: "inline-block" }}>
              Open the most recent review&rsquo;s graph →
            </Link>
          ) : null}
        </div>
      </div>
    </>
  );
}
