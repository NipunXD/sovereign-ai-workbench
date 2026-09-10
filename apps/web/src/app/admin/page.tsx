"use client";

import { useQuery } from "@tanstack/react-query";

import { Chip, Panel, PanelHeader, Spinner, StatusDot } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * The model panel.
 *
 * This is where "model-agnostic" stops being a claim: the manifest is data, the
 * lanes are data, and the same request is served by whichever backend the
 * manifest points at. Seeing text lanes on one provider and the vision lane on
 * another, live, is the proof.
 */
export default function AdminPage() {
  const { data: models, isLoading } = useQuery({
    queryKey: ["models"],
    queryFn: api.models,
    refetchInterval: 10_000,
  });
  const { data: routing } = useQuery({
    queryKey: ["routing-stats"],
    queryFn: api.routingStats,
    refetchInterval: 10_000,
  });

  if (isLoading || !models) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner className="text-fg-subtle" />
      </div>
    );
  }

  const residency = models.residency;

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mx-auto max-w-6xl space-y-4">
        <div className="grid gap-4 lg:grid-cols-3">
          <Panel className="lg:col-span-2">
            <PanelHeader
              title="Model manifest"
              actions={
                <span className="font-mono text-2xs normal-case text-fg-subtle">
                  profile {models.profile} · {models.manifest_digest.slice(0, 12)}
                </span>
              }
            />
            <table className="w-full border-collapse text-xs">
              <thead>
                <tr className="border-b border-border text-2xs uppercase tracking-wider text-fg-subtle">
                  <th className="px-3 py-1.5 text-left font-semibold">Logical</th>
                  <th className="px-2 py-1.5 text-left font-semibold">Backend</th>
                  <th className="px-2 py-1.5 text-left font-semibold">Model</th>
                  <th className="px-2 py-1.5 text-right font-semibold">Context</th>
                  <th className="px-2 py-1.5 text-right font-semibold">RAM</th>
                  <th className="px-3 py-1.5 text-right font-semibold">State</th>
                </tr>
              </thead>
              <tbody>
                {models.models.map((model) => (
                  <tr key={model.logical_name} className="border-b border-border/60">
                    <td className="px-3 py-1.5 font-mono text-fg">{model.logical_name}</td>
                    <td className="px-2 py-1.5">
                      <Chip tone={model.provider === "mock" ? "neutral" : "accent"}>
                        {model.provider}
                      </Chip>
                    </td>
                    <td className="px-2 py-1.5 font-mono text-fg-muted">{model.physical_id}</td>
                    <td className="tnum px-2 py-1.5 text-right text-fg-muted">
                      {(model.context_window / 1024).toFixed(0)}k
                    </td>
                    <td className="tnum px-2 py-1.5 text-right text-fg-muted">
                      {model.approx_ram_gb.toFixed(1)}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {model.pinned ? (
                        <Chip tone="accent">pinned</Chip>
                      ) : model.resident ? (
                        <Chip tone="ok">resident</Chip>
                      ) : (
                        <span className="text-2xs text-fg-subtle">cold</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>

          <div className="space-y-4">
            <Panel>
              <PanelHeader title="Backends" />
              <ul className="divide-y divide-border">
                {Object.entries(models.providers).map(([name, state]) => (
                  <li key={name} className="flex items-center justify-between px-3 py-2 text-xs">
                    <span className="flex items-center gap-1.5">
                      <StatusDot state={state.healthy ? "ok" : "danger"} />
                      <span className="font-mono">{name}</span>
                    </span>
                    <span className="tnum text-2xs text-fg-subtle">{state.latency_ms}ms</span>
                  </li>
                ))}
              </ul>
            </Panel>

            {residency ? (
              <Panel>
                <PanelHeader
                  title="Memory budget"
                  actions={
                    <span className="tnum normal-case text-fg-muted">
                      {residency.resident_gb.toFixed(1)} / {residency.max_resident_gb.toFixed(1)} GB
                    </span>
                  }
                />
                <div className="p-3">
                  <div className="h-2 overflow-hidden rounded-full bg-bg">
                    <div
                      className={cn(
                        "h-full rounded-full transition-all",
                        residency.resident_gb / residency.max_resident_gb > 0.9
                          ? "bg-danger"
                          : "bg-ok",
                      )}
                      style={{
                        width: `${Math.min(100, (residency.resident_gb / residency.max_resident_gb) * 100)}%`,
                      }}
                    />
                  </div>
                  <p className="mt-2 text-2xs leading-relaxed text-fg-subtle">
                    A cold model load costs several seconds on this hardware, so routing
                    prefers a resident model that is good enough over a marginally better
                    one that is not. Swapping is{" "}
                    {residency.allow_swap ? "permitted" : "disabled for this profile"}.
                  </p>
                </div>
              </Panel>
            ) : null}
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Panel>
            <PanelHeader title="Routing lanes" />
            <ul className="divide-y divide-border">
              {Object.entries(models.lanes).map(([lane, candidates]) => (
                <li key={lane} className="px-3 py-2">
                  <p className="mb-1 font-mono text-xs text-accent">{lane}</p>
                  <div className="flex flex-wrap gap-1">
                    {candidates.map((candidate, index) => (
                      <Chip key={candidate} tone={index === 0 ? "accent" : "neutral"}>
                        {index === 0 ? "1st " : `${index + 1}. `}
                        {candidate}
                      </Chip>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel>
            <PanelHeader title="Router behaviour" />
            {routing && routing.decisions ? (
              <div className="space-y-3 p-3">
                <div className="grid grid-cols-3 gap-2">
                  <Metric label="Decisions" value={String(routing.decisions)} />
                  <Metric label="p50" value={`${routing.decide_p50_ms.toFixed(2)}ms`} />
                  <Metric label="p95" value={`${routing.decide_p95_ms.toFixed(2)}ms`} />
                </div>
                <div>
                  <p className="mb-1 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
                    Decided at each stage
                  </p>
                  <ul className="space-y-0.5">
                    {Object.entries(routing.by_stage).map(([stage, count]) => (
                      <li key={stage} className="flex items-center gap-2 text-2xs">
                        <span className="w-28 text-fg-muted">
                          {stage === "0"
                            ? "0 · deterministic"
                            : stage === "1"
                              ? "1 · lexical"
                              : "2 · classifier"}
                        </span>
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-bg">
                          <div
                            className="h-full rounded-full bg-accent"
                            style={{
                              width: `${routing.decisions ? (count / routing.decisions) * 100 : 0}%`,
                            }}
                          />
                        </div>
                        <span className="tnum w-6 text-right text-fg-subtle">{count}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            ) : (
              <p className="p-3 text-xs text-fg-subtle">
                No routing decisions yet — ask a question on the workbench.
              </p>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-border bg-bg px-2 py-1.5">
      <p className="text-2xs uppercase tracking-wider text-fg-subtle">{label}</p>
      <p className="tnum mt-0.5 text-sm font-semibold text-fg">{value}</p>
    </div>
  );
}
