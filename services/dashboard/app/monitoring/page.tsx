/**
 * Monitoring — what the Watchdog has seen since the reviews closed.
 *
 * The point of the screen is the gap between *a decision was made* and *the decision is still
 * true*. A vendor approved eight months ago against evidence that has since expired is the
 * failure this component exists to catch, so the certificate horizon sits beside the feed
 * signals rather than on a different page.
 *
 * A signal that produced nothing is still shown. A monitoring surface that only listed hits
 * would be indistinguishable from one that had stopped running.
 */

import Link from "next/link";
import { ago, portfolio, signals, triageCards, when, type Doc } from "../../lib/ledger";
import { Avatar, Empty, Filters, LoadError, PageHead, Pill } from "../components";
import { GRAPH } from "../../lib/graph";

export const dynamic = "force-dynamic";

const BUCKETS: Record<string, (s: Doc) => boolean> = {
  all: () => true,
  rereview: (s) => s.action === "open_rereview",
  triage: (s) => s.action === "triage",
  discarded: (s) => s.action === "discard" || s.action === "discarded",
};

export default async function Monitoring({
  searchParams,
}: {
  searchParams: Promise<{ filter?: string }>;
}) {
  const { filter = "all" } = await searchParams;

  let feed: Doc[];
  let cards: Doc[];
  let vendors: Doc[];
  try {
    [feed, cards, vendors] = await Promise.all([signals(), triageCards(), portfolio()]);
  } catch (error) {
    return (
      <>
        <PageHead crumb="Monitoring" title="Monitoring" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  const predicate = BUCKETS[filter] ?? BUCKETS.all;
  const shown = feed.filter(predicate);
  const monitored = vendors.filter((v) => v.decided);
  const cycle = GRAPH.cycles.find((c) => c.id === "rereview");

  const options = [
    { value: "all", label: "All signals", count: feed.length },
    { value: "rereview", label: "Opened a re-review", count: feed.filter(BUCKETS.rereview).length },
    { value: "triage", label: "Sent to triage", count: feed.filter(BUCKETS.triage).length },
    { value: "discarded", label: "Discarded", count: feed.filter(BUCKETS.discarded).length },
  ];

  return (
    <>
      <PageHead
        crumb="Monitoring"
        title="Monitoring"
        subtitle="Continuous observation of approved vendors — signals, posture movement and coverage."
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <div className="grid grid-4" style={{ marginBottom: 16 }}>
          <div className="card">
            <div className="stat-value">{monitored.length}</div>
            <div className="stat-label">Vendors under monitoring</div>
          </div>
          <div className="card">
            <div className="stat-value">{feed.length}</div>
            <div className="stat-label">Signals actioned</div>
          </div>
          <div className="card">
            <div className="stat-value" style={{ color: "var(--red)" }}>
              {feed.filter(BUCKETS.rereview).length}
            </div>
            <div className="stat-label">Re-reviews opened</div>
          </div>
          <div className="card">
            <div className="stat-value" style={{ color: "var(--amber)" }}>
              {cards.length}
            </div>
            <div className="stat-label">Awaiting triage</div>
          </div>
        </div>

        <Filters options={options} active={filter} base="/monitoring" />

        <div className="grid grid-2">
          <div className="card card-tight" style={{ gridColumn: "1 / -1" }}>
            <div style={{ padding: "18px 22px 10px" }}>
              <span className="label">Signal log</span>
            </div>
            {shown.length === 0 ? (
              <div style={{ padding: "0 22px 18px" }}>
                <Empty>
                  No signals under this filter. The Watchdog fetches only from the four allowlisted
                  hosts under policy P3, and a feed outage logs and skips rather than blocking a
                  review.
                </Empty>
              </div>
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Vendor</th>
                      <th>Signal</th>
                      <th>Source</th>
                      <th>Action</th>
                      <th className="right">Seen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((s) => (
                      <tr key={String(s.signal_id ?? s.id)}>
                        <td className="strong">
                          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                            <Avatar name={String(s.vendor?.name ?? s.vendor_id ?? "?")} />
                            <Link href={`/vendors/${s.vendor_id}`}>{s.vendor?.name ?? s.vendor_id}</Link>
                          </div>
                        </td>
                        <td style={{ maxWidth: 420 }}>
                          {s.title}
                          {s.url ? (
                            <div className="mono faint" style={{ fontSize: 10.5, marginTop: 2 }}>
                              {s.url}
                            </div>
                          ) : null}
                        </td>
                        <td className="mono small faint">{s.source}</td>
                        <td>
                          <Pill
                            tone={s.action === "open_rereview" ? "red" : s.action === "triage" ? "amber" : "gray"}
                          >
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
            <span className="label">Awaiting a person</span>
            <p className="card-note" style={{ marginTop: 6, marginBottom: 12 }}>
              A signal the fleet was unsure about, or one that arrived after the re-review budget
              was spent. Both end here and the card says which.
            </p>
            {cards.length === 0 ? (
              <Empty>Nothing in triage.</Empty>
            ) : (
              <div className="timeline">
                {cards.slice(0, 12).map((c, i) => (
                  <div className="tl-item" key={i}>
                    <span className="tl-when">{when(c.at)}</span>
                    <span className="tl-rail">
                      <span className="tl-dot" style={{ background: "var(--amber)" }} />
                    </span>
                    <div>
                      <div className="tl-title">{c.title ?? c.line ?? "signal"}</div>
                      <div className="tl-detail">
                        {c.vendor_id ? (
                          <Link href={`/vendors/${c.vendor_id}`} className="mono">
                            {c.vendor_id}
                          </Link>
                        ) : null}
                        {c.reason ? ` · ${c.reason}` : ""}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card">
            <span className="label">The re-review cycle</span>
            {cycle ? (
              <>
                <p className="body-text" style={{ marginTop: 10 }}>
                  Three of the four cycles in the review graph terminate by arithmetic. This one
                  has no natural bound — every reopening is legitimate by every rule the system
                  has — so it carries a declared budget.
                </p>
                <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 9 }}>
                  <div className="row-between">
                    <span className="small faint">Budget</span>
                    <span className="mono small">{cycle.max_iterations} linked re-reviews</span>
                  </div>
                  <div className="row-between">
                    <span className="small faint">Counted by</span>
                    <span className="mono small">{cycle.counted_by}</span>
                  </div>
                </div>
                <div className="notice" style={{ marginTop: 14 }}>
                  When the budget is spent the signal still goes up as a triage card. It is never
                  &ldquo;stop monitoring&rdquo; — a vendor nobody is watching is the outcome this
                  component exists to prevent.
                </div>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}
