"use client";

import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useMemo } from "react";

import { cn } from "@/lib/utils";
import type { Citation } from "@/lib/types";
import { useViewer } from "@/stores/viewer";

/**
 * Renders an answer with its citation markers made clickable.
 *
 * The markers arrive as `[1]`, `[2]` in the text. Rather than post-processing
 * the rendered DOM, the text is split on markers before it reaches the markdown
 * renderer, so each reference becomes a real button that drives the viewer.
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

function CitationChip({ citation }: { citation: Citation }) {
  const open = useViewer((state) => state.open);
  const shaky = citation.confidence < 0.85;

  return (
    <button
      type="button"
      onClick={() => open(citation)}
      className={cn(
        "mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] items-center justify-center rounded px-1",
        "align-baseline text-2xs font-semibold transition-colors",
        shaky
          ? "bg-warn/20 text-warn hover:bg-warn/35"
          : "bg-accent/20 text-accent hover:bg-accent/35",
      )}
      title={
        `${citation.doc_title} — page ${citation.page_no}` +
        (shaky ? `\nOCR confidence ${(citation.confidence * 100).toFixed(0)}% — verify against the source` : "") +
        `\n\n${citation.snippet}`
      }
    >
      {citation.n}
    </button>
  );
}

/** The reference list beneath an answer. */
export function CitationList({ citations }: { citations: Citation[] }) {
  const open = useViewer((state) => state.open);
  if (!citations.length) return null;

  return (
    <div className="mt-3 border-t border-border pt-2">
      <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-fg-subtle">
        Sources
      </p>
      <ol className="space-y-1">
        {citations.map((citation) => (
          <li key={citation.chunk_id}>
            <button
              type="button"
              onClick={() => open(citation)}
              className="group flex w-full items-start gap-2 rounded px-1.5 py-1 text-left transition-colors hover:bg-surface-raised"
            >
              <span className="mt-0.5 flex h-4 min-w-4 items-center justify-center rounded bg-accent/20 px-1 text-2xs font-semibold text-accent">
                {citation.n}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-1.5">
                  <span className="truncate text-xs font-medium text-fg group-hover:text-accent">
                    {citation.doc_title}
                  </span>
                  <span className="tnum shrink-0 text-2xs text-fg-subtle">
                    p{citation.page_no}
                  </span>
                  {citation.confidence < 0.85 ? (
                    <span
                      className="tnum shrink-0 text-2xs text-warn"
                      title="Recognised from a scan; verify against the source"
                    >
                      OCR {(citation.confidence * 100).toFixed(0)}%
                    </span>
                  ) : null}
                </span>
                {citation.section_path.length ? (
                  <span className="mt-0.5 block truncate text-2xs text-fg-subtle">
                    {citation.section_path.join(" › ")}
                  </span>
                ) : null}
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
