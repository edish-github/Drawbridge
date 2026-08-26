"use client";

/**
 * Releasing a gate, with a step-up.
 *
 * The person confirms their password before the approval is signed. That is not friction for its
 * own sake: an approval is a named individual accepting security risk, it is quoted in an audit
 * binder years later, and a session cookie left open on an unattended laptop is not evidence that
 * the named individual was present. Re-authenticating turns the signature into a claim about a
 * person rather than about a browser.
 *
 * The identity token this produces is sent once and never stored. The approval service verifies
 * it and re-reads the caller's membership; nothing here decides whether the approval is allowed.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

type Props = {
  reviewId: string;
  scope: "contact" | "decision";
  email: string;
  canApprove: boolean;
};

export default function ApproveGate({ reviewId, scope, email, canApprove }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [conditions, setConditions] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!canApprove) {
    return (
      <div className="notice">
        <strong style={{ color: "var(--ink)" }}>You cannot release this gate.</strong> Approving is
        a named person accepting security risk, so it needs the approver role. An administrator can
        grant it, or ask an approver to review this.
      </div>
    );
  }

  async function approve(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const auth = await fetch("/api/signup", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const identity = await auth.json();
      if (!auth.ok) {
        setError("That password did not match. Nothing was approved.");
        return;
      }

      const response = await fetch("/api/approve", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          reviewId,
          scope,
          identityToken: identity.token,
          conditions: conditions
            .split("\n")
            .map((c) => c.trim())
            .filter(Boolean),
        }),
      });
      const result = await response.json();
      if (!response.ok) {
        setError(result.message ?? "That approval was refused.");
        return;
      }

      setOpen(false);
      setPassword("");
      router.refresh();
    } catch {
      setError("The approval service is unreachable. Nothing was approved.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div>
        <button className="btn" onClick={() => setOpen(true)}>
          {scope === "decision" ? "Accept the risk and approve" : "Authorise first contact"}
        </button>
        <p className="card-note" style={{ marginTop: 10 }}>
          {scope === "decision"
            ? "Your name and the time are recorded against this decision, and printed in the audit binder."
            : "This authorises the fleet to write to this vendor's security contact, once."}
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={approve}>
      {error ? <div className="auth-error">{error}</div> : null}

      <p className="body-text" style={{ marginTop: 0 }}>
        Confirm it is you. This signature is quoted in the audit record as{" "}
        <strong>{email}</strong>.
      </p>

      {scope === "decision" ? (
        <div className="field">
          <label htmlFor="conditions">Conditions (optional, one per line)</label>
          <textarea
            id="conditions"
            rows={3}
            value={conditions}
            onChange={(e) => setConditions(e.target.value)}
            placeholder={"Renew ISO 27001 before go-live\nProvide MFA coverage evidence in 90 days"}
          />
          <span className="hint">
            Recorded on the approval and carried into the next review of this vendor.
          </span>
        </div>
      ) : null}

      <div className="field">
        <label htmlFor="approve-password">Your password</label>
        <input
          id="approve-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn" type="submit" disabled={busy || !password}>
          {busy ? "Signing…" : "Sign the approval"}
        </button>
        <button
          className="btn btn-quiet"
          type="button"
          onClick={() => {
            setOpen(false);
            setError(null);
          }}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
