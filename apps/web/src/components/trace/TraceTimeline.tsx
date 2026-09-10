"use client";

import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  ClipboardList,
  Database,
  Route,
  ShieldCheck,
  Wrench,
  XCircle,
} from "lucide-react";

import { Chip } from "@/components/ui/primitives";
import { cn, formatDuration } from "@/lib/utils";
import type { TraceItem } from "@/lib/types";

/**
 * What the agent actually did.
 *
 * This panel is the difference between a chatbot and a workbench. An engineer
 * asked to act on a corrosion rate needs to see that the number came from a
 * unit-checked calculation over two retrieved measurements — not to take a
 * fluent paragraph on trust. Everything here is the real trace: which model was
 * chosen and why, what was retrieved, which tools ran, and how the answer
 * scored against its own sources.
 */
export function TraceTimeline({
  trace,
  startedAt,
  className,
}: {
  trace: TraceItem[];
  startedAt: number;
  className?: string;
}) {
  if (!trace.length) {
    return (
      <p className={cn("px-3 py-4 text-xs text-fg-subtle", className)}>
        The execution trace appears here as the agent works.
      </p>
    );
  }

  return (
    <ol className={cn("relative space-y-0", className)}>
      {trace.map((item, index) => (
        <TraceRow
          key={index}
          item={item}
          elapsed={item.at - startedAt}
          last={index === trace.length - 1}
        />
      ))}
    </ol>
  );
}

function TraceRow({
  item,
  elapsed,
  last,
}: {
  item: TraceItem;
  elapsed: number;
  last: boolean;
}) {
  const { icon, tone, title, body } = describe(item);

  return (
    <li className="relative flex gap-2.5 px-3 py-2">
      {/* The rail connecting steps. Stops at the last one so the timeline has
          an end rather than trailing into nothing. */}
      {!last ? (
        <span className="absolute left-[1.4rem] top-8 h-[calc(100%-1.5rem)] w-px bg-border" aria-hidden />
      ) : null}

      <span
        className={cn(
          "relative z-10 mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border",
          tone,
        )}
      >
        {icon}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <p className="text-xs font-medium text-fg">{title}</p>
          <span className="tnum shrink-0 text-2xs text-fg-subtle">{formatDuration(elapsed)}</span>
        </div>
        {body}
      </div>
    </li>
  );
}

