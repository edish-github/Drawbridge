/**
 * The console frame: a fixed shell with a scrolling interior.
 *
 * The whole application lives inside one rounded card on a warm ground, and the interior is the
 * only thing that scrolls. That is not decoration — an operator working a queue moves between
 * eleven screens in a session, and a shell that stays put means the navigation never reflows
 * under the cursor and the page they were reading is the only thing that moved.
 */

import type { Metadata } from "next";
import Nav from "./nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Drawbridge — vendor security review fleet",
  description:
    "Read-only operator console over the review ledger. Every number on screen was computed by the fleet.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="frame">
          <div className="console">
            <Nav />
            <main className="main">{children}</main>
          </div>
        </div>
      </body>
    </html>
  );
}
