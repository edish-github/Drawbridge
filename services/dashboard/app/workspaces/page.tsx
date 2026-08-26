/**
 * The workspace picker.
 *
 * Reached two ways, and it says which. A person who belongs to several organisations lands here
 * to choose; a person whose session names an organisation they no longer belong to lands here
 * because the guard sent them, and the difference matters — the second needs to be told what
 * happened rather than shown a menu.
 */

import Link from "next/link";
import { redirect } from "next/navigation";
import { currentPrincipal, membershipsOf } from "../../lib/auth";
import Switcher from "./switcher";

export const dynamic = "force-dynamic";

export default async function Workspaces() {
  const principal = await currentPrincipal();
  if (!principal) redirect("/login");

  const memberships = await membershipsOf(principal.uid);

  return (
    <div className="auth-frame">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="brand-mark">
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M2 13V6.2a6 6 0 0 1 12 0V13" stroke="#f6f4f0" strokeWidth="1.5" strokeLinecap="round" />
              <path d="M8 13V6.5M4.6 13V8.4M11.4 13V8.4" stroke="#f6f4f0" strokeWidth="1.3" strokeLinecap="round" />
            </svg>
          </div>
          <div>
            <div className="brand-name">DRAWBRIDGE</div>
            <div className="brand-sub">{principal.email}</div>
          </div>
        </div>

        {memberships.length === 0 ? (
          <>
            <h1 className="auth-title">No workspace yet</h1>
            <p className="auth-sub">
              Your account is signed in but does not belong to a workspace. Ask an administrator
              to invite <strong>{principal.email}</strong>, or create your own.
            </p>
            <Link href="/signup" className="btn">
              Create a workspace
            </Link>
          </>
        ) : (
          <>
            <h1 className="auth-title">Choose a workspace</h1>
            <p className="auth-sub">
              {principal.role
                ? "You belong to more than one."
                : "Your session named a workspace you are no longer a member of."}
            </p>
            <Switcher memberships={memberships} />
          </>
        )}

        <p className="auth-foot">
          <Link href="/logout">Sign out</Link>
        </p>
      </div>
    </div>
  );
}
