/**
 * Release a human gate — by asking the service that holds the key, never by signing here.
 *
 * This route is a proxy with an identity attached. It does not decide whether the caller may
 * approve; the approval service re-verifies the token and re-reads the membership, because a
 * service that trusted "the console says so" would have its security bounded by the console
 * being correct, and the console is the surface every reviewer can reach.
 *
 * The role check below is therefore **not** the control. It is a courtesy: it turns a request
 * that would be refused anyway into a clear message before a network hop.
 */

import { NextResponse } from "next/server";
import { CAN_APPROVE, currentPrincipal, may } from "../../../lib/auth";
import { recordAction } from "../../../lib/write";

function approvalsUrl(): string {
  return process.env.APPROVALS_URL ?? "http://localhost:8081";
}

export async function POST(request: Request) {
  const principal = await currentPrincipal();
  if (!principal) return NextResponse.json({ message: "Sign in first." }, { status: 401 });

  if (!may(principal.role, CAN_APPROVE)) {
    return NextResponse.json(
      {
        message:
          "Releasing a gate requires the approver role. An approval is a named person accepting risk, so it cannot be delegated to a role that does not carry it.",
      },
      { status: 403 },
    );
  }

  const body = (await request.json().catch(() => null)) as {
    reviewId?: string;
    scope?: "contact" | "decision";
    conditions?: string[];
    note?: string;
    identityToken?: string;
  } | null;

  if (!body?.reviewId || !body.scope) {
    return NextResponse.json({ message: "A review and a gate are required." }, { status: 400 });
  }
  if (!body.identityToken) {
    // The approval service verifies the person, not the session. The browser re-presents its
    // identity token for exactly this call, so the signature is bound to a fresh proof of who is
    // sitting there rather than to a cookie issued twelve hours ago.
    return NextResponse.json(
      { message: "Confirm your identity to approve.", need: "identity" },
      { status: 428 },
    );
  }

  let response: Response;
  try {
    response = await fetch(`${approvalsUrl()}/approvals`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${body.identityToken}`,
      },
      body: JSON.stringify({
        org_id: principal.orgId,
        review_id: body.reviewId,
        scope: body.scope,
        conditions: body.conditions ?? [],
        note: body.note ?? "",
      }),
    });
  } catch {
    return NextResponse.json(
      { message: "The approval service is unreachable. Nothing was approved." },
      { status: 503 },
    );
  }

  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    return NextResponse.json(
      { message: result.detail ?? "That approval was refused." },
      { status: response.status },
    );
  }

  await recordAction(
    principal,
    body.reviewId,
    "gate_released",
    `released the ${body.scope} gate`,
  );

  return NextResponse.json({ ok: true, jti: result.jti });
}
