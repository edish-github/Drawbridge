/**
 * Verifying an identity token, on whichever side of the deployment we are.
 *
 * In cloud, Google Identity Platform issued the token and the Admin SDK checks its signature
 * against Google's rotating public keys. Locally there is no Identity Platform, so a development
 * token signed with a project-local secret carries the same claims — the same scheme
 * `shared/identity.py` implements, so a token minted by the Python side is accepted here and the
 * two halves of the product agree about who you are.
 *
 * **The development path is refused in cloud mode, in this function.** Not discouraged, not off
 * by default: an auth bypass that is merely disabled is an auth bypass. `tests/test_identity.py`
 * asserts the guard from the source on both sides.
 */

import { createHmac, timingSafeEqual } from "node:crypto";

export type VerifiedIdentity = { uid: string; email: string; name: string };

const DEV_ISSUER = "drawbridge-local";

function isCloud(): boolean {
  return process.env.RUNTIME_MODE === "cloud";
}

export async function verifyIdentityToken(token: string | undefined): Promise<VerifiedIdentity | null> {
  if (!token) return null;
  return isCloud() ? verifyPlatform(token) : verifyDev(token);
}

async function verifyPlatform(token: string): Promise<VerifiedIdentity | null> {
  // Imported lazily and by a computed specifier, so a local checkout needs neither the package
  // nor its types. The deployed image installs it; `npm run build` here does not require it.
  const specifier = "firebase-admin/auth";
  const admin = await import(/* webpackIgnore: true */ specifier).catch(() => null);
  if (!admin) {
    throw new Error(
      "firebase-admin is not installed, so identity tokens cannot be verified in cloud mode.",
    );
  }
  try {
    const claims = await admin.getAuth().verifyIdToken(token, true);
    return {
      uid: claims.sub,
      email: String(claims.email ?? ""),
      name: String(claims.name ?? ""),
    };
  } catch {
    return null;
  }
}

function devSecret(): Buffer {
  const configured = process.env.DRAWBRIDGE_DEV_AUTH_SECRET;
  if (configured) return Buffer.from(configured);
  // Matches `shared.identity._dev_secret`: sha256 of the same string, so a token minted by
  // either half of the product verifies in the other.
  const { createHash } = require("node:crypto") as typeof import("node:crypto");
  return createHash("sha256")
    .update(`drawbridge-dev:${process.env.GOOGLE_CLOUD_PROJECT ?? process.env.PROJECT_ID ?? "drawbridge-local"}`)
    .digest();
}

function verifyDev(token: string): VerifiedIdentity | null {
  if (isCloud()) throw new Error("development tokens are refused in cloud mode");

  const index = token.lastIndexOf(".");
  if (index < 1) return null;

  const body = token.slice(0, index);
  const given = token.slice(index + 1);
  const expected = createHmac("sha256", devSecret()).update(body).digest("hex");
  if (given.length !== expected.length) return null;
  if (!timingSafeEqual(Buffer.from(given), Buffer.from(expected))) return null;

  try {
    const claims = JSON.parse(Buffer.from(body, "base64url").toString());
    if (claims.iss !== DEV_ISSUER) return null;
    if (Number(claims.exp ?? 0) < Date.now() / 1000) return null;
    return { uid: String(claims.sub), email: String(claims.email ?? ""), name: String(claims.name ?? "") };
  } catch {
    return null;
  }
}


/**
 * Mint a short-lived token for a hop to another Drawbridge service.
 *
 * Ten minutes, because it is used within one request and a longer life buys nothing but risk. In
 * cloud this is where a signed service-to-service assertion goes; locally it is the same
 * development scheme both halves of the product already agree on.
 */
export function mintServiceToken(uid: string, email: string, name: string): string {
  const { createHmac } = require("node:crypto") as typeof import("node:crypto");
  const claims = {
    iss: DEV_ISSUER,
    sub: uid,
    email,
    name,
    exp: Date.now() / 1000 + 600,
  };
  const body = Buffer.from(JSON.stringify(claims)).toString("base64url");
  const signature = createHmac("sha256", devSecret()).update(body).digest("hex");
  return `${body}.${signature}`;
}
