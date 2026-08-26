/**
 * Activity — the chronological, immutable record of everything the fleet and its humans did.
 *
 * Two streams merged on their timestamps: the event ledger, which is what happened, and the
 * reasoning records, which are why. Splitting them into two lists would make the reader do the
 * merge in their head, and the merge is the whole value — a policy block is only legible next to
 * the step that tried to act.
 *
 * The page says what it is showing out of what exists. This is the collection that grows fastest,
 * and a truncated list that reads as a complete one is worse than a slow page.
 */

import Link from "next/link";
import { activity, ago, when, type Doc } from "../../../lib/ledger";
import { Empty, Filters, KeyValue, LoadError, PageHead, Pill } from "../../components";
import { requirePrincipal } from "../../../lib/guard";

export const dynamic = "force-dynamic";

const BUCKETS: Record<string, (r: Doc) => boolean> = {
  all: () => true,
  events: (r) => r.kind === "event",
  reasoning: (r) => r.kind === "reasoning",
  transitions: (r) => r.type === "review.advance" || r.type === "review.park",
  policy: (r) => String(r.decision ?? "").includes("P1") || String(r.decision ?? "").includes("refused"),
};

export default async function Activity({
  searchParams,
}: {
  searchParams: Promise<{ filter?: string }>;
}) {
  const principal = await requirePrincipal();
  const orgId = principal.orgId;

  const { filter = "all" } = await searchParams;

  let data: Awaited<ReturnType<typeof activity>>;
  try {
    data = await activity(orgId, 300);
  } catch (error) {
    return (
      <>
        <PageHead crumb="System / Activity" title="Activity" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  const predicate = BUCKETS[filter] ?? BUCKETS.all;
  const shown = data.rows.filter(predicate);

  const options = [
    { value: "all", label: "Everything", count: data.rows.length },
    { value: "events", label: "Ledger events", count: data.rows.filter(BUCKETS.events).length },
    { value: "reasoning", label: "Reasoning", count: data.rows.filter(BUCKETS.reasoning).length },
    { value: "transitions", label: "State changes", count: data.rows.filter(BUCKETS.transitions).length },
    { value: "policy", label: "Policy refusals", count: data.rows.filter(BUCKETS.policy).length },
  ];

  return (
    <>
      <PageHead
        crumb="System / Activity"
        title="Activity"
        subtitle="The chronological, immutable record of everything the fleet and its humans did."
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <Filters options={options} active={filter} base="/activity" />

        <div className="card">
          {shown.length === 0 ? (
            <Empty>Nothing under this filter.</Empty>
          ) : (
            <div>
              {shown.map((row, i) => (
                <Row key={`${row.event_id ?? row.id ?? i}`} row={row} />
              ))}
            </div>
          )}
        </div>

        <p className="card-note" style={{ marginTop: 12 }}>
          Showing {shown.length} of {data.total} records in the ledger, newest first. Events are
          append-only — each is its own document keyed by its event id, and nothing updates one.
        </p>
      </div>
    </>
  );
}

function Row({ row }: { row: Doc }) {
  const isReasoning = row.kind === "reasoning";
  const refused = String(row.decision ?? "").includes("refused") || String(row.decision ?? "").includes("P1");
  const tone = refused ? "red" : isReasoning ? "purple" : "blue";

  const title = isReasoning ? row.goal : row.type;
  const detail = isReasoning ? row.decision : row.reason ?? "";

  return (
    <details className="entry">
      <summary>
        <span style={{ width: 7, height: 7, borderRadius: 999, flex: "none", background: `var(--${tone})` }} />
        <span style={{ flex: 1, minWidth: 0 }}>
          <span className="mono" style={{ fontWeight: 600, fontSize: 12 }}>
            {title}
          </span>
          {detail ? <span className="faint"> · {String(detail).slice(0, 96)}</span> : null}
        </span>
        <span className="small faint" style={{ whiteSpace: "nowrap" }}>
          {row.vendor?.name ?? ""}
        </span>
        <span className="mono small faint">{ago(row.at)}</span>
      </summary>
      <div>
        <KeyValue
          rows={[
            [
              "Review",
              row.review_id ? (
                <Link href={`/reviews/${row.review_id}`} className="mono small">
                  {row.review_id}
                </Link>
              ) : (
                "—"
              ),
            ],
            ["At", <span className="mono small">{when(row.at)}</span>],
            ...(row.agent || row.source
              ? ([["Actor", <span className="mono">{row.agent ?? row.source}</span>]] as [string, React.ReactNode][])
              : []),
            ...(row.node ? ([["Graph node", <span className="mono">{row.node}</span>]] as [string, React.ReactNode][]) : []),
            ...(isReasoning && row.decision
              ? ([["Concluded", String(row.decision)]] as [string, React.ReactNode][])
              : []),
            ...(row.from_state
              ? ([
                  ["Transition", <span className="mono">{`${row.from_state} → ${row.to_state}`}</span>],
                ] as [string, React.ReactNode][])
              : []),
            ...(row.idem_key
              ? ([["Idempotency key", <span className="mono small">{row.idem_key}</span>]] as [string, React.ReactNode][])
              : []),
            ...(row.trace_id ? ([["Trace", <span className="mono small">{row.trace_id}</span>]] as [string, React.ReactNode][]) : []),
            ...(row.addendum
              ? ([["Addendum", <Pill tone="amber">{String(row.addendum_reason ?? "out of phase")}</Pill>]] as [
                  string,
                  React.ReactNode,
                ][])
              : []),
            ...(row.payload && Object.keys(row.payload).length
              ? ([["Payload", <span className="mono small">{JSON.stringify(row.payload)}</span>]] as [
                  string,
                  React.ReactNode,
                ][])
              : []),
          ]}
        />
      </div>
    </details>
  );
}
