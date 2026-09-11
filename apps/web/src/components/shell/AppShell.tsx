"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import {
  FileText,
  LogOut,
  MessageSquare,
  ScrollText,
  Search,
  ShieldCheck,
  Settings2,
  UploadCloud,
} from "lucide-react";

import { SovereigntyBar } from "@/components/shell/SovereigntyBar";
import { SystemStatus } from "@/components/shell/SystemStatus";
import { ClassificationBadge } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import { useRun } from "@/stores/run";
import { useSession } from "@/stores/session";

/** Nav entries are permission-gated. A destination a user cannot use is not
 *  shown at all rather than shown and then refused. */
const NAV = [
  { href: "/chat", label: "Workbench", icon: MessageSquare, permission: "chat:use" },
  { href: "/documents", label: "Documents", icon: FileText, permission: "doc:read" },
  { href: "/upload", label: "Ingest", icon: UploadCloud, permission: "doc:ingest" },
  { href: "/search", label: "Retrieval", icon: Search, permission: "doc:read" },
  { href: "/approvals", label: "Approvals", icon: ShieldCheck, permission: null },
  { href: "/audit", label: "Audit log", icon: ScrollText, permission: "audit:read" },
  { href: "/admin", label: "System", icon: Settings2, permission: null },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { principal, status, restore, logout, can } = useSession();
  const running = useRun((s) => s.running);

  useEffect(() => {
    if (status === "loading") void restore();
  }, [status, restore]);

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated" || !principal) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-fg-subtle">
        Restoring session…
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-header shrink-0 items-center gap-3 border-b border-border bg-surface px-3">
        <div className="flex items-center gap-2">
          <div
            className="brand-mark flex h-6 w-6 items-center justify-center rounded text-2xs font-bold text-accent-fg"
            aria-hidden
          >
            MW
          </div>
          <div className="leading-none">
            <p className="text-xs font-semibold tracking-tight">Sovereign AI Workbench</p>
            <p className="mt-0.5 text-2xs text-fg-subtle">MRPL · Crude Distillation Unit</p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {running ? (
            <Link
              href="/chat"
              className="flex h-6 items-center gap-1.5 rounded border border-accent/40 bg-accent/10 px-2 text-2xs font-semibold uppercase tracking-wider text-accent"
              title="A run is in progress — it continues while you look at other pages"
            >
              <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-accent" aria-hidden />
              run live
            </Link>
          ) : null}
          <SystemStatus />
          <SovereigntyBar />
          <div className="mx-1 h-5 w-px bg-border" aria-hidden />
          <div className="flex items-center gap-2">
            <div className="text-right leading-none">
              <p className="text-xs font-medium">{principal.full_name || principal.username}</p>
              <p className="mt-0.5 text-2xs text-fg-subtle">{principal.roles.join(", ")}</p>
            </div>
            <ClassificationBadge level={principal.clearance} compact />
            <button
              type="button"
              onClick={() => void logout()}
              className="rounded p-1.5 text-fg-subtle transition-colors hover:bg-surface-raised hover:text-fg"
              title="Sign out"
            >
              <LogOut size={13} />
            </button>
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <nav className="flex w-rail shrink-0 flex-col items-center gap-1 border-r border-border bg-surface py-2">
          {NAV.map(({ href, label, icon: Icon, permission }) => {
            const allowed = !permission || can(permission);
            const active = pathname.startsWith(href);
            if (!allowed) return null;
            return (
              <Link
                key={href}
                href={href}
                title={label}
                className={cn(
                  "flex h-9 w-9 items-center justify-center rounded transition-colors",
                  active
                    ? "bg-accent/15 text-accent"
                    : "text-fg-subtle hover:bg-surface-raised hover:text-fg",
                )}
              >
                <Icon size={16} />
              </Link>
            );
          })}
        </nav>

        <main className="app-canvas min-w-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
