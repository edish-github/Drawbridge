/**
 * Sign in, and the front door.
 *
 * This is the first screen anyone sees, so it carries the argument as well as the form. The three
 * points on the left are the three things that distinguish this product from a spreadsheet of
 * questionnaires, and each one is a claim the rest of the application can be checked against —
 * which is the only kind of marketing copy worth writing for a security tool.
 */

import Link from "next/link";
import { redirect } from "next/navigation";
import { optionalPrincipal } from "../../lib/guard";
import SignInForm from "./form";

export const dynamic = "force-dynamic";

const POINTS = [
  {
    title: "A person decides, every time",
    body: "The fleet gathers, reconciles and scores. It never approves a vendor and never contacts one without a named human authorising it first.",
  },
  {
    title: "The score is arithmetic, not opinion",
    body: "Findings are judged by a model; the number is computed in code from a published rubric, and printed so an auditor can redo it by hand.",
  },
  {
    title: "Nothing a vendor sends reaches a model unscreened",
    body: "Every uploaded byte is inspected before anything reads it. A document that tries to steer the review becomes a finding about the vendor.",
  },
];

export default async function Login() {
  const principal = await optionalPrincipal();
  if (principal?.role) redirect("/");

  return (
    <div className="door">
      <section className="door-pitch">
        <div className="auth-brand" style={{ marginBottom: 0 }}>
          <div className="brand-mark">
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M2 13V6.2a6 6 0 0 1 12 0V13" stroke="#f6f4f0" strokeWidth="1.5" strokeLinecap="round" />
              <path d="M8 13V6.5M4.6 13V8.4M11.4 13V8.4" stroke="#f6f4f0" strokeWidth="1.3" strokeLinecap="round" />
            </svg>
          </div>
          <div>
            <div className="brand-name">DRAWBRIDGE</div>
            <div className="brand-sub" style={{ color: "#8a8781" }}>
              VENDOR SECURITY REVIEW
            </div>
          </div>
        </div>

        <h2>Vendor security reviews that finish in days.</h2>
        <p>
          Drawbridge sends the questionnaire, reads the evidence, and cross-examines one against
          the other — then stops and asks you, with everything it found on one screen.
        </p>

        <div className="door-points">
          {POINTS.map((point) => (
            <div className="door-point" key={point.title}>
              <svg width="17" height="17" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                <circle cx="10" cy="10" r="8.2" stroke="currentColor" strokeWidth="1.5" />
                <path d="m6.4 10.2 2.4 2.4 4.8-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <div>
                <strong>{point.title}</strong>
                <span>{point.body}</span>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="door-form">
        <div className="auth-card">
          <h1 className="auth-title">Sign in</h1>
          <p className="auth-sub">
            Approvals are recorded against the account you sign in with, so use your own.
          </p>

          <SignInForm />

          <p className="auth-foot">
            No workspace yet? <Link href="/signup">Create one</Link>
          </p>
        </div>
      </section>
    </div>
  );
}
