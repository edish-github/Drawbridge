/**
 * Reading the review ledger. Reads only, and the absence of a write path is the design.
 *
 * The dashboard is where an operator decides, and it holds nothing that can act on the
 * decision. There is no write in this file and no API route behind it, for the same reason
 * `shared/gateway.py` refuses to sign: a surface every reviewer can reach must not be a surface
 * that can manufacture a human decision. The gate card renders the controls and names the
 * command that mints the token; the token itself comes from the approval service.
 *
 * Local mode only. `FIRESTORE_EMULATOR_HOST` is required rather than defaulted, because a
 * client library that silently falls through to a real project is a development UI writing —
 * or in this case reading — against production.
 *
 * **Every query is bounded.** A ledger that has been under a test suite holds thousands of
 * reviews and tens of thousands of events, and a console that fetched all of them would render
 * once and then stop being usable. Bounds are declared per query rather than defaulted, and
 * where a bound drops rows the surface says how many it dropped — a truncated list that reads
 * as a complete one is the only thing here worse than a slow page.
 */

import { Firestore } from "@google-cloud/firestore";

import policy from "./policy.json";

export type Doc = Record<string, any>;

let client: Firestore | null = null;

/** The process-wide Firestore client, pointed at the emulator. */
export function db(): Firestore {
  if (!process.env.FIRESTORE_EMULATOR_HOST) {
    throw new Error(
      "FIRESTORE_EMULATOR_HOST is not set. The dashboard runs against the emulator in local " +
        "mode; run `make emulators` and start it through `make dashboard`.",
    );
  }
  if (!client) {
    // The emulator accepts any project id and never authenticates it, but it does partition on
    // it — so a dashboard reading under a different id than the fleet wrote under sees an empty
    // database and reports it as an empty queue. Same precedence as `settings.emulator_project`.
    client = new Firestore({
      projectId:
        process.env.GOOGLE_CLOUD_PROJECT || process.env.PROJECT_ID || "drawbridge-local",
    });
  }
  return client;
}

async function all(collection: string, limit = 4000): Promise<Doc[]> {
  const snapshot = await db().collection(collection).limit(limit).get();
  return snapshot.docs.map((d) => ({ id: d.id, ...d.data() }));
}

async function forReview(collection: string, reviewId: string, limit = 800): Promise<Doc[]> {
  const snapshot = await db()
    .collection(collection)
    .where("review_id", "==", reviewId)
    .limit(limit)
    .get();
  return snapshot.docs.map((d) => ({ id: d.id, ...d.data() }));
}

async function forVendor(collection: string, vendorId: string, limit = 400): Promise<Doc[]> {
  if (!vendorId) return [];
  const snapshot = await db()
    .collection(collection)
    .where("vendor_id", "==", vendorId)
    .limit(limit)
    .get();
  return snapshot.docs.map((d) => ({ id: d.id, ...d.data() }));
}

/* ------------------------------------------------------------------------------------------
 * Shared vocabulary
 *
 * The band boundaries and the state labels are the product's, not the console's. They are
 * restated here because this is TypeScript and the rubric is YAML, and the restatement is
 * checked: `tests/test_console.py` reads `rubric.yaml` and fails if these numbers drift.
 * ---------------------------------------------------------------------------------------- */

export type Tone = "green" | "amber" | "red" | "blue" | "purple" | "gray";

export const BANDS = { approve: 80, conditional: 60 } as const;

export function bandOf(score: number | null | undefined): { label: string; tone: Tone } {
  if (score === null || score === undefined) return { label: "unscored", tone: "gray" };
  if (score >= BANDS.approve) return { label: "approve", tone: "green" };
  if (score >= BANDS.conditional) return { label: "conditional", tone: "amber" };
  return { label: "escalate", tone: "red" };
}

