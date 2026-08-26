/**
 * The handful of primitives every screen shares.
 *
 * Server components without exception. The console reads a ledger and renders it, so there is
 * no state to hold on the client, and every expansion is a `<details>` element rather than a
 * hook — which means it still expands with JavaScript disabled, in a screenshot, and on the
 * machine where hydration failed five minutes before recording.
 *
 * Colour is never the sole carrier of meaning. Every pill has a word in it, every severity has
 * a label, and a screenshot printed in monochrome still says which reviews need a person.
 */

import Link from "next/link";
import type { Doc, Tone } from "../lib/ledger";
import { bandOf, severityTone, stateTone, tierTone } from "../lib/ledger";

/* --- Page chrome --------------------------------------------------------------------- */

export function PageHead({
  crumb,
  title,
  subtitle,
  actions,
}: {
  crumb: string;
  title: string;
  subtitle: string;
  actions?: React.ReactNode;
}) {
  return (
    <header className="topbar">
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 24, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px", minWidth: 260 }}>
          <p className="crumb">
            DRAWBRIDGE <span style={{ opacity: 0.5 }}>/</span>{" "}
            <span style={{ color: "#5f5d58" }}>{crumb}</span>
          </p>
          <h1 className="page-title">{title}</h1>
          <p className="page-sub">{subtitle}</p>
        </div>
        {actions ? <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{actions}</div> : null}
      </div>
    </header>
  );
}

/** A page that could not read its data. Says what broke rather than rendering an empty shell. */
export function LoadError({ error }: { error: unknown }) {
  // The message is deliberately about what the reader can do, not about what broke. An operator
  // seeing this cannot fix a database, and a stack trace on a security product's screen is an
  // information leak before it is an inconvenience. The detail goes to the server log.
  if (typeof console !== "undefined") console.error("[console] ledger read failed:", error);

  return (
    <div className="scroll-area">
      <div className="banner">
        <strong>We could not load your workspace.</strong> This is on our side, not yours.
      </div>
      <p className="body-text">
        Nothing has been changed. Try again in a moment — if it keeps happening, contact support
        and mention the time you saw this.
      </p>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="empty">{children}</p>;
}

/* --- Pills --------------------------------------------------------------------------- */

export function Pill({ tone, children, dot }: { tone: Tone; children: React.ReactNode; dot?: boolean }) {
  return (
    <span className={`pill tone-${tone}`}>
      {dot ? <i className="pill-dot" /> : null}
      {children}
    </span>
  );
}

/** A machine state, always with the word in it. `GATED` carries its scope or it means nothing. */
export function StatePill({ state, scope }: { state: string; scope?: string | null }) {
  const label = scope ? `${state.replace(/_/g, " ")} · ${scope}` : state.replace(/_/g, " ");
  return <Pill tone={stateTone(state, scope)}>{label}</Pill>;
}

export function BandPill({ band, score }: { band?: string | null; score?: number | null }) {
  const resolved = band ?? bandOf(score).label;
  if (!band && (score === null || score === undefined)) {
    return <span className="faint small">not scored</span>;
  }
  return <Pill tone={bandOf(score ?? (resolved === "approve" ? 90 : resolved === "conditional" ? 70 : 40)).tone}>{resolved}</Pill>;
}

export function TierPill({ tier }: { tier: number }) {
  return <Pill tone={tierTone(tier)}>tier {tier}</Pill>;
}

export function SeverityPill({ severity }: { severity: string }) {
  return <Pill tone={severityTone(severity)}>{severity}</Pill>;
}

/**
 * `rule` means the conclusion was arithmetic; `model` means it was judgement.
 *
 * The single most important two words on any finding, which is why they get their own component
 * and their own colours rather than being printed as text in a cell.
 */
export function ProvenancePill({ source, dateSource }: { source: string; dateSource?: string | null }) {
  return (
    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
      <Pill tone={source === "rule" ? "blue" : "purple"}>{source}</Pill>
      {dateSource ? <Pill tone="gray">date {dateSource}</Pill> : null}
    </span>
  );
}

/* --- Data display --------------------------------------------------------------------- */

export function Stat({
  value,
  label,
  tone,
  href,
}: {
  value: React.ReactNode;
  label: string;
  tone?: Tone;
  href?: string;
}) {
  const inner = (
    <div className="card">
      <div className="stat-value" style={tone ? { color: `var(--${tone})` } : undefined}>
        {value}
      </div>
      <div className="stat-label">{label}</div>
    </div>
  );
  return href ? (
    <Link href={href} style={{ display: "block" }}>
      {inner}
    </Link>
  ) : (
    inner
  );
}

export function Meter({ value, tone = "green" }: { value: number; tone?: Tone }) {
  const width = Math.max(0, Math.min(100, value));
  return (
    <div className="meter" role="img" aria-label={`${Math.round(width)} percent`}>
      <span style={{ width: `${width}%`, background: `var(--${tone})` }} />
    </div>
  );
}

export function Avatar({ name }: { name: string }) {
  const letters = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  return (
    <div className="avatar" aria-hidden="true">
      {letters || "??"}
    </div>
  );
}

/** A live agent's heartbeat. Two stacked dots, the outer one pulsing. */
export function LiveDot({ tone }: { tone: Tone }) {
  return (
    <span className="live" style={{ color: `var(--${tone})` }} aria-hidden="true">
      <i />
      <i />
    </span>
  );
}

export function KeyValue({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/* --- Filters -------------------------------------------------------------------------- */

/**
 * Filter chips as links rather than buttons.
 *
 * The filter is in the URL, so a filtered view is a thing an operator can send to a colleague
 * and a thing the back button understands. A client-side filter would be neither.
 */
export function Filters({
  options,
  active,
  base,
  param = "filter",
}: {
  options: { value: string; label: string; count?: number }[];
  active: string;
  base: string;
  param?: string;
}) {
  return (
    <div className="filters">
      {options.map((o) => (
        <Link
          key={o.value}
          href={o.value === "all" ? base : `${base}?${param}=${encodeURIComponent(o.value)}`}
          className="filter"
          data-active={active === o.value ? "true" : undefined}
        >
          {o.label}
          {o.count !== undefined ? ` · ${o.count}` : ""}
        </Link>
      ))}
    </div>
  );
}

/* --- Declarations --------------------------------------------------------------------- */

/**
 * The unscreened-fixtures banner.
 *
 * A review built on seeded evidence carries the fact on its record, and every surface that
 * shows the review shows the fact. An artefact indistinguishable from a real one would be the
 * most damaging thing this project could produce, so the declaration travels with the data
 * rather than living in a footnote somebody has to remember.
 */
export function FixtureBanner({ review }: { review: Doc }) {
  if (!review.unscreened_fixtures) return null;
  return (
    <div className="banner">
      <strong>Built on unscreened fixtures.</strong> The evidence in this review was seeded into
      the clean bucket and inspected by no detector. Its screening records carry the template{" "}
      <span className="mono">local-seed</span> and are not verdicts.
    </div>
  );
}

/**
 * What this console cannot do, said on the surface where somebody would expect it to.
 *
 * Used on the gate card and in Settings. The dashboard holds no signing key and no write path,
 * and the honest form of that is naming the command that does the thing rather than showing a
 * button that would have to be disabled.
 */
export function ReadOnlyNote({ children }: { children: React.ReactNode }) {
  return <div className="notice">{children}</div>;
}
