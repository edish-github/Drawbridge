/**
 * Mint a short-lived identity token for a service-to-service hop.
 *
 * The session cookie proves who is at the browser; it is not presentable to the approval or
 * binder services, which authenticate people rather than browsers. This exchanges one for the
 * other, server-side, for the current request only.
 *
 * The token is scoped to the same person the cookie names and expires in minutes. It is never
 * returned to a page — only to another route on this server, which is why the handler requires
 * the session cookie and returns nothing useful without it.
 */

import { NextResponse } from "next/server";
import { currentPrincipal } from "../../../../lib/auth";
import { mintServiceToken } from "../../../../lib/verify";

export async function GET() {
  const principal = await currentPrincipal();
  if (!principal?.role) {
    return NextResponse.json({ message: "not signed in" }, { status: 401 });
  }
  return NextResponse.json({
    token: mintServiceToken(principal.uid, principal.email, principal.name),
  });
}
