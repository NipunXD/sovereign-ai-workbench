"use client";

import { useQuery } from "@tanstack/react-query";
import { Calculator, FileText, HelpCircle, Search, type LucideIcon } from "lucide-react";

import { PipelineStrip } from "@/components/shell/PipelineStrip";
import { ClassificationBadge } from "@/components/ui/primitives";
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
      <h2 className="mt-1 text-2xl font-semibold tracking-tight">
        {name ? `${name}, ask` : "Ask"} the plant&apos;s documents.
      </h2>
      <p className="mt-2 max-w-xl text-sm leading-relaxed text-fg-muted">
        Every answer comes with a map of what was read and a citation on each claim. When the
        corpus does not cover something, it says so instead of guessing.
      </p>

      <div className="stagger mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat value={documents.isLoading ? "…" : String(docs.length)} label="documents you can see" />
        <Stat value={documents.isLoading ? "…" : passages.toLocaleString()} label="indexed passages" />
        <Stat value={resident == null ? "…" : String(resident)} label="models resident" />
        <div className="stat-tile flex flex-col justify-between">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">your clearance</span>
          <ClassificationBadge level={(principal?.clearance ?? "internal") as Classification} className="mt-1 self-start" />
        </div>
      </div>

      <p className="mb-2 mt-6 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
        Try one
      </p>
      <div className="stagger grid gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((s) => {
          const Icon = s.icon;
          return (
            <button
              key={s.q}
              type="button"
              onClick={() => onPick(s.q)}
              className="group flex gap-3 rounded-xl border border-border bg-surface/60 p-3.5 text-left transition-all hover:-translate-y-px hover:border-accent/50 hover:bg-surface hover:shadow-glow-sm"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border bg-bg text-fg-subtle transition-colors group-hover:border-accent/40 group-hover:text-accent">
                <Icon size={15} />
              </span>
              <span className="min-w-0">
                <span className="block text-[10px] font-semibold uppercase tracking-wider text-fg-subtle group-hover:text-accent">
                  {s.why}
                </span>
                <span className="mt-0.5 block text-xs leading-snug text-fg group-hover:text-fg">{s.q}</span>
                <span className="mt-1.5 block text-[11px] leading-snug text-fg-subtle">→ {s.shows}</span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="mt-8 rounded-xl border border-border/70 bg-surface/30 px-4 pb-3 pt-4">
        <PipelineStrip compact />
      </div>
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat-tile">
      <p className="tnum text-xl font-semibold leading-none tracking-tight text-fg">{value}</p>
      <p className="mt-1.5 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">{label}</p>
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
