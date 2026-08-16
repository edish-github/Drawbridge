/**
 * S1d/S1e · The gate card. Where a person acts.
 *
 * `GATED` renders two very different cards depending on the scope. A contact gate shows the
 * actual email before it is sent, because autonomy that asks permission is a stronger claim
 * than autonomy that does not. A decision gate shows the memo, the findings with their
 * provenance labels, and the per-domain arithmetic answering *why 60?* before it is asked.
 *
 * **The controls are inert, and that is the design rather than an unfinished edge.** This
 * dashboard holds no signing key. If it could mint an approval, then every surface an operator
 * can reach could manufacture a human decision — which is exactly what `gateway.sign` refuses
 * to do and what `shared.approvals` structurally cannot do. The card names the command that
 * issues the token instead, so the reader can see where the authority actually lives.
 */

import Link from "next/link";
import { cards, findings, memo, review, score } from "../../../../lib/ledger";
import {
  BandPill,
  Empty,
  FixtureBanner,
  ProvenancePill,
  SeverityPill,
  StatePill,
  when,
} from "../../../components";

export const dynamic = "force-dynamic";

const OPERATOR = "Elena Torres, CISO";

export default async function Gate({ params }: { params: Promise<{ id: string }> }) {
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

  const [result, text, raised, recorded] = await Promise.all([
    score(id),
    memo(id),
    findings(id),
    cards(id),
  ]);

  const scope = String(found.gate_scope ?? "");
  const blocks = recorded.filter((c) => c.kind === "policy_block");
  const parked = recorded.filter((c) => c.kind === "parked");

  return (
    <main className="shell">
      <h1>{found.vendor?.name ?? found.vendor_id}</h1>
      <p className="label">
        <StatePill state={String(found.state)} scope={found.gate_scope} /> ·{" "}
        <Link href={`/review/${id}`}>timeline</Link>
      </p>

      <FixtureBanner review={found} />

      {blocks.length > 0 ? (
        <div className="card">
          <h2>What the gateway refused</h2>
          {blocks.map((block, index) => (
            <div key={index} className="mono error" style={{ marginBottom: 6 }}>
              {block.line}
              <span style={{ color: "var(--faint)" }}> · {when(block.at)}</span>
            </div>
          ))}
          <p className="label" style={{ marginTop: 8 }}>
            A named policy, not a generic block
          </p>
        </div>
      ) : null}

      {parked.length > 0 ? (
        <div className="card">
          <h2>What the fleet refuses to do</h2>
          {parked.map((card, index) => (
            <p key={index}>
              {card.reason}
              <span className="mono" style={{ color: "var(--faint)" }}> · {when(card.at)}</span>
            </p>
          ))}
          <p className="label">A stall is a state, not a notification</p>
        </div>
      ) : null}

      <div className="columns">
        <div>
          <div className="card">
            <h2>{scope === "contact" ? "First contact, awaiting approval" : "Risk memo"}</h2>
            {scope === "contact" ? (
              <p>
                The fleet has built the questionnaire and cannot send it. Policy P1 refuses an
                outbound email without a signed, single-use approval token scoped to this review
                and this recipient, so the review is parked until a person authorises contact
                with <span className="mono">{found.vendor?.contact?.email ?? "the vendor"}</span>.
              </p>
            ) : text ? (
              <div className="memo">{text}</div>
            ) : (
              <Empty>No memo has been written for this review.</Empty>
            )}
            <p className="label" style={{ marginTop: 14 }}>
              Memo screened before display · in local mode this is recorded as not screened
            </p>
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
                    <span className="mono" style={{ color: "var(--faint)" }}>
                      {" "}
                      {f.domain}
                      {f.claim_ref ? ` · claim ${f.claim_ref}` : ""}
                    </span>
                  </div>
                </div>
              ))
            )}
            <p className="label">
              rule means arithmetic · model means judgement over a retrieved passage
            </p>
          </div>
        </div>

        <div>
          <div className="card">
            <h2>Why {result?.score ?? "this"}?</h2>
            {result ? (
              <>
                <div className={`display band-${result.band}`}>{result.score}</div>
                <p style={{ marginTop: 6 }}>
                  <BandPill band={result.band} /> · out of 100
                </p>
                <pre className="mono" style={{ marginTop: 12, whiteSpace: "pre" }}>
                  {(result.arithmetic ?? []).join("\n")}
                </pre>
              </>
            ) : (
              <Empty>This review has not been scored.</Empty>
            )}
          </div>

          <div className="card">
            <h2>{scope === "contact" ? "Authorise first contact" : "Accept the risk"}</h2>
            <div className="decision">
              <button type="button" disabled>
                Approve
              </button>
              <button type="button" disabled data-recommended={result?.band === "conditional"}>
                Conditional
              </button>
              <button type="button" disabled>
                Reject
              </button>
            </div>
            <textarea
              className="conditions"
              placeholder="Conditions attached to a conditional approval"
              readOnly
            />
            <div className="signing">
              You are signing as <strong>{OPERATOR}</strong>. This writes a signed, single-use
              approval token; no code path can set <span className="mono">DECIDED</span> without
              it.
              <p style={{ marginTop: 10, marginBottom: 0 }}>
                The controls above are inert. This dashboard holds no signing key and cannot mint
                an approval — if it could, every surface an operator can reach could manufacture
                a human decision. Issue the token where the authority lives:
              </p>
              <div className="command mono">
                {`python -m scripts.issue_token \\\n  --review-id ${id} \\\n  --scope ${scope || "decision"} \\\n  --identity you@example.com`}
              </div>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
