/**
 * One review's timeline. The screen the demo lives on.
 *
 * A single vertical event stream, because a review is a story that happened over days. Every
 * entry expands to what the step was trying to do, what it concluded, and the machine detail
 * underneath — agent, graph node, trace id, idempotency key. That expansion is worth more than
 * any other component here: it is the difference between a product that says it audits its
 * reasoning and one that shows you.
 *
 * The mechanism is on the screen rather than behind a disclosure nobody opens. Idempotency
 * keys, trace ids and policy lines are printed in monospace, because mono reads as real output
 * rather than as copy somebody wrote.
 */

import Link from "next/link";
import {
  ago,
  cards,
  chain,
  findings,
  memo as memoFor,
  review as loadReview,
  score as scoreFor,
  timeline,
  when,
  money,
  type Doc,
} from "../../../lib/ledger";
import {
  BandPill,
  Empty,
  FixtureBanner,
  KeyValue,
  LoadError,
  Meter,
  PageHead,
  Pill,
  ProvenancePill,
  SeverityPill,
  StatePill,
  TierPill,
} from "../../components";

export const dynamic = "force-dynamic";

export default async function ReviewTimeline({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let found: Doc | null;
  try {
    found = await loadReview(id);
  } catch (error) {
    return (
      <>
        <PageHead crumb="Reviews" title="Review" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  if (!found) {
    return (
      <>
        <PageHead crumb="Reviews" title="Review not found" subtitle={`No review with id ${id}.`} />
        <div className="scroll-area">
          <div className="card">
            <Empty>
              Nothing in the ledger carries this id. The queue lists every review the fleet knows
              about.
            </Empty>
          </div>
        </div>
      </>
    );
  }

  const [entries, result, raised, recorded, fourthParties, memo] = await Promise.all([
    timeline(id),
    scoreFor(id),
    findings(id),
    cards(id),
    chain(String(found.vendor_id)),
    memoFor(id),
  ]);

  return (
    <>
      <PageHead
        crumb={`Reviews / ${found.vendor?.name ?? found.vendor_id}`}
        title={String(found.vendor?.name ?? found.vendor_id)}
        subtitle={`${id} · plan v${found.plan_version ?? 1} · ${money(found.cost_usd)} spent`}
        actions={
          <>
            <Link href={`/reviews/${id}/graph`} className="filter">
              Graph
            </Link>
            <Link href={`/reviews/${id}/gate`} className="filter">
              Gate card
            </Link>
            <Link href={`/vendors/${found.vendor_id}`} className="filter">
              Vendor
            </Link>
          </>
        }
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16, alignItems: "center" }}>
          <StatePill state={String(found.state)} scope={found.gate_scope} />
          <TierPill tier={Number(found.tier ?? 2)} />
          {found.tier_history?.length ? (
            <Pill tone="amber">re-tiered from {found.tier_history[0].from_tier}</Pill>
          ) : null}
          <BandPill band={found.band} score={found.score} />
          {found.reopened_from ? (
            <Pill tone="purple">reopened from {found.reopened_from}</Pill>
          ) : null}
        </div>

        <FixtureBanner review={found} />

        {found.park_reason ? (
          <div className="banner">
            <strong>Parked.</strong> {found.park_reason}
          </div>
        ) : null}

        <div className="grid grid-2">
          <div className="card card-tight" style={{ padding: "20px 22px" }}>
            <span className="label">Timeline</span>
            {entries.length === 0 ? (
              <Empty>Nothing has been recorded against this review yet.</Empty>
            ) : (
              <div style={{ marginTop: 12 }}>
                {entries.map((entry, index) => (
                  <Entry key={`${entry.id ?? index}`} entry={entry} />
                ))}
              </div>
            )}
          </div>

          <div className="stack">
            <div className="card">
              <span className="label">Trust Score</span>
              {result ? (
                <>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 10, margin: "8px 0 12px" }}>
                    <span style={{ font: "700 40px/1 var(--sans)", letterSpacing: "-.03em" }}>
                      {result.score}
                    </span>
                    <BandPill band={result.band} score={result.score} />
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {Object.entries((result.breakdown ?? {}) as Record<string, number>)
                      .sort((a, b) => a[0].localeCompare(b[0]))
                      .map(([domain, left]) => (
                        <div key={domain}>
                          <div className="row-between" style={{ marginBottom: 4 }}>
                            <span className="small">{domain.replace(/_/g, " ")}</span>
                            <span className="mono small">{Number(left).toFixed(2)}</span>
                          </div>
                          <Meter value={Math.min(100, Number(left) * 4)} tone="blue" />
                        </div>
                      ))}
                  </div>
                  {result.adversarial_applied ? (
                    <div style={{ marginTop: 12 }}>
                      <Pill tone="red">adversarial conduct · −25 and forced escalation</Pill>
                    </div>
                  ) : null}
                </>
              ) : (
                <Empty>Not scored yet.</Empty>
              )}
            </div>

            <div className="card">
              <span className="label">Findings · {raised.length}</span>
              {raised.length === 0 ? (
                <Empty>None recorded.</Empty>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 12 }}>
                  {raised.map((f) => (
                    <div key={f.finding_id} style={{ borderTop: "1px solid var(--divider)", paddingTop: 12 }}>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
                        <SeverityPill severity={String(f.severity)} />
                        <ProvenancePill source={String(f.source)} dateSource={f.date_source} />
                        {f.contradiction ? <Pill tone="red">contradiction</Pill> : null}
                      </div>
                      <p className="small" style={{ margin: 0, color: "var(--body)" }}>
                        {f.summary}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {memo ? (
              <div className="card">
                <span className="label">Risk memo</span>
                <pre
                  className="body-text"
                  style={{ whiteSpace: "pre-wrap", margin: "10px 0 0", fontFamily: "var(--sans)" }}
                >
                  {memo}
                </pre>
              </div>
            ) : null}

            {fourthParties.length ? (
              <div className="card">
                <span className="label">Fourth-party chain</span>
                <div style={{ display: "flex", flexDirection: "column", gap: 9, marginTop: 12 }}>
                  {fourthParties.map((s) => (
                    <div className="row-between" key={s.subprocessor_id}>
                      <span className="small strong">{s.name}</span>
                      {s.known_to_org ? (
                        <Pill tone={s.register_status === "current" ? "green" : "amber"}>
                          {s.register_status ?? "known"}
                        </Pill>
                      ) : (
                        <Pill tone="red">unknown</Pill>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {recorded.length ? (
              <div className="card">
                <span className="label">Cards raised</span>
                <div style={{ display: "flex", flexDirection: "column", gap: 9, marginTop: 12 }}>
                  {recorded.map((c, i) => (
                    <div key={i} className="row-between">
                      <span className="small">{c.line ?? c.reason ?? c.kind}</span>
                      <span className="mono small faint">{ago(c.at)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}

/**
 * One ledger entry, expandable to the machine detail underneath it.
 *
 * A `<details>` rather than a hook: it still expands with JavaScript disabled, in a screenshot,
 * and on the machine where hydration failed five minutes before recording.
 */
function Entry({ entry }: { entry: Doc }) {
  const tone =
    entry.kind === "reasoning"
      ? "purple"
      : entry.kind === "policy_block"
        ? "red"
        : entry.kind === "tier_change"
          ? "amber"
          : entry.kind === "gate" || entry.kind === "parked"
            ? "amber"
            : "blue";

  const title =
    entry.goal ??
    (entry.type ? String(entry.type) : null) ??
    entry.line ??
    String(entry.kind ?? "entry");

  const detail = entry.decision ?? entry.reason ?? entry.line ?? "";

  return (
    <details className="entry">
      <summary>
        <span
          style={{ width: 7, height: 7, borderRadius: 999, flex: "none", background: `var(--${tone})` }}
        />
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ fontWeight: 600 }}>{title}</span>
          {detail ? <span className="faint"> · {String(detail).slice(0, 90)}</span> : null}
        </span>
        <span className="mono small faint">{when(entry.at ?? entry.ts)}</span>
      </summary>
      <div>
        <KeyValue
          rows={[
            ...(entry.agent ? ([["Agent", <span className="mono">{entry.agent}</span>]] as [string, React.ReactNode][]) : []),
            ...(entry.node ? ([["Graph node", <span className="mono">{entry.node}</span>]] as [string, React.ReactNode][]) : []),
            ...(entry.decision ? ([["Concluded", String(entry.decision)]] as [string, React.ReactNode][]) : []),
            ...(entry.from_state
              ? ([["Transition", <span className="mono">{`${entry.from_state} → ${entry.to_state}`}</span>]] as [string, React.ReactNode][])
              : []),
            ...(entry.idem_key
              ? ([["Idempotency key", <span className="mono small">{entry.idem_key}</span>]] as [string, React.ReactNode][])
              : []),
            ...(entry.trace_id
              ? ([["Trace", <span className="mono small">{entry.trace_id}</span>]] as [string, React.ReactNode][])
              : []),
            ...(entry.policy ? ([["Policy", <Pill tone="red">{entry.policy}</Pill>]] as [string, React.ReactNode][]) : []),
            ...(entry.payload && Object.keys(entry.payload).length
              ? ([["Payload", <span className="mono small">{JSON.stringify(entry.payload)}</span>]] as [string, React.ReactNode][])
              : []),
          ]}
        />
      </div>
    </details>
  );
}
