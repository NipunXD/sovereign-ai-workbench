"use client";

import Link from "next/link";
import { CheckCircle2, Clock, ShieldCheck, UserCheck, XCircle } from "lucide-react";
import { useEffect, useState } from "react";

import type { ApprovalState } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The human gate, shown where the person who is waiting is looking.
 *
 * A run that stops for approval used to look identical to one that was
 * thinking. This says what is happening, who can move it along, and — because
 * the decision no longer has to arrive while the run is still connected —
 * that walking away is fine: the document is produced whenever the approval
 * lands.
 */
export function ApprovalBanner({ approval }: { approval: ApprovalState }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (approval.status !== "pending") return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [approval.status]);

  const waitEndsAt = approval.requested_at + approval.waiting_s * 1000;
  const remaining = Math.max(0, Math.round((waitEndsAt - now) / 1000));
  const stillWaiting = remaining > 0;
  const tool = approval.tool.replace("artifact.", "").toUpperCase();

  if (approval.status === "pending") {
    return (
      <div className="animate-fade-in-up mt-3 overflow-hidden rounded-lg border border-accent/40 bg-accent/[0.06]">
        <div className="flex items-start gap-3 px-3 py-2.5">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-accent/50 bg-accent/15 text-accent">
            <ShieldCheck size={14} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-fg">Needs a second person</p>
            <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">
              The {tool} document is ready to generate and is waiting for someone with approval
              rights to sign it off. You cannot approve your own request — that is the point.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Link
                href="/approvals"
                className="inline-flex h-7 items-center gap-1.5 rounded bg-accent px-2.5 text-xs font-medium text-accent-fg hover:bg-accent/90"
              >
                <UserCheck size={12} /> Open the approval queue
              </Link>
              <span className="flex items-center gap-1 text-2xs text-fg-subtle">
                <Clock size={11} />
                {stillWaiting ? (
                  <>
                    this run keeps waiting for{" "}
                    <span className="tnum font-medium text-fg-muted">{formatCountdown(remaining)}</span>
                  </>
                ) : (
                  "this run has stopped waiting"
                )}
              </span>
            </div>
            <p className="mt-2 text-2xs leading-relaxed text-fg-subtle">
              {stillWaiting
                ? "If nobody decides in time, the request stays open — the document is still produced the moment it is approved, without you here."
                : "The request is still open. The document will be produced the moment someone approves it, and will appear under Approvals → Generated documents."}
            </p>
          </div>
        </div>
        {stillWaiting ? (
          <div className="h-0.5 w-full bg-accent/10">
            <div
              className="h-full bg-accent/60 transition-[width] duration-1000 ease-linear"
              style={{ width: `${(remaining / approval.waiting_s) * 100}%` }}
            />
          </div>
        ) : null}
      </div>
    );
  }

  const approved = approval.status === "approved";
  return (
    <div
      className={cn(
        "animate-fade-in-up mt-3 flex items-center gap-2.5 rounded-lg border px-3 py-2 text-xs",
        approved ? "border-ok/40 bg-ok/[0.07] text-fg" : "border-danger/40 bg-danger/[0.07] text-fg",
      )}
    >
      {approved ? <CheckCircle2 size={14} className="shrink-0 text-ok" /> : <XCircle size={14} className="shrink-0 text-danger" />}
      <span>
        {approved ? "Approved" : approval.status === "expired" ? "Expired" : "Rejected"}
        {approval.decided_by ? (
          <>
            {" "}
            by <span className="font-medium">{approval.decided_by}</span>
          </>
        ) : null}
        {approval.comment ? <span className="text-fg-muted"> — “{approval.comment}”</span> : null}
      </span>
    </div>
  );
}

function formatCountdown(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
