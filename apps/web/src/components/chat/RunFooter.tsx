"use client";

import {
  AlertTriangle,
  Check,
  ChevronDown,
  Clock,
  Database,
  Hash,
  Sigma,
  Wrench,
  X,
} from "lucide-react";
import { useState } from "react";

import { cn, formatDuration } from "@/lib/utils";
import type { CheckedFigure, RunSummary, ValidationReport } from "@/lib/types";

/**
 * How much to trust this answer, and why.
 *
 * Three different questions, deliberately kept apart rather than averaged into
 * one score:
 *
 *  - Is each claim cited? — the grounding ring.
 *  - Does each cited passage exist? — invented references, removed and counted.
 *  - **Did the numbers come from the documents?** — every measurement in the
 *    answer looked for, character for character, in the passage it cites.
 *
 * The third is the one an inspector actually asks, and the one the other two
 * cannot answer: a model can attach a real citation to a figure it invented,
 * and a citation-presence check passes that answer happily. It is also the
 * only one of the three a reader can confirm by eye — the source panel is one
 * click away, and the number is either on that page or it is not.
 */
export function RunFooter({
  summary,
  validation,
}: {
  summary: RunSummary;
  validation: ValidationReport | null;
}) {
  const [open, setOpen] = useState(false);

  const refusal = validation?.is_refusal ?? false;
  const grounded = validation ? Math.round(validation.grounded_ratio * 100) : null;
  const figures = validation?.figures ?? [];
  const verified = figures.filter((f) => f.found).length;
  // A derived number is in no document by construction. Counting it against
  // the answer showed the calculator's own output as the answer's weak point.
  const derived = figures.filter((f) => !f.found && f.computed).length;
  const unsupported = validation?.unsupported ?? [];
  const invented = validation?.unresolved_citations?.length ?? 0;
  // A run that ended early — refused by policy, or replayed from a trace
  // written before a field existed — has no budget. Reading through it
  // unguarded took the whole page down with an unhandled TypeError.
  const toolCalls = summary.budget?.tool_calls_used ?? 0;

  const hasDetail = figures.length > 0 || unsupported.length > 0 || invented > 0;

  return (
    <div className="mt-4 border-t border-border pt-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-2xs text-fg-subtle">
        {refusal ? (
          <span className="flex items-center gap-1.5 rounded-full bg-warn/10 px-2.5 py-1 font-semibold text-warn ring-1 ring-inset ring-warn/20">
            declined — not in the corpus
          </span>
        ) : grounded !== null ? (
          <GroundingRing value={grounded} />
        ) : null}

        {figures.length ? (
          <FigureBadge verified={verified} derived={derived} total={figures.length} />
        ) : null}

        <Stat icon={<Database size={12} />} label={`${summary.evidence_used} passages read`} />
        <Stat
          icon={<Wrench size={12} />}
          label={`${toolCalls} tool call${toolCalls === 1 ? "" : "s"}`}
        />
        <Stat icon={<Clock size={12} />} label={formatDuration(summary.wall_ms)} />

        {invented ? (
          <span className="font-medium text-danger">
            {invented} invented reference{invented === 1 ? "" : "s"} removed
          </span>
        ) : null}

        {hasDetail ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="ml-auto flex items-center gap-1 rounded-md px-1.5 py-0.5 font-medium text-fg-subtle transition-colors hover:bg-surface-raised hover:text-fg"
          >
            <ChevronDown size={12} className={cn("transition-transform", open && "rotate-180")} />
            {open ? "Hide check" : "How this was checked"}
          </button>
        ) : null}
      </div>

      {open && hasDetail ? (
        <div className="mt-3 space-y-3 rounded-xl border border-border bg-surface-raised/40 p-3.5">
          {figures.length ? (
            <section>
              <p className="section-label mb-2 flex items-center gap-1.5">
                <Hash size={11} /> Figures in this answer
              </p>
              <ul className="space-y-1">
                {figures.map((figure) => (
                  <FigureRow key={`${figure.value}-${figure.unit}`} figure={figure} />
                ))}
              </ul>
              <p className="mt-2 text-2xs leading-relaxed text-fg-subtle">
                Each measurement is searched for in the passage it cites, exactly as written.
                A figure the calculator produced is marked as calculated, with its working in
                the trace. Anything left is in neither — open the source and check it.
              </p>
            </section>
          ) : null}

          {unsupported.length ? (
            <section className={figures.length ? "border-t border-border pt-3" : undefined}>
              <p className="section-label mb-2 flex items-center gap-1.5 text-warn">
                <AlertTriangle size={11} />
                {unsupported.length} passage{unsupported.length === 1 ? "" : "s"} with no citation
              </p>
              <ul className="space-y-1.5">
                {unsupported.map((text) => (
                  <li
                    key={text.slice(0, 48)}
                    className="rounded-lg border-l-2 border-warn/50 bg-warn/[0.06] px-3 py-2 text-xs leading-relaxed text-fg-muted"
                  >
                    {plain(text)}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {invented ? (
            <section className="border-t border-border pt-3">
              <p className="section-label mb-1.5 flex items-center gap-1.5 text-danger">
                <X size={11} /> {invented} reference{invented === 1 ? "" : "s"} pointed at nothing
              </p>
              <p className="text-2xs leading-relaxed text-fg-subtle">
                The model cited a passage that was never retrieved. Those markers were removed
                from the answer rather than shown — a reference to nothing looks like evidence.
              </p>
            </section>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Stat({ icon, label }: { icon: React.ReactNode; label: string }) {
  return (
    <span className="tnum flex items-center gap-1.5">
      {icon}
      {label}
    </span>
  );
}

function FigureBadge({
  verified,
  derived,
  total,
}: {
  verified: number;
  derived: number;
  total: number;
}) {
  const accounted = verified + derived;
  const all = accounted === total;
  return (
    <span
      className={cn(
        "flex items-center gap-1.5 rounded-full px-2.5 py-1 font-semibold ring-1 ring-inset",
        all ? "bg-ok/10 text-ok ring-ok/20" : "bg-warn/10 text-warn ring-warn/20",
      )}
      title={
        derived
          ? `${verified} found character-for-character in a cited passage, ${derived} calculated during this run`
          : "Measurements found character-for-character in a cited passage"
      }
    >
      {all ? <Check size={11} /> : <AlertTriangle size={11} />}
      <span className="tnum">
        {accounted}/{total}
      </span>
      {derived ? (
        <>
          figure{total === 1 ? "" : "s"} traced
          <span className="font-normal opacity-70">
            ({verified} cited · {derived} calculated)
          </span>
        </>
      ) : (
        <>figure{total === 1 ? "" : "s"} in sources</>
      )}
    </span>
  );
}

function FigureRow({ figure }: { figure: CheckedFigure }) {
  // A number the calculator derived is in no document by construction, so it
  // gets its own mark rather than the warning meant for an unsourced figure.
  const computed = !figure.found && Boolean(figure.computed);
  return (
    <li className="flex items-baseline gap-2 text-xs">
      <span
        className={cn(
          "flex h-4 w-4 shrink-0 items-center justify-center rounded-full",
          figure.found
            ? "bg-ok/15 text-ok"
            : computed
              ? "bg-accent/15 text-accent"
              : "bg-warn/15 text-warn",
        )}
        aria-hidden
      >
        {figure.found ? (
          <Check size={10} />
        ) : computed ? (
          <Sigma size={10} />
        ) : (
          <AlertTriangle size={9} />
        )}
      </span>
      <span className="tnum font-mono font-medium text-fg">{figure.text}</span>
      <span className="min-w-0 flex-1 truncate text-fg-subtle">
        {figure.found ? (
          <>
            found in {figure.sources.length === 1 ? "source" : "sources"}{" "}
            {figure.sources.map((n) => `[${n}]`).join(" ")}
          </>
        ) : computed ? (
          <>calculated in this run by {figure.computed}</>
        ) : (
          "not found in any cited source"
        )}
      </span>
    </li>
  );
}

/** The answer is markdown; these excerpts are quoted, so drop the syntax. */
function plain(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/(^|\s)[*_](\S.*?\S)[*_](\s|$)/g, "$1$2$3")
    .replace(/`([^`]+)`/g, "$1")
    .trim();
}

export function GroundingRing({ value, size = 22 }: { value: number; size?: number }) {
  const r = (size - 4) / 2;
  const c = 2 * Math.PI * r;
  const good = value >= 80;
  const mid = value >= 50;
  const tone = good ? "text-ok" : mid ? "text-warn" : "text-danger";
  return (
    <span
      className={cn("flex items-center gap-1.5 font-semibold", tone)}
      title="Share of the answer's claims that carry a citation"
    >
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
