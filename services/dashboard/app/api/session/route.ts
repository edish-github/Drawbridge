/**
 * Exchange a verified identity for a session cookie, and destroy one on sign out.
 *
 * This is the only route in the console that writes anything, and what it writes is a cookie —
 * never a tenant document. The distinction is the whole security posture: a surface every
 * reviewer can reach may establish who you are, and may not change what a review concluded.
 *
 * The identity token is verified server-side and then discarded. It is never persisted and never
 * returned to the browser, so the credential with the longest life in this system exists for the
 * duration of one request.
 */

import { NextResponse } from "next/server";
import { SESSION_COOKIE, cookieOptions, seal } from "../../../lib/auth";
import { db } from "../../../lib/ledger";
import { verifyIdentityToken } from "../../../lib/verify";

export async function POST(request: Request) {
  let body: { token?: string; orgId?: string };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "malformed request" }, { status: 400 });
  }

  const principal = await verifyIdentityToken(body.token);
  if (!principal) {
    // One message for every failure — expired, forged, unknown issuer. A response that
    // distinguished them would be an oracle, and nothing the caller does differs.
    return NextResponse.json({ error: "could not verify that sign-in" }, { status: 401 });
  }

  const memberships = await db()
    .collection("memberships")
    .where("uid", "==", principal.uid)
    .where("status", "==", "active")
    .get();

  if (memberships.empty) {
    return NextResponse.json(
      { error: "no_workspace", message: "This account does not belong to a workspace yet." },
      { status: 403 },
    );
  }

  const available = memberships.docs.map((d) => String((d.data() as Record<string, unknown>).org_id));
  const orgId = body.orgId && available.includes(body.orgId) ? body.orgId : available[0];

  // The product's own copy of the person. The identity provider owns the account; this is what
  // a binder prints beside an approval six months later.
  await db()
    .collection("users")
    .doc(principal.uid)
    .set(
      {
        uid: principal.uid,
        email: principal.email,
        name: principal.name,
        last_seen_at: new Date().toISOString(),
      },
      { merge: true },
    );

  const response = NextResponse.json({ ok: true, orgId });
  response.cookies.set(
    SESSION_COOKIE,
    seal({
      uid: principal.uid,
      email: principal.email,
      name: principal.name,
      orgId,
      issuedAt: Math.floor(Date.now() / 1000),
    }),
    cookieOptions,
  );
  return response;
}

/** Sign out. Clears the cookie and nothing else — sessions hold no server-side state. */
export async function DELETE() {
  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, "", { ...cookieOptions, maxAge: 0 });
  return response;
}
