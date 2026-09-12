"use client";

import { useQuery } from "@tanstack/react-query";
import { Cpu } from "lucide-react";
import { useState } from "react";

import { StatusDot } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Live system health, including how much of the memory budget is spoken for.
 *
 * The residency meter is here rather than buried in an admin page because on a
 * 24 GB machine it is the number that explains why an answer was slow: a cold
 * model load costs several seconds, and seeing "13.1 / 14.0 GB" makes that
 * legible instead of mysterious.
 */
export function SystemStatus() {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["readiness"],
    queryFn: api.readiness,
    refetchInterval: 15_000,
  });

  const components = data?.components;
  const providers = Object.entries(components?.providers ?? {});
  const healthy = data?.status === "ready";
  const residency = components?.residency;
  const usedFraction = residency ? residency.resident_gb / residency.max_resident_gb : 0;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex h-7 items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 text-2xs font-medium text-fg-muted shadow-xs transition-colors hover:border-border-strong"
        title="System status"
      >
        <StatusDot state={healthy ? "ok" : "warn"} pulse={!healthy} />
        {residency ? (
          <span className="tnum">
            {residency.resident_gb.toFixed(1)}/{residency.max_resident_gb.toFixed(0)} GB
          </span>
        ) : (
          <span>system</span>
        )}
      </button>

      {open ? (
        <div className="absolute right-0 top-9 z-50 w-72 animate-slide-up rounded-xl border border-border bg-surface p-3.5 shadow-popover">
          <p className="mb-2 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
            Backends
          </p>
          <ul className="space-y-1">
            {providers.map(([name, state]) => (
              <li key={name} className="flex items-center justify-between text-xs">
                <span className="flex items-center gap-1.5 text-fg-muted">
                  <StatusDot state={state.healthy ? "ok" : "danger"} />
                  {name}
                </span>
                <span className="tnum text-fg-subtle">
                  {state.latency_ms != null ? `${state.latency_ms}ms` : "—"}
                </span>
              </li>
            ))}
            {(["database", "vector_store"] as const).map((key) => (
              <li key={key} className="flex items-center justify-between text-xs">
                <span className="flex items-center gap-1.5 text-fg-muted">
                  <StatusDot state={components?.[key]?.healthy ? "ok" : "danger"} />
                  {key.replace("_", " ")}
                </span>
                <span className="tnum text-fg-subtle">
                  {components?.[key]?.latency_ms != null
                    ? `${components[key]?.latency_ms}ms`
                    : "—"}
                </span>
              </li>
            ))}
          </ul>

          {residency ? (
            <div className="mt-3 border-t border-border pt-2">
              <div className="mb-1.5 flex items-center justify-between text-2xs">
                <span className="flex items-center gap-1 font-semibold uppercase tracking-wider text-fg-subtle">
                  <Cpu size={10} /> Model residency
                </span>
                <span className="tnum text-fg-muted">
                  {residency.resident_gb.toFixed(1)} / {residency.max_resident_gb.toFixed(1)} GB
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-surface-sunken">
                <div
                  className={cn(
                    "h-full rounded-full transition-all",
                    usedFraction > 0.9 ? "bg-danger" : usedFraction > 0.7 ? "bg-warn" : "bg-ok",
                  )}
                  style={{ width: `${Math.min(100, usedFraction * 100)}%` }}
                />
              </div>
              <ul className="mt-2 space-y-0.5">
                {residency.models.map((model) => (
                  <li key={model.logical_name} className="flex items-center justify-between text-2xs">
                    <span className="font-mono text-fg-muted">
                      {model.logical_name}
                      {model.pinned ? <span className="ml-1 text-accent">pinned</span> : null}
                    </span>
                    <span className="tnum text-fg-subtle">{model.size_gb.toFixed(1)} GB</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
