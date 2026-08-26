/**
 * Switch the session to another workspace.
 *
 * Re-issues the cookie rather than storing a preference, and **re-checks membership** before it
 * does. A person who was removed from an organisation between sign-in and switching must not be
 * able to switch into it, and the membership read here is the only thing that stops them.
 */

import { NextResponse } from "next/server";
import { SESSION_COOKIE, cookieOptions, seal, unseal } from "../../../lib/auth";
import { db } from "../../../lib/ledger";

export async function POST(request: Request) {
  const jar = request.headers.get("cookie") ?? "";
  const raw = jar
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${SESSION_COOKIE}=`))
    ?.slice(SESSION_COOKIE.length + 1);

  const session = unseal(raw ? decodeURIComponent(raw) : undefined);
  if (!session) return NextResponse.json({ error: "not signed in" }, { status: 401 });

  const body = (await request.json().catch(() => null)) as { orgId?: string } | null;
  if (!body?.orgId) return NextResponse.json({ error: "no workspace named" }, { status: 400 });

  const membership = await db()
    .collection("memberships")
    .doc(`${body.orgId}:${session.uid}`)
    .get();

  if (!membership.exists || membership.data()?.status !== "active") {
    return NextResponse.json({ error: "not a member of that workspace" }, { status: 403 });
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set(
    SESSION_COOKIE,
    seal({ ...session, orgId: body.orgId, issuedAt: Math.floor(Date.now() / 1000) }),
    cookieOptions,
  );
  return response;
}
