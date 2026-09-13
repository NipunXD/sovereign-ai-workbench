"use client";

import {
  AlertTriangle,
  Brain,
  CheckCircle2,
  ClipboardList,
  Database,
  RefreshCw,
  ShieldAlert,
  FileCheck2,
  Route,
  ShieldCheck,
  UserCheck,
  Wrench,
  XCircle,
} from "lucide-react";

import { Chip } from "@/components/ui/primitives";
import { cn, formatBytes, formatDuration } from "@/lib/utils";
import type { CalculationDisplay, TraceItem } from "@/lib/types";
import { useInspector } from "@/stores/inspector";

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
/**
 * Plain titles for the failures an operator can actually act on.
 *
 * The card used to be titled with the Python class name — "ProviderUnavailableError"
 * — which names the code that raised rather than the machine that is off.
 */
const ERROR_TITLES: Record<string, string> = {
  provider_unavailable: "Model server not reachable",
  provider_timeout: "Model did not respond in time",
  model_not_found: "Model not installed",
  plan_step_dropped: "A planned step was dropped",
};

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
    <li className="animate-fade-in-up relative flex gap-2.5 px-3 py-2">
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
              {item.hits.slice(0, 6).map((hit) => (
                <li key={hit.chunk_id}>
                  <button
                    type="button"
                    onClick={() => useInspector.getState().showHit(hit, null)}
                    className="flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-2xs transition-colors hover:bg-surface-raised"
                    title="Open this passage"
                  >
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
                  </button>
                </li>
              ))}
              {item.hits.length > 6 ? (
                <li className="px-1 text-2xs text-fg-subtle">+{item.hits.length - 6} more on the evidence map</li>
              ) : null}
            </ul>
          </div>
        ),
      };

    case "tool": {
      const finished = item.ok !== undefined;
      // A tool that checked its inputs and said no is this system working.
      // Drawn as a failure it reads as a broken calculator, which is the
      // opposite of what happened and the opposite of what it is for.
      const refused = item.ok === false && item.refused === true;
      const failed = item.ok === false && !refused;
      return {
        icon: failed ? <XCircle size={11} /> : refused ? <ShieldAlert size={11} /> : <Wrench size={11} />,
        tone: failed
          ? "border-danger/40 bg-danger/10 text-danger"
          : refused
            ? "border-warn/40 bg-warn/10 text-warn"
            : "border-accent/40 bg-accent/10 text-accent",
        title: finished
          ? failed
            ? `${item.tool} failed`
            : refused
              ? `${item.tool} declined the inputs`
              : item.retried
                ? `${item.tool} completed on checked inputs`
                : `${item.tool} completed`
          : `Calling ${item.tool}`,
        body: (
          <div className="mt-1 space-y-1.5">
            {isCalculation(item.display) ? (
              <CalculationCard calc={item.display} />
            ) : item.args ? (
              <pre className="overflow-x-auto rounded-lg border border-border bg-surface-raised px-1.5 py-1 font-mono text-2xs text-fg-muted">
                {JSON.stringify(item.args, null, 0).slice(0, 240)}
              </pre>
            ) : null}
            {item.retried ? (
              <p className="flex items-start gap-1.5 text-2xs leading-relaxed text-fg-subtle">
                <RefreshCw size={11} className="mt-px shrink-0" aria-hidden />
                <span>
                  The first arguments did not match the sources, so they were read again and
                  corrected before the tool ran. These are the values it ran on.
                </span>
              </p>
            ) : null}
            {item.error ? (
              item.refused ? (
                <div className="rounded-lg border border-warn/30 bg-warn/[0.06] px-2.5 py-2">
                  <p className="text-2xs font-semibold text-warn">Inputs did not check out</p>
                  <p className="mt-0.5 text-2xs leading-relaxed text-fg-muted">{item.error}</p>
                  <p className="mt-1.5 text-2xs text-fg-subtle">
                    The calculation was not run. Nothing derived from these inputs reached the
                    answer.
                  </p>
                </div>
              ) : (
                <p className="text-2xs text-danger">{item.error}</p>
              )
            ) : null}
          </div>
        ),
      };
    }

    case "validation": {
      const v = item.data;
      // Runs recorded before the early-stop notice got its own event name
      // carry a validation payload with none of these fields. They are
      // replayed from the database long after the fact, so the guards stay.
      const ratio = typeof v.grounded_ratio === "number" ? v.grounded_ratio : 0;
      const invented = v.unresolved_citations?.length ?? 0;
      const grounded = Math.round(ratio * 100);
      const good = v.is_refusal || ratio >= 0.8;
      return {
        icon: good ? <ShieldCheck size={11} /> : <AlertTriangle size={11} />,
        tone: good ? "border-ok/40 bg-ok/10 text-ok" : "border-warn/40 bg-warn/10 text-warn",
        title: v.is_refusal ? "Declined — sources do not cover this" : "Grounding checked",
        body: v.is_refusal ? null : (
          <div className="mt-1 space-y-1">
            <div className="flex items-center gap-1.5">
              <div className="h-1.5 w-20 overflow-hidden rounded-full bg-surface-sunken">
                <div
                  className={cn("h-full rounded-full", good ? "bg-ok" : "bg-warn")}
                  style={{ width: `${grounded}%` }}
                />
              </div>
              <span className="tnum text-2xs text-fg-muted">{grounded}% cited</span>
            </div>
            {invented ? (
              <p className="text-2xs text-danger">
                {invented} invented citation{invented === 1 ? "" : "s"} removed
              </p>
            ) : null}
          </div>
        ),
      };
    }

    case "artifact":
      return {
        icon: <FileCheck2 size={11} />,
        tone: "border-ok/40 bg-ok/10 text-ok",
        title: `Produced ${item.artifact.filename}`,
        body: (
          <p className="mt-0.5 text-2xs text-fg-subtle">
            {item.artifact.kind.toUpperCase()} · {formatBytes(item.artifact.size_bytes)} ·{" "}
            {(item.artifact.provenance.sources ?? []).length} sources
          </p>
        ),
      };

    case "approval":
      return {
        icon: <UserCheck size={11} />,
        tone:
          item.approval.status === "pending"
            ? "border-accent/40 bg-accent/10 text-accent"
            : item.approval.status === "approved"
              ? "border-ok/40 bg-ok/10 text-ok"
              : "border-danger/40 bg-danger/10 text-danger",
        title:
          item.approval.status === "pending"
            ? "Waiting for a second person"
            : `${item.approval.status[0].toUpperCase()}${item.approval.status.slice(1)}${item.approval.decided_by ? ` by ${item.approval.decided_by}` : ""}`,
        body: (
          <p className="mt-0.5 font-mono text-2xs text-fg-subtle">{item.approval.tool}</p>
        ),
      };

    case "error":
      return {
        icon: <AlertTriangle size={11} />,
        tone: item.recoverable
          ? "border-warn/40 bg-warn/10 text-warn"
          : "border-danger/40 bg-danger/10 text-danger",
        title: ERROR_TITLES[item.code] ?? item.code.replace(/_/g, " "),
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
    <details className="group mt-2 rounded-lg border border-border bg-surface-raised/50">
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

function isCalculation(display: unknown): display is CalculationDisplay {
  return Boolean(display) && (display as CalculationDisplay).kind === "calculation";
}

/**
 * A finished calculation, shown the way a calculation sheet is written.
 *
 * The point of doing this arithmetic in a tool rather than in the answer is
 * that it can be checked, and a bare "0.2333 mm/year" in a trace line cannot
 * be. So the inputs, the substitution, the result and the standard it follows
 * are all here — and the assumptions, because "corrosion is uniform between
 * the two measurements" is the sentence that decides whether the number means
 * anything.
 */
function CalculationCard({ calc }: { calc: CalculationDisplay }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-baseline gap-2 border-b border-border bg-surface-raised/60 px-2.5 py-1.5">
        <span className="font-mono text-2xs text-fg-subtle">{calc.calculation}</span>
        <span className="tnum ml-auto text-sm font-semibold text-fg">{calc.formatted}</span>
      </div>

      <dl className="space-y-1 px-2.5 py-2">
        {Object.entries(calc.inputs).map(([name, value]) => (
          <div key={name} className="flex items-baseline gap-2 text-2xs">
            <dt className="min-w-0 truncate font-mono text-fg-subtle">{name}</dt>
            <dd className="tnum ml-auto shrink-0 font-medium text-fg-muted">{value}</dd>
          </div>
        ))}
      </dl>

      <ol className="space-y-1.5 border-t border-border px-2.5 py-2">
        {calc.steps.map((step) => (
          <li key={step.description}>
            <p className="text-2xs text-fg-subtle">{step.description}</p>
            <p className="mt-0.5 font-mono text-2xs leading-relaxed text-fg">
              {step.expression}
              <span className="text-fg-subtle"> = </span>
              <span className="font-semibold">{step.result}</span>
            </p>
          </li>
        ))}
      </ol>

      {calc.assumptions.length || calc.caveats.length ? (
        <div className="space-y-1 border-t border-border px-2.5 py-2">
          {calc.caveats.map((note) => (
            <p key={note} className="text-2xs leading-relaxed text-warn">
              {note}
            </p>
          ))}
          {calc.assumptions.map((note) => (
            <p key={note} className="text-2xs leading-relaxed text-fg-subtle">
              {note}
            </p>
          ))}
        </div>
      ) : null}

      <p className="border-t border-border bg-surface-raised/40 px-2.5 py-1.5 text-2xs text-fg-subtle">
        {calc.standard_ref}
      </p>
    </div>
  );
}
