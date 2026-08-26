"use client";

/**
 * The sign-in form.
 *
 * In cloud the browser talks to Identity Platform, gets an ID token, and posts it here. Locally
 * there is no Identity Platform, so the form asks the server to mint a development token for the
 * address typed — the same scheme `shared/identity.py` uses, refused outright in cloud mode by
 * both halves.
 *
 * The mode is decided by the server and passed in, never guessed from the browser: a client that
 * chose its own authentication path could choose the weaker one.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function SignInForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    try {
      const auth = await fetch("/api/signup", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const result = await auth.json();
      if (!auth.ok) {
        setError(result.message ?? "That sign-in did not work.");
        return;
      }

      const session = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token: result.token }),
      });
      const outcome = await session.json();

      if (!session.ok) {
        setError(
          outcome.error === "no_workspace"
            ? "That account is not a member of any workspace yet. Ask an administrator for an invitation."
            : (outcome.message ?? "Could not start a session."),
        );
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
        <label htmlFor="email">Work email</label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
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
          autoComplete="current-password"
          required
          minLength={10}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>

      <button className="btn" type="submit" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
