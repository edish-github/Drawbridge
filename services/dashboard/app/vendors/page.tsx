/**
 * The vendor portfolio. Cards rather than rows, because the unit here is a company you have a
 * relationship with rather than a task in a list.
 *
 * Each card answers three questions at a glance: what is this vendor's trust posture, is anyone
 * still watching it, and when did we last look. The third is the one that catches the vendor
 * approved eighteen months ago that nothing has re-examined since.
 */

import Link from "next/link";
import { ago, bandOf, portfolio, type Doc } from "../../lib/ledger";
import { Avatar, BandPill, Filters, LoadError, Meter, PageHead, Pill, TierPill } from "../components";

export const dynamic = "force-dynamic";

const BUCKETS: Record<string, (v: Doc) => boolean> = {
  all: () => true,
  reviewed: (v) => Boolean(v.decided),
  "in-flight": (v) => Boolean(v.latest) && !["decided", "monitored"].includes(String(v.latest.state)),
  "never-reviewed": (v) => !v.latest,
  adversarial: (v) => Boolean(v.adversarial_flag),
  ai: (v) => Boolean(v.is_ai_vendor),
};

export default async function Vendors({
  searchParams,
}: {
  searchParams: Promise<{ filter?: string }>;
}) {
  const { filter = "all" } = await searchParams;

  let vendors: Doc[];
  try {
    vendors = await portfolio();
  } catch (error) {
    return (
      <>
        <PageHead crumb="Reviews / Vendors" title="Vendors" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  const predicate = BUCKETS[filter] ?? BUCKETS.all;
  const shown = vendors.filter(predicate);

  const options = [
    { value: "all", label: "All", count: vendors.length },
    { value: "in-flight", label: "In review", count: vendors.filter(BUCKETS["in-flight"]).length },
    { value: "reviewed", label: "Decided", count: vendors.filter(BUCKETS.reviewed).length },
    { value: "never-reviewed", label: "Never reviewed", count: vendors.filter(BUCKETS["never-reviewed"]).length },
    { value: "ai", label: "AI services", count: vendors.filter(BUCKETS.ai).length },
    { value: "adversarial", label: "Conduct flag", count: vendors.filter(BUCKETS.adversarial).length },
  ];

  return (
    <>
      <PageHead
        crumb="Reviews / Vendors"
        title="Vendors"
        subtitle="The vendor portfolio, its trust posture and continuous monitoring state."
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <Filters options={options} active={filter} base="/vendors" />

        {shown.length === 0 ? (
          <div className="card">
            <p className="empty">No vendors match this filter.</p>
          </div>
        ) : (
          <div className="grid grid-3">
            {shown.map((v) => {
              const score = v.score?.score ?? null;
              const band = bandOf(score);
              const latest = v.latest;
              return (
                <Link key={v.vendor_id} href={`/vendors/${v.vendor_id}`} className="card" style={{ display: "block" }}>
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 11, marginBottom: 14 }}>
                    <Avatar name={String(v.name ?? v.vendor_id)} />
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div
                        style={{
                          fontSize: 14,
                          fontWeight: 600,
                          lineHeight: 1.3,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                        title={String(v.name ?? "")}
                      >
                        {v.name}
                      </div>
                      {/* Two lines, then an ellipsis. A category that wrapped to four pushed the
                          Trust Score below the fold on the one card that most needed it visible. */}
                      <div
                        className="small faint"
                        style={{
                          marginTop: 2,
                          display: "-webkit-box",
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                        }}
                      >
                        {v.category ?? "—"}
                      </div>
                    </div>
                    <TierPill tier={Number(v.tier ?? 2)} />
                  </div>

                  <div className="row-between" style={{ marginBottom: 7 }}>
                    <span className="label">Trust Score</span>
                    <span style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
                      <span className="mono" style={{ fontSize: 19, fontWeight: 700 }}>
                        {score ?? "—"}
                      </span>
                      <BandPill band={v.score?.band} score={score} />
                    </span>
                  </div>
                  <Meter value={score ?? 0} tone={band.tone} />

                  <div style={{ marginTop: 14, borderTop: "1px solid var(--divider)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 7 }}>
                    <Row label="Reviews" value={`${v.reviewCount}`} />
                    <Row
                      label="Latest"
                      value={latest ? `${String(latest.state).replace(/_/g, " ")} · ${ago(latest.opened_at)}` : "never reviewed"}
                    />
                    <Row
                      label="Monitoring"
                      value={
                        v.decided ? (
                          <Pill tone="green" dot>
                            active
                          </Pill>
                        ) : (
                          <span className="faint small">not yet approved</span>
                        )
                      }
                    />
                  </div>

                  {v.adversarial_flag ? (
                    <div style={{ marginTop: 12 }}>
                      <Pill tone="red">adversarial conduct on record</Pill>
                    </div>
                  ) : null}
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="row-between">
      <span className="small faint">{label}</span>
      <span className="small" style={{ color: "var(--body)" }}>
        {value}
      </span>
    </div>
  );
}
