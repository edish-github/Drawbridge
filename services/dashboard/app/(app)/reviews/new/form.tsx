"use client";

/**
 * The intake form.
 *
 * Two columns: what is being bought, and what it will touch. The second decides the tier, and the
 * form shows the tier updating as the answers change — so nobody is surprised by a sixty-question
 * review, and nobody quietly under-declares to avoid one without seeing what they did.
 */

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

const DATA_CATEGORIES = [
  { value: "customer_content", label: "Customer content", tier1: true },
  { value: "customer_pii", label: "Customer personal data", tier1: true },
  { value: "employee_data", label: "Employee data", tier1: false },
  { value: "internal_operational", label: "Internal operational data", tier1: false },
  { value: "public_only", label: "Public information only", tier1: false },
];

const ACCESS_LEVELS = [
  { value: "none", label: "No access to our systems" },
  { value: "read_only", label: "Read-only integration" },
  { value: "production", label: "Production system access" },
];

export default function IntakeForm() {
  const router = useRouter();
  const [form, setForm] = useState({
    name: "",
    category: "",
    legalEntity: "",
    primaryDomain: "",
    contactEmail: "",
    systemAccess: "none",
    description: "",
    isAiVendor: false,
  });
  const [dataCategories, setDataCategories] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  /**
   * The same rule the planner applies, shown live.
   *
   * A copy of `planner.tier_from`, and a copy is a thing that can drift — but the alternative is a
   * form that asks four questions and tells you nothing until the review has already started.
   * The authoritative tier is still computed server-side, and this only ever previews it.
   */
  const tier = useMemo(() => {
    const tier1Data = dataCategories.some(
      (c) => DATA_CATEGORIES.find((d) => d.value === c)?.tier1,
    );
    if (tier1Data || form.systemAccess !== "none" || form.isAiVendor) return 1;
    if (dataCategories.some((c) => c === "internal_operational" || c === "employee_data")) return 2;
    return 3;
  }, [dataCategories, form.systemAccess, form.isAiVendor]);

  const questions = tier === 1 ? "around sixty" : tier === 2 ? "around thirty" : "around twelve";

  function toggle(value: string) {
    setDataCategories((current) =>
      current.includes(value) ? current.filter((c) => c !== value) : [...current, value],
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/reviews", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...form, dataCategories }),
      });
      const result = await response.json();
      if (!response.ok) {
        setError(result.message ?? "Could not open that review.");
        return;
      }
      router.push(`/reviews/${result.reviewId}`);
      router.refresh();
    } catch {
      setError("Could not reach the server. Nothing was opened.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      {error ? <div className="auth-error" style={{ maxWidth: 620 }}>{error}</div> : null}

      <div className="grid grid-2">
        <div className="card">
          <span className="label">The vendor</span>
          <div style={{ marginTop: 14 }}>
            <div className="field">
              <label htmlFor="name">Name</label>
              <input
                id="name"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Meridian Cloud"
              />
            </div>
            <div className="field">
              <label htmlFor="category">What they do</label>
              <input
                id="category"
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                placeholder="Cloud infrastructure"
              />
            </div>
            <div className="field">
              <label htmlFor="contact">Security contact</label>
              <input
                id="contact"
                type="email"
                required
                value={form.contactEmail}
                onChange={(e) => setForm({ ...form, contactEmail: e.target.value })}
                placeholder="security@meridian.example"
              />
              <span className="hint">
                Nothing is sent here until a person authorises first contact.
              </span>
            </div>
            <div className="field">
              <label htmlFor="entity">Legal entity</label>
              <input
                id="entity"
                value={form.legalEntity}
                onChange={(e) => setForm({ ...form, legalEntity: e.target.value })}
                placeholder="Meridian Cloud Ltd"
              />
              <span className="hint">Used to match breach reports to this vendor later.</span>
            </div>
            <div className="field">
              <label htmlFor="domain">Primary domain</label>
              <input
                id="domain"
                value={form.primaryDomain}
                onChange={(e) => setForm({ ...form, primaryDomain: e.target.value })}
                placeholder="meridian.example"
              />
            </div>
          </div>
        </div>

        <div className="stack">
          <div className="card">
            <span className="label">What they will handle</span>
            <p className="card-note" style={{ marginTop: 6, marginBottom: 12 }}>
              These decide the tier. Answer them as they are, not as you would like them to be —
              the review corrects itself upward if the vendor&rsquo;s own answers say otherwise.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
              {DATA_CATEGORIES.map((c) => (
                <label
                  key={c.value}
                  style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13.2, cursor: "pointer" }}
                >
                  <input
                    type="checkbox"
                    checked={dataCategories.includes(c.value)}
                    onChange={() => toggle(c.value)}
                    style={{ width: 16, height: 16, accentColor: "var(--green)" }}
                  />
                  {c.label}
                </label>
              ))}
            </div>

            <div className="field" style={{ marginTop: 16 }}>
              <label htmlFor="access">System access</label>
              <select
                id="access"
                value={form.systemAccess}
                onChange={(e) => setForm({ ...form, systemAccess: e.target.value })}
              >
                {ACCESS_LEVELS.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
            </div>

            <label
              style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13.2, marginTop: 10, cursor: "pointer" }}
            >
              <input
                type="checkbox"
                checked={form.isAiVendor}
                onChange={(e) => setForm({ ...form, isAiVendor: e.target.checked })}
                style={{ width: 16, height: 16, accentColor: "var(--green)" }}
              />
              This is an AI service that will process our text
            </label>
          </div>

          <div className="card" style={{ borderLeft: `3px solid var(--${tier === 1 ? "red" : tier === 2 ? "amber" : "gray"})` }}>
            <div className="row-between">
              <span className="label">This will be a tier {tier} review</span>
              <span className="mono" style={{ fontSize: 22, fontWeight: 700 }}>
                {tier}
              </span>
            </div>
            <p className="card-note" style={{ marginTop: 8 }}>
              The vendor will be asked {questions} questions and the evidence requested will match.
              The tier can rise once their answers arrive; it never falls.
            </p>
          </div>

          <div className="card">
            <div className="field" style={{ marginBottom: 0 }}>
              <label htmlFor="description">Anything else worth knowing</label>
              <textarea
                id="description"
                rows={4}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="What this is for, who asked for it, when it needs to be live."
              />
              <span className="hint">
                Context for the reviewer. It is deliberately not used to decide the tier — the
                tier comes from the enumerated answers above, so a persuasive description cannot
                reduce the scrutiny applied to the vendor it describes.
              </span>
            </div>
          </div>

          <button className="btn" type="submit" disabled={busy || !form.name || !form.contactEmail}>
            {busy ? "Opening…" : "Open review"}
          </button>
        </div>
      </div>
    </form>
  );
}
