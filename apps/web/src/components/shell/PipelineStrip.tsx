"use client";

import {
  BrainCircuit,
  CheckCheck,
  FileOutput,
  ListTree,
  Search,
  Wrench,
} from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The agent loop, drawn once and used everywhere it is explained.
 *
 * Six stages, in the order a run actually goes through them. A pulse travels
 * the connector so the strip reads as a process rather than a list of nouns —
 * the same order the stepper in a live run walks through, so what a person
 * sees on the sign-in page is what they then watch happen.
 */
const STAGES = [
  { label: "Understand", icon: BrainCircuit, note: "route to the right local model" },
  { label: "Plan", icon: ListTree, note: "steps, each with an intent" },
  { label: "Retrieve", icon: Search, note: "hybrid search, clearance-filtered" },
  { label: "Tools", icon: Wrench, note: "calculate, run code, build files" },
  { label: "Validate", icon: CheckCheck, note: "every claim needs a source" },
  { label: "Deliver", icon: FileOutput, note: "answer, citations, documents" },
] as const;

export function PipelineStrip({
  compact = false,
  className,
}: {
  compact?: boolean;
  className?: string;
}) {
  return (
    <ol className={cn("pipeline relative flex items-start", className)} aria-label="How a run works">
      {STAGES.map((stage, index) => {
        const Icon = stage.icon;
        return (
          <li
            key={stage.label}
            className="relative flex min-w-0 flex-1 flex-col items-center text-center"
            style={{ animationDelay: `${index * 70}ms` }}
          >
            {index < STAGES.length - 1 ? (
              <span className="pipeline-link" aria-hidden />
            ) : null}
            <span
              className={cn(
                "pipeline-node relative z-10 flex items-center justify-center rounded-full border border-border bg-surface text-fg-subtle shadow-xs",
                compact ? "h-8 w-8" : "h-10 w-10",
              )}
              style={{ animationDelay: `${index * 420}ms` }}
            >
              <Icon size={compact ? 13 : 16} />
            </span>
            <span className={cn("mt-2 font-semibold tracking-tight text-fg", compact ? "text-2xs" : "text-xs")}>
              {stage.label}
            </span>
            {!compact ? (
              <span className="mt-0.5 px-1 text-2xs leading-snug text-fg-subtle">{stage.note}</span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
