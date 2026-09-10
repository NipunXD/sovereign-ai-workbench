"use client";

import { useEffect, useState } from "react";
import { ShieldCheck, ShieldAlert } from "lucide-react";

import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { EgressReport } from "@/lib/types";

/**
 * The sovereignty indicator.
 *
 * Every other product like this asserts "your data stays private" in marketing
 * copy. This reads the live egress report and shows the actual list of hosts
 * the deployment is configured to reach — which is a claim someone can check
 * while standing in front of it, and which would go red the moment an external
 * destination appeared in the configuration.
 */
export function SovereigntyBar() {
  const [report, setReport] = useState<EgressReport | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    api.egress().then(setReport).catch(() => setReport(null));
  }, []);

  const sovereign = report?.all_private && report.external_ai_services.length === 0;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "flex h-6 items-center gap-1.5 rounded border px-2 text-2xs font-semibold uppercase tracking-wider transition-colors",
          sovereign
            ? "border-ok/40 bg-ok/10 text-ok hover:bg-ok/20"
            : "border-danger/40 bg-danger/10 text-danger hover:bg-danger/20",
        )}
        title="Show every network destination this deployment can reach"
      >
        {sovereign ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />}
        {sovereign ? "air-gapped" : "egress unverified"}
      </button>

      {open && report ? (
        <div className="absolute right-0 top-7 z-50 w-80 animate-slide-up rounded-md border border-border bg-surface-raised p-3 shadow-xl">
          <p className="mb-2 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
            Configured destinations
          </p>
          <ul className="space-y-1">
            {report.destinations.map((host) => (
              <li key={host} className="flex items-center gap-2 font-mono text-xs text-fg-muted">
                <span className="h-1 w-1 rounded-full bg-ok" aria-hidden />
                {host}
              </li>
            ))}
          </ul>
          <div className="mt-3 border-t border-border pt-2">
            <p className="flex items-center justify-between text-xs">
              <span className="text-fg-subtle">External AI services</span>
              <span className={report.external_ai_services.length ? "text-danger" : "text-ok"}>
                {report.external_ai_services.length || "none"}
              </span>
            </p>
            <p className="mt-2 text-2xs leading-relaxed text-fg-subtle">{report.note}</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
