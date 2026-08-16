/**
 * S1b · The review timeline. The screen the demo lives on.
 *
 * A single vertical event stream, because a review is a story that happened over days. Every
 * entry expands to what the step was trying to do, what it concluded, and the machine detail
 * underneath — agent, trace id, idempotency key. That expansion is worth more than any other
 * component here: it is the difference between a product that says it audits its reasoning and
 * one that shows you.
 *
 * The mechanism is on the screen rather than behind a disclosure nobody opens. Idempotency
 * keys, trace ids and policy lines are printed in monospace, because mono reads as real output
 * rather than as copy somebody wrote.
 */

import Link from "next/link";
import {
  cards,
  findings,
  review,
  score,
  timeline,
  type Doc,
} from "../../../lib/ledger";
import {
  BandPill,
  Empty,
  FixtureBanner,
  ProvenancePill,
  SeverityPill,
  StatePill,
  money,
  when,
} from "../../components";

export const dynamic = "force-dynamic";

export default async function Timeline({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const found = await review(id);

  if (!found) {
    return (
      <main className="shell">
        <h1>Review not found</h1>
        <p className="empty">No review with id {id}.</p>
      </main>
    );
  }

  const [entries, result, raised, recorded] = await Promise.all([
    timeline(id),
    score(id),
    findings(id),
    cards(id),
  ]);

  return (
    <main className="shell">
      <h1>{found.vendor?.name ?? found.vendor_id}</h1>
      <p className="label mono">{id}</p>

      <p style={{ margin: "10px 0 18px" }}>
        <StatePill state={String(found.state)} scope={found.gate_scope} /> · Tier {found.tier}
        {found.tier_history?.length ? (
          <span className="label"> re-tiered from {found.tier_history[0].from_tier}</span>
        ) : null}{" "}
        · plan v{found.plan_version} · {money(found.cost_usd)} spent ·{" "}
        <Link href={`/review/${id}/gate`}>gate card</Link>
      </p>

      <FixtureBanner review={found} />

      <div className="columns">
        <div>
          <div className="card">
            <h2>Timeline</h2>
            {entries.length === 0 ? (
              <Empty>Nothing has been recorded against this review yet.</Empty>
            ) : (
              <div className="stream">
                {entries.map((entry, index) => (
                  <Entry key={`${entry.id ?? index}`} entry={entry} />
                ))}
              </div>
            )}
          </div>
        </div>

        <div>
          <div className="card">
            <h2>Trust Score</h2>
            {result ? (
              <>
                <div className={`display band-${result.band}`}>{result.score}</div>
                <p style={{ marginTop: 6 }}>
                  <BandPill band={result.band} />
                </p>
                <p className="label" style={{ marginTop: 14 }}>
                  Per domain
                </p>
                {Object.entries(result.breakdown ?? {}).map(([domain, left]) => (
                  <div key={domain} style={{ marginBottom: 8 }}>
                    <div style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>{domain.replace(/_/g, " ")}</span>
                      <span className="mono num">{Number(left).toFixed(2)}</span>
                    </div>
                    <div className="bar">
                      <span style={{ width: `${Math.min(100, Number(left) * 4)}%` }} />
                    </div>
                  </div>
                ))}
                <p className="label" style={{ marginTop: 14 }}>
                  Computed in Python, not by a model
                </p>
              </>
            ) : (
              <Empty>This review has not been scored yet.</Empty>
            )}
          </div>

          <div className="card">
            <h2>Findings</h2>
            {raised.length === 0 ? (
              <Empty>No findings recorded.</Empty>
            ) : (
              raised.map((f) => (
                <div key={f.finding_id} style={{ marginBottom: 14 }}>
                  <div>{f.summary}</div>
                  <div style={{ marginTop: 4 }}>
                    <SeverityPill severity={String(f.severity)} />{" "}
                    <ProvenancePill source={String(f.source)} />{" "}
                    {f.contradiction ? (
                      <span className="pill pill-contradiction">contradiction</span>
                    ) : null}
                  </div>
                  <div className="mono" style={{ color: "var(--faint)", marginTop: 3 }}>
                    {f.domain}
                    {f.claim_ref ? ` · claim ${f.claim_ref}` : ""}
                    {f.evidence_ref ? ` · ${f.evidence_ref}` : ""}
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="card">
            <h2>Cards raised</h2>
            {recorded.length === 0 ? (
              <Empty>No gates or refusals were recorded.</Empty>
            ) : (
              recorded.map((card, index) => (
                <div key={index} style={{ marginBottom: 10 }}>
                  <span className="label">{String(card.kind).replace(/_/g, " ")}</span>
                  <div className={card.kind === "policy_block" ? "mono error" : ""}>
                    {card.line ?? card.reason ?? ""}
                  </div>
                  <div className="mono" style={{ color: "var(--faint)" }}>
                    {when(card.at)}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </main>
  );
}

/**
 * One entry, collapsed by default and expandable to the machine detail underneath.
 *
 * `<details>` rather than a client component. There is no state worth hydrating for, and an
 * expansion that works without JavaScript is one that also works in print and in a screenshot.
 */
function Entry({ entry }: { entry: Doc }) {
  const kind = String(entry.kind ?? "event");
  const headline =
    entry.goal ??
    (kind === "tier_change"
      ? `Re-tiered ${entry.from_tier} → ${entry.to_tier}`
      : kind === "policy_block"
        ? entry.line
        : kind === "watchdog_triage"
          ? `Monitoring signal for review: ${entry.title}`
          : transitionOf(entry));

  return (
    <div className="entry" data-kind={kind}>
      <details>
        <summary>{headline}</summary>
        <div className="detail">{entry.decision ?? entry.reason ?? ""}</div>
        <div className="expanded">
          <dl>
            <Row label="Agent" value={entry.agent ?? entry.source} />
            <Row label="Kind" value={kind} />
            <Row label="Type" value={entry.type} />
            <Row label="Goal" value={entry.goal} />
            <Row label="Decision" value={entry.decision} />
            <Row label="Reason" value={entry.reason} />
            <Row label="Trace id" value={entry.trace_id} mono />
            <Row label="Idem key" value={entry.idem_key} mono />
            <Row label="Event id" value={entry.event_id} mono />
            <Row label="When" value={when(entry.at)} mono />
          </dl>
        </div>
      </details>
      <div className="when mono">{when(entry.at)}</div>
    </div>
  );
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string;
  value?: unknown;
  mono?: boolean;
}) {
  if (value === undefined || value === null || value === "") return null;
  return (
    <>
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{String(value)}</dd>
    </>
  );
}

function transitionOf(entry: Doc): string {
  if (entry.to_state) {
    return `${entry.from_state ?? "new"} → ${entry.to_state}`;
  }
  return String(entry.type ?? "event");
}