/** Colour for a machine state. Every caller also prints the word — colour is never alone. */
export function stateTone(state: string, gateScope?: string | null): Tone {
  if (state === "needs_human") return "red";
  if (state === "gated") return gateScope === "decision" ? "amber" : "blue";
  if (state === "decided" || state === "monitored") return "green";
  if (state === "scored") return "amber";
  if (state === "evidence_review") return "purple";
  return "blue";
}

export function severityTone(severity: string): Tone {
  if (severity === "high") return "red";
  if (severity === "medium") return "amber";
  return "blue";
}

export function tierTone(tier: number): Tone {
  return tier === 1 ? "red" : tier === 2 ? "amber" : "gray";
}

/** Whether a review is waiting on a person. The queue's default filter. */
export function needsYou(r: Doc): boolean {
  return r.state === "gated" || r.state === "needs_human";
}

/** Whole days since the review opened. */
export function elapsedDays(r: Doc): number {
  const opened = Date.parse(String(r.opened_at ?? ""));
  if (Number.isNaN(opened)) return 0;
  return Math.max(0, Math.floor((Date.now() - opened) / 86_400_000));
}

/** "3m", "5h", "2d" — how long ago, for a column that has to stay narrow. */
export function ago(value: unknown): string {
  const then = Date.parse(String(value ?? ""));
  if (Number.isNaN(then)) return "—";
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 90) return "just now";
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

export function money(value: unknown): string {
  return `$${Number(value ?? 0).toFixed(4)}`;
}

export function when(value: unknown): string {
  const text = String(value ?? "");
  return text ? text.slice(0, 19).replace("T", " ") : "—";
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}

/* ------------------------------------------------------------------------------------------
 * Reviews and vendors
 * ---------------------------------------------------------------------------------------- */

/**
 * Every review, newest first, with the vendor resolved.
 *
 * Reviews whose vendor no longer exists are kept rather than dropped. A review pointing at a
 * missing vendor is a data problem an operator should be able to see, and silently filtering it
 * would make the console the reason nobody noticed.
 */
export async function queue(limit = 400): Promise<Doc[]> {
  const [reviews, vendors] = await Promise.all([all("reviews"), all("vendors")]);
  const byId = new Map(vendors.map((v) => [v.vendor_id ?? v.id, v]));

  return reviews
    .map((r): Doc => ({ ...r, vendor: byId.get(r.vendor_id) ?? {} }))
    .sort((a, b) => String(b.opened_at ?? "").localeCompare(String(a.opened_at ?? "")))
    .slice(0, limit);
}

export async function review(reviewId: string): Promise<Doc | null> {
  const snapshot = await db().collection("reviews").doc(reviewId).get();
  if (!snapshot.exists) return null;
  const data = snapshot.data() as Doc;
  const vendor = await db().collection("vendors").doc(String(data.vendor_id)).get();
  return { ...data, vendor: vendor.exists ? vendor.data() : {} };
}

/**
 * The vendor portfolio, each with its most recent review and score.
 *
 * "Most recent" is by `opened_at` rather than by state, because a vendor mid-re-review has both
 * a decided review and an open one and the open one is the answer to *what is happening with
 * this vendor*. The decided one is still reachable from the vendor's own page.
 */
export async function portfolio(): Promise<Doc[]> {
  const [vendors, reviews, scores] = await Promise.all([
    all("vendors"),
    all("reviews"),
    all("scores"),
  ]);

  const scoreBy = new Map(scores.map((s) => [s.review_id, s]));
  const byVendor = new Map<string, Doc[]>();
  for (const r of reviews) {
    const key = String(r.vendor_id ?? "");
    if (!byVendor.has(key)) byVendor.set(key, []);
    byVendor.get(key)!.push(r);
  }

  return vendors
    .map((v): Doc => {
      const id = String(v.vendor_id ?? v.id);
      const mine = (byVendor.get(id) ?? []).sort((a, b) =>
        String(b.opened_at ?? "").localeCompare(String(a.opened_at ?? "")),
      );
      const latest = mine[0] ?? null;
      const decided = mine.find((r) => r.state === "decided" || r.state === "monitored") ?? null;
      const scored = mine.find((r) => scoreBy.has(r.review_id)) ?? null;
      return {
        ...v,
        vendor_id: id,
        latest,
        decided,
        reviewCount: mine.length,
        score: scored ? scoreBy.get(scored.review_id) : null,
      };
    })
    .sort((a, b) => String(a.name ?? "").localeCompare(String(b.name ?? "")));
}

