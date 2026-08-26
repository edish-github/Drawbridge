/**
 * Create a workspace.
 *
 * Signup creates three things atomically: the person, the organisation, and an administrator
 * membership joining them. An organisation with no administrator is one nobody can ever get into,
 * so the three are one transaction rather than three requests.
 */

import Link from "next/link";
import { redirect } from "next/navigation";
import { optionalPrincipal } from "../../lib/guard";
import SignUpForm from "./form";

export const dynamic = "force-dynamic";

export default async function SignUp() {
  const principal = await optionalPrincipal();
  if (principal?.role) redirect("/");

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
            <div className="brand-sub">VENDOR SECURITY REVIEW</div>
          </div>
        </div>

        <h1 className="auth-title">Create your workspace</h1>
        <p className="auth-sub">
          You will be its first administrator. Invite the rest of your team once you are in.
        </p>

        <SignUpForm />

        <p className="auth-foot">
          Already have a workspace? <Link href="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
