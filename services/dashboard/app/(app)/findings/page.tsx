/**
 * Findings — every contradiction and control gap the fleet raised, across the portfolio.
 *
 * The provenance label is the most important thing on the row, which is why it is a pill rather
 * than a column of text. `rule` means the conclusion was arithmetic the code performed; `model`
 * means it was judgement. On a finding that turns on a date there is a second label for the
 * date itself, because the first was quietly claiming more than it proved: a certificate expiry
 * is a comparison the code made over a date a model read off a page.
 *
 * Selecting a finding opens it beside the list rather than navigating away, so the reader keeps
 * their place in a list they are working through.
 */

import Link from "next/link";
import { allFindings, ago, type Doc } from "../../../lib/ledger";
import {
  Empty,
  Filters,
  KeyValue,
  LoadError,
  PageHead,
  Pill,
  ProvenancePill,
  SeverityPill,
} from "../../components";
import { requirePrincipal } from "../../../lib/guard";

export const dynamic = "force-dynamic";

const BUCKETS: Record<string, (f: Doc) => boolean> = {
  all: () => true,
  high: (f) => f.severity === "high",
  medium: (f) => f.severity === "medium",
  low: (f) => f.severity === "low",
  contradiction: (f) => Boolean(f.contradiction),
  conduct: (f) => f.domain === "conduct",
  rule: (f) => f.source === "rule",
  model: (f) => f.source === "model",
};

export default async function Findings({
  searchParams,
}: {
  searchParams: Promise<{ filter?: string; open?: string }>;
}) {
  const principal = await requirePrincipal();
  const orgId = principal.orgId;

  const { filter = "all", open } = await searchParams;

  let rows: Doc[];
  try {
    rows = await allFindings(orgId);
  } catch (error) {
    return (
      <>
        <PageHead crumb="Evidence / Findings" title="Findings" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  const predicate = BUCKETS[filter] ?? BUCKETS.all;
  const shown = rows.filter(predicate);
  const selected = shown.find((f) => f.finding_id === open) ?? shown[0] ?? null;

  const options = [
    { value: "all", label: "All", count: rows.length },
    { value: "high", label: "High", count: rows.filter(BUCKETS.high).length },
    { value: "medium", label: "Medium", count: rows.filter(BUCKETS.medium).length },
    { value: "low", label: "Low", count: rows.filter(BUCKETS.low).length },
    { value: "contradiction", label: "Contradictions", count: rows.filter(BUCKETS.contradiction).length },
    { value: "conduct", label: "Adversarial conduct", count: rows.filter(BUCKETS.conduct).length },
    { value: "rule", label: "Arithmetic", count: rows.filter(BUCKETS.rule).length },
    { value: "model", label: "Judgement", count: rows.filter(BUCKETS.model).length },
  ];

  return (
    <>
      <PageHead
        crumb="Evidence / Findings"
        title="Findings"
        subtitle="Contradictions and control gaps awaiting a human decision, labelled by how each conclusion was reached."
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <Filters options={options} active={filter} base="/findings" />

        {shown.length === 0 ? (
          <div className="card">
            <Empty>No findings under this filter.</Empty>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.1fr) minmax(0, 1fr)", gap: 14 }}>
            <div className="stack">
              {shown.slice(0, 60).map((f) => {
                const active = selected?.finding_id === f.finding_id;
                return (
                  <Link
                    key={f.finding_id}
                    href={`/findings?filter=${filter}&open=${encodeURIComponent(f.finding_id)}`}
                    className="card"
                    style={{
                      display: "block",
                      borderColor: active ? "var(--hairline-strong)" : undefined,
                      boxShadow: active ? "var(--card-shadow-hover)" : undefined,
                      borderLeft: `3px solid var(--${
                        f.severity === "high" ? "red" : f.severity === "medium" ? "amber" : "blue"
                      })`,
                    }}
                  >
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 9 }}>
                      <SeverityPill severity={String(f.severity)} />
                      <ProvenancePill source={String(f.source)} dateSource={f.date_source} />
                      {f.contradiction ? <Pill tone="red">contradiction</Pill> : null}
                    </div>
                    <p className="small" style={{ margin: 0, color: "var(--ink)", fontWeight: 500 }}>
                      {String(f.summary).slice(0, 190)}
                    </p>
                    <div className="row-between" style={{ marginTop: 10 }}>
                      <span className="small faint">
                        {f.vendor?.name ?? f.review?.vendor_id ?? "—"} · {f.domain}
                      </span>
                      <span className="mono small faint">{ago(f.openedAt)}</span>
                    </div>
                  </Link>
                );
              })}
            </div>

            {selected ? (
              <div style={{ position: "sticky", top: 0, alignSelf: "start" }}>
                <div className="card">
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
                    <SeverityPill severity={String(selected.severity)} />
                    <ProvenancePill source={String(selected.source)} dateSource={selected.date_source} />
                    {selected.contradiction ? <Pill tone="red">contradiction</Pill> : null}
                  </div>

                  <p className="body-text" style={{ marginTop: 0, color: "var(--ink)" }}>
                    {selected.summary}
                  </p>

                  <div style={{ marginTop: 16 }}>
                    <KeyValue
                      rows={[
                        ["Finding id", <span className="mono small">{selected.finding_id}</span>],
                        ["Domain", <span className="mono">{selected.domain}</span>],
                        [
                          "Vendor",
                          selected.review ? (
                            <Link href={`/vendors/${selected.review.vendor_id}`}>
                              {selected.vendor?.name ?? selected.review.vendor_id}
                            </Link>
                          ) : (
                            "—"
                          ),
                        ],
                        [
                          "Review",
                          <Link href={`/reviews/${selected.review_id}`} className="mono small">
                            {selected.review_id}
                          </Link>,
                        ],
                        ...(selected.claim_ref
                          ? ([["Claim", <span className="mono">{selected.claim_ref}</span>]] as [string, React.ReactNode][])
                          : []),
                        ...(selected.evidence_ref
                          ? ([["Evidence chunk", <span className="mono small">{selected.evidence_ref}</span>]] as [
                              string,
                              React.ReactNode,
                            ][])
                          : []),
                      ]}
                    />
                  </div>

                  <div className="notice" style={{ marginTop: 16 }}>
                    {selected.source === "rule" ? (
                      <>
                        <strong style={{ color: "var(--ink)" }}>This conclusion is arithmetic.</strong>{" "}
                        The code performed a comparison and the result is reproducible by hand.
                        {selected.date_source ? (
                          <>
                            {" "}
                            The date it turns on was{" "}
                            <strong style={{ color: "var(--ink)" }}>{selected.date_source}</strong> —{" "}
                            {selected.date_source === "extracted"
                              ? "a model read it off the page"
                              : selected.date_source === "declared"
                                ? "a person or an internal system stated it"
                                : "this system derived it from other dates"}
                            .
                          </>
                        ) : null}
                      </>
                    ) : (
                      <>
                        <strong style={{ color: "var(--ink)" }}>This conclusion is judgement.</strong>{" "}
                        A model reconciled a questionnaire claim against the passage retrieved for
                        it. The severity it assigned is what the Trust Score arithmetic consumes —
                        the number itself is never a model&rsquo;s.
                      </>
                    )}
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        )}

        <p className="card-note" style={{ marginTop: 12 }}>
          Showing {Math.min(shown.length, 60)} of {shown.length} matching findings.
        </p>
      </div>
    </>
  );
}
