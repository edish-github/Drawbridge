/**
 * Audit Binders — the record an auditor asks for, one per closed review.
 *
 * A binder is eight sections of HTML rendered from a template in about fifty milliseconds, and
 * the cover says it was rendered by a template rather than by a model. That claim is asserted by
 * an import graph, because a document that could be steered by the content it reports on is
 * worse than no document at all.
 *
 * This console lists them and names the command that renders one. It does not render them
 * itself: a read-only surface that could start a process is a read-only surface in name.
 */

import Link from "next/link";
import { binders, when, type Doc } from "../../lib/ledger";
import { Avatar, BandPill, Empty, Filters, LoadError, PageHead, Pill, TierPill } from "../components";

export const dynamic = "force-dynamic";

const SECTIONS = [
  ["01", "Review timeline", "every ledger event, in order"],
  ["02", "Agent decisions and reasoning traces", "goal and conclusion per step, in plain English"],
  ["03", "Screening records by template", "per-filter verdicts for every vendor-origin byte"],
  ["04", "Questionnaire and parsed responses", "every answer with its confidence and provenance"],
  ["05", "Findings labelled rule or model", "and the date attribution where one applies"],
  ["06", "Trust Score arithmetic", "reproducible by hand from the rubric"],
  ["07", "Human approvals with identity", "who accepted the risk, and when"],
  ["08", "Monitoring history since the decision", "every sweep, including the ones that found nothing"],
];

const BUCKETS: Record<string, (b: Doc) => boolean> = {
  all: () => true,
  approve: (b) => b.score?.band === "approve",
  conditional: (b) => b.score?.band === "conditional",
  escalate: (b) => b.score?.band === "escalate",
};

export default async function Binders({
  searchParams,
}: {
  searchParams: Promise<{ filter?: string }>;
}) {
  const { filter = "all" } = await searchParams;

  let rows: Doc[];
  try {
    rows = await binders();
  } catch (error) {
    return (
      <>
        <PageHead crumb="Governance / Audit Binders" title="Audit Binders" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  const predicate = BUCKETS[filter] ?? BUCKETS.all;
  const shown = rows.filter(predicate);

  const options = [
    { value: "all", label: "All", count: rows.length },
    { value: "approve", label: "Approve", count: rows.filter(BUCKETS.approve).length },
    { value: "conditional", label: "Conditional", count: rows.filter(BUCKETS.conditional).length },
    { value: "escalate", label: "Escalate", count: rows.filter(BUCKETS.escalate).length },
  ];

  return (
    <>
      <PageHead
        crumb="Governance / Audit Binders"
        title="Audit Binders"
        subtitle="Sealed, audit-ready records generated from reasoning traces — never written by a model."
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <div className="grid grid-2" style={{ marginBottom: 16 }}>
          <div className="panel">
            <span className="label">What is in one</span>
            <div style={{ marginTop: 14, display: "flex", flexDirection: "column" }}>
              {SECTIONS.map(([num, label, note]) => (
                <div
                  key={num}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "28px 1fr",
                    gap: 12,
                    padding: "9px 0",
                    borderTop: "1px solid rgba(255,255,255,.07)",
                  }}
                >
                  <span className="mono" style={{ fontSize: 11, color: "#8a8781" }}>
                    {num}
                  </span>
                  <div>
                    <div style={{ fontSize: 12.8, fontWeight: 600, color: "#f2f0ec" }}>{label}</div>
                    <div style={{ fontSize: 11.5, color: "#8a8781", marginTop: 2 }}>{note}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <span className="label">Rendering a binder</span>
            <p className="body-text" style={{ marginTop: 10 }}>
              The binder is built from the immutable event ledger and the reasoning records, by a
              template. The cover states that, and{" "}
              <code className="mono">services/binder/render.py</code> cannot reach the model router
              in its transitive imports — asserted by a test, because an import graph is the only
              form of <em>no model call happens here</em> that cannot quietly stop being true.
            </p>
            <pre
              className="mono"
              style={{
                margin: "14px 0 0",
                padding: "12px 14px",
                background: "var(--sunken)",
                borderRadius: 12,
                fontSize: 11.5,
                overflowX: "auto",
              }}
            >
              make binder REVIEW=&lt;review id&gt;
            </pre>
            <p className="card-note" style={{ marginTop: 10 }}>
              Written to <code className="mono">.binders/&lt;review&gt;.html</code>, with a print
              stylesheet. A review built on seeded fixtures says so on its cover.
            </p>
          </div>
        </div>

        <Filters options={options} active={filter} base="/binders" />

        <div className="card card-tight">
          {shown.length === 0 ? (
            <div style={{ padding: 22 }}>
              <Empty>
                No closed reviews yet. A binder exists for every review that reached a decision.
              </Empty>
            </div>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Vendor</th>
                    <th>Review</th>
                    <th>Tier</th>
                    <th>Outcome</th>
                    <th>Decided by</th>
                    <th className="right">Closed</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((b) => (
                    <tr key={b.review_id}>
                      <td className="strong">
                        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                          <Avatar name={String(b.vendor?.name ?? b.vendor_id)} />
                          <Link href={`/vendors/${b.vendor_id}`}>{b.vendor?.name ?? b.vendor_id}</Link>
                        </div>
                      </td>
                      <td>
                        <Link href={`/reviews/${b.review_id}`} className="mono small">
                          {b.review_id}
                        </Link>
                        {b.unscreened_fixtures ? (
                          <div style={{ marginTop: 4 }}>
                            <Pill tone="amber">fixtures</Pill>
                          </div>
                        ) : null}
                      </td>
                      <td>
                        <TierPill tier={Number(b.tier ?? 2)} />
                      </td>
                      <td>
                        <span className="mono" style={{ fontWeight: 600 }}>
                          {b.score?.score ?? "—"}
                        </span>{" "}
                        <BandPill band={b.score?.band} score={b.score?.score ?? null} />
                      </td>
                      <td className="small">{b.gate_released_by ?? <span className="faint">—</span>}</td>
                      <td className="right mono small faint">{when(b.decided_at ?? b.opened_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
