/**
 * The console's write surface, and the line drawn through it.
 *
 * The console may perform **workflow** writes: opening a review, marking a reply thread complete,
 * resolving a parked review. Those are things an analyst does, and a product where they require a
 * terminal is a product nobody can use.
 *
 * The console may not perform **approval** writes. It holds no signing key, never writes the
 * `approvals` collection, and releases a gate by asking the approval service — which re-verifies
 * the caller rather than believing this one. That distinction is the entire human-gate story: if
 * a surface every reviewer can reach could mint an approval, the gateway refusing to sign would
 * be decoration.
 *
 * `tests/test_console.py` asserts both halves against the source tree.
 */

import { randomUUID } from "node:crypto";
import { db } from "./ledger";
import type { Principal } from "./auth";

/** Tenant-scoped collection, mirroring `shared.tenancy.collection`. */
export function scoped(orgId: string, name: string) {
  return db().collection("orgs").doc(orgId).collection(name);
}

/**
 * Append to the immutable ledger.
 *
 * Every workflow write the console performs leaves a record naming the person who performed it.
 * A console action that did not is an action an auditor cannot attribute, and the whole product
 * is an argument about attribution.
 */
export async function recordAction(
  principal: Principal,
  reviewId: string,
  action: string,
  detail: string,
): Promise<void> {
  const id = randomUUID().replace(/-/g, "");
  const now = new Date().toISOString();

  await Promise.all([
    scoped(principal.orgId, "events").doc(id).set({
      event_id: id,
      type: `console.${action}`,
      org_id: principal.orgId,
      review_id: reviewId,
      idem_key: `${reviewId}:console:${action}:${id}`,
      trace_id: id,
      source: "console",
      ts: now,
      payload: { by: principal.email, uid: principal.uid, detail },
    }),
    scoped(principal.orgId, "decisions").add({
      review_id: reviewId,
      org_id: principal.orgId,
      agent: "console",
      node: "",
      goal: `${action.replace(/_/g, " ")}, requested by a person`,
      decision: `${detail} · ${principal.email}`,
      trace_id: id,
      idem_key: null,
      at: now,
    }),
  ]);
}

/** Publish onto the event backbone through the Pub/Sub REST API. */
export async function publishEvent(
  orgId: string,
  topic: string,
  reviewId: string,
  payload: Record<string, unknown>,
): Promise<void> {
  const host = process.env.PUBSUB_EMULATOR_HOST;
  const project =
    process.env.GOOGLE_CLOUD_PROJECT || process.env.PROJECT_ID || "drawbridge-local";

  const envelope = {
    event_id: randomUUID().replace(/-/g, ""),
    type: topic,
    org_id: orgId,
    review_id: reviewId,
    idem_key: `${reviewId}:plan_v1:${topic}`,
    trace_id: randomUUID().replace(/-/g, ""),
    source: "console",
    ts: new Date().toISOString(),
    payload,
  };

  const body = JSON.stringify({
    messages: [{ data: Buffer.from(JSON.stringify(envelope)).toString("base64") }],
  });

  // The emulator speaks the same REST API as the service, so this one path covers both. In
  // cloud the endpoint is pubsub.googleapis.com and the request carries the service account's
  // token, supplied by the metadata server.
  const base = host ? `http://${host}` : "https://pubsub.googleapis.com";
  const headers: Record<string, string> = { "content-type": "application/json" };

  if (!host) {
    const token = await metadataToken();
    headers.authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${base}/v1/projects/${project}/topics/${topic}:publish`, {
    method: "POST",
    headers,
    body,
  });

  if (!response.ok) {
    throw new Error(`could not publish ${topic}: ${response.status} ${await response.text()}`);
  }
}

async function metadataToken(): Promise<string> {
  const response = await fetch(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    { headers: { "Metadata-Flavor": "Google" } },
  );
  if (!response.ok) throw new Error("could not obtain a service account token");
  return (await response.json()).access_token as string;
}
