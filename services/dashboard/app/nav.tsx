"use client";

/**
 * The sidebar. The only client component in the console, and only because it needs the URL.
 *
 * Everything else here is a server component reading Firestore directly. This one needs
 * `usePathname` to know which leaf is lit, and nothing else — no data, no state, no effects. A
 * navigation that fetched anything would be a navigation that can be slow.
 *
 * Sections behave differently from leaves on purpose. A section header takes weight when one of
 * its children is active but never the lit background, so exactly one row on screen is ever
 * highlighted and it is always the page you are on.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

type Leaf = { href: string; label: string; match?: (p: string) => boolean };
type Item = { href: string; label: string; icon: React.ReactNode; children?: Leaf[] };

const icon = (paths: React.ReactNode) => (
  <svg width="17" height="17" viewBox="0 0 20 20" fill="none" aria-hidden="true">
    {paths}
  </svg>
);

const ITEMS: Item[] = [
  {
    href: "/",
    label: "Overview",
    icon: icon(
      <>
        <rect x="2.5" y="2.5" width="6.4" height="6.4" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <rect x="11.1" y="2.5" width="6.4" height="6.4" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <rect x="2.5" y="11.1" width="6.4" height="6.4" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <rect x="11.1" y="11.1" width="6.4" height="6.4" rx="2" stroke="currentColor" strokeWidth="1.5" />
      </>,
    ),
  },
  {
    href: "/queue",
    label: "Reviews",
    icon: icon(
      <>
        <rect x="3.5" y="3" width="13" height="14" rx="3" stroke="currentColor" strokeWidth="1.5" />
        <path d="M7 8h6M7 11.5h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </>,
    ),
    children: [
      { href: "/queue", label: "Queue" },
      { href: "/vendors", label: "Vendors", match: (p) => p.startsWith("/vendors") },
    ],
  },
  {
    href: "/monitoring",
    label: "Monitoring",
    icon: icon(
      <path
        d="M2.5 10h3l2-5 3 10 2.5-6 1.5 3h3"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />,
    ),
  },
  {
    href: "/evidence",
    label: "Evidence",
    icon: icon(
      <>
        <path
          d="M5 2.5h6.5L15.5 6.5V17a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V3.5a1 1 0 0 1 1-1Z"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path d="M11 2.6V7h4.4" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      </>,
    ),
    children: [{ href: "/findings", label: "Findings" }],
  },
  {
    href: "/binders",
    label: "Governance",
    icon: icon(
      <path
        d="M10 2.5 3.5 5.2v4.4c0 3.6 2.7 6.6 6.5 7.9 3.8-1.3 6.5-4.3 6.5-7.9V5.2L10 2.5Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />,
    ),
    children: [
      { href: "/binders", label: "Audit Binders" },
      { href: "/agents", label: "Agent Registry" },
    ],
  },
  {
    href: "/activity",
    label: "System",
    icon: icon(
      <>
        <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10 6v4.3l2.6 1.6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </>,
    ),
    children: [
      { href: "/activity", label: "Activity" },
      { href: "/settings", label: "Settings" },
    ],
  },
];

export default function Nav() {
  const pathname = usePathname() || "/";
  const isLeaf = (leaf: Leaf) => (leaf.match ? leaf.match(pathname) : pathname === leaf.href);

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M2 13V6.2a6 6 0 0 1 12 0V13" stroke="#f6f4f0" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M8 13V6.5M4.6 13V8.4M11.4 13V8.4" stroke="#f6f4f0" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
        </div>
        <div>
          <div className="brand-name">DRAWBRIDGE</div>
          <div className="brand-sub">FLEET v1.2</div>
        </div>
      </div>

      <nav className="nav" aria-label="Console sections">
        {ITEMS.map((item) => {
          const childActive = (item.children ?? []).some(isLeaf);
          const selfActive = !item.children && pathname === item.href;

          return (
            <div key={item.label}>
              {item.label !== "Overview" ? <div className="nav-gap" /> : null}
              <Link
                href={item.href}
                className={item.children ? "nav-item nav-section" : "nav-item"}
                data-active={selfActive ? "true" : undefined}
                data-open={childActive ? "true" : undefined}
                aria-current={selfActive ? "page" : undefined}
              >
                {item.icon}
                {item.label}
              </Link>

              {item.children ? (
                <div className="nav-children">
                  {item.children.map((leaf) => (
                    <Link
                      key={leaf.href}
                      href={leaf.href}
                      className="nav-leaf"
                      data-active={isLeaf(leaf) ? "true" : undefined}
                      aria-current={isLeaf(leaf) ? "page" : undefined}
                    >
                      {leaf.label}
                    </Link>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </nav>

      <div className="sidebar-foot">
        <div className="avatar" aria-hidden="true">
          NG
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 12.2, fontWeight: 600, lineHeight: 1.3 }}>Northgate Security</div>
          <div
            style={{
              fontFamily: "var(--mono)",
              fontSize: 10,
              fontWeight: 500,
              lineHeight: 1.3,
              color: "var(--faint)",
            }}
          >
            WORKSPACE · LOCAL
          </div>
        </div>
      </div>
    </aside>
  );
}
