/**
 * Evidence — every document the fleet screened, and what the detectors said about it.
 *
 * One row per document rather than per screening. A document is screened once per template, so
 * the raw collection holds two rows for the same bytes; folding them is also the only form in
 * which "is this admissible" has an answer, because admissibility is a property of all a
 * document's verdicts together.
 *
 * The four verdicts are kept apart deliberately, and they are the same four
 * `shared.armor.verdict_is_trustworthy` makes rather than a softer set invented here:
 *
 * **Blocked** — a critical filter matched.
 * **Not a verdict** — the template was the local stub or a seeded fixture. The pipeline shape ran
 * and no detector did, so the row is evidence of nothing and says so.
 * **Incomplete** — a critical filter did not execute. A detector that never ran is not a detector
 * that found nothing, and folding this into *clean* is how somebody comes to trust a document
 * nobody actually screened.
 * **Clean** — every critical filter ran and none matched.
 */

import Link from "next/link";
import { ago, evidenceDocuments, type Doc } from "../../../lib/ledger";
import { Empty, Filters, LoadError, PageHead, Pill } from "../../components";
import { requirePrincipal } from "../../../lib/guard";

export const dynamic = "force-dynamic";

const BUCKETS: Record<string, (d: Doc) => boolean> = {
  all: () => true,
  clean: (d) => d.verdict === "clean",
  blocked: (d) => d.verdict === "blocked",
  incomplete: (d) => d.verdict === "incomplete",
  "not-a-verdict": (d) => d.verdict === "not-a-verdict",
};

const TONE = {
  clean: "green",
  blocked: "red",
  incomplete: "amber",
  "not-a-verdict": "gray",
} as const;

const LABEL = {
  clean: "clean",
  blocked: "blocked",
  incomplete: "incomplete",
  "not-a-verdict": "not a verdict",
} as const;

export default async function Evidence({
  searchParams,
}: {
  searchParams: Promise<{ filter?: string }>;
}) {
  const principal = await requirePrincipal();
  const orgId = principal.orgId;

  const { filter = "all" } = await searchParams;

  let docs: Doc[];
  try {
    docs = await evidenceDocuments(orgId);
  } catch (error) {
    return (
      <>
        <PageHead crumb="Evidence" title="Evidence" subtitle="" />
        <LoadError error={error} />
      </>
    );
  }

  const predicate = BUCKETS[filter] ?? BUCKETS.all;
  const shown = docs.filter(predicate);

  const options = [
    { value: "all", label: "All", count: docs.length },
    { value: "clean", label: "Clean", count: docs.filter(BUCKETS.clean).length },
    { value: "blocked", label: "Blocked", count: docs.filter(BUCKETS.blocked).length },
    { value: "incomplete", label: "Incomplete screening", count: docs.filter(BUCKETS.incomplete).length },
    {
      value: "not-a-verdict",
      label: "Not a verdict",
      count: docs.filter(BUCKETS["not-a-verdict"]).length,
    },
  ];

  return (
    <>
      <PageHead
        crumb="Evidence"
        title="Evidence"
        subtitle="Every screened document, the verdict each detector returned, and what it was admitted to."
      />

      <div className="scroll-area" style={{ animation: "db-rise 260ms ease both" }}>
        {docs.some((d) => d.verdict === "not-a-verdict") ? (
          <div className="banner">
            <strong>{docs.filter((d) => d.verdict === "not-a-verdict").length} of these are not
            verdicts.</strong>{" "}
            They were screened by the local stub or seeded straight into the clean bucket, so the
            pipeline shape ran and no detector did. Nothing they carry is admissible to a model on
            the strength of its stamp, and the reviews built on them say so on every surface —
            including the cover of their binders.
          </div>
        ) : null}

        <div className="notice" style={{ marginBottom: 16 }}>
          Nothing a vendor wrote reaches a model without a verified, verdict-bearing clean-stamp.
          Policy <strong>P2</strong> is enforced in <code className="mono">shared/routing.py</code> —
          in the router rather than at the tool gateway, because the router is the only place every
          model call passes through. A blocked excerpt is stored inert and never re-prompted.
        </div>

        <Filters options={options} active={filter} base="/evidence" />

        <div className="card card-tight">
          {shown.length === 0 ? (
            <div style={{ padding: 22 }}>
              <Empty>No documents under this filter.</Empty>
            </div>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Document</th>
                    <th>Vendor</th>
                    <th>Verdict</th>
                    <th>Detectors</th>
                    <th className="right">Screened</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((d) => (
                    <tr key={d.key}>
                      <td className="strong">
                        <span className="mono small">{d.name}</span>
                        <div className="faint" style={{ fontSize: 10.5, marginTop: 2 }}>
                          <Link href={`/reviews/${d.review_id}`} className="mono">
                            {d.review_id}
                          </Link>
                        </div>
                      </td>
                      <td>
                        {d.review ? (
                          <Link href={`/vendors/${d.review.vendor_id}`}>
                            {d.vendor?.name ?? d.review.vendor_id}
                          </Link>
                        ) : (
                          <span className="faint">—</span>
                        )}
                      </td>
                      <td>
                        <Pill tone={TONE[d.verdict as keyof typeof TONE]}>
                          {LABEL[d.verdict as keyof typeof LABEL]}
                        </Pill>
                        {d.matched.length ? (
                          <div className="mono faint" style={{ fontSize: 10.5, marginTop: 3 }}>
                            {d.matched.join(", ")}
                          </div>
                        ) : null}
                      </td>
                      <td className="mono small faint" style={{ maxWidth: 300 }}>
                        {Object.entries(d.filters as Record<string, string>)
                          .map(([k, v]) => `${k}: ${v.replace("MATCH_FOUND", "MATCH").replace("NO_MATCH_FOUND", "none")}`)
                          .join(" · ") || "—"}
                        <div style={{ marginTop: 2 }}>
                          templates: {[...new Set(d.templates as string[])].join(", ")}
                        </div>
                      </td>
                      <td className="right mono small faint">{ago(d.openedAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {shown.some((d) => d.excerpts.length) ? (
          <div className="card" style={{ marginTop: 14 }}>
            <span className="label">Blocked excerpts, stored inert</span>
            <p className="card-note" style={{ marginTop: 6, marginBottom: 12 }}>
              What a detector matched on, preserved as evidence for the binder and never fed back
              to a model. This is the one place vendor-authored text appears in this console, and
              it appears as a quotation rather than as an instruction.
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {shown
                .filter((d) => d.excerpts.length)
                .slice(0, 8)
                .map((d) => (
                  <div key={d.key} style={{ borderTop: "1px solid var(--divider)", paddingTop: 10 }}>
                    <div className="mono small faint" style={{ marginBottom: 5 }}>
                      {d.name} · {d.matched.join(", ")}
                    </div>
                    {(d.excerpts as string[]).slice(0, 2).map((e, i) => (
                      <blockquote
                        key={i}
                        className="mono small"
                        style={{
                          margin: "0 0 6px",
                          padding: "9px 12px",
                          background: "var(--red-bg)",
                          borderRadius: 10,
                          color: "#7a2f22",
                        }}
                      >
                        {e}
                      </blockquote>
                    ))}
                  </div>
                ))}
            </div>
          </div>
        ) : null}

        <p className="card-note" style={{ marginTop: 12 }}>
          Showing {shown.length} of {docs.length} screened documents.
        </p>
      </div>
    </>
  );
}
