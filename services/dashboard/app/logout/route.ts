/**
 * Sign out.
 *
 * A route handler rather than a page, because Next.js only permits a cookie to be modified in a
 * route handler or a server action — a page that tried would render a runtime error instead of
 * signing anybody out, which is the worst possible failure mode for this particular button.
 *
 * `GET` as well as `POST`, so the sidebar's link works and so does a pasted URL. Signing out is
 * the one destructive-looking action where being reachable by a link is right: the cost of an
 * accidental sign-out is signing back in, and the cost of a sign-out that did not work is
 * somebody walking away from an authenticated session.
 */

import { NextResponse } from "next/server";
import { SESSION_COOKIE, cookieOptions } from "../../lib/auth";

function signOut(request: Request) {
  const response = NextResponse.redirect(new URL("/login", request.url));
  response.cookies.set(SESSION_COOKIE, "", { ...cookieOptions, maxAge: 0 });
  return response;
}

export async function GET(request: Request) {
  return signOut(request);
}

export async function POST(request: Request) {
  return signOut(request);
}
