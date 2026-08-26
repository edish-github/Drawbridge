/**
 * Overview — the opening screen, and the only one that answers "what should I do now?"
 *
 * The dark panel is first and largest because the product's whole claim is that humans appear
 * at decision points and nowhere else. A console that opened on a portfolio chart would be
 * asking the operator to go looking for their own work.
 *
 * Every number here is a count over documents the fleet wrote. Nothing is sampled, nothing is
 * cached, and where a figure cannot be computed from the ledger it is absent rather than
 * estimated — the throughput sparkline draws the days that have reviews and says so if none do.
 */

import Link from "next/link";
import {
  ago,
  bandOf,
  elapsedDays,
  money,
  needsYou,
  overview,
  signals,
  type Doc,
} from "../../lib/ledger";
import { Avatar, BandPill, LoadError, Meter, PageHead, Pill, StatePill } from "../components";
import { AGENT_NODES } from "../../lib/graph-summary";
import { requirePrincipal } from "../../lib/guard";

export const dynamic = "force-dynamic";

export default async function Overview() {
  const principal = await requirePrincipal();
  const orgId = principal.orgId;

  let data: Awaited<ReturnType<typeof overview>>;
  let feed: Doc[];
  try {
    [data, feed] = await Promise.all([overview(orgId), signals(orgId)]);
  } catch (error) {
    return (
      <>
        <PageHead crumb="Overview" title="Overview" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  const scored = data.reviews.filter((r) => typeof r.score?.score === "number");
  const average = scored.length
    ? Math.round(scored.reduce((sum, r) => sum + Number(r.score.score), 0) / scored.length)
    : null;

  const decided = data.reviews.filter((r) => r.decided_at);
  const cycleDays = decided
    .map((r) => {
      const opened = Date.parse(String(r.opened_at ?? ""));
      const closed = Date.parse(String(r.decided_at ?? ""));
      return Number.isNaN(opened) || Number.isNaN(closed) ? null : (closed - opened) / 86_400_000;
    })
    .filter((d): d is number => d !== null)
    .sort((a, b) => a - b);
  const median = cycleDays.length ? cycleDays[Math.floor(cycleDays.length / 2)] : null;

  const costed = data.reviews.filter((r) => Number(r.cost_usd ?? 0) > 0);
  const perReview = costed.length ? data.cost / costed.length : 0;

  const attention = [
    {
      label: "Reviews parked for a person",
      count: data.waiting.length,
      tone: "red" as const,
      href: "/queue?filter=needs-you",
    },
    {
      label: "Contradictions in open reviews",
      count: data.contradictions,
      tone: "red" as const,
      href: "/findings?filter=contradiction",
    },
    {
      label: "Adversarial conduct findings",
      count: data.adversarial,
      tone: "red" as const,
      href: "/findings?filter=conduct",
    },
    {
      label: "Gateway policy blocks recorded",
      count: data.policyBlocks,
      tone: "amber" as const,
      href: "/activity?filter=policy",
    },
  ];

  return (
    <>
      <PageHead
        crumb="Overview"
        title="Overview"
        subtitle="Vendor security posture across your portfolio, and what needs you today."
        actions={
          <Link href="/reviews/new" className="btn btn-inline">
            + New review
          </Link>
        }
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <div className="grid grid-2" style={{ marginBottom: 14 }}>
          {/* --- Needs your attention ------------------------------------------------- */}
          <div className="panel">
            <div className="row-between" style={{ marginBottom: 18 }}>
              <span className="label">Needs your attention</span>
              <span className="label" style={{ display: "inline-flex", alignItems: "center", gap: 7, color: "var(--green)" }}>
                <span className="live" style={{ color: "var(--green)" }}>
                  <i />
                  <i />
                </span>
                FLEET ACTIVE
              </span>
            </div>

            <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 20 }}>
              <span style={{ font: "700 44px/1 var(--sans)", letterSpacing: "-.03em" }}>
                {data.waiting.length}
              </span>
              <span style={{ font: "500 13px/1.4 var(--sans)", color: "#c9c6c0", maxWidth: 220 }}>
                {data.waiting.length === 1 ? "review is" : "reviews are"} parked and require a human
              </span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
              {attention.map((a) => (
                <Link
                  key={a.label}
                  href={a.href}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "10px 0",
                    borderTop: "1px solid rgba(255,255,255,.07)",
                    color: "#e6e3dd",
                  }}
                >
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: 999,
                      flex: "none",
                      background: `var(--${a.tone})`,
                    }}
                  />
                  <span style={{ flex: 1, fontSize: 12.6, fontWeight: 500 }}>{a.label}</span>
                  <span className="mono" style={{ fontSize: 13, fontWeight: 600 }}>
                    {a.count}
                  </span>
                  <span style={{ color: "#8a8781" }}>›</span>
                </Link>
              ))}
            </div>

            <Link
              href="/queue"
              style={{
                display: "inline-block",
                marginTop: 18,
                font: "600 12.6px/1 var(--sans)",
                color: "#f2f0ec",
              }}
            >
              Open review queue →
            </Link>
          </div>

          {/* --- Portfolio ------------------------------------------------------------ */}
          <div className="card">
            <div className="row-between" style={{ marginBottom: 16 }}>
              <span className="label">Portfolio</span>
              <span className="label">{data.vendors} vendors</span>
            </div>

            <div className="grid grid-4" style={{ gap: 12, marginBottom: 20 }}>
              {[
                { value: data.monitored, label: "Decided or monitored" },
                { value: data.inFlight, label: "Reviews in flight" },
                { value: data.waiting.length, label: "Awaiting human gate", tone: "amber" },
                { value: data.escalated, label: "Escalate band", tone: "red" },
              ].map((s) => (
                <div key={s.label}>
                  <div
                    className="stat-value"
                    style={{ fontSize: 26, color: s.tone ? `var(--${s.tone})` : undefined }}
                  >
                    {s.value}
                  </div>
                  <div className="stat-label" style={{ fontSize: 11 }}>
                    {s.label}
                  </div>
                </div>
              ))}
            </div>

            <div style={{ borderTop: "1px solid var(--divider)", paddingTop: 16 }}>
              <div className="row-between" style={{ marginBottom: 8 }}>
                <span className="small muted">Average Trust Score</span>
                <span className="mono" style={{ fontSize: 18, fontWeight: 700 }}>
                  {average ?? "—"}
                </span>
              </div>
              <Meter value={average ?? 0} tone={bandOf(average).tone} />
              <div
                className="label"
                style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 9.5 }}
              >
                <span>0 escalate</span>
                <span>60 conditional</span>
                <span>80 approve</span>
              </div>
            </div>
          </div>
        </div>

        {/* --- Throughput and distribution -------------------------------------------- */}
        <div className="grid grid-2" style={{ marginBottom: 14 }}>
          <div className="card">
            <div className="row-between" style={{ marginBottom: 4 }}>
              <span className="label">Review throughput</span>
              <span className="label">{decided.length} closed</span>
            </div>
            <Sparkline reviews={data.reviews} />
            <div
              className="grid grid-3"
              style={{ gap: 12, marginTop: 18, borderTop: "1px solid var(--divider)", paddingTop: 16 }}
            >
              <Metric value={median === null ? "—" : `${median.toFixed(1)}d`} label="Median cycle time" />
              <Metric value={`${data.reviews.length}`} label="Reviews in the ledger" />
              <Metric value={perReview ? money(perReview) : "—"} label="Model cost per review" />
            </div>
          </div>

          <div className="card">
            <span className="label">Trust Score distribution</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 14, margin: "16px 0 14px" }}>
              {data.bands.map((b) => {
                const total = data.bands.reduce((s, x) => s + x.count, 0) || 1;
                return (
                  <div key={b.label}>
                    <div className="row-between" style={{ marginBottom: 6 }}>
                      <Pill tone={b.tone}>{b.label}</Pill>
                      <span className="mono small muted">
                        {b.label === "approve" ? "80–100" : b.label === "conditional" ? "60–79" : "0–59"}
                      </span>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                      <div style={{ flex: 1 }}>
                        <Meter value={(b.count / total) * 100} tone={b.tone} />
                      </div>
                      <span className="mono" style={{ fontSize: 13, fontWeight: 600, minWidth: 24, textAlign: "right" }}>
                        {b.count}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
            <p className="card-note">
              Scores are deterministic arithmetic over model-assigned finding severities — never
              produced by a model.
            </p>
          </div>
        </div>

        {/* --- Signals and fleet ------------------------------------------------------ */}
        <div className="grid grid-2">
          <div className="card card-tight">
            <div className="row-between" style={{ padding: "18px 22px 12px" }}>
              <span className="label">Recent security signals</span>
              <Link href="/monitoring" className="label" style={{ color: "var(--mute)" }}>
                View all →
              </Link>
            </div>
            {feed.length === 0 ? (
              <div style={{ padding: "0 22px 18px" }}>
                <p className="empty">
                  Nothing yet. Monitoring begins once a vendor is approved, and sweeps run daily
                  from then on.
                </p>
              </div>
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Vendor</th>
                      <th>Signal</th>
                      <th>Action</th>
                      <th className="right">Detected</th>
                    </tr>
                  </thead>
                  <tbody>
                    {feed.slice(0, 5).map((s) => (
                      <tr key={String(s.signal_id ?? s.id)}>
                        <td className="strong">
                          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                            <Avatar name={String(s.vendor?.name ?? s.vendor_id ?? "?")} />
                            {s.vendor?.name ?? s.vendor_id}
                          </div>
                        </td>
                        <td>{s.title}</td>
                        <td>
                          <Pill tone={s.action === "open_rereview" ? "red" : s.action === "triage" ? "amber" : "gray"}>
                            {String(s.action ?? "—").replace(/_/g, " ")}
                          </Pill>
                        </td>
                        <td className="right mono small faint">{ago(s.at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <div className="row-between" style={{ marginBottom: 14 }}>
              <span className="label">Fleet status</span>
              <Link href="/agents" className="label" style={{ color: "var(--mute)" }}>
                Registry →
              </Link>
            </div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              {AGENT_NODES.map((a) => {
                const count = data.reviews.filter((r) => r.state === a.activeState).length;
                return (
                  <div
                    key={a.identity}
                    className="row-between"
                    style={{ padding: "10px 0", borderBottom: "1px solid var(--divider)" }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
                      <span className="live" style={{ color: count ? "var(--green)" : "var(--gray)" }}>
                        <i />
                        {count ? <i /> : null}
                      </span>
                      <span style={{ fontSize: 12.8, fontWeight: 600 }}>{a.label}</span>
                    </div>
                    <span className="mono small faint">
                      {count ? `${count} in flight` : "idle"}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* --- Recent reviews --------------------------------------------------------- */}
        <div className="card card-tight" style={{ marginTop: 14 }}>
          <div className="row-between" style={{ padding: "18px 22px 12px" }}>
            <span className="label">Most recent reviews</span>
            <Link href="/queue" className="label" style={{ color: "var(--mute)" }}>
              Full queue →
            </Link>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>State</th>
                  <th>Trust Score</th>
                  <th className="right">Days</th>
                  <th className="right">Cost</th>
                  <th className="right">Needs you</th>
                </tr>
              </thead>
              <tbody>
                {data.recent.map((r) => (
                  <tr key={r.review_id}>
                    <td className="strong">
                      <Link href={`/reviews/${r.review_id}`}>{r.vendor?.name ?? r.vendor_id}</Link>
                      <div className="mono faint" style={{ fontSize: 10.5 }}>
                        {r.review_id}
                      </div>
                    </td>
                    <td>
                      <StatePill state={String(r.state)} scope={r.gate_scope} />
                    </td>
                    <td>
                      <span className="mono" style={{ fontWeight: 600 }}>
                        {r.score?.score ?? "—"}
                      </span>{" "}
                      <BandPill band={r.band ?? r.score?.band} score={r.score?.score ?? r.score_value} />
                    </td>
                    <td className="right mono">{elapsedDays(r)}</td>
                    <td className="right mono faint">{money(r.cost_usd)}</td>
                    <td className="right">
                      {needsYou(r) ? (
                        <Link href={`/reviews/${r.review_id}/gate`} style={{ fontWeight: 600 }}>
                          {r.gate_scope === "decision" ? "Sign off" : "Review"}
                        </Link>
                      ) : (
                        <span className="faint">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}

function Metric({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <div className="mono" style={{ fontSize: 17, fontWeight: 700 }}>
        {value}
      </div>
      <div className="stat-label" style={{ fontSize: 11 }}>
        {label}
      </div>
    </div>
  );
}

/**
 * Reviews closed per day for the last fortnight, drawn from `decided_at`.
 *
 * Server-rendered SVG with no library and no client JavaScript. The area is drawn from the same
 * points as the line so the two cannot disagree, and the last point is marked because "where are
 * we now" is the only question a sparkline is ever really asked.
 */
function Sparkline({ reviews }: { reviews: Doc[] }) {
  const days = 14;
  const today = new Date();
  const buckets: number[] = Array(days).fill(0);

  for (const r of reviews) {
    const closed = Date.parse(String(r.decided_at ?? ""));
    if (Number.isNaN(closed)) continue;
    const offset = Math.floor((today.getTime() - closed) / 86_400_000);
    if (offset >= 0 && offset < days) buckets[days - 1 - offset] += 1;
  }

  const peak = Math.max(1, ...buckets);
  const w = 558;
  const h = 128;
  const step = w / (days - 1);
  const points = buckets.map((v, i) => [i * step, h - 14 - (v / peak) * (h - 34)] as const);
  const line = points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const area = `${line} L${w} ${h} L0 ${h} Z`;
  const last = points[points.length - 1];

  if (buckets.every((v) => v === 0)) {
    return (
      <p className="empty" style={{ paddingBottom: 0 }}>
        No reviews closed in the last {days} days.
      </p>
    );
  }

  return (
    <>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height="140" fill="none" preserveAspectRatio="none" role="img" aria-label={`Reviews closed per day over ${days} days, peak ${peak}`}>
        <path d={area} fill="var(--sunken)" />
        <path d={line} stroke="var(--ink)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx={last[0]} cy={last[1]} r="4" fill="var(--ink)" />
      </svg>
      <div className="label" style={{ display: "flex", justifyContent: "space-between", fontSize: 9.5 }}>
        <span>{days} days ago</span>
        <span>peak {peak}/day</span>
        <span>today</span>
      </div>
    </>
  );
}
