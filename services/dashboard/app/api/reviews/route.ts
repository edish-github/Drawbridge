/**
 * Open a review.
 *
 * The gap that made the product unusable without a terminal: no service identity could write the
 * `reviews` collection, so a review could only be opened by running a script. This is that path,
 * with a role check in front of it and an attribution record behind it.
 *
 * It writes the vendor and the review, then publishes `review.intake`. Publishing last is
 * deliberate — a worker that consumed the event before the records existed would park the message
 * for redelivery, which is correct but noisy, and there is no reason to make it happen.
 */

import { randomUUID } from "node:crypto";
import { NextResponse } from "next/server";
import { CAN_ACT, may } from "../../../lib/auth";
import { currentPrincipal } from "../../../lib/auth";
import { publishEvent, recordAction, scoped } from "../../../lib/write";

const SLUG = /[^a-z0-9]+/g;

export async function POST(request: Request) {
  const principal = await currentPrincipal();
  if (!principal) return NextResponse.json({ message: "Sign in first." }, { status: 401 });
  if (!may(principal.role, CAN_ACT)) {
    return NextResponse.json(
      { message: "Opening a review requires the analyst role." },
      { status: 403 },
    );
  }

  const body = (await request.json().catch(() => null)) as {
    name?: string;
    category?: string;
    contactEmail?: string;
    legalEntity?: string;
    primaryDomain?: string;
    isAiVendor?: boolean;
    dataCategories?: string[];
    systemAccess?: string;
    description?: string;
  } | null;

  if (!body?.name || !body.contactEmail) {
    return NextResponse.json(
      { message: "A vendor name and a contact address are required." },
      { status: 400 },
    );
  }

  const orgId = principal.orgId;
  const vendorId = body.name.toLowerCase().replace(SLUG, "-").replace(/^-|-$/g, "").slice(0, 48);
  const reviewId = `rev-${new Date().getFullYear()}-${randomUUID().slice(0, 6)}`;
  const now = new Date().toISOString();

  // Merged rather than overwritten: a second review of a vendor already on file must not discard
  // what previous reviews recorded about them, including the adversarial conduct flag.
  await scoped(orgId, "vendors").doc(vendorId).set(
    {
      vendor_id: vendorId,
      org_id: orgId,
      name: body.name,
      category: body.category ?? "",
      legal_entity_name: body.legalEntity ?? null,
      primary_domain: body.primaryDomain ?? null,
      is_ai_vendor: Boolean(body.isAiVendor),
      status: "active",
      contact: { email: body.contactEmail },
      intake: {
        declared_data_categories: body.dataCategories ?? [],
        declared_system_access: body.systemAccess ?? "none",
        description: body.description ?? "",
        submitted_by: principal.email,
        submitted_at: now,
      },
    },
    { merge: true },
  );

  await scoped(orgId, "reviews").doc(reviewId).set({
    review_id: reviewId,
    org_id: orgId,
    vendor_id: vendorId,
    state: "intake",
    tier: 2,
    plan_version: 1,
    tier_history: [],
    score: null,
    band: null,
    opened_at: now,
    decided_at: null,
    cost_usd: 0,
    reopened_from: null,
    opened_by: principal.email,
  });

  await recordAction(principal, reviewId, "review_opened", `opened a review of ${body.name}`);
  await publishEvent(orgId, "review.intake", reviewId, {
    vendor_id: vendorId,
    opened_by: principal.email,
  });

  return NextResponse.json({ ok: true, reviewId, vendorId });
}
