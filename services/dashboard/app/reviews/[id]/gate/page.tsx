/**
 * The gate card. Everything a named human needs to accept or refuse the risk, and nothing that
 * could do it for them.
 *
 * The controls are rendered and inert, and the card says why rather than showing a disabled
 * button with no explanation. The dashboard holds no signing key and there is no write path in
 * it — if a surface every reviewer can reach could mint an approval, the gateway refusing to
 * sign would be decorative. What the card does instead is name the exact command that issues
 * the token, scoped to this review.
 *
 * The order is deliberate: what the fleet concluded, then what it rests on, then what it is
 * asking for. A card that opened with the button would be asking for a decision before showing
 * the evidence.
 */

import Link from "next/link";
import {
  approvals as approvalsFor,
  findings as findingsFor,
  memo as memoFor,
  review as loadReview,
  score as scoreFor,
  when,
  type Doc,
} from "../../../../lib/ledger";
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
  ReadOnlyNote,
  SeverityPill,
  StatePill,
  TierPill,
} from "../../../components";

export const dynamic = "force-dynamic";

export default async function GateCard({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let found: Doc | null;
  try {
    found = await loadReview(id);
  } catch (error) {
    return (
      <>
        <PageHead crumb="Reviews / Gate" title="Gate card" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  if (!found) {
    return (
      <>
        <PageHead crumb="Reviews / Gate" title="Review not found" subtitle={`No review with id ${id}.`} />
        <div className="scroll-area" />
      </>
    );
  }

  const [result, raised, memo, tokens] = await Promise.all([
    scoreFor(id),
    findingsFor(id),
    memoFor(id),
    approvalsFor(id),
  ]);

  const scope = String(found.gate_scope ?? "");
  const waiting = found.state === "gated";
  const contradictions = raised.filter((f) => f.contradiction);
  const ruleFindings = raised.filter((f) => f.source === "rule");

  return (
    <>
      <PageHead
        crumb={`Reviews / ${found.vendor?.name ?? found.vendor_id} / Gate`}
        title={waiting ? (scope === "decision" ? "Risk acceptance" : "Authorise first contact") : "Gate card"}
        subtitle={
          waiting
            ? scope === "decision"
              ? "Human gate G1. A named person accepts the residual risk, or refuses it."
              : "Human gate G2. Nothing leaves the building until a person authorises the thread."
            : `This review is ${String(found.state).replace(/_/g, " ")} and is not waiting at a gate.`
        }
        actions={
          <>
            <Link href={`/reviews/${id}`} className="filter">
              Timeline
            </Link>
            <Link href={`/reviews/${id}/graph`} className="filter">
              Graph
            </Link>
          </>
        }
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <FixtureBanner review={found} />

        {found.park_reason && found.state === "needs_human" ? (
          <div className="banner">
            <strong>Parked, not gated.</strong> {found.park_reason} — this review stopped rather
            than reaching a decision point. Resolve what stalled and the fleet resumes it.
          </div>
        ) : null}

        <div className="grid grid-2">
          <div className="stack">
            {/* --- What the fleet concluded ------------------------------------------ */}
            <div className="card">
              <div className="row-between" style={{ marginBottom: 14 }}>
                <span className="label">What the fleet concluded</span>
                <StatePill state={String(found.state)} scope={found.gate_scope} />
              </div>

              {result ? (
                <>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 12 }}>
                    <span style={{ font: "700 46px/1 var(--sans)", letterSpacing: "-.03em" }}>
                      {result.score}
                    </span>
                    <div>
                      <BandPill band={result.band} score={result.score} />
                      <div className="small faint" style={{ marginTop: 4 }}>
                        Trust Score, 0–100, higher is safer
                      </div>
                    </div>
                  </div>
                  <Meter value={Number(result.score)} tone={result.band === "approve" ? "green" : result.band === "conditional" ? "amber" : "red"} />

                  {result.adversarial_applied ? (
                    <div className="banner" style={{ marginTop: 14, marginBottom: 0 }}>
                      <strong>Adversarial conduct.</strong> 25 points were taken after the domain
                      arithmetic and regardless of it, and the band was forced to escalate. A
                      vendor that tried to manipulate the reviewing system disclosed something no
                      questionnaire answer would have.
                    </div>
                  ) : null}

                  {result.arithmetic ? (
                    <details className="entry" style={{ marginTop: 14 }}>
                      <summary>Show the arithmetic, exactly as the scorer computed it</summary>
                      <div>
                        <pre
                          className="mono"
                          style={{ fontSize: 11.5, whiteSpace: "pre-wrap", margin: 0, color: "var(--mute)" }}
                        >
                          {(result.arithmetic as string[]).join("\n")}
                        </pre>
                        <p className="card-note" style={{ marginTop: 10 }}>
                          Pure Python over rubric.yaml. `agents/risk_scorer/scoring.py` cannot
                          reach the model router in its transitive imports, and that is asserted
                          by a test rather than promised.
                        </p>
                      </div>
                    </details>
                  ) : null}
                </>
              ) : (
                <Empty>This review has not been scored, so there is no risk to accept yet.</Empty>
              )}
            </div>

            {/* --- The memo --------------------------------------------------------- */}
            {memo ? (
              <div className="card">
                <span className="label">Risk memo</span>
                <p className="card-note" style={{ marginTop: 6, marginBottom: 10 }}>
                  Written by the deep model against a band it was given rather than one it
                  decided. One of exactly two places the deep model is spent.
                </p>
                <pre
                  className="body-text"
                  style={{ whiteSpace: "pre-wrap", margin: 0, fontFamily: "var(--sans)" }}
                >
                  {memo}
                </pre>
              </div>
            ) : null}
          </div>

          <div className="stack">
            {/* --- What it rests on -------------------------------------------------- */}
            <div className="card">
              <div className="row-between" style={{ marginBottom: 12 }}>
                <span className="label">What it rests on</span>
                <span className="mono small faint">
                  {raised.length} findings · {ruleFindings.length} arithmetic
                </span>
              </div>

              {raised.length === 0 ? (
                <Empty>No findings recorded.</Empty>
              ) : (
                <div style={{ display: "flex", flexDirection: "column" }}>
                  {raised.map((f) => (
                    <div
                      key={f.finding_id}
                      style={{ borderTop: "1px solid var(--divider)", padding: "12px 0" }}
                    >
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 7 }}>
                        <SeverityPill severity={String(f.severity)} />
                        <ProvenancePill source={String(f.source)} dateSource={f.date_source} />
                        {f.contradiction ? <Pill tone="red">contradiction</Pill> : null}
                        <span className="mono small faint">{f.domain}</span>
                      </div>
                      <p className="small" style={{ margin: 0, color: "var(--body)" }}>
                        {f.summary}
                      </p>
                    </div>
                  ))}
                </div>
              )}

              {contradictions.length ? (
                <p className="card-note" style={{ marginTop: 12 }}>
                  {contradictions.length} of these are contradictions — a control the vendor
                  claims is in place, disagreed with by the vendor&rsquo;s own evidence.
                </p>
              ) : null}
            </div>

            {/* --- What it is asking for -------------------------------------------- */}
            <div className="card">
              <span className="label">The decision</span>
              <div style={{ marginTop: 12 }}>
                <KeyValue
                  rows={[
                    ["Gate", scope === "decision" ? "G1 · risk acceptance" : scope === "contact" ? "G2 · first outbound contact" : "—"],
                    ["Review", <span className="mono">{id}</span>],
                    ["Vendor", found.vendor?.name ?? found.vendor_id],
                    ["Tier", <TierPill tier={Number(found.tier ?? 2)} />],
                    ["Plan version", <span className="mono">v{found.plan_version ?? 1}</span>],
                    ["Requested", when(found.opened_at)],
                    ["Released by", found.gate_released_by ?? <span className="faint">not yet</span>],
                  ]}
                />
              </div>
            </div>

            <ReadOnlyNote>
              <strong style={{ color: "var(--ink)" }}>This console cannot approve anything.</strong>{" "}
              It holds no signing key and there is no write path in it — the approval service holds
              the private half and the gateway holds only the public half, so the gateway can
              recognise a human decision and is structurally incapable of manufacturing one. The
              token it recognises is <strong style={{ color: "var(--ink)" }}>single-use and scoped
              to this review and this gate</strong>: an approval spent at G2 cannot be replayed at
              G1, and neither can be spent twice.
              <div style={{ marginTop: 10 }}>
                <div className="label" style={{ marginBottom: 5 }}>
                  Issue the token
                </div>
                <pre
                  className="mono"
                  style={{
                    margin: 0,
                    padding: "10px 12px",
                    background: "var(--sunken)",
                    borderRadius: 10,
                    fontSize: 11.5,
                    overflowX: "auto",
                  }}
                >
                  {`python -m scripts.issue_token \\\n  --review ${id} \\\n  --scope ${scope || "decision"} \\\n  --identity you@example.com`}
                </pre>
              </div>
            </ReadOnlyNote>

            {tokens.length ? (
              <div className="card">
                <span className="label">Approvals on record</span>
                <div className="table-scroll" style={{ marginTop: 10 }}>
                  <table style={{ minWidth: 380 }}>
                    <thead>
                      <tr>
                        <th>Scope</th>
                        <th>Identity</th>
                        <th className="right">Issued</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tokens.map((t) => (
                        <tr key={t.jti}>
                          <td>
                            <Pill tone={t.scope === "decision" ? "amber" : "blue"}>{t.scope}</Pill>
                          </td>
                          <td className="mono small">{t.identity}</td>
                          <td className="right mono small faint">{when(t.issued_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}
