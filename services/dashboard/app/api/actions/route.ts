/**
 * The workflow actions an analyst performs on a review.
 *
 * Three of them, and none closes a review. Each corresponds to something the fleet is genuinely
 * waiting on a person for, and each writes a record naming who did it — a console action an
 * auditor cannot attribute is the one thing worse than no console action.
 *
 * `mark_replies_complete` is the coverage join's declared override. A vendor who answers most of
 * what was asked and stops is the ordinary case, and the review says afterwards that it
 * reconciled below the threshold rather than reporting a figure it never reached.
 *
 * `resolve_park` returns a stalled review to the fleet. It does not decide what stalled it — the
 * person did that — it records that they judged it resolved and lets the workflow resume.
 *
 * `mark_read` clears a dashboard card. The lightest possible write, and still attributed.
 */

import { NextResponse } from "next/server";
import { CAN_ACT, currentPrincipal, may } from "../../../lib/auth";
import { publishEvent, recordAction, scoped } from "../../../lib/write";

const ACTIONS = ["mark_replies_complete", "resolve_park", "mark_read"] as const;
type Action = (typeof ACTIONS)[number];

export async function POST(request: Request) {
  const principal = await currentPrincipal();
  if (!principal) return NextResponse.json({ message: "Sign in first." }, { status: 401 });
  if (!may(principal.role, CAN_ACT)) {
    return NextResponse.json(
      { message: "This requires the analyst role." },
      { status: 403 },
    );
  }

  const body = (await request.json().catch(() => null)) as {
    action?: Action;
    reviewId?: string;
    note?: string;
  } | null;

  if (!body?.action || !ACTIONS.includes(body.action) || !body.reviewId) {
    return NextResponse.json({ message: "Unknown action." }, { status: 400 });
  }

  const orgId = principal.orgId;
  const reviews = scoped(orgId, "reviews");
  const snapshot = await reviews.doc(body.reviewId).get();
  if (!snapshot.exists) {
    return NextResponse.json({ message: "No such review in this workspace." }, { status: 404 });
  }
  const review = snapshot.data() as Record<string, unknown>;

  if (body.action === "mark_replies_complete") {
    await reviews.doc(body.reviewId).set(
      { replies_complete: true, replies_complete_by: principal.email },
      { merge: true },
    );
    await recordAction(
      principal,
      body.reviewId,
      "replies_marked_complete",
      "declared the reply thread finished; the coverage join may open evidence review below its threshold",
    );
    // Nudge the fleet: the join is evaluated when a reply arrives, so a review that has stopped
    // receiving them needs one more event to re-evaluate.
    await publishEvent(orgId, "vendor.reply_received", body.reviewId, {
      body: "",
      message_id: `analyst-closed-${Date.now()}`,
      analyst_closed: true,
    });
    return NextResponse.json({ ok: true });
  }

  if (body.action === "resolve_park") {
    if (review.state !== "needs_human") {
      return NextResponse.json(
        { message: "That review is not parked." },
        { status: 409 },
      );
    }
    // The park reason is kept, not cleared. What stalled a review is part of its history, and a
    // resolved park that erased its own cause would make the timeline a story with a gap in it.
    await reviews.doc(body.reviewId).set(
      {
        park_resolved_by: principal.email,
        park_resolved_at: new Date().toISOString(),
      },
      { merge: true },
    );
    await recordAction(
      principal,
      body.reviewId,
      "park_resolved",
      `judged '${String(review.park_reason ?? "the stall")}' resolved${body.note ? `: ${body.note}` : ""}`,
    );
    return NextResponse.json({ ok: true });
  }

  await scoped(orgId, "dashboard_events")
    .doc(String(body.note ?? ""))
    .set({ read_by: principal.email, read_at: new Date().toISOString() }, { merge: true });
  return NextResponse.json({ ok: true });
}