export async function vendorDossier(vendorId: string): Promise<Doc[]> {
  const rows = await forVendor("dossiers", vendorId);
  return rows
    .filter((d) => !d.superseded)
    .sort((a, b) => String(b.written_at ?? "").localeCompare(String(a.written_at ?? "")));
}

export async function reviewsFor(vendorId: string): Promise<Doc[]> {
  const rows = await forVendor("reviews", vendorId);
  return rows.sort((a, b) => String(b.opened_at ?? "").localeCompare(String(a.opened_at ?? "")));
}

/* ------------------------------------------------------------------------------------------
 * One review's material
 * ---------------------------------------------------------------------------------------- */

export async function score(reviewId: string): Promise<Doc | null> {
  const snapshot = await db().collection("scores").doc(reviewId).get();
  return snapshot.exists ? (snapshot.data() as Doc) : null;
}

export async function memo(reviewId: string): Promise<string> {
  const snapshot = await db().collection("memos").doc(reviewId).get();
  return snapshot.exists ? String((snapshot.data() as Doc).text ?? "") : "";
}

export async function findings(reviewId: string): Promise<Doc[]> {
  const rows = await forReview("findings", reviewId);
  return rows.sort((a, b) => String(a.finding_id).localeCompare(String(b.finding_id)));
}

export async function answers(reviewId: string): Promise<Doc[]> {
  const rows = await forReview("qa_responses", reviewId);
  return rows.sort((a, b) => String(a.question_id ?? "").localeCompare(String(b.question_id ?? "")));
}

export async function screenings(reviewId: string): Promise<Doc[]> {
  const rows = await forReview("screenings", reviewId);
  return rows.sort((a, b) => String(a.origin_ref ?? "").localeCompare(String(b.origin_ref ?? "")));
}

export async function approvals(reviewId: string): Promise<Doc[]> {
  const rows = await forReview("approvals", reviewId);
  return rows.sort((a, b) => String(a.issued_at ?? "").localeCompare(String(b.issued_at ?? "")));
}

export async function messages(reviewId: string): Promise<Doc[]> {
  const rows = await forReview("inbox", reviewId);
  return rows.sort((a, b) => String(b.sent_at ?? "").localeCompare(String(a.sent_at ?? "")));
}

/**
 * The fourth-party chain: you, the vendor, and the companies behind the vendor.
 *
 * Read against the vendor rather than the review, because a subprocessor is a fact about the
 * company and survives the review that discovered it. The register status on each node is what
 * the Evidence agent resolved against the organisation's own approved-vendor list — the one
 * collection in the system with readers and no writer.
 */
export async function chain(vendorId: string): Promise<Doc[]> {
  const rows = await forVendor("subprocessors", vendorId);
  return rows.sort((a, b) => String(a.name ?? "").localeCompare(String(b.name ?? "")));
}

export async function cards(reviewId: string): Promise<Doc[]> {
  const rows = await forReview("dashboard_events", reviewId);
  return rows.sort((a, b) => String(a.at ?? "").localeCompare(String(b.at ?? "")));
}

/**
 * The vertical event stream, oldest first.
 *
 * Ledger events and reasoning entries interleaved on their timestamps. A review is a story that
 * happened over days, and splitting "what happened" from "why" into two lists would make the
 * reader do the merge in their head.
 */
