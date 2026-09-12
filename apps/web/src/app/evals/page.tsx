"use client";

import { useQuery } from "@tanstack/react-query";
import { Check, FlaskConical, Minus, X } from "lucide-react";

import { Chip, EmptyState, Panel, StatTile } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import type { EvalMetric, EvalSuite } from "@/lib/types";
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
    <Panel className={cn("overflow-hidden", !suite.passed && "ring-1 ring-warn/30")}>
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
          {suite.metrics.map((metric) => (
            <MetricRow key={metric.key} metric={metric} />
          ))}
        </tbody>
      </table>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border bg-surface-raised/40 px-4 py-2.5 text-2xs text-fg-subtle">
        <span className="tnum">{suite.cases} cases</span>
        <span className="tnum">{formatSeconds(suite.duration_s)}</span>
        <span>{relativeTime(suite.ran_at)}</span>
        {suite.failed_cases.length ? (
          <span className="font-medium text-warn">
            failing: {suite.failed_cases.join(", ")}
          </span>
        ) : null}
        {suite.error ? <span className="font-medium text-danger">{suite.error}</span> : null}
      </div>
    </Panel>
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
