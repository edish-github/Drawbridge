/**
 * The handful of primitives every screen shares.
 *
 * Server components without exception. The dashboard reads a ledger and renders it, so there is
 * no state to hold on the client, and the timeline's expansion is a `<details>` element rather
 * than a hook — which means it still expands with JavaScript disabled, in a screenshot, and on
 * the machine where hydration failed five minutes before recording.
 */

import type { Doc } from "../lib/ledger";

/** A machine state, always with the word in it — colour is never the sole carrier of meaning. */
export function StatePill({ state, scope }: { state: string; scope?: string | null }) {
  const label = scope ? `${state.replace(/_/g, " ")} · ${scope}` : state.replace(/_/g, " ");
  return <span className={`pill pill-${state}`}>{label}</span>;
}

export function BandPill({ band }: { band?: string | null }) {
  if (!band) return <span className="empty">not scored</span>;
  return <span className={`pill pill-${band}`}>{band}</span>;
}

/** `rule` means the conclusion was arithmetic; `model` means it was judgement. */
export function ProvenancePill({ source }: { source: string }) {
  return <span className={`pill pill-${source}`}>{source}</span>;
}

export function SeverityPill({ severity }: { severity: string }) {
  return <span className={`pill pill-${severity}`}>{severity}</span>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function money(value: unknown): string {
  return `$${Number(value ?? 0).toFixed(4)}`;
}

export function when(value: unknown): string {
  const text = String(value ?? "");
  return text ? text.slice(0, 19).replace("T", " ") : "—";
}

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