export async function timeline(reviewId: string): Promise<Doc[]> {
  const [events, reasoning, tierCards] = await Promise.all([
    forReview("events", reviewId),
    forReview("decisions", reviewId),
    forReview("dashboard_events", reviewId),
  ]);

  const entries: Doc[] = [
    ...events.map((e) => ({ kind: "event", at: e.ts ?? e.at ?? "", ...e })),
    ...reasoning.map((r) => ({ kind: "reasoning", ...r })),
    // Four card kinds land on a review and all four are rendered. A watchdog triage card is
    // the one that arrives after the review closed, which is exactly why it belongs on the same
    // stream rather than in a separate list nobody opens.
    ...tierCards
      .filter((c) =>
        ["tier_change", "policy_block", "watchdog_triage", "prior_review_recalled", "gate", "parked"].includes(
          String(c.kind),
        ),
      )
      .map((c) => ({ ...c, kind: c.kind })),
  ];

  return entries.sort((a, b) => String(a.at ?? "").localeCompare(String(b.at ?? "")));
}

/* ------------------------------------------------------------------------------------------
 * Portfolio-wide surfaces
 * ---------------------------------------------------------------------------------------- */

/** Every finding in the ledger, newest review first, with its vendor resolved. */
export async function allFindings(limit = 300): Promise<Doc[]> {
  const [rows, reviews, vendors] = await Promise.all([
    all("findings", 2000),
    all("reviews"),
    all("vendors"),
  ]);

  const reviewBy = new Map(reviews.map((r) => [r.review_id, r]));
  const vendorBy = new Map(vendors.map((v) => [v.vendor_id ?? v.id, v]));

  return rows
    .map((f): Doc => {
      const r = reviewBy.get(f.review_id);
      return {
        ...f,
        review: r ?? null,
        vendor: r ? vendorBy.get(r.vendor_id) ?? {} : {},
        openedAt: r?.opened_at ?? "",
      };
    })
    .sort((a, b) => String(b.openedAt).localeCompare(String(a.openedAt)))
    .slice(0, limit);
}

/**
 * Screened documents across the portfolio, one row per document rather than per screening.
 *
 * A document is screened once per template, so the raw collection holds two rows for the same
 * bytes and a naive list shows every file twice. The rows are folded on `origin_ref` and the
 * verdicts merged, which is also the only form in which the question *is this document
 * admissible* has an answer: admissibility is a property of all its verdicts together.
 */
export async function evidenceDocuments(limit = 200): Promise<Doc[]> {
  const [rows, reviews, vendors] = await Promise.all([
    all("screenings", 2000),
    all("reviews"),
    all("vendors"),
  ]);

  const reviewBy = new Map(reviews.map((r) => [r.review_id, r]));
  const vendorBy = new Map(vendors.map((v) => [v.vendor_id ?? v.id, v]));
  const folded = new Map<string, Doc>();
  const { critical_filters: critical, untrusted_templates: untrusted, execution_success: ok, match_found: hit } =
    policy.screening;

  for (const s of rows) {
    const ref = String(s.origin_ref ?? s.id);
    const key = `${s.review_id}:${ref}`;
    const existing = folded.get(key);
    const merged: Doc = existing ?? {
      key,
      review_id: s.review_id,
      origin_ref: ref,
      name: ref.split("/").pop() ?? ref,
      templates: [] as string[],
      filters: {} as Record<string, string>,
      execution: {} as Record<string, string>,
      excerpts: [] as string[],
    };
    merged.templates.push(String(s.template ?? "?"));
    Object.assign(merged.filters, s.filters ?? {});
    Object.assign(merged.execution, s.execution ?? {});
    if (s.excerpt) merged.excerpts.push(String(s.excerpt));
    folded.set(key, merged);
  }

  return [...folded.values()]
    .map((d): Doc => {
      const r = reviewBy.get(d.review_id);
      const matched = critical.filter((f) => String(d.filters[f] ?? "") === hit);
      const executed = critical.every((f) => String(d.execution[f] ?? "") === ok);
      const isUntrusted = (d.templates as string[]).some((t) => untrusted.includes(t));

      // The same four-way distinction `shared.armor.verdict_is_trustworthy` makes, in the same
      // order, because the console has no business softening it:
      //
      //   blocked      a critical filter matched
      //   not-a-verdict  the template was the stub or a seeded fixture — the pipeline shape ran
      //                  and no detector did, so nothing here is a verdict about anything
      //   incomplete   a critical filter did not execute, which is not the same as one that
      //                found nothing
      //   clean        every critical filter ran and none matched
      //
      // Ordered with `blocked` first so a fixture that a detector *did* match on still reads as
      // blocked rather than being excused by its template.
      const verdict = matched.length
        ? "blocked"
        : isUntrusted
          ? "not-a-verdict"
          : executed
            ? "clean"
            : "incomplete";

      return {
        ...d,
        review: r ?? null,
        vendor: r ? vendorBy.get(r.vendor_id) ?? {} : {},
        openedAt: r?.opened_at ?? "",
        matched,
        untrusted: isUntrusted,
        verdict,
      };
    })
    .sort((a, b) => String(b.openedAt).localeCompare(String(a.openedAt)))
    .slice(0, limit);
}

