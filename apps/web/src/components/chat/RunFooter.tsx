"use client";

import { Clock, Database, Wrench } from "lucide-react";

import { cn, formatDuration } from "@/lib/utils";
import type { RunSummary, ValidationReport } from "@/lib/types";

/**
 * The line under an answer that says how much to trust it.
 *
 * The grounding figure is drawn as a ring rather than printed, because it is
 * the one number a reader should take in before reading anything else, and a
 * ring at 40% looks different from one at 95% from across the room.
 */
export function RunFooter({
  summary,
  validation,
}: {
  summary: RunSummary;
  validation: ValidationReport | null;
}) {
  const refusal = validation?.is_refusal ?? false;
  const grounded = validation ? Math.round(validation.grounded_ratio * 100) : null;

  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-border pt-2.5 text-2xs text-fg-subtle">
      {refusal ? (
        <span className="flex items-center gap-1.5 rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 font-medium text-warn">
          declined — not in the corpus
        </span>
      ) : grounded !== null ? (
        <GroundingRing value={grounded} />
      ) : null}
      <Stat icon={<Database size={11} />} label={`${summary.evidence_used} passages read`} />
      <Stat
        icon={<Wrench size={11} />}
        label={`${summary.budget.tool_calls_used} tool call${summary.budget.tool_calls_used === 1 ? "" : "s"}`}
      />
      <Stat icon={<Clock size={11} />} label={formatDuration(summary.wall_ms)} />
      {validation?.unresolved_citations.length ? (
        <span className="text-danger">
          {validation.unresolved_citations.length} invented reference
          {validation.unresolved_citations.length === 1 ? "" : "s"} removed
        </span>
      ) : null}
    </div>
  );
}

function Stat({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <span className="flex items-center gap-1 tnum">
      {icon}
      {label}
    </span>
  );
}

export function GroundingRing({ value, size = 22 }: { value: number; size?: number }) {
  const r = (size - 4) / 2;
  const c = 2 * Math.PI * r;
  const good = value >= 80;
  const mid = value >= 50;
  const tone = good ? "text-ok" : mid ? "text-warn" : "text-danger";
  return (
    <span className={cn("flex items-center gap-1.5 font-medium", tone)} title="Share of the answer that carries a citation">
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeOpacity={0.18} strokeWidth={2.5} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - value / 100)}
          className="ring-grow"
        />
      </svg>
      <span className="tnum">{value}% grounded</span>
    </span>
  );
}
