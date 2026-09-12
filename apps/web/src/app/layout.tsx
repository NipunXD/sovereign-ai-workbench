import type { Metadata } from "next";
import localFont from "next/font/local";

import { Providers } from "@/app/providers";
import "@/app/globals.css";

/**
 * Plus Jakarta Sans, served from our own origin.
 *
 * Committed to the repository as a variable woff2 rather than fetched from
 * fonts.googleapis.com, because this product is meant to run with no route to
 * the internet at all. `display: swap` means the system stack renders first
 * and is replaced, so a slow font never leaves the page blank.
 */
const sans = localFont({
  src: "./fonts/PlusJakartaSans.woff2",
  weight: "400 800",
  display: "swap",
  variable: "--font-sans",
  fallback: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
});

export const metadata: Metadata = {
  title: "Sovereign AI Workbench — MRPL",
  description:
    "On-premise agentic AI for confidential industrial work. All inference runs on local open-weight models.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Light is the resting state now — see globals.css. The attribute stays so
    // the dark theme remains one flag away.
    <html lang="en" data-theme="light" className={sans.variable} suppressHydrationWarning>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
