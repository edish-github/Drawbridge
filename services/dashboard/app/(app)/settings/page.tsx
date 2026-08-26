/**
 * Settings — the policy the fleet runs on, as configured, and read-only on purpose.
 *
 * Settings in most consoles is a page of toggles that write somewhere. This one writes nowhere.
 * The reason is the reason there is no approve button: a surface every reviewer can reach must
 * not be able to change how reviews are scored, and a toggle that silently re-weighted a domain
 * would make every binder rendered before it a document about a policy that no longer exists.
 *
 * What the screen shows instead is where each policy actually lives, and what it currently says.
 * Every value is generated from the file that is the authority on it — `rubric.yaml`,
 * `permission-matrix.yaml`, `pubsub.yaml`, the gateway constants — by `make console`, and a test
 * regenerates and diffs so a stale copy fails CI rather than misleading a reader.
 */

import Link from "next/link";
import policy from "../../../lib/policy.json";
import { Empty, KeyValue, Meter, PageHead, Pill } from "../../components";
import { requirePrincipal } from "../../../lib/guard";

export const dynamic = "force-dynamic";

const SECTIONS = ["Scoring", "Questionnaire", "Gateway", "Identities", "Events", "Graph"] as const;
type Section = (typeof SECTIONS)[number];

export default async function Settings({
  searchParams,
}: {
  searchParams: Promise<{ section?: string }>;
}) {
  await requirePrincipal();
  const { section: raw } = await searchParams;
  const section = (SECTIONS.find((s) => s.toLowerCase() === String(raw ?? "").toLowerCase()) ??
    "Scoring") as Section;

  const weightTotal = Object.values(policy.scoring.domains).reduce((a, b) => a + b, 0);

  return (
    <>
      <PageHead
        crumb="System / Settings"
        title="Settings"
        subtitle="Review policy and risk thresholds, as configured. This console reads them and cannot change them."
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        <div className="notice" style={{ marginBottom: 18 }}>
          <strong style={{ color: "var(--ink)" }}>These are read-only.</strong> Scoring policy is
          changed through a reviewed release rather than a form, and the version a review ran
          under is recorded in its audit binder. A Trust Score anyone could re-weight from this
          screen would be a preference rather than an argument, and the number is the argument.
        </div>

        <div className="tabs">
          {SECTIONS.map((s) => (
            <Link
              key={s}
              href={`/settings?section=${s.toLowerCase()}`}
              className="tab"
              data-active={section === s ? "true" : undefined}
            >
              {s}
            </Link>
          ))}
        </div>

        {section === "Scoring" ? (
          <div className="grid grid-2">
            <div className="card">
              <div className="row-between" style={{ marginBottom: 12 }}>
                <span className="label">Domain weights</span>
                <Pill tone={weightTotal === 100 ? "green" : "red"}>sums to {weightTotal}</Pill>
              </div>
              {Object.entries(policy.scoring.domains)
                .sort((a, b) => b[1] - a[1])
                .map(([domain, weight]) => (
                  <div key={domain} style={{ marginBottom: 11 }}>
                    <div className="row-between" style={{ marginBottom: 4 }}>
                      <span className="small">{domain.replace(/_/g, " ")}</span>
                      <span className="mono small">{weight}</span>
                    </div>
                    <Meter value={weight * 5} tone="blue" />
                  </div>
                ))}
              <p className="card-note" style={{ marginTop: 12 }}>
                Weights sum to exactly 100, so a band read as a percentage is arithmetically true.
                <code className="mono"> scoring.load_rubric</code> raises if that stops being the
                case, and CI checks it on every push.
              </p>
            </div>

            <div className="stack">
              <div className="card">
                <span className="label">Bands</span>
                <div style={{ marginTop: 12 }}>
                  <KeyValue
                    rows={[
                      ["Approve", <span className="mono">{policy.scoring.bands.approve}–100</span>],
                      [
                        "Conditional",
                        <span className="mono">
                          {policy.scoring.bands.conditional}–{policy.scoring.bands.approve - 1}
                        </span>,
                      ],
                      ["Escalate", <span className="mono">0–{policy.scoring.bands.conditional - 1}</span>],
                    ]}
                  />
                </div>
              </div>

              <div className="card">
                <span className="label">Penalties per finding</span>
                <div style={{ marginTop: 12 }}>
                  <KeyValue
                    rows={Object.entries(policy.scoring.penalties).map(([k, v]) => [
                      k,
                      <span className="mono">−{v}</span>,
                    ])}
                  />
                </div>
                <p className="card-note" style={{ marginTop: 12 }}>
                  <strong>No contradiction multiplier</strong>, and its absence is the rule rather
                  than an omission: no fact is priced twice. The severity anchors already define{" "}
                  <em>high</em> as a control the vendor claims is in place being contradicted by
                  their own evidence, so a contradiction was priced the moment the severity was
                  assigned.
                </p>
              </div>

              <div className="card">
                <span className="label">Modifiers</span>
                <div style={{ marginTop: 12 }}>
                  <KeyValue
                    rows={[
                      [
                        "Adversarial conduct",
                        <span>
                          <span className="mono">−{policy.scoring.modifiers.adversarial_conduct.penalty}</span>{" "}
                          <Pill tone="red">
                            forces {policy.scoring.modifiers.adversarial_conduct.forces_band}
                          </Pill>
                        </span>,
                      ],
                    ]}
                  />
                </div>
                <p className="card-note" style={{ marginTop: 10 }}>
                  Applied after the domain arithmetic and regardless of it. A vendor that tried to
                  manipulate the reviewing system disclosed something material that no
                  questionnaire answer would have revealed.
                </p>
              </div>
            </div>

            <div className="card" style={{ gridColumn: "1 / -1" }}>
              <span className="label">Tier profiles — which domains each tier is scored over</span>
              <div className="table-scroll" style={{ marginTop: 12 }}>
                <table style={{ minWidth: 460 }}>
                  <thead>
                    <tr>
                      <th>Tier</th>
                      <th>Domains</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(policy.scoring.tier_profiles).map(([tier, domains]) => (
                      <tr key={tier}>
                        <td className="strong mono">{tier}</td>
                        <td className="mono small">{(domains as string[]).join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="card-note" style={{ marginTop: 10 }}>
                A review is scored over the domains its plan asked about, renormalised to 100 —
                not over the tier&rsquo;s nominal profile. Scoring a freight company on
                AI-specific controls awarded it ten out of ten in a domain nobody put a question
                to.
              </p>
            </div>
          </div>
        ) : section === "Questionnaire" ? (
          <div className="grid grid-2">
            <div className="card">
              <span className="label">Coverage threshold</span>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, margin: "10px 0 12px" }}>
                <span style={{ font: "700 36px/1 var(--sans)" }}>
                  {Math.round(policy.questionnaire.coverage_to_proceed * 100)}%
                </span>
                <span className="small faint">of the plan&rsquo;s questions answered</span>
              </div>
              <Meter value={policy.questionnaire.coverage_to_proceed * 100} tone="amber" />
              <p className="card-note" style={{ marginTop: 12 }}>
                Below this, the review keeps waiting. The escape hatch is an analyst marking the
                reply thread complete, which is the ordinary case of a vendor who answers most of
                what was asked and stops — and a review that reconciled below the threshold says so
                rather than reporting a figure it never reached.
              </p>
            </div>

            <div className="card">
              <div className="row-between" style={{ marginBottom: 12 }}>
                <span className="label">Question bank</span>
                <span className="mono small">{policy.questionnaire.total_questions} questions</span>
              </div>
              {Object.entries(policy.questionnaire.questions_by_domain).map(([domain, count]) => (
                <div className="row-between" key={domain} style={{ padding: "6px 0" }}>
                  <span className="small">{domain.replace(/_/g, " ")}</span>
                  <span className="mono small">{count}</span>
                </div>
              ))}
              <p className="card-note" style={{ marginTop: 12 }}>
                No question in the bank may be answerable yes or no — a yes/no question makes later
                contradiction detection impossible, so the style rule is enforced in CI rather than
                remembered.
              </p>
            </div>
          </div>
        ) : section === "Gateway" ? (
          <div className="stack">
            {policy.gateway.policies.map((p) => (
              <div className="card" key={p.id}>
                <div className="row-between" style={{ marginBottom: 8 }}>
                  <span style={{ fontSize: 15, fontWeight: 600 }}>{p.name}</span>
                  <Pill tone="blue">{p.id}</Pill>
                </div>
                <p className="body-text" style={{ margin: 0 }}>
                  {p.detail}
                </p>
              </div>
            ))}

            <div className="grid grid-2">
              <div className="card">
                <span className="label">Feed allowlist · policy P3</span>
                <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 7 }}>
                  {policy.gateway.feed_allowlist.map((h) => (
                    <div key={h} className="mono small" style={{ color: "var(--body)" }}>
                      {h}
                    </div>
                  ))}
                </div>
                <p className="card-note" style={{ marginTop: 12 }}>
                  Named individually rather than by wildcard: a wildcard on a hosting provider is an
                  allowlist of everyone who bought a subdomain there. Adding one is a security
                  decision, so it is a diff in <code className="mono">shared/gateway.py</code> rather
                  than a value somebody sets at deploy time.
                </p>
              </div>

              <div className="card">
                <span className="label">Where content may reach a model</span>
                <div style={{ marginTop: 12 }}>
                  <KeyValue
                    rows={[
                      [
                        "Model inputs",
                        <span className="mono small">{policy.gateway.model_input_tools.join(", ")}</span>,
                      ],
                      [
                        "Deep model",
                        <span className="mono small">{policy.gateway.deep_model_tools.join(", ")}</span>,
                      ],
                    ]}
                  />
                </div>
                <p className="card-note" style={{ marginTop: 12 }}>
                  Sanitised content is admissible to the Evidence agent and inadmissible to the
                  memo. A sanitised document is by definition one that tried something, and the
                  memo is the artefact a human acts on.
                </p>
              </div>
            </div>
          </div>
        ) : section === "Identities" ? (
          <div className="stack">
            <p className="card-note">
              Ten identities, permissions written at collection level. A row that said
              &ldquo;Firestore&rdquo; could not express the difference between the Questionnaire
              agent writing <code className="mono">qa_responses</code> (which it must) and writing{" "}
              <code className="mono">findings</code> (which it must never), so either the agent is
              over-permissioned or the parse cannot persist.
            </p>
            {policy.identities.map((i) => (
              <div className="card" key={i.name}>
                <div className="row-between" style={{ marginBottom: 8, alignItems: "flex-start" }}>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600 }}>{i.display}</div>
                    <div className="mono small faint">{i.name}</div>
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {i.vertex_ai ? <Pill tone="purple">vertex ai</Pill> : <Pill tone="gray">no model</Pill>}
                    {i.secrets.length ? <Pill tone="red">holds a signing key</Pill> : null}
                  </div>
                </div>
                {i.purpose ? (
                  <p className="body-text" style={{ marginTop: 0 }}>
                    {i.purpose}
                  </p>
                ) : null}
                <div style={{ marginTop: 10 }}>
                  <KeyValue
                    rows={[
                      ["Reads", <span className="mono small">{i.reads.join(", ") || "—"}</span>],
                      ["Writes", <span className="mono small">{i.writes.join(", ") || "—"}</span>],
                      [
                        "Never",
                        <span style={{ color: "var(--red)" }}>✗ {i.never.join(" · ") || "—"}</span>,
                      ],
                    ]}
                  />
                </div>
              </div>
            ))}
          </div>
        ) : section === "Events" ? (
          <div className="grid grid-2">
            <div className="card">
              <span className="label">Topics · {policy.events.topics.length}</span>
              <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
                {policy.events.topics.map((t) => (
                  <div key={t} className="mono small" style={{ color: "var(--body)" }}>
                    {t}
                  </div>
                ))}
              </div>
              <p className="card-note" style={{ marginTop: 12 }}>
                The topic list appears in three places — the code, <code className="mono">pubsub.yaml</code>{" "}
                and the bootstrap script — and CI diffs them. An undocumented topic is one bootstrap
                does not create, and an untested one.
              </p>
            </div>

            <div className="stack">
              <div className="card">
                <span className="label">Delivery</span>
                <div style={{ marginTop: 12 }}>
                  <KeyValue
                    rows={[
                      ["Ack deadline", <span className="mono">{policy.events.ack_deadline_seconds}s</span>],
                      [
                        "Max deliveries",
                        <span className="mono">{policy.events.max_delivery_attempts}</span>,
                      ],
                      ["On exhaustion", "the review parks in needs_human with a card"],
                    ]}
                  />
                </div>
              </div>

              <div className="card">
                <span className="label">Out-of-phase behaviour</span>
                <p className="card-note" style={{ marginTop: 6, marginBottom: 10 }}>
                  Pub/Sub guarantees delivery, not sequence. Each case is defined rather than
                  incidental, and none of them is &ldquo;drop it&rdquo;.
                </p>
                {policy.events.out_of_phase.map((o, i) => (
                  <div key={i} style={{ borderTop: "1px solid var(--divider)", padding: "10px 0" }}>
                    <div className="mono small strong">{o.event}</div>
                    <div className="small faint" style={{ margin: "3px 0" }}>
                      when {o.condition}
                    </div>
                    <div className="small" style={{ color: "var(--body)" }}>
                      {o.behaviour}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="grid grid-2">
            <div className="card">
              <span className="label">The review graph</span>
              <div className="grid grid-3" style={{ gap: 12, marginTop: 14 }}>
                {[
                  ["Nodes", policy.graph.nodes],
                  ["Edges", policy.graph.edges],
                  ["Routers", policy.graph.routers],
                  ["Joins", policy.graph.joins],
                  ["Cycles", policy.graph.cycles],
                  ["Collections", policy.collections.length],
                ].map(([label, value]) => (
                  <div key={String(label)}>
                    <div className="mono" style={{ fontSize: 22, fontWeight: 700 }}>
                      {value as number}
                    </div>
                    <div className="stat-label" style={{ fontSize: 11 }}>
                      {label as string}
                    </div>
                  </div>
                ))}
              </div>
              <p className="card-note" style={{ marginTop: 14 }}>
                Declared in <code className="mono">shared/graph.py</code> and diffed against the
                subscriber&rsquo;s handler table, the event contract, the transition table and the
                permission matrix on every push. Nothing executes it — dispatch is Pub/Sub and the
                review&rsquo;s position is a checkpointed row.
              </p>
              <Link href="/agents" className="filter" style={{ marginTop: 12, display: "inline-block" }}>
                Open the Agent Registry →
              </Link>
            </div>

            <div className="card">
              <span className="label">Plan step vocabulary</span>
              <p className="card-note" style={{ marginTop: 6, marginBottom: 12 }}>
                The only step names a plan may contain. A closed vocabulary is what makes
                idempotency-key derivation reproducible after a restart — a step name invented by a
                model is a key nothing can recompute. Work with no name here is returned as{" "}
                <code className="mono">needs_human</code> rather than given one.
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {policy.graph.step_vocabulary.map((s) => (
                  <span key={s} className="filter" style={{ cursor: "default" }}>
                    {s}
                  </span>
                ))}
              </div>
            </div>

            <div className="card" style={{ gridColumn: "1 / -1" }}>
              <span className="label">Firestore collections · {policy.collections.length}</span>
              <p className="card-note" style={{ marginTop: 6, marginBottom: 12 }}>
                Every one is named in the permission matrix and enforced at collection level by
                generated Firestore rules. Two are deliberately asymmetric:{" "}
                <code className="mono">approved_vendors</code> has readers and no writer, and{" "}
                <code className="mono">decisions</code> has writers and no deleter.
              </p>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {policy.collections.map((c) => (
                  <span key={c} className="mono small filter" style={{ cursor: "default" }}>
                    {c}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
