"use client";

import { useQuery } from "@tanstack/react-query";
import { Calculator, FileText, HelpCircle, Search, type LucideIcon } from "lucide-react";

import { PipelineStrip } from "@/components/shell/PipelineStrip";
import { ClassificationBadge, StatTile } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import type { Classification } from "@/lib/types";
import { useSession } from "@/stores/session";

/**
 * The blank page of a new session.
 *
 * It answers the three questions a person has before they type: what can I
 * ask, what will I get, and what am I allowed to see. The numbers are live —
 * how many documents this clearance can reach, how many passages are indexed,
 * which models are resident right now — because a landing page that says
 * "10 documents" when the corpus has 400 is one nobody trusts twice.
 */
const SUGGESTIONS: Array<{ q: string; why: string; shows: string; icon: LucideIcon }> = [
  {
    q: "What is the depressurisation rate limit for V-1201, and what hold time does the SOP require?",
    why: "Grounded lookup",
    shows: "an answer with a citation you can open to the exact passage",
    icon: Search,
  },
  {
    q: "Using the 2023 and 2029 CML-04 readings for V-1201, compute the corrosion rate and remaining life against the 8.0 mm minimum.",
    why: "Unit-checked calculation",
    shows: "arithmetic done in a sandbox, units carried through, inputs cited",
    icon: Calculator,
  },
  {
    q: "Produce a Word report of the V-1201 thickness survey with every reading in a table.",
    why: "Document, gated by a second person",
    shows: "a .docx with inline citations that waits for an approver",
    icon: FileText,
  },
  {
    q: "What was the purge duration used during the 2019 turnaround?",
    why: "Deliberately unanswerable",
    shows: "a refusal that names what the corpus does not contain",
    icon: HelpCircle,
  },
];

export function Welcome({ onPick }: { onPick: (q: string) => void }) {
  const { principal } = useSession();
  const documents = useQuery({ queryKey: ["documents", "all"], queryFn: () => api.documents({ limit: 200 }) });
  const readiness = useQuery({ queryKey: ["readiness"], queryFn: api.readiness, refetchInterval: 15_000 });

  const docs = documents.data ?? [];
  const passages = docs.reduce((sum, d) => sum + d.chunk_count, 0);
  const resident = readiness.data?.components.residency?.models.length;
  // "S. Nair (Senior Inspection)" is a name and a role; greet the name.
  const name = (principal?.full_name || principal?.username || "").split(" (")[0].trim();

  return (
    <div className="animate-fade-in-up mt-6">
      <p className="text-2xs font-semibold uppercase tracking-[0.18em] text-fg-subtle">{greeting()}</p>
      <h2 className="mt-1.5 text-3xl font-semibold tracking-tight">
        {name ? `${name}, ask` : "Ask"} the plant&apos;s documents.
      </h2>
      <p className="mt-3 max-w-xl text-md leading-relaxed text-fg-muted">
        Every answer comes with a map of what was read and a citation on each claim. When the
        corpus does not cover something, it says so instead of guessing.
      </p>

      <div className="stagger mt-6 grid grid-cols-[repeat(auto-fit,minmax(8.5rem,1fr))] gap-3">
        <StatTile value={documents.isLoading ? "…" : String(docs.length)} label="Documents you can see" />
        <StatTile value={documents.isLoading ? "…" : passages.toLocaleString()} label="Indexed passages" />
        <StatTile value={resident == null ? "…" : String(resident)} label="Models resident" />
        <div className="stat-tile flex flex-col justify-between">
          <span className="section-label">Your clearance</span>
          <ClassificationBadge level={(principal?.clearance ?? "internal") as Classification} className="mt-1 self-start" />
        </div>
      </div>

      <p className="section-label mb-2.5 mt-7">Try one</p>
      <div className="stagger grid grid-cols-[repeat(auto-fit,minmax(17rem,1fr))] gap-3">
        {SUGGESTIONS.map((s) => {
          const Icon = s.icon;
          return (
            <button
              key={s.q}
              type="button"
              onClick={() => onPick(s.q)}
              className="group flex gap-3 rounded-xl border border-border bg-surface p-4 text-left shadow-xs transition-all duration-150 hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-card"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-raised text-fg-subtle transition-colors group-hover:bg-accent-muted group-hover:text-accent">
                <Icon size={16} />
              </span>
              <span className="min-w-0">
                <span className="block text-2xs font-semibold uppercase tracking-wider text-fg-subtle group-hover:text-accent">
                  {s.why}
                </span>
                <span className="mt-1 block text-sm font-medium leading-snug text-fg">{s.q}</span>
                <span className="mt-2 block text-xs leading-snug text-fg-subtle">→ {s.shows}</span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="mt-8 rounded-xl border border-border bg-surface px-4 pb-4 pt-5 shadow-xs">
        <PipelineStrip compact />
      </div>
    </div>
  );
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Night shift";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}
