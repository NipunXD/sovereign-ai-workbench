"use client";

import { Check, Database, PenLine, Wrench } from "lucide-react";

import { Spinner } from "@/components/ui/primitives";
import type { StepProgress } from "@/lib/types";
import { cn, formatDuration } from "@/lib/utils";

/**
 * The plan as a progress rail across the top of an answer.
 *
 * "What is it doing right now" should not require reading a trace. Each
 * planned step is a pill that lights up as the run reaches it, so a pause on
 * step three of five reads as exactly that.
 */
export function RunStepper({ steps, running }: { steps: StepProgress[]; running: boolean }) {
  if (!steps.length) return null;
  const done = steps.filter((s) => s.state === "done").length;
  const activeIndex = steps.findIndex((s) => s.state === "active");
  const finishedAll = !running && done >= steps.length - (steps.at(-1)?.intent === "synthesize" ? 1 : 0);

  return (
    <div className="stepper mb-3 flex items-center gap-1 overflow-x-auto pb-1" aria-label="Plan progress">
      {steps.map((step, index) => {
        const state = finishedAll ? "done" : step.state;
        const active = state === "active" || (running && activeIndex === -1 && index === done);
        const Icon = step.intent === "retrieve" ? Database : step.intent === "tool" ? Wrench : PenLine;
        const elapsed =
          step.startedAt && step.finishedAt ? formatDuration(step.finishedAt - step.startedAt) : null;
        return (
          <div key={step.id} className="flex items-center">
            <div
              className={cn(
                "group relative flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-2xs font-medium transition-all",
                state === "done"
                  ? "border-ok/40 bg-ok/10 text-ok"
                  : active
                    ? "border-accent/60 bg-accent/15 text-accent shadow-glow-sm"
                    : "border-border bg-surface text-fg-subtle",
              )}
              title={step.description}
            >
              {state === "done" ? (
                <Check size={11} />
              ) : active ? (
                <Spinner className="h-2.5 w-2.5" />
              ) : (
                <Icon size={11} />
              )}
              <span className="max-w-[9rem] truncate">
                {step.tool ? step.tool.replace("artifact.", "") : label(step)}
              </span>
              {elapsed ? <span className="tnum text-2xs opacity-70">{elapsed}</span> : null}
            </div>
            {index < steps.length - 1 ? (
              <span
                className={cn(
                  "mx-0.5 h-px w-3 shrink-0",
                  state === "done" ? "bg-ok/50" : "bg-border-strong",
                )}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function label(step: StepProgress): string {
  if (step.intent === "retrieve") return "retrieve";
  if (step.intent === "synthesize") return "answer";
  return step.description || "step";
}
