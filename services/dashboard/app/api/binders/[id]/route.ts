/**
 * Download a review's audit binder.
 *
 * A proxy to the binder service, with the caller's identity attached. The binder service verifies
 * that identity itself rather than trusting this route — a binder holds the vendor's evidence, the
 * fleet's reasoning and the name of whoever accepted the risk, and the service that assembles one
 * should not depend on somebody else having checked.
 */

import { NextResponse } from "next/server";
import { currentPrincipal } from "../../../../lib/auth";

function binderUrl(): string {
  return process.env.BINDER_URL ?? "http://localhost:8082";
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const principal = await currentPrincipal();
  if (!principal?.role) {
    return NextResponse.json({ message: "Sign in first." }, { status: 401 });
  }

  // The identity token is re-minted for this hop rather than reusing the session cookie: the
  // binder service authenticates people, not browsers, and a cookie is not presentable to it.
  const identity = await fetch(new URL("/api/session/token", request.url), {
    headers: { cookie: request.headers.get("cookie") ?? "" },
  });
  if (!identity.ok) {
    return NextResponse.json({ message: "Sign in again." }, { status: 401 });
  }
  const { token } = await identity.json();

  let response: Response;
  try {
    response = await fetch(`${binderUrl()}/binders/${principal.orgId}/${id}`, {
      headers: { authorization: `Bearer ${token}` },
    });
  } catch {
    return NextResponse.json(
      { message: "The binder service is unreachable. Try again shortly." },
      { status: 503 },
    );
  }

  if (!response.ok) {
    return NextResponse.json(
      { message: response.status === 404 ? "No such review." : "The binder could not be built." },
      { status: response.status },
    );
  }

  return new NextResponse(await response.text(), {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "content-disposition": `attachment; filename="${id}-audit-binder.html"`,
      "cache-control": "no-store",
    },
  });
}
