/**
 * Account creation and password sign-in.
 *
 * **In cloud, this route does not handle passwords at all.** Identity Platform owns credentials;
 * the browser authenticates against it directly and this route only ever sees an already-verified
 * token. That is the arrangement a security product has to be able to describe: we do not store
 * your password because we never receive it.
 *
 * Locally there is no Identity Platform, so a development credential store stands in — scrypt
 * hashes in the `users` collection, and a development token in place of an ID token. It is
 * refused in cloud mode by `verifyIdentityToken`, and by an explicit guard here, because the
 * dangerous version of this file is the one where the local path survives a deploy.
 *
 * `POST` creates an organisation and its first administrator, atomically. `PUT` signs an existing
 * account in. Two verbs rather than two routes because they share the credential handling, and a
 * second copy of that is a second place to get it wrong.
 */

import { createHash, randomBytes, scryptSync, timingSafeEqual } from "node:crypto";
import { NextResponse } from "next/server";
import { db } from "../../../lib/ledger";

const SCRYPT_KEYLEN = 64;
const MIN_PASSWORD = 10;
const ORG_ID = /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/;

function isCloud(): boolean {
  return process.env.RUNTIME_MODE === "cloud";
}

/** Refuse the local credential path in cloud, loudly, before anything else happens. */
function assertLocalCredentialsAllowed() {
  if (isCloud()) {
    throw new Error(
      "password handling is not available in cloud mode: Identity Platform owns credentials, " +
        "and the browser authenticates against it directly.",
    );
  }
}

function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const key = scryptSync(password, salt, SCRYPT_KEYLEN);
  return `scrypt$${salt.toString("hex")}$${key.toString("hex")}`;
}

function passwordMatches(password: string, stored: string): boolean {
  const [scheme, saltHex, keyHex] = stored.split("$");
  if (scheme !== "scrypt" || !saltHex || !keyHex) return false;
  const key = scryptSync(password, Buffer.from(saltHex, "hex"), SCRYPT_KEYLEN);
  const expected = Buffer.from(keyHex, "hex");
  return key.length === expected.length && timingSafeEqual(key, expected);
}

/** Mint a development token in the scheme `lib/verify.ts` and `shared/identity.py` both accept. */
function devToken(uid: string, email: string, name: string): string {
  const { createHmac } = require("node:crypto") as typeof import("node:crypto");
  const secret =
    process.env.DRAWBRIDGE_DEV_AUTH_SECRET ??
    createHash("sha256")
      .update(
        `drawbridge-dev:${process.env.GOOGLE_CLOUD_PROJECT ?? process.env.PROJECT_ID ?? "drawbridge-local"}`,
      )
      .digest();
  const claims = {
    iss: "drawbridge-local",
    sub: uid,
    email,
    name,
    exp: Date.now() / 1000 + 60 * 60 * 12,
  };
  const body = Buffer.from(JSON.stringify(claims)).toString("base64url");
  const signature = createHmac("sha256", secret).update(body).digest("hex");
  return `${body}.${signature}`;
}

function uidFor(email: string): string {
  // Deterministic so the same address is the same person across restarts of a local database.
  return `local:${createHash("sha256").update(email.toLowerCase()).digest("hex").slice(0, 24)}`;
}

/** POST — create an organisation and its first administrator. */
export async function POST(request: Request) {
  try {
    assertLocalCredentialsAllowed();
  } catch (error) {
    return NextResponse.json({ message: String((error as Error).message) }, { status: 501 });
  }

  const body = (await request.json().catch(() => null)) as {
    name?: string;
    orgName?: string;
    orgId?: string;
    email?: string;
    password?: string;
  } | null;

  if (!body?.email || !body.password || !body.orgId || !body.orgName) {
    return NextResponse.json({ message: "Every field is required." }, { status: 400 });
  }
  if (body.password.length < MIN_PASSWORD) {
    return NextResponse.json(
      { message: `Passwords must be at least ${MIN_PASSWORD} characters.` },
      { status: 400 },
    );
  }
  if (!ORG_ID.test(body.orgId)) {
    return NextResponse.json(
      { message: "That workspace name cannot be used in a URL. Try letters, digits and hyphens." },
      { status: 400 },
    );
  }

  const email = body.email.toLowerCase();
  const uid = uidFor(email);
  const now = new Date().toISOString();

  const existingOrg = await db().collection("orgs").doc(body.orgId).get();
  if (existingOrg.exists) {
    // Never silently joins. A signup that added a stranger to an existing customer's workspace
    // is the worst outcome this endpoint has available to it.
    return NextResponse.json(
      { message: "That workspace name is taken. Choose another." },
      { status: 409 },
    );
  }

  const existingUser = await db().collection("users").doc(uid).get();
  if (existingUser.exists && !passwordMatches(body.password, String(existingUser.data()?.password_hash ?? ""))) {
    return NextResponse.json(
      { message: "An account with that address already exists. Sign in instead." },
      { status: 409 },
    );
  }

  const batch = db().batch();
  batch.set(
    db().collection("users").doc(uid),
    {
      uid,
      email,
      name: body.name ?? "",
      created_at: existingUser.exists ? existingUser.data()?.created_at : now,
      last_seen_at: now,
      email_verified: false,
      password_hash: existingUser.exists
        ? existingUser.data()?.password_hash
        : hashPassword(body.password),
    },
    { merge: true },
  );
  batch.set(db().collection("orgs").doc(body.orgId), {
    org_id: body.orgId,
    name: body.orgName,
    created_at: now,
    created_by: uid,
    plan: "trial",
    status: "active",
    review_ceiling_usd: 1.0,
    monthly_ceiling_usd: 500.0,
    settings: {},
  });
  batch.set(db().collection("memberships").doc(`${body.orgId}:${uid}`), {
    org_id: body.orgId,
    uid,
    role: "admin",
    invited_by: uid,
    invited_at: now,
    joined_at: now,
    status: "active",
  });
  await batch.commit();

  return NextResponse.json({
    ok: true,
    orgId: body.orgId,
    token: devToken(uid, email, body.name ?? ""),
  });
}

/** PUT — sign an existing account in. */
export async function PUT(request: Request) {
  try {
    assertLocalCredentialsAllowed();
  } catch (error) {
    return NextResponse.json({ message: String((error as Error).message) }, { status: 501 });
  }

  const body = (await request.json().catch(() => null)) as {
    email?: string;
    password?: string;
  } | null;

  if (!body?.email || !body.password) {
    return NextResponse.json({ message: "Enter your email and password." }, { status: 400 });
  }

  const email = body.email.toLowerCase();
  const snap = await db().collection("users").doc(uidFor(email)).get();

  // One message whether the account is unknown or the password is wrong. A response that told
  // them apart is an account-enumeration endpoint.
  const wrong = NextResponse.json(
    { message: "That email and password do not match an account." },
    { status: 401 },
  );

  if (!snap.exists) return wrong;
  const user = snap.data() as Record<string, unknown>;
  if (!passwordMatches(body.password, String(user.password_hash ?? ""))) return wrong;

  return NextResponse.json({
    ok: true,
    token: devToken(String(user.uid), email, String(user.name ?? "")),
  });
}
