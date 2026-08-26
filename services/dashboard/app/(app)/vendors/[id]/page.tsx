/**
 * One vendor, across every review it has ever had.
 *
 * The tabs are the design's, and they map onto the phases of a review rather than onto the
 * collections behind them. A tab per Firestore collection would be a database browser; a tab per
 * phase is the thing an analyst is actually reasoning about.
 *
 * Everything shown belongs to **one** review, and the selector above the tabs says which. A
 * vendor page that silently blended two would be the fastest way to attribute a finding to the
 * wrong evidence.
 *
 * The default is the newest review, because *what is happening with this vendor right now* is the
 * question a vendor page is usually opened to answer. On a vendor the Watchdog has reopened that
 * newest review is often an empty one parked at the contact gate — correct, and useless to read —
 * so the selector is on the page rather than behind a link, and it says how much material each
 * review carries.
 */

import Link from "next/link";
import { notFound } from "next/navigation";
import {
  ago,
  answers,
  bandOf,
  chain,
  findings as findingsFor,
  memo as memoFor,
  portfolio,
  reviewsFor,
  score as scoreFor,
  screenings,
  signals,
  timeline,
  vendorDossier,
  when,
  type Doc,
} from "../../../../lib/ledger";
import {
  Avatar,
  BandPill,
  Empty,
  KeyValue,
  LoadError,
  Meter,
  PageHead,
  Pill,
  ProvenancePill,
  ReadOnlyNote,
  SeverityPill,
  StatePill,
  TierPill,
} from "../../../components";
import { requirePrincipal } from "../../../../lib/guard";

export const dynamic = "force-dynamic";

const TABS = ["Overview", "Review", "Evidence", "Findings", "Monitoring", "Decision", "Audit"] as const;
type Tab = (typeof TABS)[number];

