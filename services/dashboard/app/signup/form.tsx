"use client";

/**
 * The signup form.
 *
 * The workspace id is derived from the organisation name as it is typed, and shown, because it
 * becomes part of every URL and every audit reference. Somebody should see what they are choosing
 * before they choose it — an id that turned out to be `acme-security-2` because the first was
 * taken is a thing to find out now rather than in a support ticket.
 */

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

export default function SignUpForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const orgId = useMemo(() => slugify(orgName), [orgName]);
  const idUsable = orgId.length >= 3;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const response = await fetch("/api/signup", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, orgName, orgId, email, password }),
      });
      const result = await response.json();

      if (!response.ok) {
        setError(result.message ?? "Could not create that workspace.");
        return;
      }

      const session = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: result.token, orgId: result.orgId }),
      });
      if (!session.ok) {
        setError("The workspace was created, but sign-in failed. Try signing in.");
        return;
      }

      router.push("/");
      router.refresh();
    } catch {
      setError("Could not reach the server. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      {error ? <div className="auth-error">{error}</div> : null}

      <div className="field">
        <label htmlFor="org">Organisation</label>
        <input
          id="org"
          required
          value={orgName}
          onChange={(e) => setOrgName(e.target.value)}
          placeholder="Northgate Security"
        />
        <span className="hint">
          {orgName
            ? idUsable
              ? `Your workspace will be drawbridge.app/${orgId}`
              : "A little longer, please — at least three letters or digits."
            : "This becomes part of every URL and every audit reference."}
        </span>
      </div>

      <div className="field">
        <label htmlFor="name">Your name</label>
        <input
          id="name"
          required
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Alex Mercer"
        />
        <span className="hint">Printed beside any approval you sign.</span>
      </div>

      <div className="field">
        <label htmlFor="email">Work email</label>
        <input
          id="email"
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
        />
      </div>

      <div className="field">
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          required
          minLength={10}
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <span className="hint">At least ten characters.</span>
      </div>

      <button className="btn" type="submit" disabled={busy || !idUsable}>
        {busy ? "Creating…" : "Create workspace"}
      </button>
    </form>
  );
}
