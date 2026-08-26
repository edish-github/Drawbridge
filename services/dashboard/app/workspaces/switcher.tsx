"use client";

/**
 * Switching workspace re-issues the session cookie rather than storing a selection.
 *
 * The organisation is inside the signed cookie, so choosing one is an authentication event and
 * not a preference. A workspace held in local storage would be a workspace a person could change
 * by editing local storage.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

type Membership = { orgId: string; role: string; name: string };

export default function Switcher({ memberships }: { memberships: Membership[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function choose(orgId: string) {
    setBusy(orgId);
    setError(null);
    const response = await fetch("/api/workspace", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ orgId }),
    });
    if (!response.ok) {
      setError("Could not switch to that workspace.");
      setBusy(null);
      return;
    }
    router.push("/");
    router.refresh();
  }

  return (
    <>
      {error ? <div className="auth-error">{error}</div> : null}
      <div className="workspace-list">
        {memberships.map((m) => (
          <button
            key={m.orgId}
            className="workspace"
            onClick={() => choose(m.orgId)}
            disabled={busy !== null}
          >
            <span className="avatar" aria-hidden="true">
              {m.name.slice(0, 2).toUpperCase()}
            </span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ display: "block", fontSize: 13.5, fontWeight: 600 }}>{m.name}</span>
              <span className="mono" style={{ fontSize: 11, color: "var(--faint)" }}>
                {m.orgId} · {m.role}
              </span>
            </span>
            <span style={{ color: "var(--faint)" }}>{busy === m.orgId ? "…" : "›"}</span>
          </button>
        ))}
      </div>
    </>
  );
}
