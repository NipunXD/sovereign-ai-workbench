"use client";

import { Activity, FileSearch, Share2, X } from "lucide-react";

import { EvidenceMap } from "@/components/chat/EvidenceMap";
import { PassagePanel } from "@/components/chat/PassagePanel";
import { DocumentViewer } from "@/components/documents/DocumentViewer";
import { TraceTimeline } from "@/components/trace/TraceTimeline";
import { Spinner } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/stores/run";
import { useInspector, type InspectorTab } from "@/stores/inspector";
import { useViewer } from "@/stores/viewer";

/**
 * The right-hand panel: three angles on "why should I believe this?"
 *
 *  - Trace — what the agent did, in order.
 *  - Evidence — what it read, as a map.
 *  - Source — the passage or page it is pointing at.
 *
 * One panel rather than three, because they are the same question and a
 * reader moves between them constantly: a claim in the answer, to its passage,
 * to the page it sits on, back to the map to see what else was retrieved.
 */
export function Inspector({
  message,
  question,
  running,
}: {
  message: ChatMessage | null;
  question: string;
  running: boolean;
}) {
  const { tab, setTab, passage, sourceMode, setSourceMode, clearPassage } = useInspector();
  const viewerDocId = useViewer((s) => s.docId);
  const closeViewer = useViewer((s) => s.close);

  const trace = message?.trace ?? [];
  const citations = message?.citations ?? [];
  const passages = trace.reduce((n, t) => (t.kind === "retrieval" ? n + t.hits.length : n), 0);

  const tabs: Array<{ id: InspectorTab; label: string; icon: React.ReactNode; badge?: string | number }> = [
    { id: "trace", label: "Trace", icon: <Activity size={12} />, badge: trace.length || undefined },
    { id: "evidence", label: "Evidence", icon: <Share2 size={12} />, badge: passages || undefined },
    {
      id: "source",
      label: "Source",
      icon: <FileSearch size={12} />,
      badge: passage?.n != null ? `[${passage.n}]` : undefined,
    },
  ];

  const hasSource = Boolean(passage || viewerDocId);

  return (
    <aside className="inspector flex w-[26rem] shrink-0 flex-col border-l border-border bg-surface 2xl:w-[32rem]">
      <div className="flex h-10 shrink-0 items-center gap-1 border-b border-border px-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cn(
              "relative flex h-7 items-center gap-1.5 rounded-md px-2.5 text-2xs font-semibold uppercase tracking-wider transition-colors",
              tab === t.id ? "bg-surface-raised text-fg" : "text-fg-subtle hover:text-fg-muted",
            )}
          >
            {t.icon}
            {t.label}
            {t.badge !== undefined ? (
              <span
                className={cn(
                  "tnum rounded px-1 py-px text-[10px] font-medium normal-case tracking-normal",
                  tab === t.id ? "bg-accent/20 text-accent" : "bg-surface-raised text-fg-subtle",
                )}
              >
                {t.badge}
              </span>
            ) : null}
            {tab === t.id ? (
              <span className="absolute -bottom-[7px] left-2 right-2 h-0.5 rounded-full bg-accent" />
            ) : null}
          </button>
        ))}
        <span className="ml-auto flex items-center gap-2 pr-1">
          {running ? <Spinner className="text-accent" /> : null}
          {tab === "source" && hasSource ? (
            <button
              type="button"
              onClick={() => {
                clearPassage();
                closeViewer();
              }}
              className="rounded p-1 text-fg-subtle hover:bg-surface-raised hover:text-fg"
              title="Close the source"
            >
              <X size={13} />
            </button>
          ) : null}
        </span>
      </div>

      <div className="relative min-h-0 flex-1">
        {tab === "trace" ? (
          <div className="h-full overflow-y-auto">
            {trace.length ? (
              <TraceTimeline trace={trace} startedAt={message?.startedAt ?? Date.now()} />
            ) : (
              <Empty
                icon={<Activity size={20} />}
                title="Nothing running"
                hint="Routing, the plan, every retrieval and every tool call appear here as the agent works."
              />
            )}
          </div>
        ) : tab === "evidence" ? (
          <EvidenceMap question={question} trace={trace} citations={citations} running={running} />
        ) : hasSource ? (
          <div className="flex h-full flex-col">
            {passage ? (
              <div className="flex h-8 shrink-0 items-center gap-1 border-b border-border bg-bg/40 px-2">
                {(["passage", "page"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setSourceMode(mode)}
                    className={cn(
                      "h-6 rounded px-2 text-2xs font-medium capitalize transition-colors",
                      sourceMode === mode ? "bg-accent/20 text-accent" : "text-fg-subtle hover:text-fg",
                    )}
                  >
                    {mode}
                  </button>
                ))}
                <span className="ml-auto truncate text-2xs text-fg-subtle">
                  {sourceMode === "page" ? "highlight shows the cited region" : "full indexed passage"}
                </span>
              </div>
            ) : null}
            <div className="min-h-0 flex-1">
              {passage && sourceMode === "passage" ? <PassagePanel passage={passage} /> : <DocumentViewer />}
            </div>
          </div>
        ) : (
          <Empty
            icon={<FileSearch size={20} />}
            title="No source selected"
            hint="Click a citation number in an answer, or a passage on the evidence map, to see exactly what was read."
          />
        )}
      </div>
    </aside>
  );
}

function Empty({ icon, title, hint }: { icon: React.ReactNode; title: string; hint: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-center">
      <div className="max-w-[16rem]">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full border border-border text-fg-subtle">
          {icon}
        </div>
        <p className="text-xs font-medium text-fg-muted">{title}</p>
        <p className="mt-1 text-2xs leading-relaxed text-fg-subtle">{hint}</p>
      </div>
    </div>
  );
}
