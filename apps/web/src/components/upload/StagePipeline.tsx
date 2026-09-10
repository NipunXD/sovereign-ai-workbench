"use client";

import { Check, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The ingestion pipeline, shown as the instrument it is.
 *
 * Ingesting a scanned report takes a minute or two on a laptop, and a single
 * indeterminate spinner for that long is indistinguishable from a hang. Showing
 * the named stages also makes the work legible: a user who can see "recognising
 * page 4 of 6" understands why a scan costs more than a Word document, and an
 * operator diagnosing a slow ingest can see exactly which stage is slow.
 */
const STAGES = [
  { key: "receive", label: "Receive" },
  { key: "identify", label: "Identify" },
  { key: "dedupe", label: "Deduplicate" },
  { key: "store", label: "Store" },
  { key: "classify", label: "Classify" },
  { key: "extract", label: "Extract" },
  { key: "ocr", label: "Recognise" },
  { key: "vision", label: "Vision" },
  { key: "normalize", label: "Normalise" },
  { key: "render", label: "Render" },
  { key: "chunk", label: "Chunk" },
  { key: "embed", label: "Embed" },
  { key: "index", label: "Index" },
  { key: "finalize", label: "Finalise" },
] as const;

export function StagePipeline({
  currentStage,
  done,
  className,
}: {
  currentStage: string | null;
  done: boolean;
  className?: string;
}) {
  const index = STAGES.findIndex((stage) => stage.key === currentStage);

  return (
    <ol className={cn("flex flex-wrap gap-x-1 gap-y-1.5", className)}>
      {STAGES.map((stage, position) => {
        const complete = done || position < index;
        const active = !done && position === index;
        return (
          <li
            key={stage.key}
            className={cn(
              "flex items-center gap-1 rounded border px-1.5 py-0.5 text-2xs transition-colors",
              complete
                ? "border-ok/40 bg-ok/10 text-ok"
                : active
                  ? "border-accent/50 bg-accent/15 text-accent"
                  : "border-border bg-surface text-fg-subtle",
            )}
          >
            {complete ? (
              <Check size={9} />
            ) : active ? (
              <Loader2 size={9} className="animate-spin" />
            ) : (
              <span className="h-1 w-1 rounded-full bg-current opacity-50" />
            )}
            {stage.label}
          </li>
        );
      })}
    </ol>
  );
}
