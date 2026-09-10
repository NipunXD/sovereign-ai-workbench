import type { Metadata } from "next";

import { Providers } from "@/app/providers";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: "Sovereign AI Workbench — MRPL",
  description:
    "On-premise agentic AI for confidential industrial work. All inference runs on local open-weight models.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Dark is the resting state, not a preference — see globals.css.
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
