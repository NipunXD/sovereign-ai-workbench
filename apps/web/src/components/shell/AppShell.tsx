"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  FileText,
  LogOut,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  Search,
  ShieldCheck,
  Settings2,
  UploadCloud,
  type LucideIcon,
} from "lucide-react";

import { SovereigntyBar } from "@/components/shell/SovereigntyBar";
import { SystemStatus } from "@/components/shell/SystemStatus";
import { ClassificationBadge } from "@/components/ui/primitives";
import type { Classification } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useRun } from "@/stores/run";
import { useSession } from "@/stores/session";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  permission: string | null;
  title: string;
  subtitle: string;
}

/**
 * Navigation, grouped by what the person is doing rather than by module.
 *
 * Entries are permission-gated: a destination someone cannot use is not shown
 * at all, rather than shown and then refused. Every item carries a label —
 * an icon-only rail is faster for the person who built it and slower for
 * everyone else.
 */
const NAV: Array<{ group: string; items: NavItem[] }> = [
  {
    group: "Workspace",
    items: [
      {
        href: "/chat",
        label: "Workbench",
        icon: MessageSquare,
        permission: "chat:use",
        title: "Workbench",
        subtitle: "Ask the plant's documents. Every answer carries its sources.",
      },
      {
        href: "/documents",
        label: "Documents",
        icon: FileText,
        permission: "doc:read",
        title: "Documents",
        subtitle: "The indexed corpus, filtered to your clearance.",
      },
      {
        href: "/search",
        label: "Retrieval",
        icon: Search,
        permission: "doc:read",
        title: "Retrieval",
        subtitle: "The passages search returns, before a model sees them.",
      },
      {
        href: "/upload",
        label: "Ingest",
        icon: UploadCloud,
        permission: "doc:ingest",
        title: "Ingest",
        subtitle: "Add documents to the knowledge base.",
      },
    ],
  },
  {
    group: "Governance",
    items: [
      {
        href: "/approvals",
        label: "Approvals",
        icon: ShieldCheck,
        permission: null,
        title: "Approvals",
        subtitle: "Generated documents wait here for a second person.",
      },
      {
        href: "/audit",
        label: "Audit log",
        icon: ScrollText,
        permission: "audit:read",
        title: "Audit log",
        subtitle: "Hash-chained record of every action and every refusal.",
      },
      {
        href: "/admin",
        label: "System",
        icon: Settings2,
        permission: null,
        title: "System",
        subtitle: "Models, backends, routing and the memory budget.",
      },
    ],
  },
];

