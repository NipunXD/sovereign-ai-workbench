"use client";

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import type { Citation } from "@/lib/types";
import { useInspector } from "@/stores/inspector";
import { useViewer } from "@/stores/viewer";

/**
 * Renders an answer with its citation markers made clickable.
 *
 * The markers arrive as `[1]`, `[2]` in the text. Rather than post-processing
 * the rendered DOM, the text is split on markers before it reaches the markdown
 * renderer, so each reference becomes a real control: hover shows the passage,
 * click opens it in full in the inspector.
 */
export function Answer({
  text,
  citations,
  streaming,
}: {
  text: string;
  citations: Citation[];
  streaming: boolean;
}) {
  const byNumber = useMemo(
    () => new Map(citations.map((citation) => [citation.n, citation])),
    [citations],
  );

  return (
    <div className="prose-answer">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Markers can appear inside any text node, so interception happens
          // at the paragraph, list-item and table-cell level.
          p: ({ children }) => <p>{linkify(children, byNumber)}</p>,
          li: ({ children }) => <li>{linkify(children, byNumber)}</li>,
          td: ({ children }) => <td>{linkify(children, byNumber)}</td>,
          th: ({ children }) => <th>{linkify(children, byNumber)}</th>,
        }}
      >
        {text}
      </Markdown>
      {streaming ? (
        <span className="ml-0.5 inline-block h-3.5 w-1.5 translate-y-0.5 animate-pulse-dot bg-accent" />
      ) : null}
    </div>
  );
}

const MARKER = /(\[\d+\])/g;

function linkify(children: React.ReactNode, byNumber: Map<number, Citation>): React.ReactNode {
  return Array.isArray(children)
    ? children.map((child, index) => transform(child, byNumber, index))
    : transform(children, byNumber, 0);
}

function transform(node: React.ReactNode, byNumber: Map<number, Citation>, key: number): React.ReactNode {
  if (typeof node !== "string") return node;
  const parts = node.split(MARKER);
  if (parts.length === 1) return node;

  return parts.map((part, index) => {
    const match = /^\[(\d+)\]$/.exec(part);
    if (!match) return part;
    const citation = byNumber.get(Number(match[1]));
    if (!citation) return part;
    return <CitationChip key={`${key}-${index}`} citation={citation} />;
  });
}

/** The `[n]` chip. Hover previews the passage; click opens it in full. */
function CitationChip({ citation }: { citation: Citation }) {
  const showCitation = useInspector((state) => state.showCitation);
  const setHoverChunk = useInspector((state) => state.setHoverChunk);
  const linked = useInspector((state) => state.hoverChunk === citation.chunk_id);
  const openViewer = useViewer((state) => state.open);
  const [hover, setHover] = useState(false);
  const shaky = citation.confidence < 0.85;

  return (
    <span className="relative inline-block align-baseline">
      <button
        type="button"
        onClick={() => {
          showCitation(citation);
          openViewer(citation);
        }}
        onMouseEnter={() => {
          setHover(true);
          setHoverChunk(citation.chunk_id);
        }}
        onMouseLeave={() => {
          setHover(false);
          setHoverChunk(null);
        }}
        className={cn(
          "citation-chip mx-0.5 inline-flex h-[1.2rem] min-w-[1.2rem] items-center justify-center rounded px-1",
          "font-mono text-2xs font-semibold transition-all",
          shaky
            ? "bg-warn/15 text-warn ring-1 ring-warn/40 hover:bg-warn/30"
            : "bg-accent/15 text-accent ring-1 ring-accent/30 hover:bg-accent/30 hover:ring-accent/60",
          // Lit from the map: the pointer is on this passage over there.
          linked && !hover && (shaky ? "bg-warn/35 ring-warn/80" : "bg-accent/40 ring-accent/90 shadow-glow-sm"),
        )}
        aria-label={`Citation ${citation.n}: ${citation.doc_title}, page ${citation.page_no}`}
      >
        {citation.n}
      </button>

      {hover ? (
        <span
          role="tooltip"
          className="citation-card pointer-events-none absolute left-0 top-full z-30 mt-2 w-80 rounded-xl border border-border bg-surface p-3 text-left text-2xs shadow-popover"
        >
          <span className="flex items-baseline gap-1.5">
            <span className="min-w-0 flex-1 truncate font-semibold text-fg">{citation.doc_title}</span>
            <span className="tnum shrink-0 text-fg-subtle">p{citation.page_no}</span>
          </span>
          {citation.section_path.length ? (
            <span className="mt-0.5 block truncate text-fg-subtle">{citation.section_path.join(" › ")}</span>
          ) : null}
          <span className="mt-1.5 block border-l-2 border-accent/50 pl-2 font-normal leading-snug text-fg-muted">
            {citation.snippet}
          </span>
          <span className="mt-1.5 flex items-center gap-2 text-fg-subtle">
            <span className={citation.retrieval_method === "hybrid" ? "text-accent" : undefined}>
              {citation.retrieval_method}
            </span>
            {shaky ? (
              <span className="text-warn">OCR {(citation.confidence * 100).toFixed(0)}% — verify against the page</span>
            ) : null}
            <span className="ml-auto">click to open</span>
          </span>
        </span>
      ) : null}
    </span>
  );
}

/** The reference list beneath an answer. */
export function CitationList({ citations }: { citations: Citation[] }) {
  const showCitation = useInspector((state) => state.showCitation);
  const setHoverChunk = useInspector((state) => state.setHoverChunk);
  const hoverChunk = useInspector((state) => state.hoverChunk);
  const openViewer = useViewer((state) => state.open);
  if (!citations.length) return null;

  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-border bg-surface-raised/40">
      <p className="flex items-center gap-2 border-b border-border bg-surface-raised/60 px-3.5 py-2 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
        Sources
        <span className="tnum rounded-full bg-surface px-1.5 font-semibold normal-case tracking-normal ring-1 ring-inset ring-border">
          {citations.length}
        </span>
      </p>
      <ol className="divide-y divide-border/60">
        {citations.map((citation) => (
          <li key={citation.chunk_id}>
            <button
              type="button"
              onClick={() => {
                showCitation(citation);
                openViewer(citation);
              }}
              onMouseEnter={() => setHoverChunk(citation.chunk_id)}
              onMouseLeave={() => setHoverChunk(null)}
              className={cn(
                "group flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-surface-raised/70",
                hoverChunk === citation.chunk_id && "bg-accent/[0.07]",
              )}
            >
              <span className="mt-0.5 flex h-4.5 min-w-[1.15rem] items-center justify-center rounded bg-accent/15 px-1 font-mono text-2xs font-semibold text-accent ring-1 ring-accent/30">
                {citation.n}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-1.5">
                  <span className="truncate text-xs font-medium text-fg group-hover:text-accent">
                    {citation.doc_title}
                  </span>
                  <span className="tnum shrink-0 text-2xs text-fg-subtle">p{citation.page_no}</span>
                  {citation.confidence < 0.85 ? (
                    <span className="tnum shrink-0 text-2xs text-warn" title="Recognised from a scan; verify against the source">
                      OCR {(citation.confidence * 100).toFixed(0)}%
                    </span>
                  ) : null}
                </span>
                <span className="mt-0.5 block line-clamp-2 text-2xs leading-snug text-fg-muted">
                  {citation.snippet}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
