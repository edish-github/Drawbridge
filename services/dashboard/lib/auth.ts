/**
 * Sessions, and what the signed-in person may do.
 *
 * Authentication is Google Identity Platform's job — a security product that rolls its own
 * password hashing has already lost the argument it exists to make. This file does the two
 * things that are ours: turn a verified identity into a **session cookie**, and turn that cookie
 * into an **organisation and a role**.
 *
 * The cookie is `httpOnly`, `sameSite=lax` and `secure` outside local mode. It holds a signed
 * payload rather than a bearer token: the identity provider's token is exchanged once, at sign
 * in, and never stored anywhere the browser's JavaScript can read it.
 *
 * **The role is resolved per request, from the membership document, and never from the cookie.**
 * That is the difference between a session and a capability. If an administrator removes
 * somebody's approver role, the next page load reflects it — a role baked into a cookie at sign
 * in would keep working for twelve hours, which is exactly the window in which somebody is
 * removed for a reason.
 */

import { cookies } from "next/headers";
import { createHmac, timingSafeEqual } from "node:crypto";
import { db } from "./ledger";

export const SESSION_COOKIE = "drawbridge_session";
export const SESSION_TTL_SECONDS = 60 * 60 * 12;

export type Role = "viewer" | "analyst" | "approver" | "admin";

export const CAN_APPROVE: Role[] = ["approver", "admin"];
export const CAN_ACT: Role[] = ["analyst", "approver", "admin"];
export const CAN_ADMINISTER: Role[] = ["admin"];

export type Session = {
  uid: string;
  email: string;
  name: string;
  /** The organisation this browser tab is working in. A person may belong to several. */
  orgId: string;
  issuedAt: number;
};

export type Principal = Session & {
  /** `null` when the person is signed in but not a member of the org they asked for. */
  role: Role | null;
  orgName: string;
};

function secret(): string {
  const configured = process.env.DRAWBRIDGE_SESSION_SECRET;
  if (configured) return configured;
  if (process.env.RUNTIME_MODE === "cloud") {
    // Not a warning. A deployment with no session secret would sign every cookie with a value
    // an attacker can read out of this file, which is indistinguishable from no signing at all.
    throw new Error(
      "DRAWBRIDGE_SESSION_SECRET is required in cloud mode. Set it from Secret Manager.",
    );
  }
  return `drawbridge-dev:${process.env.GOOGLE_CLOUD_PROJECT ?? "local"}`;
}

function sign(payload: string): string {
  return createHmac("sha256", secret()).update(payload).digest("hex");
}

/** Encode a session into the cookie value. Signed, not encrypted — it holds no secret. */
export function seal(session: Session): string {
  const body = Buffer.from(JSON.stringify(session)).toString("base64url");
  return `${body}.${sign(body)}`;
}

/** Decode a cookie value, or `null` for anything that does not verify. */
export function unseal(value: string | undefined): Session | null {
  if (!value) return null;
  const index = value.lastIndexOf(".");
  if (index < 1) return null;

  const body = value.slice(0, index);
  const given = value.slice(index + 1);
  const expected = sign(body);

  // Constant-time, because a comparison that returns early leaks the signature one byte at a
  // time to anyone willing to make enough requests.
  if (given.length !== expected.length) return null;
  if (!timingSafeEqual(Buffer.from(given), Buffer.from(expected))) return null;

  try {
    const session = JSON.parse(Buffer.from(body, "base64url").toString()) as Session;
    if (!session.uid || !session.orgId) return null;
    if (Date.now() / 1000 - session.issuedAt > SESSION_TTL_SECONDS) return null;
    return session;
  } catch {
    return null;
  }
}

/**
 * The signed-in person and their role in the current organisation, or `null`.
 *
 * The membership read is the authorisation. A session says who you are; the membership says what
 * you may do, and it is read fresh every request so a revoked role takes effect on the next page
 * rather than in twelve hours.
 */
export async function currentPrincipal(): Promise<Principal | null> {
  const jar = await cookies();
  const session = unseal(jar.get(SESSION_COOKIE)?.value);
  if (!session) return null;

  const [membership, org] = await Promise.all([
    db().collection("memberships").doc(`${session.orgId}:${session.uid}`).get(),
    db().collection("orgs").doc(session.orgId).get(),
  ]);

  const orgData = org.exists ? (org.data() as Record<string, unknown>) : null;
  const memberData = membership.exists
    ? (membership.data() as Record<string, unknown>)
    : null;

  const active =
    memberData?.status === "active" && (orgData?.status ?? "active") === "active";

  return {
    ...session,
    role: active ? ((memberData?.role as Role) ?? null) : null,
    orgName: (orgData?.name as string) ?? session.orgId,
  };
}

export function may(role: Role | null, capability: Role[]): boolean {
  return role !== null && capability.includes(role);
}

/** Every organisation this person belongs to, for the workspace switcher. */
export async function membershipsOf(uid: string): Promise<{ orgId: string; role: Role; name: string }[]> {
  const rows = await db()
    .collection("memberships")
    .where("uid", "==", uid)
    .where("status", "==", "active")
    .get();

  const orgIds = rows.docs.map((d) => String((d.data() as Record<string, unknown>).org_id));
  if (orgIds.length === 0) return [];

  const orgs = await Promise.all(orgIds.map((id) => db().collection("orgs").doc(id).get()));
  const names = new Map(
    orgs.filter((o) => o.exists).map((o) => [o.id, String((o.data() as Record<string, unknown>).name ?? o.id)]),
  );

  return rows.docs.map((d) => {
    const data = d.data() as Record<string, unknown>;
    const orgId = String(data.org_id);
    return { orgId, role: data.role as Role, name: names.get(orgId) ?? orgId };
  });
}

export const cookieOptions = {
  httpOnly: true,
  sameSite: "lax" as const,
  secure: process.env.RUNTIME_MODE === "cloud",
  path: "/",
  maxAge: SESSION_TTL_SECONDS,
};
