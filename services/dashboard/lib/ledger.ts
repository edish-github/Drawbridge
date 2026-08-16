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
 */

import { Firestore } from "@google-cloud/firestore";

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

async function all(collection: string): Promise<Doc[]> {
  const snapshot = await db().collection(collection).get();
  return snapshot.docs.map((d) => ({ id: d.id, ...d.data() }));
}

async function forReview(collection: string, reviewId: string): Promise<Doc[]> {
  const snapshot = await db()
    .collection(collection)
    .where("review_id", "==", reviewId)
    .get();
  return snapshot.docs.map((d) => ({ id: d.id, ...d.data() }));
}

/** Every review, newest first, with the vendor name resolved. */
export async function queue(): Promise<Doc[]> {
  const [reviews, vendors] = await Promise.all([all("reviews"), all("vendors")]);
  const byId = new Map(vendors.map((v) => [v.vendor_id ?? v.id, v]));

  const joined: Doc[] = reviews.map((r) => ({ ...r, vendor: byId.get(r.vendor_id) ?? {} }));
  return joined.sort((a, b) =>
    String(b.opened_at ?? "").localeCompare(String(a.opened_at ?? "")),
  );
}

export async function review(reviewId: string): Promise<Doc | null> {
  const snapshot = await db().collection("reviews").doc(reviewId).get();
  if (!snapshot.exists) return null;
  const data = snapshot.data() as Doc;
  const vendor = await db().collection("vendors").doc(String(data.vendor_id)).get();
  return { ...data, vendor: vendor.exists ? vendor.data() : {} };
}

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

/**
 * The fourth-party chain: you, the vendor, and the companies behind the vendor.
 *
 * Read against the vendor rather than the review, because a subprocessor is a fact about the
 * company and survives the review that discovered it. The register status on each node is what
 * the Evidence agent resolved against the organisation's own approved-vendor list — the one
 * collection in the system with readers and no writer.
 */
export async function chain(vendorId: string): Promise<Doc[]> {
  if (!vendorId) return [];
  const snapshot = await db()
    .collection("subprocessors")
    .where("vendor_id", "==", vendorId)
    .get();
  return snapshot.docs
    .map((d) => d.data() as Doc)
    .sort((a, b) => String(a.name ?? "").localeCompare(String(b.name ?? "")));
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
        ["tier_change", "policy_block", "watchdog_triage", "prior_review_recalled"].includes(
          String(c.kind),
        ),
      )
      .map((c) => ({ ...c, kind: c.kind })),
  ];

  return entries.sort((a, b) => String(a.at ?? "").localeCompare(String(b.at ?? "")));
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
