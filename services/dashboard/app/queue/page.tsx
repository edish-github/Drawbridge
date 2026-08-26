/**
 * The review queue. The screen an operator lives on.
 *
 * One row carries state, tier, Trust Score, elapsed days and model cost together, so a single
 * glance makes three arguments: the fleet is running many reviews, it stops at the ones that
 * need a person, and it knows what each one cost.
 *
 * The default filter is *needs you*, because the product's claim is that humans appear only at
 * decision points. A queue that opened on everything would be showing work nobody has to do.
 *
 * Rows that need a person carry an inset rail as well as a pill, so the ones that matter are
 * findable by shape when the table is long.
 */

import Link from "next/link";
import { elapsedDays, money, needsYou, queue, type Doc } from "../../lib/ledger";
import { BandPill, Filters, LoadError, PageHead, StatePill, TierPill } from "../components";

export const dynamic = "force-dynamic";

const BUCKETS: Record<string, (r: Doc) => boolean> = {
  "needs-you": needsYou,
  gated: (r) => r.state === "gated",
  parked: (r) => r.state === "needs_human",
  "in-flight": (r) => !["decided", "monitored", "needs_human"].includes(String(r.state)),
  closed: (r) => r.state === "decided" || r.state === "monitored",
  all: () => true,
};

export default async function Queue({
  searchParams,
}: {
  searchParams: Promise<{ filter?: string }>;
}) {
  const { filter = "needs-you" } = await searchParams;

  let reviews: Doc[];
  try {
    reviews = await queue();
  } catch (error) {
    return (
      <>
        <PageHead crumb="Reviews / Queue" title="Review Queue" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  const predicate = BUCKETS[filter] ?? BUCKETS["needs-you"];
  const shown = reviews.filter(predicate);

  const options = [
    { value: "needs-you", label: "Needs you", count: reviews.filter(BUCKETS["needs-you"]).length },
    { value: "gated", label: "At a gate", count: reviews.filter(BUCKETS.gated).length },
    { value: "parked", label: "Parked", count: reviews.filter(BUCKETS.parked).length },
    { value: "in-flight", label: "In flight", count: reviews.filter(BUCKETS["in-flight"]).length },
    { value: "closed", label: "Closed", count: reviews.filter(BUCKETS.closed).length },
    { value: "all", label: "All", count: reviews.length },
  ];

  return (
    <>
      <PageHead
        crumb="Reviews / Queue"
        title="Review Queue"
        subtitle="Active review workload and the human gates the fleet is parked at."
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <Filters options={options} active={filter} base="/queue" />

        <div className="card card-tight">
          {shown.length === 0 ? (
            <div style={{ padding: "22px" }}>
              <p className="empty">
                {filter === "needs-you"
                  ? `No reviews need you right now — the fleet is carrying ${reviews.length}.`
                  : "No reviews match this filter."}
              </p>
            </div>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Vendor</th>
                    <th>Tier</th>
                    <th>State</th>
                    <th>Trust Score</th>
                    <th className="right">Days</th>
                    <th className="right">Model cost</th>
                    <th className="right">Needs you</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <tr
                      key={r.review_id}
                      style={
                        needsYou(r)
                          ? { boxShadow: `inset 3px 0 0 0 var(--${r.state === "needs_human" ? "red" : "amber"})` }
                          : undefined
                      }
                    >
                      <td className="strong">
                        <Link href={`/reviews/${r.review_id}`}>{r.vendor?.name ?? r.vendor_id}</Link>
                        <div className="mono faint" style={{ fontSize: 10.5 }}>
                          {r.review_id}
                        </div>
                      </td>
                      <td>
                        <TierPill tier={Number(r.tier ?? 2)} />
                        {r.tier_history?.length ? (
                          <div className="faint small" style={{ marginTop: 3 }}>
                            was {r.tier_history[0].from_tier}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        <StatePill state={String(r.state)} scope={r.gate_scope} />
                        {r.park_reason ? (
                          <div className="mono faint" style={{ fontSize: 10.5, marginTop: 3, maxWidth: 220 }}>
                            {r.park_reason}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        <span className="mono" style={{ fontWeight: 600 }}>
                          {r.score ?? "—"}
                        </span>{" "}
                        <BandPill band={r.band} score={r.score} />
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
          )}
        </div>

        <p className="card-note" style={{ marginTop: 12 }}>
          Showing {shown.length} of {reviews.length} reviews in the ledger.
        </p>
      </div>
    </>
  );
}