const ALL_ITEMS = NAV.flatMap((group) => group.items);

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { principal, status, restore, logout, can } = useSession();
  const running = useRun((s) => s.running);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (status === "loading") void restore();
  }, [status, restore]);

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  // The workbench carries its own session list and an inspector, so the
  // labelled nav is folded away there and kept open everywhere else. Decided
  // after mount so the server and client agree on the first paint.
  useEffect(() => {
    const stored = window.localStorage.getItem("nav-collapsed");
    if (stored !== null) {
      setCollapsed(stored === "1");
      return;
    }
    setCollapsed(pathname.startsWith("/chat") || window.innerWidth < 1400);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggle() {
    setCollapsed((value) => {
      window.localStorage.setItem("nav-collapsed", value ? "0" : "1");
      return !value;
    });
  }

  if (status !== "authenticated" || !principal) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-fg-subtle">
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
        Restoring session…
      </div>
    );
  }

  const active = ALL_ITEMS.filter((item) => pathname.startsWith(item.href)).sort(
    (a, b) => b.href.length - a.href.length,
  )[0];

  return (
    <div className="flex h-full bg-bg">
      {/* ---------------------------------------------------------- sidebar */}
      <aside
        className={cn(
          "flex shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-200",
          collapsed ? "w-sidebar-sm" : "w-sidebar",
        )}
      >
        <div className={cn("flex h-header shrink-0 items-center border-b border-border", collapsed ? "justify-center px-2" : "gap-2.5 px-4")}>
          <div
            className="brand-mark flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-2xs font-bold text-white"
            aria-hidden
          >
            MW
          </div>
          {!collapsed ? (
            <div className="min-w-0 leading-tight">
              <p className="truncate text-sm font-semibold tracking-tight">Sovereign AI</p>
              <p className="truncate text-2xs text-fg-subtle">MRPL · Crude Distillation</p>
            </div>
          ) : null}
        </div>

        <nav className={cn("min-h-0 flex-1 space-y-5 overflow-y-auto py-4", collapsed ? "px-2" : "px-3")}>
          {NAV.map((group) => {
            const items = group.items.filter((item) => !item.permission || can(item.permission));
            if (!items.length) return null;
            return (
              <div key={group.group}>
                {!collapsed ? <p className="section-label mb-1.5 px-2.5">{group.group}</p> : null}
                <ul className="space-y-0.5">
                  {items.map(({ href, label, icon: Icon }) => {
                    const isActive = active?.href === href;
                    return (
                      <li key={href}>
                        <Link
                          href={href}
                          title={collapsed ? label : undefined}
                          className={cn(
                            "group relative flex h-9 items-center rounded-lg text-sm font-medium transition-colors",
                            collapsed ? "justify-center" : "gap-2.5 px-2.5",
                            isActive
                              ? "bg-accent-muted text-accent"
                              : "text-fg-muted hover:bg-surface-raised hover:text-fg",
                          )}
                        >
                          <Icon size={16} strokeWidth={2} className="shrink-0" />
                          {!collapsed ? <span className="truncate">{label}</span> : null}
                          {isActive && collapsed ? (
                            <span className="absolute left-0 h-5 w-0.5 rounded-r-full bg-accent" aria-hidden />
                          ) : null}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </nav>

        {/* ------------------------------------------------------ user block */}
        <div className={cn("shrink-0 border-t border-border p-3", collapsed && "px-2")}>
          <div
            className={cn(
              "flex items-center rounded-lg",
              collapsed ? "flex-col gap-1.5" : "gap-2.5 bg-surface-raised p-2",
            )}
          >
            <span
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/12 text-2xs font-semibold text-accent"
              aria-hidden
            >
              {initials(principal.full_name || principal.username)}
            </span>
            {!collapsed ? (
              <div className="min-w-0 flex-1 leading-tight">
                <p className="truncate text-xs font-semibold">{principal.full_name || principal.username}</p>
                <p className="truncate text-2xs text-fg-subtle">{principal.roles.join(", ")}</p>
              </div>
            ) : null}
            <button
              type="button"
              onClick={() => void logout()}
              className="rounded-md p-1.5 text-fg-subtle transition-colors hover:bg-surface hover:text-danger"
              title="Sign out"
            >
              <LogOut size={14} />
            </button>
          </div>
          <button
            type="button"
            onClick={toggle}
            className={cn(
              "mt-1.5 flex h-8 w-full items-center rounded-lg text-2xs font-medium text-fg-subtle transition-colors hover:bg-surface-raised hover:text-fg",
              collapsed ? "justify-center" : "gap-2 px-2.5",
            )}
            title={collapsed ? "Expand navigation" : "Collapse navigation"}
          >
            {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
            {!collapsed ? "Collapse" : null}
          </button>
        </div>
      </aside>

      {/* ------------------------------------------------------------- main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-header shrink-0 items-center gap-3 border-b border-border bg-surface px-5">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-semibold tracking-tight">{active?.title ?? "Workbench"}</h1>
            <p className="truncate text-2xs text-fg-subtle">{active?.subtitle ?? ""}</p>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            {running ? (
              <Link
                href="/chat"
                className="flex h-7 items-center gap-1.5 rounded-full bg-accent-muted px-2.5 text-2xs font-semibold text-accent ring-1 ring-inset ring-accent/20"
                title="A run is in progress — it continues while you look at other pages"
              >
                <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-accent" aria-hidden />
                Run live
              </Link>
            ) : null}
            <SystemStatus />
            <SovereigntyBar />
            <ClassificationBadge level={principal.clearance as Classification} compact />
          </div>
        </header>

        <main className="app-canvas min-w-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}

function initials(name: string): string {
  const parts = name.replace(/\(.*\)/, "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
