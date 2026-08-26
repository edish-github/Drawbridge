/**
 * The one line every page starts with.
 *
 * `requirePrincipal` resolves the signed-in person and the organisation they are working in, or
 * sends them somewhere they can do something about it — the sign-in page if there is no session,
 * the workspace picker if their session names an organisation they no longer belong to.
 *
 * It is a redirect rather than a thrown error because the alternative is a page that renders an
 * access-denied message inside a shell full of another workspace's navigation.
 *
 * `requireCapability` is the second line on the pages that need it. Roles are read from the
 * membership document on every request, never from the cookie, so a revoked approver loses the
 * gate on their next page load rather than in twelve hours.
 */

import { redirect } from "next/navigation";
import { CAN_ACT, CAN_ADMINISTER, CAN_APPROVE, currentPrincipal, may, type Principal, type Role } from "./auth";

export type { Principal, Role };
export { CAN_ACT, CAN_ADMINISTER, CAN_APPROVE, may };

/** The signed-in person, or a redirect. Never returns an unauthenticated caller. */
export async function requirePrincipal(): Promise<Principal> {
  const principal = await currentPrincipal();
  if (!principal) redirect("/login");
  if (!principal.role) redirect("/workspaces");
  return principal;
}

/** The signed-in person, or `null`. For pages that render differently when signed out. */
export async function optionalPrincipal(): Promise<Principal | null> {
  return currentPrincipal();
}

/**
 * Refuse a page to somebody whose role does not carry the capability.
 *
 * Redirects rather than rendering a disabled screen: a viewer who lands on the settings editor
 * should be told where they are, not shown a form that will not submit.
 */
export async function requireCapability(capability: Role[]): Promise<Principal> {
  const principal = await requirePrincipal();
  if (!may(principal.role, capability)) redirect("/?denied=1");
  return principal;
}
