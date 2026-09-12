"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Download,
  FileSpreadsheet,
  FileText,
  ShieldCheck,
  Terminal,
  XCircle,
} from "lucide-react";
import { useState } from "react";

import { KindGlyph } from "@/components/chat/ArtifactCard";
import {
  Button,
  Chip,
  EmptyState,
  Panel,
  PanelHeader,
  Spinner,
} from "@/components/ui/primitives";
import { api, downloadArtifact } from "@/lib/api";
import type { Approval, Artifact } from "@/lib/types";
import { cn, formatBytes, relativeTime } from "@/lib/utils";
import { useSession } from "@/stores/session";

/**
 * The approval queue and the artifacts it governs.
 *
 * The separation-of-duties rule is visible here rather than merely enforced:
 * a request you raised yourself shows why you cannot decide it, instead of
 * silently omitting the buttons and leaving you to wonder.
 */
export default function ApprovalsPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<"pending" | "all">("pending");

  const approvals = useQuery({
    queryKey: ["approvals", statusFilter],
    queryFn: () => api.approvals(statusFilter),
    refetchInterval: 5_000,
  });

  const artifacts = useQuery({
    queryKey: ["artifacts"],
    queryFn: () => api.artifacts(),
    refetchInterval: 10_000,
  });

  const decide = useMutation({
    mutationFn: ({ id, approved, comment }: { id: string; approved: boolean; comment: string }) =>
      api.decideApproval(id, approved, comment),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["approvals"] });
      void queryClient.invalidateQueries({ queryKey: ["artifacts"] });
    },
  });

  const mayApprove = can("artifact:approve");
  const pending = (approvals.data ?? []).filter((a) => a.status === "pending");

  return (
    <div className="h-full overflow-y-auto p-5">
      <div className="mx-auto max-w-6xl space-y-5">
        <Panel className={pending.length ? "ring-1 ring-accent/25" : undefined}>
          <PanelHeader
            title={`Approval queue${pending.length ? ` — ${pending.length} waiting` : ""}`}
            actions={
              <div className="flex items-center gap-0.5 rounded-lg bg-surface-raised p-0.5">
                {(["pending", "all"] as const).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setStatusFilter(value)}
                    className={cn(
                      "rounded-md px-2 py-1 text-2xs font-semibold capitalize transition-colors",
                      statusFilter === value
                        ? "bg-accent text-accent-fg shadow-xs"
                        : "text-fg-subtle hover:bg-surface-raised hover:text-fg",
                    )}
                  >
                    {value}
                  </button>
                ))}
              </div>
            }
          />

          {approvals.isLoading ? (
            <div className="flex justify-center py-8">
              <Spinner className="text-fg-subtle" />
            </div>
          ) : !approvals.data?.length ? (
            <EmptyState
              icon={<ShieldCheck size={20} />}
              title="Nothing waiting"
              hint={
                mayApprove
                  ? "Generated documents and code execution pause here until someone signs them off."
                  : "Requests you raise appear here while they wait for an approver."
              }
            />
          ) : (
            <ul className="divide-y divide-border">
              {approvals.data.map((approval) => (
                <ApprovalRow
                  key={approval.id}
                  approval={approval}
                  mayApprove={mayApprove}
                  busy={decide.isPending}
                  onDecide={(approved, comment) =>
                    decide.mutate({ id: approval.id, approved, comment })
                  }
                />
              ))}
            </ul>
          )}

          {decide.error ? (
            <p className="border-t border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
              {(decide.error as Error).message}
            </p>
          ) : null}
        </Panel>

        <Panel>
          <PanelHeader title={`Generated documents${artifacts.data ? ` (${artifacts.data.length})` : ""}`} />
          {!artifacts.data?.length ? (
            <EmptyState
              title="No documents generated yet"
              hint="Ask the workbench for a report or a workbook and it will appear here once approved."
            />
          ) : (
            <ul className="stagger grid grid-cols-[repeat(auto-fill,minmax(17rem,1fr))] gap-3 p-4">
              {artifacts.data.map((artifact) => (
                <ArtifactTile key={artifact.sha256} artifact={artifact} />
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

function ApprovalRow({
  approval,
  mayApprove,
  busy,
  onDecide,
}: {
  approval: Approval;
  mayApprove: boolean;
  busy: boolean;
  onDecide: (approved: boolean, comment: string) => void;
}) {
  const [comment, setComment] = useState("");
  const summary = approval.payload_summary as {
    tool?: string;
    question?: string;
    description?: string;
  };

  return (
    <li className="p-4">
      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        <ToolIcon tool={summary.tool ?? approval.subject_id} />
        <span className="font-mono text-xs text-fg">{summary.tool ?? approval.subject_id}</span>
        <Chip tone={approval.kind === "tool" ? "accent" : "neutral"}>{approval.kind}</Chip>
        <StatusChip status={approval.status} />
        <span className="ml-auto text-2xs text-fg-subtle">
          {approval.requested_by_name ?? "unknown"} · {relativeTime(approval.requested_at)}
        </span>
      </div>

      {summary.question ? (
        <p className="mb-2.5 rounded-lg border border-border bg-surface-raised px-3 py-2 text-xs italic text-fg-muted">
          “{summary.question}”
        </p>
      ) : null}

      {approval.status === "pending" ? (
        approval.is_own_request ? (
          // Stated rather than silently omitted: the rule is the point.
          <p className="flex items-center gap-1.5 text-2xs text-fg-subtle">
            <AlertTriangle size={11} className="text-warn" />
            You raised this request, so it needs a second person. Approval means
            somebody else looked.
          </p>
        ) : mayApprove ? (
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="Note for the record (optional)"
              className="h-8 min-w-0 flex-1 rounded-lg border border-border bg-surface px-2.5 text-xs shadow-xs placeholder:text-fg-subtle focus:border-accent/60 focus:outline-none focus:ring-4 focus:ring-accent/10"
            />
            <Button
              size="sm"
              variant="primary"
              disabled={busy}
              onClick={() => onDecide(true, comment)}
            >
              <CheckCircle2 size={12} /> Approve
            </Button>
            <Button size="sm" variant="danger" disabled={busy} onClick={() => onDecide(false, comment)}>
              <XCircle size={12} /> Reject
            </Button>
          </div>
        ) : (
          <p className="flex items-center gap-1.5 text-2xs text-fg-subtle">
            <Clock size={11} /> Waiting for an approver.
          </p>
        )
      ) : approval.comment ? (
        <p className="text-2xs text-fg-subtle">“{approval.comment}”</p>
      ) : null}
    </li>
  );
}

function ArtifactTile({ artifact }: { artifact: Artifact }) {
  const [open, setOpen] = useState(false);
  const provenance = artifact.provenance;
  const sources = provenance.sources ?? [];
  const approved = Boolean(provenance.approved_by);

  return (
    <li className="flex flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-xs transition-shadow hover:shadow-card">
      <div className="flex items-start gap-3 p-3">
        <KindGlyph kind={artifact.kind} size="lg" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold text-fg" title={artifact.filename}>
            {artifact.filename}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-fg-subtle">
            <span className="uppercase">{artifact.kind}</span>
            <span className="tnum">{formatBytes(artifact.size_bytes)}</span>
            <span className="tnum">
              {sources.length} source{sources.length === 1 ? "" : "s"}
            </span>
            <span>{relativeTime(artifact.created_at)}</span>
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <StatusChip status={artifact.status} />
            {provenance.has_uncertain_sources ? (
              <Chip tone="warn">
                <AlertTriangle size={9} /> scanned source
              </Chip>
            ) : null}
          </div>
        </div>
      </div>

      <div className="mt-auto flex items-center gap-1 border-t border-border px-2.5 py-2">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="rounded-md px-2 py-1 text-2xs font-medium text-fg-subtle transition-colors hover:bg-surface-raised hover:text-fg"
        >
          {open ? "Hide provenance" : "Provenance"}
        </button>
        <span className="ml-auto truncate text-2xs text-fg-subtle">
          {approved ? (
            <>
              signed off by <span className="text-fg-muted">{provenance.approved_by}</span>
            </>
          ) : (
            "awaiting sign-off"
          )}
        </span>
        <Button
          size="sm"
          variant="primary"
          onClick={() => void downloadArtifact(artifact.sha256, artifact.filename)}
          title="Download"
        >
          <Download size={12} />
        </Button>
      </div>

      {open ? (
        <div className="space-y-2.5 border-t border-border bg-surface-raised/50 p-3.5 text-2xs">
          <div className="grid gap-y-1">
            <Field label="Requested by" value={provenance.generated_by ?? "—"} />
            <Field
              label="Approved by"
              value={
                provenance.approved_by
                  ? `${provenance.approved_by}${provenance.approved_at ? ` · ${new Date(provenance.approved_at).toLocaleString()}` : ""}`
                  : "not approved"
              }
              tone={provenance.approved_by ? "ok" : "warn"}
            />
            <Field
              label="Models"
              value={Object.values(provenance.models ?? {}).join(", ") || "—"}
              mono
            />
            <Field label="Digest" value={(provenance.sha256 ?? artifact.sha256).slice(0, 24)} mono />
          </div>

          <div>
            <p className="mb-1 font-semibold uppercase tracking-wider text-fg-subtle">
              Sources ({sources.length})
            </p>
            {sources.length ? (
              <ul className="space-y-0.5">
                {sources.map((source) => (
                  <li key={source.title} className="flex items-baseline gap-2">
                    <span className="min-w-0 flex-1 truncate text-fg-muted">{source.title}</span>
                    <span className="tnum shrink-0 text-fg-subtle">p{source.pages.join(", ")}</span>
                    {source.lowest_confidence < 0.85 ? (
                      <span className="tnum shrink-0 text-warn">
                        OCR {(source.lowest_confidence * 100).toFixed(0)}%
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-warn">
                No documents were cited — this content was not grounded in the corpus.
              </p>
            )}
          </div>

          <p className="border-t border-border pt-1.5 leading-relaxed text-fg-subtle">
            This block is written into the document itself, so it travels with the file
            when it is sent on.
          </p>
        </div>
      ) : null}
    </li>
  );
}

function Field({
  label,
  value,
  mono,
  tone,
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: "ok" | "warn";
}) {
  return (
    <div className="flex gap-2">
      <span className="w-24 shrink-0 text-fg-subtle">{label}</span>
      <span
        className={cn(
          "min-w-0 flex-1 truncate",
          mono && "font-mono",
          tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-fg-muted",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const tone =
    status === "approved"
      ? "ok"
      : status === "rejected" || status === "expired"
        ? "danger"
        : status === "pending" || status === "pending_approval"
          ? "warn"
          : "neutral";
  return <Chip tone={tone as never}>{status.replace("_", " ")}</Chip>;
}

function ToolIcon({ tool }: { tool: string }) {
  if (tool.includes("code")) return <Terminal size={13} className="shrink-0 text-accent" />;
  if (tool.includes("xlsx")) return <FileSpreadsheet size={13} className="shrink-0 text-ok" />;
  return <FileText size={13} className="shrink-0 text-info" />;
}
