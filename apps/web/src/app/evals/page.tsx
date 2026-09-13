"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, FlaskConical, Minus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Chip, EmptyState, Panel, StatTile } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import type { EvalCase, EvalMetric, EvalSuite } from "@/lib/types";
import { cn, relativeTime } from "@/lib/utils";

/**
 * The measurement harness, on screen.
 *
 * Every accuracy claim this project makes comes from `evals/suites`, and those
 * numbers used to live in timestamped JSON on a laptop. "How do you know it
 * doesn't hallucinate" deserves a page you can open, with the thresholds next
 * to the values — a metric without the bar it had to clear is trivia — and
 * with the failing cases named rather than averaged away.
 */
export default function EvalsPage() {
  const { data, isLoading } = useQuery({ queryKey: ["evals"], queryFn: api.evals });

  const suites = data?.suites ?? [];
  const allPassed = suites.length > 0 && suites.every((s) => s.passed);
  const cases = suites.reduce((n, s) => n + s.cases, 0);
  const failing = suites.reduce((n, s) => n + s.failed_cases.length, 0);

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mx-auto max-w-6xl space-y-5">
        {isLoading ? (
          <div className="grid grid-cols-[repeat(auto-fit,minmax(9.5rem,1fr))] gap-3">
            {[0, 1, 2, 3].map((n) => (
              <div key={n} className="skeleton h-24" />
            ))}
          </div>
        ) : !suites.length ? (
          <Panel>
            <EmptyState
              icon={<FlaskConical size={18} />}
              title="No evaluation results yet"
              hint="Run `make eval` to measure grounding, retrieval, OCR and routing. Results are written to evals/results and appear here."
            />
          </Panel>
        ) : (
          <>
            <div className="stagger grid grid-cols-[repeat(auto-fit,minmax(9.5rem,1fr))] gap-3">
              <StatTile
                value={allPassed ? "All pass" : "Attention"}
                label="Suites"
                hint={`${suites.length} measured`}
                tone={allPassed ? "ok" : "warn"}
              />
              <StatTile value={String(cases)} label="Cases run" />
              <StatTile
                value={String(failing)}
                label="Cases failing"
                tone={failing ? "warn" : undefined}
                hint={failing ? "named in the suite below" : "none"}
              />
              <StatTile
                value={relativeTime(
                  suites.reduce((latest, s) => (s.ran_at > latest ? s.ran_at : latest), suites[0].ran_at),
                )}
                label="Most recent run"
              />
            </div>

            <p className="px-1 text-xs leading-relaxed text-fg-muted">
              These are this system measuring itself. Each suite runs against a fixed set of
              questions with a known answer, and every metric is shown next to the threshold it
              had to clear — a number without its bar means nothing. Re-run them with{" "}
              <code className="rounded-md border border-border bg-surface-raised px-1.5 py-0.5 font-mono text-2xs">
                make eval
              </code>
              .
            </p>

            <div className="grid gap-5 xl:grid-cols-2">
              {suites.map((suite) => (
                <SuiteCard key={suite.suite} suite={suite} />
              ))}
            </div>

            {data?.never_run.length ? (
              <p className="px-1 text-xs text-fg-subtle">
                Never run: {data.never_run.join(", ")}.
              </p>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

function SuiteCard({ suite }: { suite: EvalSuite }) {
  return (
    // Not overflow-hidden: the breakdown popover has to be able to escape the
    // card. The corners are kept by rounding the footer instead.
    <Panel className={cn(!suite.passed && "ring-1 ring-warn/30")}>
      <div className="card-header">
        <div className="min-w-0">
          <p className="card-title flex items-center gap-2">
            {suiteName(suite.suite)}
            <Chip tone={suite.passed ? "ok" : "warn"}>
              {suite.passed ? <Check size={10} /> : <X size={10} />}
              {suite.passed ? "passing" : "below threshold"}
            </Chip>
          </p>
          <p className="mt-1 text-xs leading-relaxed text-fg-muted">{suite.blurb}</p>
        </div>
      </div>

      <table className="w-full border-collapse text-sm">
        <tbody>
          {suite.metrics
            .filter((metric) => !metric.key.startsWith(FAULT_PREFIX))
            .map((metric) => (
              <MetricRow key={metric.key} metric={metric} />
            ))}
        </tbody>
      </table>

      <WordErrorBreakdown metrics={suite.metrics} />

      {suite.failures.length ? (
        <div className="border-t border-border">
          {suite.failures.map((failure) => (
            <FailingCase key={failure.case_id} failure={failure} />
          ))}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-b-xl border-t border-border bg-surface-raised/40 px-4 py-2.5 text-2xs text-fg-subtle">
        <span className="tnum">{suite.cases} cases</span>
        <span className="tnum">{formatSeconds(suite.duration_s)}</span>
        <span>{relativeTime(suite.ran_at)}</span>
        {suite.failed_cases.length && !suite.failures.length ? (
          <span className="font-medium text-warn">failing: {suite.failed_cases.join(", ")}</span>
        ) : null}
        {suite.error ? <span className="font-medium text-danger">{suite.error}</span> : null}
      </div>
    </Panel>
  );
}

const FAULT_PREFIX = "wer_share_";

//: What each bucket means, in the order a reader should meet them: the errors
//: that change meaning first, the ones that change only layout last.
const FAULTS: Array<{ key: string; label: string; blurb: string }> = [
  { key: "misread", label: "Misread", blurb: "the recogniser could not read the word" },
  { key: "one_char", label: "One character off", blurb: "same length, a single character wrong" },
  { key: "missing_extra", label: "Dropped or invented", blurb: "a word absent from one side" },
  { key: "spacing", label: "Spacing only", blurb: "identical characters, a lost or added space" },
];

const FAULT_TONE: Record<string, string> = {
  misread: "bg-danger",
  one_char: "bg-warn",
  missing_extra: "bg-classification-confidential",
  spacing: "bg-info",
};

/**
 * What the wrong words are actually wrong about.
 *
 * Word error rate counts a word as failed if any character in it differs, so a
 * single lost space costs several words while costing almost no character
 * error. On its own the headline reads as "one word in five was misrecognised",
 * which is not what happened — most of those words have identical characters
 * and differ only in where the spaces fell.
 *
 * Shown as a share of the *errors* rather than of the document, so the bars
 * total a hundred. The two accountings do not reconcile exactly — word error
 * rate is an edit distance over sequences, this is a classification of the
 * differing runs — and presenting it as a percentage of the document would
 * invite a subtraction that does not come out.
 */
function WordErrorBreakdown({ metrics }: { metrics: EvalMetric[] }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);

  // A popover rather than an expanding section: the cards sit two to a row and
  // growing one of them by a table's height shoves its neighbour down the page
  // every time somebody looks.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: PointerEvent) => {
      if (!anchor.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const shares = new Map(
    metrics
      .filter((m) => m.key.startsWith(FAULT_PREFIX))
      .map((m) => [m.key.slice(FAULT_PREFIX.length), m.value]),
  );
  if (!shares.size) return null;

  const total = [...shares.values()].reduce((sum, v) => sum + v, 0);
  if (total <= 0) return null;
  const rows = FAULTS.filter((f) => shares.has(f.key)).map((f) => ({
    ...f,
    ofWords: shares.get(f.key) ?? 0,
    ofErrors: (shares.get(f.key) ?? 0) / total,
  }));
  const layout = rows.filter((r) => r.key === "spacing").reduce((sum, r) => sum + r.ofErrors, 0);

  return (
    <div ref={anchor} className="relative border-t border-border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(
          "flex w-full items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-surface-raised",
          open && "bg-surface-raised",
        )}
      >
        <ChevronDown
          size={13}
          className={cn("shrink-0 text-fg-subtle transition-transform", open && "rotate-180")}
        />
        <span className="text-xs font-medium text-fg-muted">What the word errors are</span>
        <span className="tnum ml-auto text-2xs text-fg-subtle">
          {(layout * 100).toFixed(0)}% are spacing, not misreading
        </span>
      </button>

      {open ? (
        <div className="absolute right-2 top-full z-30 mt-1 w-[min(26rem,calc(100vw-3rem))] animate-slide-up space-y-3 rounded-xl border border-border bg-surface p-4 shadow-popover">
          <div className="flex h-2 gap-0.5 overflow-hidden rounded-full bg-surface-sunken">
            {rows
              .filter((r) => r.ofErrors > 0)
              .map((r) => (
                <span
                  key={r.key}
                  className={cn("h-full rounded-full", FAULT_TONE[r.key])}
                  style={{ width: `${r.ofErrors * 100}%` }}
                  title={`${r.label}: ${(r.ofErrors * 100).toFixed(1)}% of word errors`}
                />
              ))}
          </div>

          {/* table-fixed, so the description column is the one that gives way.
              Left to itself the no-wrap numeric cells widened the table past
              the popover and off the side of the screen. */}
          <table className="w-full table-fixed border-collapse text-xs">
            <caption className="sr-only">
              What the word errors are, as a share of the errors and of all words
            </caption>
            <thead>
              <tr className="text-2xs uppercase tracking-wider text-fg-subtle">
                <th className="py-1 text-left font-semibold">Fault</th>
                <th className="w-[4.5rem] py-1 pl-2 text-right font-semibold">Errors</th>
                <th className="w-[4.5rem] py-1 pl-2 text-right font-semibold">Words</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className="border-t border-border/70">
                  <td className="py-1.5" title={`${r.label} — ${r.blurb}`}>
                    <span className="flex min-w-0 items-center gap-2">
                      <span className={cn("h-2 w-2 shrink-0 rounded-full", FAULT_TONE[r.key])} />
                      <span className="shrink-0 whitespace-nowrap text-fg">{r.label}</span>
                      <span className="min-w-0 truncate text-2xs text-fg-subtle">{r.blurb}</span>
                    </span>
                  </td>
                  <td className="tnum whitespace-nowrap py-1.5 pl-2 text-right font-semibold text-fg">
                    {(r.ofErrors * 100).toFixed(1)}%
                  </td>
                  <td className="tnum whitespace-nowrap py-1.5 pl-2 text-right text-fg-subtle">
                    {(r.ofWords * 100).toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <p className="text-2xs leading-relaxed text-fg-subtle">
            Word error rate fails a word if any character in it differs, so one lost space costs
            several words and almost no character error. Punctuation and case are folded away
            before scoring and cannot appear here.
          </p>
        </div>
      ) : null}
    </div>
  );
}

/**
 * A case that did not pass, opened up.
 *
 * A bare case id is an accusation nobody can check. The question, what it was
 * meant to do, what it did instead and the file it is defined in are all
 * recoverable, so a reader can decide for themselves whether the failure is
 * the system's or the test's — which is the only way a red mark on a
 * self-reported metric means anything.
 */
function FailingCase({ failure }: { failure: EvalCase }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-border/70 last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left transition-colors hover:bg-surface-raised"
        title={failure.prompt || undefined}
      >
        <ChevronDown
          size={13}
          className={cn("shrink-0 text-fg-subtle transition-transform", open && "rotate-180")}
        />
        <span className="rounded-full bg-warn/10 px-2 py-0.5 font-mono text-2xs font-semibold text-warn ring-1 ring-inset ring-warn/20">
          {failure.case_id}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs text-fg-muted">
          {failure.prompt || failure.detail || "failed"}
        </span>
      </button>

      {open ? (
        <dl className="space-y-3 bg-surface-raised/40 px-4 pb-4 pt-1 text-xs">
          {failure.prompt ? (
            <Field term="Asked">
              <span className="text-fg">{failure.prompt}</span>
            </Field>
          ) : null}
          {failure.expectation ? (
            <Field term="Expected">
              <span className="text-fg-muted">{failure.expectation}</span>
            </Field>
          ) : null}
          {failure.detail ? (
            <Field term="Why it failed">
              <span className="text-warn">{failure.detail}</span>
            </Field>
          ) : null}
          {failure.actual ? (
            <Field term="What it answered">
              <span className="block rounded-lg border-l-2 border-warn/50 bg-warn/[0.06] px-3 py-2 leading-relaxed text-fg-muted">
                {failure.actual}
              </span>
            </Field>
          ) : null}
          {failure.source ? (
            <Field term="Defined in">
              <span className="font-mono text-2xs text-fg-subtle">{failure.source}</span>
            </Field>
          ) : null}
        </dl>
      ) : null}
    </div>
  );
}

function Field({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[7.5rem_minmax(0,1fr)] sm:gap-3">
      <dt className="section-label sm:pt-0.5">{term}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

function MetricRow({ metric }: { metric: EvalMetric }) {
  return (
    <tr className="border-b border-border/70 last:border-b-0">
      <td className="px-4 py-2.5 text-fg-muted">{label(metric.key)}</td>
      <td className="tnum px-3 py-2.5 text-right font-semibold text-fg">{format(metric)}</td>
      <td className="tnum whitespace-nowrap px-3 py-2.5 text-right text-2xs text-fg-subtle">
        {metric.threshold ?? "tracked"}
      </td>
      <td className="w-9 px-3 py-2.5">
        {metric.passed === null ? (
          <Minus size={12} className="text-fg-subtle/60" aria-label="no threshold" />
        ) : metric.passed ? (
          <Check size={13} className="text-ok" aria-label="within threshold" />
        ) : (
          <X size={13} className="text-danger" aria-label="outside threshold" />
        )}
      </td>
    </tr>
  );
}

//: "Ocr" is not a word. Title-casing the key is right for everything else.
const SUITE_NAMES: Record<string, string> = { ocr: "OCR", rag: "RAG" };

function suiteName(suite: string): string {
  return SUITE_NAMES[suite] ?? suite.replace(/^./, (c) => c.toUpperCase());
}

/** Metric keys are written for the harness. These are written for a reader. */
const LABELS: Record<string, string> = {
  answerable_accuracy: "Answerable questions answered",
  cer: "Character error rate",
  cer_no_spaces: "Character error rate, ignoring spaces",
  classifier_p95_ms: "Classifier p95",
  escalation_rate: "Pages escalated to vision",
  failed_run_rate: "Runs that failed outright",
  fast_path_p50_ms: "Fast path p50",
  fast_path_p95_ms: "Fast path p95",
  hallucinated_citation_rate: "Citations pointing at nothing",
  hybrid_share: "Hits found by hybrid search",
  lane_accuracy: "Requests routed to the right lane",
  mean_confidence: "Mean OCR confidence",
  mean_grounded_ratio: "Mean share of claims cited",
  mrr: "MRR",
  ndcg_at_10: "nDCG@10",
  p95_latency_s: "Retrieval p95",
  p95_s: "Answer p95",
  recall_at_5: "Recall@5",
  recall_at_10: "Recall@10",
  refusal_rate: "Unanswerable questions refused",
  render_dpi: "Render DPI",
  stage0_share: "Decided without a model",
  stage2_share: "Needed the classifier",
  vision_capability_rate: "Vision requests given a vision model",
  wer: "Word error rate",
};

function label(key: string): string {
  return LABELS[key] ?? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function format(metric: EvalMetric): string {
  const { key, value } = metric;
  // Sub-millisecond needs its decimals; ten seconds expressed in milliseconds
  // is a number nobody reads as ten seconds.
  if (key.endsWith("_ms")) {
    if (value >= 1000) return formatSeconds(value / 1000);
    return `${value < 1 ? value.toFixed(3) : value.toFixed(0)} ms`;
  }
  if (key.endsWith("_s")) return formatSeconds(value);
  if (key === "render_dpi") return String(value);
  // Everything else in these suites is a 0..1 proportion — accuracy, recall,
  // error rates and shares alike — and reads far better as a percentage.
  if (value >= 0 && value <= 1) return `${(value * 100).toFixed(value < 0.1 ? 2 : 1)}%`;
  return value.toFixed(4).replace(/\.?0+$/, "");
}

function formatSeconds(value: number): string {
  if (value < 1) return `${(value * 1000).toFixed(0)} ms`;
  if (value < 60) return `${value.toFixed(value < 10 ? 2 : 0)} s`;
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}
