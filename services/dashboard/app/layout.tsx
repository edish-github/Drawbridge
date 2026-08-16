import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Drawbridge",
  description: "The fleet that decides what crosses into the castle.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <div className="topbar-inner">
            <Link href="/" className="wordmark">
              Drawbridge
            </Link>
            <span className="tagline">
              Vendor security review fleet · read-only operator view
            </span>
          </div>
        </header>
        {children}
      </body>
    </html>
  );
}