export default async function VendorDetail({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string; review?: string }>;
}) {
  const principal = await requirePrincipal();
  const orgId = principal.orgId;

  const { id } = await params;
  const { tab: rawTab, review: wantedReview } = await searchParams;
  const tab = (TABS.find((t) => t.toLowerCase() === String(rawTab ?? "").toLowerCase()) ??
    "Overview") as Tab;

  let vendor: Doc | undefined;
  let reviews: Doc[] = [];
  try {
    const all = await portfolio(orgId);
    vendor = all.find((v) => v.vendor_id === id);
    if (!vendor) notFound();
    reviews = await reviewsFor(orgId, id);
  } catch (error) {
    if ((error as { digest?: string }).digest === "NEXT_NOT_FOUND") throw error;
    return (
      <>
        <PageHead crumb="Reviews / Vendors" title="Vendor" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  const latest =
    reviews.find((r) => r.review_id === wantedReview) ?? reviews[0] ?? null;
  const reviewId = latest?.review_id ?? "";

  const SELECTOR_WINDOW = 8;
  const window_ = reviews.slice(0, SELECTOR_WINDOW);
  const shownReviews =
    latest && !window_.some((r) => r.review_id === reviewId) ? [latest, ...window_] : window_;

  const [score, memo, findings, qa, docs, subs, dossier, feed, tl] = await Promise.all([
    reviewId ? scoreFor(orgId, reviewId) : null,
    reviewId ? memoFor(orgId, reviewId) : "",
    reviewId ? findingsFor(orgId, reviewId) : [],
    reviewId ? answers(orgId, reviewId) : [],
    reviewId ? screenings(orgId, reviewId) : [],
    chain(orgId, id),
    vendorDossier(orgId, id),
    signals(orgId),
    reviewId ? timeline(orgId, reviewId) : [],
  ]);

  const mySignals = feed.filter((s) => s.vendor_id === id);
  const band = bandOf(score?.score ?? null);
  const answered = qa.filter((a) => a.text && !a.needs_human).length;
  const coverage = qa.length ? Math.round((answered / qa.length) * 100) : 0;

  return (
    <>
      <PageHead
        crumb={`Reviews / Vendors / ${vendor.name}`}
        title={String(vendor.name)}
        subtitle={`${vendor.category ?? "—"} · Tier ${vendor.tier ?? "?"}${
          latest ? ` · ${String(latest.state).replace(/_/g, " ")}` : " · never reviewed"
        }`}
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        {reviews.length > 1 ? (
          <div className="filters">
            <span className="label" style={{ alignSelf: "center", marginRight: 4 }}>
              Review
            </span>
            {/* Bounded, and the count says by how much. A vendor under a test suite accumulates
                hundreds of reviews, and a selector that listed them all would push the page it is
                supposed to be selecting for below three screens of chips. The selected review is
                always shown even when it falls outside the window. */}
            {shownReviews.map((r) => (
              <Link
                key={r.review_id}
                href={`/vendors/${id}?tab=${tab.toLowerCase()}&review=${r.review_id}`}
                className="filter"
                data-active={r.review_id === reviewId ? "true" : undefined}
              >
                <span className="mono">{r.review_id.slice(0, 24)}</span>
                <span style={{ opacity: 0.65 }}> · {String(r.state).replace(/_/g, " ")}</span>
              </Link>
            ))}
            {reviews.length > shownReviews.length ? (
              <span className="filter faint" style={{ cursor: "default" }}>
                + {reviews.length - shownReviews.length} older
              </span>
            ) : null}
          </div>
        ) : null}

        <div className="tabs">
          {TABS.map((t) => (
            <Link
              key={t}
              href={`/vendors/${id}?tab=${t.toLowerCase()}${
                wantedReview ? `&review=${wantedReview}` : ""
              }`}
              className="tab"
              data-active={tab === t ? "true" : undefined}
            >
              {t}
            </Link>
          ))}
        </div>

        {!latest ? (
          <div className="card">
            <Empty>
              No review has been opened for this vendor yet.{" "}
              <Link href="/reviews/new" style={{ fontWeight: 600 }}>
                Open one
              </Link>
              .
            </Empty>
          </div>
        ) : tab === "Overview" ? (
          <div className="grid grid-2">
            <div className="card">
              <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 18 }}>
                <Avatar name={String(vendor.name)} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="row-between">
                    <span className="label">Trust Score</span>
                    <TierPill tier={Number(vendor.tier ?? 2)} />
                  </div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 10, margin: "6px 0 10px" }}>
                    <span style={{ font: "700 40px/1 var(--sans)", letterSpacing: "-.03em" }}>
                      {score?.score ?? "—"}
                    </span>
                    <BandPill band={score?.band} score={score?.score ?? null} />
                  </div>
                  <Meter value={score?.score ?? 0} tone={band.tone} />
                </div>
              </div>

              <KeyValue
                rows={[
                  ["Vendor id", <span className="mono">{id}</span>],
                  ["Legal entity", vendor.legal_entity_name ?? "—"],
                  ["Primary domain", <span className="mono">{vendor.primary_domain ?? "—"}</span>],
                  ["AI service", vendor.is_ai_vendor ? "yes" : "no"],
                  ["Reviews on record", String(reviews.length)],
                  ["Latest review", <Link href={`/reviews/${reviewId}`} className="mono">{reviewId}</Link>],
                ]}
              />

              {vendor.adversarial_flag ? (
                <div style={{ marginTop: 14 }}>
                  <Pill tone="red">adversarial conduct on record</Pill>
                </div>
              ) : null}
            </div>

            <div className="card">
              <span className="label">Per-domain arithmetic</span>
              {score?.breakdown ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 11, marginTop: 14 }}>
                  {Object.entries(score.breakdown as Record<string, number>)
                    .sort((a, b) => a[0].localeCompare(b[0]))
                    .map(([domain, left]) => (
                      <div key={domain}>
                        <div className="row-between" style={{ marginBottom: 5 }}>
                          <span className="small">{domain.replace(/_/g, " ")}</span>
                          <span className="mono small">{Number(left).toFixed(2)}</span>
                        </div>
                        <Meter value={Math.min(100, Number(left) * 4)} tone="blue" />
                      </div>
                    ))}
                </div>
              ) : (
                <Empty>Not scored yet.</Empty>
              )}
              {score?.arithmetic ? (
                <details className="entry" style={{ marginTop: 14 }}>
                  <summary>Show the arithmetic as the scorer printed it</summary>
                  <div>
                    <pre className="mono" style={{ fontSize: 11.5, whiteSpace: "pre-wrap", margin: 0, color: "var(--mute)" }}>
                      {(score.arithmetic as string[]).join("\n")}
                    </pre>
                  </div>
                </details>
              ) : null}
            </div>

            <div className="card" style={{ gridColumn: "1 / -1" }}>
              <span className="label">Review history</span>
              <div className="table-scroll" style={{ marginTop: 12 }}>
                <table>
                  <thead>
                    <tr>
                      <th>Review</th>
                      <th>State</th>
                      <th>Tier</th>
                      <th>Opened</th>
                      <th>Decided</th>
                      <th>Reopened from</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reviews.map((r) => (
                      <tr key={r.review_id}>
                        <td className="strong">
                          <Link href={`/reviews/${r.review_id}`} className="mono">
                            {r.review_id}
                          </Link>
                        </td>
                        <td>
                          <StatePill state={String(r.state)} scope={r.gate_scope} />
                        </td>
                        <td className="mono">{r.tier}</td>
                        <td className="mono small faint">{when(r.opened_at)}</td>
                        <td className="mono small faint">{r.decided_at ? when(r.decided_at) : "—"}</td>
                        <td className="mono small faint">{r.reopened_from ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        ) : tab === "Review" ? (
          <div className="grid grid-2">
            <div className="card">
              <span className="label">Questionnaire coverage</span>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, margin: "8px 0 10px" }}>
                <span style={{ font: "700 32px/1 var(--sans)" }}>{coverage}%</span>
                <span className="small faint">
                  {answered} of {qa.length} answered
                </span>
              </div>
              <Meter value={coverage} tone={coverage >= 90 ? "green" : "amber"} />
              <p className="card-note" style={{ marginTop: 12 }}>
                Evidence review opens at 90%, or when an analyst marks the thread complete. The
                threshold is the coverage join declared in the review graph.
              </p>
            </div>

            <div className="card">
              <span className="label">Plan</span>
              <div style={{ marginTop: 12 }}>
                <KeyValue
                  rows={[
                    ["Plan version", <span className="mono">v{latest.plan_version ?? 1}</span>],
                    ["Tier", <span className="mono">{latest.tier}</span>],
                    [
                      "Completed steps",
                      <span className="mono small">
                        {(latest.completed_steps ?? []).join(", ") || "none yet"}
                      </span>,
                    ],
                    ["Current step", <span className="mono">{latest.current_step ?? "—"}</span>],
                    [
                      "Re-tiers",
                      String((latest.tier_history ?? []).length) +
                        ((latest.tier_history ?? []).length
                          ? ` (from tier ${latest.tier_history[0].from_tier})`
                          : ""),
                    ],
                  ]}
                />
              </div>
            </div>

            <div className="card card-tight" style={{ gridColumn: "1 / -1" }}>
              <div style={{ padding: "18px 22px 10px" }}>
                <span className="label">Answers, as parsed</span>
              </div>
              {qa.length === 0 ? (
                <div style={{ padding: "0 22px 18px" }}>
                  <Empty>No replies parsed yet.</Empty>
                </div>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Question</th>
                        <th>Answer</th>
                        <th className="right">Confidence</th>
                        <th className="right">Usable</th>
                      </tr>
                    </thead>
                    <tbody>
                      {qa.slice(0, 60).map((a) => (
                        <tr key={a.id}>
                          <td className="mono strong">{a.question_id}</td>
                          <td style={{ maxWidth: 520 }}>{a.text || <span className="faint">no answer</span>}</td>
                          <td className="right mono">
                            {a.confidence !== undefined ? Number(a.confidence).toFixed(2) : "—"}
                          </td>
                          <td className="right">
                            {a.needs_human ? <Pill tone="amber">re-asked</Pill> : <Pill tone="green">usable</Pill>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        ) : tab === "Evidence" ? (
          <div className="stack">
            <div className="card card-tight">
              <div style={{ padding: "18px 22px 10px" }}>
                <span className="label">Screened documents</span>
              </div>
              {docs.length === 0 ? (
                <div style={{ padding: "0 22px 18px" }}>
                  <Empty>
                    No documents screened for <span className="mono">{reviewId}</span>.
                    {reviews.length > 1
                      ? " This vendor has other reviews — the selector above switches between them."
                      : ""}
                  </Empty>
                </div>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Document</th>
                        <th>Template</th>
                        <th>Verdict</th>
                        <th>Filters</th>
                      </tr>
                    </thead>
                    <tbody>
                      {docs.map((d, i) => {
                        const filters = Object.entries((d.filters ?? {}) as Record<string, string>);
                        const matched = filters.filter(([, v]) => String(v).includes("MATCH_FOUND"));
                        return (
                          <tr key={`${d.id}-${i}`}>
                            <td className="strong mono small">
                              {String(d.origin_ref ?? "").split("/").pop()}
                            </td>
                            <td className="mono small faint">{d.template}</td>
                            <td>
                              {matched.length ? <Pill tone="red">blocked</Pill> : <Pill tone="green">clean</Pill>}
                            </td>
                            <td className="mono small faint">
                              {filters.map(([k, v]) => `${k}:${v}`).join(" · ") || "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="card">
              <span className="label">Fourth-party chain</span>
              <p className="card-note" style={{ marginTop: 6, marginBottom: 12 }}>
                A model reads five fields per subprocessor; a set difference against the
                approved-vendor register decides what they mean. Every finding here is{" "}
                <span className="mono">source=rule</span>.
              </p>
              {subs.length === 0 ? (
                <Empty>No subprocessors extracted for this vendor.</Empty>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Subprocessor</th>
                        <th>Purpose</th>
                        <th>Customer data</th>
                        <th>Jurisdiction</th>
                        <th>Register</th>
                      </tr>
                    </thead>
                    <tbody>
                      {subs.map((s) => (
                        <tr key={s.subprocessor_id}>
                          <td className="strong">{s.name}</td>
                          <td className="small">{s.purpose ?? "—"}</td>
                          <td>
                            {s.processes_customer_data === true ? (
                              <Pill tone="amber">yes</Pill>
                            ) : s.processes_customer_data === false ? (
                              <Pill tone="green">no</Pill>
                            ) : (
                              <Pill tone="gray">not stated</Pill>
                            )}
                          </td>
                          <td className="small">{s.jurisdiction ?? "—"}</td>
                          <td>
                            {s.known_to_org ? (
                              <Pill tone={s.register_status === "current" ? "green" : "amber"}>
                                {s.register_status ?? "known"}
                              </Pill>
                            ) : (
                              <Pill tone="red">unknown</Pill>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        ) : tab === "Findings" ? (
          <div className="stack">
            {findings.length === 0 ? (
              <div className="card">
                <Empty>No findings recorded for this review.</Empty>
              </div>
            ) : (
              findings.map((f) => (
                <div
                  key={f.finding_id}
                  className="card"
                  style={{ borderLeft: `3px solid var(--${f.severity === "high" ? "red" : f.severity === "medium" ? "amber" : "blue"})` }}
                >
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 10 }}>
                    <SeverityPill severity={String(f.severity)} />
                    <ProvenancePill source={String(f.source)} dateSource={f.date_source} />
                    {f.contradiction ? <Pill tone="red">contradiction</Pill> : null}
                    <span className="mono small faint">{f.domain}</span>
                  </div>
                  <p className="body-text" style={{ margin: 0 }}>
                    {f.summary}
                  </p>
                  {f.claim_ref || f.evidence_ref ? (
                    <div style={{ marginTop: 12 }}>
                      <KeyValue
                        rows={[
                          ...(f.claim_ref ? ([["Claim", <span className="mono">{f.claim_ref}</span>]] as [string, React.ReactNode][]) : []),
                          ...(f.evidence_ref
                            ? ([["Evidence", <span className="mono small">{f.evidence_ref}</span>]] as [string, React.ReactNode][])
                            : []),
                        ]}
                      />
                    </div>
                  ) : null}
                </div>
              ))
            )}
          </div>
        ) : tab === "Monitoring" ? (
          <div className="stack">
            <div className="card">
              <span className="label">Watchdog signals for this vendor</span>
              {mySignals.length === 0 ? (
                <Empty>Nothing recorded. The Watchdog sweeps approved vendors on a schedule.</Empty>
              ) : (
                <div className="timeline" style={{ marginTop: 12 }}>
                  {mySignals.map((s) => (
                    <div className="tl-item" key={s.signal_id}>
                      <span className="tl-when">{when(s.at)}</span>
                      <span className="tl-rail">
                        <span
                          className="tl-dot"
                          style={{ background: `var(--${s.action === "open_rereview" ? "red" : s.action === "triage" ? "amber" : "gray"})` }}
                        />
                      </span>
                      <div>
                        <div className="tl-title">{s.title}</div>
                        <div className="tl-detail">
                          {s.source} · {String(s.action ?? "").replace(/_/g, " ")}
                          {s.url ? (
                            <>
                              {" · "}
                              <span className="mono">{s.url}</span>
                            </>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="card">
              <span className="label">Durable memory — what the next review will already know</span>
              {dossier.length === 0 ? (
                <Empty>Nothing carried yet. Memory is written when a review reaches a decision.</Empty>
              ) : (
                <div className="table-scroll" style={{ marginTop: 12 }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Type</th>
                        <th>Value</th>
                        <th className="right">Written</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dossier.slice(0, 30).map((d, i) => (
                        <tr key={i}>
                          <td className="mono strong">{d.note?.type ?? "—"}</td>
                          <td className="mono small">{JSON.stringify(d.note?.value ?? {}).slice(0, 160)}</td>
                          <td className="right mono small faint">{ago(d.written_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        ) : tab === "Decision" ? (
          <div className="grid grid-2">
            <div className="card">
              <span className="label">Risk memo</span>
              {memo ? (
                <pre
                  className="body-text"
                  style={{ whiteSpace: "pre-wrap", margin: "12px 0 0", fontFamily: "var(--sans)" }}
                >
                  {memo}
                </pre>
              ) : (
                <Empty>No memo written yet.</Empty>
              )}
            </div>
            <div className="stack">
              <div className="card">
                <span className="label">Decision</span>
                <div style={{ marginTop: 12 }}>
                  <KeyValue
                    rows={[
                      ["State", <StatePill state={String(latest.state)} scope={latest.gate_scope} />],
                      ["Band", <BandPill band={score?.band} score={score?.score ?? null} />],
                      ["Score", <span className="mono">{score?.score ?? "—"}</span>],
                      ["Decided at", latest.decided_at ? when(latest.decided_at) : "—"],
                      ["Released by", latest.gate_released_by ?? "—"],
                      [
                        "Adversarial modifier",
                        score?.adversarial_applied ? <Pill tone="red">−25 applied</Pill> : "not applied",
                      ],
                    ]}
                  />
                </div>
              </div>
              <ReadOnlyNote>
                Approvals are signed by a separate service that verifies the approver
                independently. Open the{" "}
                <Link href={`/reviews/${reviewId}/gate`} style={{ fontWeight: 600 }}>
                  gate card
                </Link>{" "}
                to review the evidence and decide.
              </ReadOnlyNote>
            </div>
          </div>
        ) : (
          <div className="stack">
            <div className="card">
              <span className="label">Audit trail — latest review</span>
              <p className="card-note" style={{ marginTop: 6 }}>
                {tl.length} entries. The complete audit binder is rendered from this record by a
                template, never by a model —{" "}
                <Link href={`/api/binders/${reviewId}`} style={{ fontWeight: 600 }}>
                  download it
                </Link>
                .
              </p>
              <div className="timeline" style={{ marginTop: 12 }}>
                {tl.slice(-25).map((e, i) => (
                  <div className="tl-item" key={i}>
                    <span className="tl-when">{when(e.at || e.ts)}</span>
                    <span className="tl-rail">
                      <span
                        className="tl-dot"
                        style={{ background: `var(--${e.kind === "reasoning" ? "purple" : "blue"})` }}
                      />
                    </span>
                    <div>
                      <div className="tl-title">{e.goal ?? e.type ?? e.kind}</div>
                      <div className="tl-detail">{e.decision ?? e.reason ?? e.line ?? ""}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
