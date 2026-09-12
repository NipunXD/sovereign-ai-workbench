"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Ban,
  Download,
  Link2,
  ShieldAlert,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { useState } from "react";

import {
  Button,
  Chip,
  EmptyState,
  Panel,
  PanelHeader,
  Spinner,
} from "@/components/ui/primitives";
import { ApiError, api, getAccessToken } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The audit log.
 *
 * Two things make this more than a table of rows. The chain banner recomputes
 * every hash and reports the exact sequence number where the chain breaks, so
 * "the log has not been altered" is something a reviewer establishes rather
 * than assumes. And denials are surfaced first, because "what did someone try
 * that they were not allowed to do" is the question an auditor actually opens
 * this page to answer.
 *
 * Note what is absent: document contents. The log records that a restricted
 * document was retrieved, never what it said — otherwise audit access would be
 * a way around classification.
 */
export default function AuditPage() {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");
  const [decision, setDecision] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["audit-events", actor, action, decision],
    retry: false,
    queryFn: () =>
      api.auditEvents({
        actor: actor || undefined,
        action: action || undefined,
        decision: decision || undefined,
        limit: 200,
      }),
    refetchInterval: 20_000,
  });

  const { data: summary } = useQuery({
    queryKey: ["audit-summary"],
    queryFn: () => api.auditSummary(24),
    refetchInterval: 30_000,
  });

  const verify = useMutation({ mutationFn: api.verifyChain });

  async function exportLog() {
    // The endpoint is authenticated, so an <a download> cannot fetch it.
    const response = await fetch(api.auditExportUrl(), {
      credentials: "include",
      headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` },
    });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `audit-${new Date().toISOString().slice(0, 10)}.jsonl`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const denials = summary?.recent_denials ?? [];

  // Refused, not empty. A page that says "no matching events" to someone the
  // server has just turned away is lying to them about what happened.
  if (error instanceof ApiError && error.status === 403) {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="max-w-md rounded-xl border border-border bg-surface p-8 text-center shadow-card">
          <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-surface-raised text-fg-subtle"><Ban size={20} /></span>
          <p className="mt-4 text-md font-semibold">The audit log is not yours to read</p>
          <p className="mt-2 text-xs leading-relaxed text-fg-muted">
            Reading it needs the <span className="font-mono">audit:read</span> permission, which
            the demo grants to Internal Audit and nobody else — the people who act are not the
            people who review. This refusal was itself recorded.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mx-auto max-w-6xl space-y-5">
        {/* --- chain integrity --- */}
        <Panel
          className={cn(
            verify.data && !verify.data.valid ? "border-danger/50" : undefined,
          )}
        >
          <div className="flex flex-wrap items-center gap-3 p-4">
            <div className="flex items-center gap-2">
              {verify.data ? (
                verify.data.valid ? (
                  <ShieldCheck size={18} className="text-ok" />
                ) : (
                  <ShieldAlert size={18} className="text-danger" />
                )
              ) : (
                <Link2 size={18} className="text-fg-subtle" />
              )}
              <div>
                <p className="text-xs font-semibold">
                  {verify.data
                    ? verify.data.valid
                      ? "Hash chain verified"
                      : "Hash chain BROKEN"
                    : "Hash chain integrity"}
                </p>
                <p className="mt-0.5 text-2xs text-fg-subtle">
                  {verify.data
                    ? verify.data.detail
                    : "Each record commits to the digest of the one before it, so altering any historical row invalidates every hash after it."}
                </p>
              </div>
            </div>

            <div className="ml-auto flex items-center gap-2">
              <Button
                size="sm"
                variant="primary"
                onClick={() => verify.mutate()}
                disabled={verify.isPending}
              >
                {verify.isPending ? <Spinner /> : <ShieldCheck size={12} />} Verify chain
              </Button>
              <Button size="sm" onClick={() => void exportLog()}>
                <Download size={12} /> Export evidence
              </Button>
            </div>
          </div>

          {verify.data && !verify.data.valid ? (
            <div className="border-t border-danger/40 bg-danger/10 px-3 py-2">
              <p className="text-xs font-medium text-danger">
                Tampering detected at sequence {verify.data.first_invalid_seq}. Treat this
                as a security incident: the log has been modified after the fact.
              </p>
            </div>
          ) : null}
        </Panel>

        {/* --- denials first --- */}
        {denials.length ? (
          <Panel className="border-warn/40">
            <PanelHeader
              title={`Refused actions — last 24 hours (${denials.length})`}
              actions={<TriangleAlert size={11} className="text-warn" />}
            />
            <ul className="divide-y divide-border">
              {denials.slice(0, 5).map((event) => (
                <li key={event.seq} className="flex items-baseline gap-2 px-3 py-1.5 text-xs">
                  <span className="tnum w-10 shrink-0 text-fg-subtle">#{event.seq}</span>
                  <Chip tone="danger">{event.action}</Chip>
                  <span className="shrink-0 font-mono text-fg-muted">
                    {event.actor_username ?? "—"}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-fg-subtle">{event.reason}</span>
                  <span className="shrink-0 text-2xs text-fg-subtle">
                    {new Date(event.ts).toLocaleTimeString()}
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}

        {/* --- activity --- */}
        {summary?.by_action.length ? (
          <Panel>
            <PanelHeader title="Activity — last 24 hours" />
            <div className="grid gap-3 p-3 sm:grid-cols-2">
              <ul className="space-y-1">
                {summary.by_action.slice(0, 8).map(({ action: name, count }) => {
                  const max = summary.by_action[0].count || 1;
                  return (
                    <li key={name} className="flex items-center gap-2 text-2xs">
                      <span className="w-32 shrink-0 truncate font-mono text-fg-muted">
                        {name}
                      </span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-bg">
                        <div
                          className="h-full rounded-full bg-accent"
                          style={{ width: `${(count / max) * 100}%` }}
                        />
                      </div>
                      <span className="tnum w-8 shrink-0 text-right text-fg-subtle">{count}</span>
                    </li>
                  );
                })}
              </ul>
              <div className="flex flex-wrap content-start gap-1.5">
                {Object.entries(summary.by_decision).map(([name, count]) => (
                  <Chip
                    key={name}
                    tone={name === "deny" ? "danger" : name === "error" ? "warn" : "ok"}
                  >
                    {count} {name}
                  </Chip>
                ))}
              </div>
            </div>
          </Panel>
        ) : null}

        {/* --- the log --- */}
        <Panel>
          <PanelHeader title={`Audit log${data ? ` (${data.total})` : ""}`} />

          <div className="flex flex-wrap items-center gap-2 border-b border-border p-2">
            <Select
              value={actor}
              onChange={setActor}
              placeholder="All actors"
              options={data?.actors ?? []}
            />
            <Select
              value={action}
              onChange={setAction}
              placeholder="All actions"
              options={data?.actions ?? []}
            />
            <Select
              value={decision}
              onChange={setDecision}
              placeholder="All outcomes"
              options={["allow", "deny", "error"]}
            />
            {actor || action || decision ? (
              <button
                type="button"
                onClick={() => {
                  setActor("");
                  setAction("");
                  setDecision("");
                }}
                className="flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs text-fg-subtle hover:text-fg"
              >
                <Ban size={9} /> Clear
              </button>
            ) : null}
          </div>

          {isLoading ? (
            <div className="flex justify-center py-8">
              <Spinner className="text-fg-subtle" />
            </div>
          ) : !data?.events.length ? (
            <EmptyState title="No matching events" />
          ) : (
            <table className="w-full table-fixed border-collapse text-xs">
              <thead>
                <tr className="border-b border-border text-2xs uppercase tracking-wider text-fg-subtle">
                  <th className="w-[6%] px-3 py-1.5 text-right font-semibold">Seq</th>
                  <th className="w-[13%] px-2 py-1.5 text-left font-semibold">Time</th>
                  <th className="w-[10%] px-2 py-1.5 text-left font-semibold">Actor</th>
                  <th className="w-[16%] px-2 py-1.5 text-left font-semibold">Action</th>
                  <th className="w-[9%] px-2 py-1.5 text-left font-semibold">Outcome</th>
                  <th className="w-[28%] px-2 py-1.5 text-left font-semibold">Resource</th>
                  <th className="w-[18%] px-3 py-1.5 text-left font-semibold">Chain</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((event) => (
                  <AuditRow
                    key={event.seq}
                    event={event}
                    expanded={expanded === event.seq}
                    onToggle={() => setExpanded(expanded === event.seq ? null : event.seq)}
                  />
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </div>
  );
}

function AuditRow({
  event,
  expanded,
  onToggle,
}: {
  event: AuditEvent;
  expanded: boolean;
  onToggle: () => void;
}) {
  const denied = event.decision === "deny";
  return (
    <>
      <tr
        onClick={onToggle}
        className={cn(
          "cursor-pointer border-b border-border/60 transition-colors hover:bg-surface-raised",
          denied && "bg-danger/5",
          expanded && "bg-surface-raised",
        )}
      >
        <td className="tnum px-3 py-1.5 text-right text-fg-subtle">{event.seq}</td>
        <td className="tnum px-2 py-1.5 text-fg-muted">
          {new Date(event.ts).toLocaleString(undefined, {
            month: "short",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          })}
        </td>
        <td className="truncate px-2 py-1.5 font-mono text-fg-muted">
          {event.actor_username ?? "—"}
        </td>
        <td className="truncate px-2 py-1.5 font-mono text-fg">{event.action}</td>
        <td className="px-2 py-1.5">
          <Chip tone={denied ? "danger" : event.decision === "error" ? "warn" : "ok"}>
            {event.decision}
          </Chip>
        </td>
        <td className="truncate px-2 py-1.5 text-fg-subtle">
          {event.reason ??
            (event.resource_type
              ? `${event.resource_type}${event.resource_id ? ` · ${event.resource_id.slice(0, 18)}` : ""}`
              : "—")}
        </td>
        <td className="truncate px-3 py-1.5 font-mono text-2xs text-fg-subtle">
          {event.hash.slice(0, 10)}…
        </td>
      </tr>

      {expanded ? (
        <tr className="border-b border-border bg-bg">
          <td colSpan={7} className="px-3 py-2">
            <div className="grid gap-3 sm:grid-cols-2">
              <dl className="space-y-1 text-2xs">
                <Field label="Roles" value={event.actor_roles.join(", ") || "—"} />
                <Field label="Run" value={event.run_id ?? "—"} mono />
                <Field label="Model" value={event.model_used ?? "—"} mono />
                <Field label="Lane" value={event.lane ?? "—"} />
                <Field label="Tool" value={event.tool_name ?? "—"} mono />
                <Field
                  label="Latency"
                  value={event.latency_ms != null ? `${event.latency_ms}ms` : "—"}
                />
              </dl>
              <div className="space-y-1 text-2xs">
                <Field label="This hash" value={event.hash} mono wrap />
                <Field label="Previous" value={event.prev_hash} mono wrap />
                {Object.keys(event.metadata).length ? (
                  <div>
                    <dt className="text-fg-subtle">Metadata</dt>
                    <dd>
                      <pre className="mt-0.5 overflow-x-auto rounded border border-border bg-surface p-1.5 font-mono text-2xs text-fg-muted">
                        {JSON.stringify(event.metadata, null, 2)}
                      </pre>
                    </dd>
                  </div>
                ) : null}
              </div>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function Field({
  label,
  value,
  mono,
  wrap,
}: {
  label: string;
  value: string;
  mono?: boolean;
  wrap?: boolean;
}) {
  return (
    <div className="flex gap-2">
      <dt className="w-16 shrink-0 text-fg-subtle">{label}</dt>
      <dd
        className={cn(
          "min-w-0 flex-1 text-fg-muted",
          mono && "font-mono",
          wrap ? "break-all" : "truncate",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function Select({
  value,
  onChange,
  placeholder,
  options,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  options: string[];
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-7 rounded border border-border bg-bg px-1.5 text-xs text-fg focus:border-accent/60"
    >
      <option value="">{placeholder}</option>
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  );
}
