/**
 * S1a · The review queue. The opening shot.
 *
 * One row carries state, tier, Trust Score, elapsed days and model cost together, so a single
 * glance makes three arguments: the fleet is running many reviews, it stops at the ones that
 * need a person, and it knows what each one cost.
 *
 * The default filter is *Needs you*, because the product's claim is that humans appear only at
 * decision points. A queue that opened on everything would be showing work nobody has to do.
 */

import Link from "next/link";
import { elapsedDays, needsYou, queue, type Doc } from "../lib/ledger";
import { BandPill, Empty, StatePill, money } from "./components";

export const dynamic = "force-dynamic";

type Search = { filter?: string };

export default async function Queue({
  searchParams,
}: {
  searchParams: Promise<Search>;
}) {
  const { filter = "needs-you" } = await searchParams;

  let reviews: Doc[];
  try {
    reviews = await queue();
  } catch (error) {
    return (
      <main className="shell">
        <h1>Review queue</h1>
        <p className="error">{String(error)}</p>
      </main>
    );
  }

  const waiting = reviews.filter(needsYou);
  const shown = filter === "all" ? reviews : waiting;

  return (
    <main className="shell">
      <h1>Review queue</h1>
      <p className="label">
        {reviews.length} reviews · {waiting.length} need a person
      </p>

      <div className="chips">
        <Link
          href="/?filter=needs-you"
          className="chip"
          data-active={filter !== "all"}
        >
          Needs you · {waiting.length}
        </Link>
        <Link href="/?filter=all" className="chip" data-active={filter === "all"}>
          All · {reviews.length}
        </Link>
      </div>

      <div className="card">
        {shown.length === 0 ? (
          <Empty>
            No reviews need you right now — the fleet is carrying {reviews.length}.
          </Empty>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Tier</th>
                  <th>State</th>
                  <th>Trust Score</th>
                  <th>Days</th>
                  <th>Model cost</th>
                  <th>Needs you</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.review_id}>
                    <td>
                      <Link href={`/review/${r.review_id}`}>
                        {r.vendor?.name ?? r.vendor_id}
                      </Link>
                      <div className="mono" style={{ color: "var(--faint)" }}>
                        {r.review_id}
                      </div>
                    </td>
                    <td className="num">
                      {r.tier}
                      {r.tier_history?.length ? (
                        <span className="label"> was {r.tier_history[0].from_tier}</span>
                      ) : null}
                    </td>
                    <td>
                      <StatePill state={String(r.state)} scope={r.gate_scope} />
                    </td>
                    <td>
                      <span className="num" style={{ fontWeight: 600 }}>
                        {r.score ?? "—"}
                      </span>{" "}
                      <BandPill band={r.band} />
                    </td>
                    <td className="num">{elapsedDays(r)}</td>
                    <td className="num mono">{money(r.cost_usd)}</td>
                    <td>
                      {needsYou(r) ? (
                        <Link href={`/review/${r.review_id}/gate`}>
                          {r.gate_scope === "decision" ? "Sign off" : "Review"}
                        </Link>
                      ) : (
                        <span className="empty">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </main>
  );
}