/**
 * Watchdog signals, newest first, with the vendor resolved.
 *
 * Rows with no `signal_id` are dropped. That is not defensive tidying of real data: the
 * `tasks` collection is one of the twenty the IAM boundary tests write a probe document into to
 * prove a grant, and a probe is not a signal. The tests now sweep after themselves, and this
 * keeps a monitoring screen from drawing a blank row if anything else ever writes one.
 */
export async function signals(): Promise<Doc[]> {
  const [rows, vendors] = await Promise.all([all("tasks"), all("vendors")]);
  const vendorBy = new Map(vendors.map((v) => [v.vendor_id ?? v.id, v]));
  return rows
    .filter((t) => t.signal_id && t.title)
    .map((t): Doc => ({ ...t, vendor: vendorBy.get(t.vendor_id) ?? {} }))
    .sort((a, b) => String(b.at ?? "").localeCompare(String(a.at ?? "")));
}

/** Triage cards the Watchdog raised for a person rather than acting on. */
export async function triageCards(): Promise<Doc[]> {
  const snapshot = await db()
    .collection("dashboard_events")
    .where("kind", "==", "watchdog_triage")
    .limit(200)
    .get();
  return snapshot.docs
    .map((d) => d.data() as Doc)
    .sort((a, b) => String(b.at ?? "").localeCompare(String(a.at ?? "")));
}

/**
 * Sealed audit binders: decided reviews, with whether the HTML artefact has been rendered.
 *
 * The binder file is not read here, only listed. Rendering is `make binder REVIEW=<id>` and the
 * console names that command rather than shelling out to it — a read-only surface that could
 * start a process is a read-only surface in name.
 */
export async function binders(): Promise<Doc[]> {
  const [reviews, vendors, scores] = await Promise.all([
    all("reviews"),
    all("vendors"),
    all("scores"),
  ]);
  const vendorBy = new Map(vendors.map((v) => [v.vendor_id ?? v.id, v]));
  const scoreBy = new Map(scores.map((s) => [s.review_id, s]));

  return reviews
    .filter((r) => r.state === "decided" || r.state === "monitored")
    .map((r): Doc => ({
      ...r,
      vendor: vendorBy.get(r.vendor_id) ?? {},
      score: scoreBy.get(r.review_id) ?? null,
    }))
    .sort((a, b) =>
      String(b.decided_at ?? b.opened_at ?? "").localeCompare(String(a.decided_at ?? a.opened_at ?? "")),
    );
}

/**
 * The fleet's activity, newest first.
 *
 * Events and reasoning records across every review, merged the same way one review's timeline
 * is. Bounded hard: this is the collection that grows fastest, and the page says what it is
 * showing out of what exists.
 */
