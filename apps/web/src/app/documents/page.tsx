"use client";

import { useQuery } from "@tanstack/react-query";
import { FileText, ScanLine } from "lucide-react";
import { useState } from "react";

import { DocumentViewer } from "@/components/documents/DocumentViewer";
import {
  Chip,
  ClassificationBadge,
  EmptyState,
  Input,
  Spinner,
} from "@/components/ui/primitives";
import { api } from "@/lib/api";
import type { Classification, DocumentSummary } from "@/lib/types";
import { cn, formatBytes, relativeTime } from "@/lib/utils";
import { useSession } from "@/stores/session";
import { useViewer } from "@/stores/viewer";

export default function DocumentsPage() {
  const [search, setSearch] = useState("");
  const { principal } = useSession();
  const { docId, openDocument } = useViewer();

  const { data, isLoading } = useQuery({
    queryKey: ["documents", search],
    queryFn: () => api.documents({ search: search || undefined }),
  });

  return (
    <div className="flex h-full">
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-11 shrink-0 items-center gap-3 border-b border-border bg-surface px-4">
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Filter by title…"
            className="max-w-xs"
          />
          <p className="ml-auto text-2xs text-fg-subtle">
            {data?.length ?? 0} document{data?.length === 1 ? "" : "s"} visible at{" "}
            <span className="text-fg-muted">{principal?.clearance}</span> clearance
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {data?.length ? <CorpusStrip documents={data} /> : null}
          {isLoading ? (
            <div className="flex justify-center py-10">
              <Spinner className="text-fg-subtle" />
            </div>
          ) : !data?.length ? (
            <EmptyState
              icon={<FileText size={20} />}
              title="No documents"
              hint="Run `python scripts/seed_corpus.py` to generate and ingest the sample MRPL corpus."
            />
          ) : (
            <table className="w-full table-fixed border-collapse text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="border-b border-border text-2xs uppercase tracking-wider text-fg-subtle">
                  <th className="w-[38%] px-4 py-2 text-left font-semibold">Title</th>
                  <th className="w-[11%] px-2 py-2 text-left font-semibold">Type</th>
                  <th className="w-[12%] px-2 py-2 text-left font-semibold">Classification</th>
                  <th className="w-[7%] px-2 py-2 text-right font-semibold">Pages</th>
                  <th className="w-[8%] px-2 py-2 text-right font-semibold">Chunks</th>
                  <th className="w-[10%] px-2 py-2 text-right font-semibold">Quality</th>
                  <th className="w-[15%] px-4 py-2 text-right font-semibold">Ingested</th>
                </tr>
              </thead>
              <tbody>
                {data.map((doc) => (
                  <tr
                    key={doc.id}
                    onClick={() => openDocument(doc.id, 1)}
                    className={cn(
                      "cursor-pointer border-b border-border/60 transition-colors hover:bg-surface-raised",
                      docId === doc.id && "bg-surface-raised",
                    )}
                  >
                    <td className="px-4 py-2">
                      <p className="truncate font-medium text-fg">{doc.title}</p>
                      {doc.tags.length ? (
                        <p className="mt-0.5 truncate font-mono text-2xs text-fg-subtle">
                          {doc.tags.slice(0, 6).join(" · ")}
                        </p>
                      ) : null}
                    </td>
                    <td className="truncate px-2 py-2 text-fg-muted">{doc.doc_type}</td>
                    <td className="px-2 py-2">
                      <ClassificationBadge level={doc.classification} />
                    </td>
                    <td className="tnum px-2 py-2 text-right text-fg-muted">{doc.page_count}</td>
                    <td className="tnum px-2 py-2 text-right text-fg-muted">{doc.chunk_count}</td>
                    <td className="px-2 py-2 text-right">
                      {doc.mean_confidence < 0.999 ? (
                        <Chip tone={doc.mean_confidence < 0.85 ? "warn" : "neutral"}>
                          <ScanLine size={9} /> {(doc.mean_confidence * 100).toFixed(0)}%
                        </Chip>
                      ) : (
                        <span className="text-2xs text-fg-subtle">native</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right text-2xs text-fg-subtle">
                      {relativeTime(doc.created_at)}
                      <span className="ml-1.5">{formatBytes(doc.size_bytes)}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <aside className="flex w-[28rem] shrink-0 flex-col border-l border-border bg-surface 2xl:w-[34rem]">
        <DocumentViewer />
      </aside>
    </div>
  );
}

const LADDER: Classification[] = ["public", "internal", "confidential", "restricted"];
const LADDER_BG: Record<Classification, string> = {
  public: "bg-classification-public",
  internal: "bg-classification-internal",
  confidential: "bg-classification-confidential",
  restricted: "bg-classification-restricted",
};

/**
 * The corpus at a glance, for the clearance you hold.
 *
 * The classification bar is the point: it shows the shape of what this person
 * can reach, and a viewer and a senior engineer see visibly different bars
 * for the same corpus — which is the access model, demonstrated rather than
 * described.
 */
function CorpusStrip({ documents }: { documents: DocumentSummary[] }) {
  const pages = documents.reduce((n, d) => n + d.page_count, 0);
  const passages = documents.reduce((n, d) => n + d.chunk_count, 0);
  const scanned = documents.filter((d) => d.mean_confidence < 0.999);
  const meanOcr = scanned.length
    ? scanned.reduce((n, d) => n + d.mean_confidence, 0) / scanned.length
    : null;
  const counts = LADDER.map((level) => ({
    level,
    count: documents.filter((d) => d.classification === level).length,
  }));

  return (
    <div className="stagger grid gap-2 border-b border-border px-4 py-3 sm:grid-cols-[repeat(4,minmax(0,1fr))_minmax(14rem,1.4fr)]">
      <Tile value={String(documents.length)} label="documents" />
      <Tile value={pages.toLocaleString()} label="pages" />
      <Tile value={passages.toLocaleString()} label="indexed passages" />
      <Tile
        value={meanOcr == null ? "—" : `${(meanOcr * 100).toFixed(0)}%`}
        label={scanned.length ? `mean OCR · ${scanned.length} scanned` : "no scanned pages"}
        tone={meanOcr != null && meanOcr < 0.85 ? "warn" : undefined}
      />
      <div className="stat-tile">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">by classification</p>
        <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-bg">
          {counts
            .filter((c) => c.count)
            .map((c) => (
              <span
                key={c.level}
                className={cn("h-full", LADDER_BG[c.level])}
                style={{ width: `${(c.count / documents.length) * 100}%` }}
                title={`${c.level}: ${c.count}`}
              />
            ))}
        </div>
        <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-fg-subtle">
          {counts.map((c) => (
            <span key={c.level} className="flex items-center gap-1">
              <span className={cn("h-1.5 w-1.5 rounded-full", LADDER_BG[c.level], !c.count && "opacity-30")} />
              {c.level} <span className="tnum text-fg-muted">{c.count}</span>
            </span>
          ))}
        </p>
      </div>
    </div>
  );
}

function Tile({ value, label, tone }: { value: string; label: string; tone?: "warn" }) {
  return (
    <div className="stat-tile">
      <p className={cn("tnum text-lg font-semibold leading-none tracking-tight", tone === "warn" ? "text-warn" : "text-fg")}>
        {value}
      </p>
      <p className="mt-1.5 text-[10px] font-semibold uppercase tracking-wider text-fg-subtle">{label}</p>
    </div>
  );
}
