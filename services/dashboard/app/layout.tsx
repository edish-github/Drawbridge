/**
 * The document. Nothing else.
 *
 * The console's frame lives in `(app)/layout.tsx` so it wraps the signed-in application only —
 * the authentication screens render on the bare ground, because there is no workspace to draw
 * navigation for until somebody has chosen one.
 */

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Drawbridge — vendor security review",
  description:
    "Autonomous vendor security reviews with a human at every decision point. Every number on screen was computed, not written.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
