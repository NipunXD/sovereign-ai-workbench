"use client";

import { useQuery } from "@tanstack/react-query";
import { Copy, ExternalLink, FileText, ScanLine } from "lucide-react";
import { useMemo, useState } from "react";

import { Button, Chip, Spinner } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useInspector, type SelectedPassage } from "@/stores/inspector";
import { useViewer } from "@/stores/viewer";

/**
 * The exact passage behind a citation, in full.
 *
 * A citation chip shows a 240-character snippet; that is enough to recognise
 * a claim, not enough to check it. This is the whole indexed chunk, with the
 * cited span highlighted inside it, and the neighbouring chunks from the same
 * document one click away — because the sentence before the one the model
 * quoted is often the one that changes its meaning.
 */
export function PassagePanel({ passage }: { passage: SelectedPassage }) {
  const setSourceMode = useInspector((s) => s.setSourceMode);
  const openViewer = useViewer((s) => s.open);
  const [showNeighbours, setShowNeighbours] = useState(false);
  const [copied, setCopied] = useState(false);

  const chunks = useQuery({
    queryKey: ["chunks", passage.doc_id],
    queryFn: () => api.documentChunks(passage.doc_id),
    enabled: !!passage.doc_id,
    staleTime: 5 * 60_000,
  });

  const index = chunks.data?.findIndex((c) => c.chunk_id === passage.chunk_id) ?? -1;
  const chunk = index >= 0 ? chunks.data![index] : null;
  const previous = index > 0 ? chunks.data![index - 1] : null;
  const next = index >= 0 && chunks.data && index < chunks.data.length - 1 ? chunks.data[index + 1] : null;

  const text = chunk?.text ?? passage.snippet;
  const highlighted = useMemo(() => highlightSpan(text, passage.snippet), [text, passage.snippet]);

  function openPage() {
    openViewer({
      n: passage.n ?? 0,
      chunk_id: passage.chunk_id,
      doc_id: passage.doc_id,
      doc_title: passage.doc_title,
      doc_type: "",
      page_no: passage.page_no,
      bbox: passage.bbox,
      snippet: passage.snippet,
      section_path: passage.section_path,
      score: passage.score,
      retrieval_method: passage.retrieval_method,
      confidence: passage.confidence,
    });
    setSourceMode("page");
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(`${text}\n\n— ${passage.doc_title}, page ${passage.page_no}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable — nothing to do */
    }
  }

  const shaky = passage.confidence < 0.85;

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-border px-3 py-2.5">
        <div className="flex items-start gap-2">
          {passage.n != null ? (
            <span className="mt-0.5 flex h-5 min-w-5 items-center justify-center rounded bg-accent px-1.5 font-mono text-2xs font-bold text-accent-fg">
              {passage.n}
            </span>
          ) : (
            <span className="mt-0.5 rounded border border-border px-1.5 py-0.5 text-2xs text-fg-subtle">
              not cited
            </span>
          )}
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-fg">
              <FileText size={12} className="shrink-0 text-info" />
              <span className="truncate" title={passage.doc_title}>
                {passage.doc_title}
              </span>
            </p>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-fg-subtle">
              <span className="tnum">page {passage.page_no}</span>
              {passage.section_path.length ? (
                <span className="truncate">{passage.section_path.join(" › ")}</span>
              ) : null}
            </p>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <Chip tone={passage.retrieval_method === "hybrid" ? "accent" : "neutral"}>
            {passage.retrieval_method}
          </Chip>
          {shaky ? (
            <Chip tone="warn">
              <ScanLine size={9} /> OCR {(passage.confidence * 100).toFixed(0)}% — verify
            </Chip>
          ) : chunk ? (
            <Chip tone="ok">
              <span className="tnum">{(chunk.mean_confidence * 100).toFixed(0)}% confidence</span>
            </Chip>
          ) : null}
          <div className="ml-auto flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={copy} title="Copy passage with its reference">
              <Copy size={12} /> {copied ? "copied" : "copy"}
            </Button>
            <Button size="sm" variant="primary" onClick={openPage} title="Open the page and highlight this region">
              <ExternalLink size={12} /> Open page
            </Button>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {chunks.isLoading && !chunk ? (
          <div className="flex items-center gap-2 text-2xs text-fg-subtle">
            <Spinner /> loading the full passage…
          </div>
        ) : null}

        {showNeighbours && previous ? (
          <Neighbour label="before" text={previous.text} />
        ) : null}

        <blockquote
          className={cn(
            "passage rounded-md border border-accent/30 bg-accent/[0.04] px-3 py-2.5 text-sm leading-relaxed text-fg",
            shaky && "border-warn/40",
          )}
        >
          {highlighted}
        </blockquote>

        {showNeighbours && next ? <Neighbour label="after" text={next.text} /> : null}

        {(previous || next) && !showNeighbours ? (
          <button
            type="button"
            onClick={() => setShowNeighbours(true)}
            className="mt-2 text-2xs text-fg-subtle underline-offset-2 hover:text-fg-muted hover:underline"
          >
            show the surrounding text
          </button>
        ) : null}

        {chunk ? (
          <p className="mt-3 text-2xs text-fg-subtle">
            Chunk {chunk.ordinal + 1}
            {chunks.data ? ` of ${chunks.data.length}` : ""} · {chunk.token_count} tokens
            {chunk.page_from !== chunk.page_to ? ` · pages ${chunk.page_from}–${chunk.page_to}` : ""}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function Neighbour({ label, text }: { label: string; text: string }) {
  return (
    <div className="my-2 rounded-md border border-dashed border-border px-3 py-2 text-xs leading-relaxed text-fg-subtle">
      <span className="mb-1 block text-2xs font-semibold uppercase tracking-wider">{label}</span>
      {text}
    </div>
  );
}

/**
 * Mark the cited span inside the full passage.
 *
 * The snippet is a prefix of the chunk that may end in an ellipsis and may
 * have had its whitespace collapsed, so it is matched on its first few dozen
 * characters after normalising both sides. When it cannot be found the whole
 * passage is shown unmarked rather than mis-highlighting a random sentence.
 */
function highlightSpan(text: string, snippet: string): React.ReactNode {
  const probe = snippet.replace(/…$/, "").trim().slice(0, 48);
  if (!probe) return text;
  const norm = (s: string) => s.replace(/\s+/g, " ");
  const haystack = norm(text);
  const start = haystack.indexOf(norm(probe));
  if (start < 0) return text;

  const end = Math.min(haystack.length, start + norm(snippet.replace(/…$/, "")).length);
  return (
    <>
      {haystack.slice(0, start)}
      <mark className="rounded-sm bg-highlight/25 px-0.5 text-fg">{haystack.slice(start, end)}</mark>
      {haystack.slice(end)}
    </>
  );
}