function describe(item: TraceItem): {
  icon: React.ReactNode;
  tone: string;
  title: string;
  body: React.ReactNode;
} {
  switch (item.kind) {
    case "route": {
      const d = item.data;
      return {
        icon: <Route size={11} />,
        tone: "border-info/40 bg-info/10 text-info",
        title: `Routed to ${d.lane}`,
        body: (
          <div className="mt-1 space-y-1">
            <div className="flex flex-wrap items-center gap-1">
              <Chip tone="accent">
                <span className="font-mono">{d.model}</span>
              </Chip>
              <Chip>{d.provider}</Chip>
              <Chip>stage {d.stage}</Chip>
              {d.resident ? <Chip tone="ok">resident</Chip> : <Chip tone="warn">cold load</Chip>}
            </div>
            <p className="text-2xs leading-snug text-fg-subtle">{d.reason}</p>
          </div>
        ),
      };
    }

    case "plan":
      return {
        icon: <ClipboardList size={11} />,
        tone: "border-accent/40 bg-accent/10 text-accent",
        title: `Planned ${item.steps.length} step${item.steps.length === 1 ? "" : "s"}`,
        body: (
          <ol className="mt-1 space-y-0.5">
            {item.steps.map((step, i) => (
              <li key={step.id} className="flex gap-1.5 text-2xs text-fg-muted">
                <span className="tnum shrink-0 text-fg-subtle">{i + 1}.</span>
                <span className="shrink-0 font-mono text-fg-subtle">[{step.intent}]</span>
                <span className="min-w-0 flex-1">{step.description}</span>
                {step.tool ? (
                  <span className="shrink-0 font-mono text-accent">{step.tool}</span>
                ) : null}
              </li>
            ))}
          </ol>
        ),
      };

    case "step":
      return {
        icon: <span className="h-1.5 w-1.5 rounded-full bg-current" />,
        tone: "border-border bg-surface-raised text-fg-subtle",
        title: item.description || item.intent,
        body: null,
      };

    case "retrieval":
      return {
        icon: <Database size={11} />,
        tone: "border-info/40 bg-info/10 text-info",
        title: `Retrieved ${item.hits.length} passage${item.hits.length === 1 ? "" : "s"}`,
        body: (
          <div className="mt-1 space-y-1">
            <p className="truncate font-mono text-2xs text-fg-subtle">“{item.query}”</p>
            <ul className="space-y-0.5">
              {item.hits.slice(0, 4).map((hit) => (
                <li key={hit.chunk_id} className="flex items-center gap-1.5 text-2xs">
                  <Chip tone={hit.method === "hybrid" ? "accent" : "neutral"}>{hit.method}</Chip>
                  <span className="min-w-0 flex-1 truncate text-fg-muted">
                    {hit.doc_title || "untitled"}
                  </span>
                  <span className="tnum shrink-0 text-fg-subtle">p{hit.page}</span>
                  {hit.confidence < 0.85 ? (
                    <span className="tnum shrink-0 text-warn" title="OCR confidence">
                      {(hit.confidence * 100).toFixed(0)}%
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ),
      };

    case "tool": {
      const finished = item.ok !== undefined;
      const failed = item.ok === false;
      return {
        icon: failed ? <XCircle size={11} /> : <Wrench size={11} />,
        tone: failed
          ? "border-danger/40 bg-danger/10 text-danger"
          : "border-accent/40 bg-accent/10 text-accent",
        title: finished
          ? failed
            ? `${item.tool} failed`
            : `${item.tool} completed`
          : `Calling ${item.tool}`,
        body: (
          <div className="mt-1 space-y-1">
            {item.args ? (
              <pre className="overflow-x-auto rounded border border-border bg-bg px-1.5 py-1 font-mono text-2xs text-fg-muted">
                {JSON.stringify(item.args, null, 0).slice(0, 240)}
              </pre>
            ) : null}
            {item.error ? <p className="text-2xs text-danger">{item.error}</p> : null}
          </div>
        ),
      };
    }

    case "validation": {
      const v = item.data;
      const grounded = Math.round(v.grounded_ratio * 100);
      const good = v.is_refusal || v.grounded_ratio >= 0.8;
      return {
        icon: good ? <ShieldCheck size={11} /> : <AlertTriangle size={11} />,
        tone: good ? "border-ok/40 bg-ok/10 text-ok" : "border-warn/40 bg-warn/10 text-warn",
        title: v.is_refusal ? "Declined — sources do not cover this" : "Grounding checked",
        body: v.is_refusal ? null : (
          <div className="mt-1 space-y-1">
            <div className="flex items-center gap-1.5">
              <div className="h-1 w-20 overflow-hidden rounded-full bg-bg">
                <div
                  className={cn("h-full rounded-full", good ? "bg-ok" : "bg-warn")}
                  style={{ width: `${grounded}%` }}
                />
              </div>
              <span className="tnum text-2xs text-fg-muted">{grounded}% cited</span>
            </div>
            {v.unresolved_citations.length ? (
              <p className="text-2xs text-danger">
                {v.unresolved_citations.length} invented citation
                {v.unresolved_citations.length === 1 ? "" : "s"} removed
              </p>
            ) : null}
          </div>
        ),
      };
    }

    case "error":
      return {
        icon: <AlertTriangle size={11} />,
        tone: item.recoverable
          ? "border-warn/40 bg-warn/10 text-warn"
          : "border-danger/40 bg-danger/10 text-danger",
        title: item.code.replace(/_/g, " "),
        body: <p className="mt-0.5 text-2xs text-fg-muted">{item.message}</p>,
      };

    default:
      return {
        icon: <CheckCircle2 size={11} />,
        tone: "border-border bg-surface-raised text-fg-subtle",
        title: "Step",
        body: null,
      };
  }
}

/** The collapsible thinking panel. */
export function ReasoningPanel({
  reasoning,
  streaming,
}: {
  reasoning: string;
  streaming: boolean;
}) {
  if (!reasoning) return null;
  return (
    <details className="group mt-2 rounded border border-border bg-bg/50">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2 py-1.5 text-2xs font-medium text-fg-subtle hover:text-fg-muted">
        <Brain size={11} className={streaming ? "animate-pulse-dot" : undefined} />
        Model reasoning
        <span className="tnum ml-auto">{reasoning.length.toLocaleString()} chars</span>
      </summary>
      <div className="max-h-56 overflow-y-auto border-t border-border px-2 py-1.5">
        <p className="whitespace-pre-wrap font-mono text-2xs leading-relaxed text-fg-subtle">
          {reasoning}
        </p>
      </div>
    </details>
  );
}