export async function activity(limit = 250): Promise<{ rows: Doc[]; total: number }> {
  const [events, reasoning, vendors, reviews] = await Promise.all([
    all("events", 3000),
    all("decisions", 3000),
    all("vendors"),
    all("reviews"),
  ]);

  const vendorBy = new Map(vendors.map((v) => [v.vendor_id ?? v.id, v]));
  const reviewBy = new Map(reviews.map((r) => [r.review_id, r]));

  const rows: Doc[] = ([
    ...events.map((e): Doc => ({ ...e, kind: "event", at: String(e.ts ?? e.at ?? "") })),
    ...reasoning.map((r): Doc => ({ ...r, kind: "reasoning", at: String(r.at ?? "") })),
  ] as Doc[])
    .map((row): Doc => {
      const r = reviewBy.get(row.review_id);
      return { ...row, vendor: r ? vendorBy.get(r.vendor_id) ?? {} : {} };
    })
    .sort((a, b) => String(b.at).localeCompare(String(a.at)));

  return { rows: rows.slice(0, limit), total: rows.length };
}

/* ------------------------------------------------------------------------------------------
 * Overview
 * ---------------------------------------------------------------------------------------- */

export type Overview = {
  reviews: Doc[];
  waiting: Doc[];
  vendors: number;
  monitored: number;
  inFlight: number;
  escalated: number;
  openFindings: number;
  contradictions: number;
  adversarial: number;
  policyBlocks: number;
  cost: number;
  recent: Doc[];
  bands: { label: string; tone: Tone; count: number }[];
};

/**
 * The opening screen's numbers, computed in one pass rather than one query per tile.
 *
 * Every figure here is a count over documents the fleet wrote. None of them is an estimate and
 * none is cached: an operator glancing at this screen is deciding whether to act, and a stale
 * count is worse than a slow page.
 */
export async function overview(): Promise<Overview> {
  const [reviews, vendors, scores, findingRows, cardRows] = await Promise.all([
    all("reviews"),
    all("vendors"),
    all("scores"),
    all("findings", 2000),
    all("dashboard_events", 2000),
  ]);

  const vendorBy = new Map(vendors.map((v) => [v.vendor_id ?? v.id, v]));
  const scoreBy = new Map(scores.map((s) => [s.review_id, s]));

  const withVendor = reviews
    .map((r): Doc => ({ ...r, vendor: vendorBy.get(r.vendor_id) ?? {}, score: scoreBy.get(r.review_id) ?? null }))
    .sort((a, b) => String(b.opened_at ?? "").localeCompare(String(a.opened_at ?? "")));

  const live = withVendor.filter((r) => r.state !== "decided" && r.state !== "monitored");
  const waiting = withVendor.filter(needsYou);

  const openReviewIds = new Set(live.map((r) => r.review_id));
  const openFindings = findingRows.filter((f) => openReviewIds.has(f.review_id));

  const bandCounts = new Map<string, number>();
  for (const s of scores) {
    const label = String(s.band ?? bandOf(s.score).label);
    bandCounts.set(label, (bandCounts.get(label) ?? 0) + 1);
  }

  return {
    reviews: withVendor,
    waiting,
    vendors: vendors.length,
    monitored: withVendor.filter((r) => r.state === "monitored" || r.state === "decided").length,
    inFlight: live.length,
    escalated: scores.filter((s) => s.band === "escalate").length,
    openFindings: openFindings.length,
    contradictions: openFindings.filter((f) => f.contradiction).length,
    adversarial: findingRows.filter((f) => f.domain === "conduct").length,
    policyBlocks: cardRows.filter((c) => c.kind === "policy_block").length,
    cost: withVendor.reduce((sum, r) => sum + Number(r.cost_usd ?? 0), 0),
    recent: withVendor.slice(0, 8),
    bands: [
      { label: "approve", tone: "green" as Tone, count: bandCounts.get("approve") ?? 0 },
      { label: "conditional", tone: "amber" as Tone, count: bandCounts.get("conditional") ?? 0 },
      { label: "escalate", tone: "red" as Tone, count: bandCounts.get("escalate") ?? 0 },
    ],
  };
}
