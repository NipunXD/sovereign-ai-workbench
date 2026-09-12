"use client";

import { FileSpreadsheet } from "lucide-react";

import type { Citation, DocumentBlock } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * What was indexed, when there is no page to show.
 *
 * A spreadsheet or a CSV has no rendered page — there is nothing to
 * photograph — so the viewer used to sit on a spinner forever waiting for an
 * image the ingester never produced. The extracted blocks are right there and
 * are the honest answer to "what did it read": the same text the retriever
 * matched, laid out the way the pipeline understood it, with the cited span
 * marked.
 */
export function PageTranscript({
  blocks,
  citation,
  page,
}: {
  blocks: DocumentBlock[];
  citation: Citation | null;
  page: number;
}) {
  const probe = citation && citation.page_no === page ? normalise(citation.snippet) : null;
  // A spreadsheet never had a page; anything else should have had one and
  // does not, which is a different statement and worth making accurately.
  const office = blocks.length > 0 && blocks.every((block) => block.source === "office");

  return (
    <div className="mx-auto w-full max-w-2xl">
      <p className="mb-4 flex items-start gap-2.5 rounded-xl border border-info/20 bg-info/5 px-3.5 py-2.5 text-2xs leading-relaxed text-fg-muted">
        <FileSpreadsheet size={13} className="mt-px shrink-0 text-info" />
        <span>
          {office
            ? "This source is a spreadsheet, so there is no scanned page to show."
            : "No page render is stored for this page."}{" "}
          Below is exactly what the ingester extracted and the retriever searched.
        </span>
      </p>

      <div className="space-y-3.5 rounded-xl border border-border bg-surface p-4 shadow-xs">
        {blocks.map((block) => (
          <Block key={block.block_id} block={block} probe={probe} />
        ))}
        {!blocks.length ? (
          <p className="py-6 text-center text-xs text-fg-subtle">
            Nothing was extracted from this page.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function Block({ block, probe }: { block: DocumentBlock; probe: string | null }) {
  const cited = probe != null && normalise(block.text).includes(probe.slice(0, 48));

  if (block.type === "heading") {
    return (
      <p className={cn("text-xs font-semibold uppercase tracking-wider text-fg-muted", cited && "text-accent")}>
        {block.text}
      </p>
    );
  }

  const rows = block.type === "table" ? parseRows(block.text) : null;
  if (rows && rows.length > 1) {
    const [header, ...body] = rows;
    return (
      <div className={cn("overflow-x-auto rounded border", cited ? "border-accent/50" : "border-border")}>
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="bg-surface-raised">
              {header.map((cell, index) => (
                <th key={index} className="border-b border-border px-2 py-1 text-left font-semibold text-fg-muted">
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, rowIndex) => (
              <tr key={rowIndex} className="even:bg-surface-raised/50">
                {row.map((cell, index) => (
                  <td key={index} className="tnum border-b border-border/50 px-2 py-1 text-fg-muted">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <p
      className={cn(
        "whitespace-pre-wrap text-xs leading-relaxed text-fg-muted",
        cited && "rounded border-l-2 border-highlight bg-highlight/10 py-1 pl-2 text-fg",
      )}
    >
      {block.text}
    </p>
  );
}

/** Office blocks arrive as pipe-delimited rows, one per line. */
function parseRows(text: string): string[][] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.split("|").map((cell) => cell.trim()));
}

function normalise(text: string): string {
  return text.replace(/…$/, "").replace(/\s+/g, " ").trim();
}
