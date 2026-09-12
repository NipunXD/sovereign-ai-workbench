"use client";

import { useQuery } from "@tanstack/react-query";
import { FileText, ScanLine, Search as SearchIcon } from "lucide-react";
import { useState } from "react";

import { DocumentViewer } from "@/components/documents/DocumentViewer";
import { Chip, ClassificationBadge, EmptyState, Input, StatTile } from "@/components/ui/primitives";
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
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <div className="mx-auto max-w-6xl space-y-5">
            {data?.length ? <CorpusStrip documents={data} /> : null}

            <div className="card overflow-hidden">
              <div className="card-header">
                <div className="relative max-w-xs flex-1">
                  <SearchIcon
                    size={14}
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-subtle"
                  />
                  <Input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Filter by title…"
                    className="pl-8"
                  />
                </div>
                <p className="text-xs text-fg-subtle">
                  <span className="tnum font-medium text-fg">{data?.length ?? 0}</span> visible at{" "}
                  <span className="font-medium text-fg-muted">{principal?.clearance}</span> clearance
                </p>
              </div>

              {isLoading ? (
                <div className="space-y-2 p-4">
                  {[0, 1, 2, 3, 4].map((n) => (
                    <div key={n} className="skeleton h-11 w-full" />
                  ))}
                </div>
              ) : !data?.length ? (
                <EmptyState
                  icon={<FileText size={18} />}
                  title="No documents"
                  hint="Run `python scripts/seed_corpus.py` to generate and ingest the sample MRPL corpus."
                />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-border bg-surface-raised/60 text-2xs uppercase tracking-wider text-fg-subtle">
                        <th className="w-full px-4 py-2.5 text-left font-semibold">Document</th>
                        <th className="px-3 py-2.5 text-left font-semibold">Type</th>
                        <th className="px-3 py-2.5 text-left font-semibold">Classification</th>
                        <th className="px-3 py-2.5 text-right font-semibold">Pages</th>
                        <th className="px-3 py-2.5 text-right font-semibold">Passages</th>
                        <th className="px-3 py-2.5 text-right font-semibold">Quality</th>
                        <th className="px-4 py-2.5 text-right font-semibold">Ingested</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.map((doc) => (
                        <tr
                          key={doc.id}
                          onClick={() => openDocument(doc.id, 1)}
                          className={cn(
                            "cursor-pointer border-b border-border/70 transition-colors last:border-b-0",
                            docId === doc.id ? "bg-accent-muted" : "hover:bg-surface-raised",
                          )}
                        >
                          <td className="max-w-[1px] px-4 py-3">
                            <p className="truncate font-medium text-fg">{doc.title}</p>
                            {doc.tags.length ? (
                              <p className="mt-0.5 truncate font-mono text-2xs text-fg-subtle">
                                {doc.tags.slice(0, 6).join(" · ")}
                              </p>
                            ) : null}
                          </td>
                          <td className="whitespace-nowrap px-3 py-3 text-xs text-fg-muted">{doc.doc_type}</td>
                          <td className="px-3 py-3">
                            <ClassificationBadge level={doc.classification} />
                          </td>
                          <td className="tnum px-3 py-3 text-right text-fg-muted">{doc.page_count}</td>
                          <td className="tnum px-3 py-3 text-right text-fg-muted">{doc.chunk_count}</td>
                          <td className="px-3 py-3 text-right">
                            {doc.mean_confidence < 0.999 ? (
                              <Chip tone={doc.mean_confidence < 0.85 ? "warn" : "neutral"}>
                                <ScanLine size={10} /> {(doc.mean_confidence * 100).toFixed(0)}%
                              </Chip>
                            ) : (
                              <span className="text-2xs text-fg-subtle">native</span>
                            )}
                          </td>
                          <td className="whitespace-nowrap px-4 py-3 text-right text-2xs text-fg-subtle">
                            {relativeTime(doc.created_at)}
                            <span className="ml-1.5 tnum">{formatBytes(doc.size_bytes)}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      </section>

      {docId ? (
        <aside className="flex w-[26rem] shrink-0 flex-col border-l border-border bg-surface 2xl:w-[32rem]">
          <DocumentViewer />
        </aside>
      ) : null}
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
    <div className="stagger grid grid-cols-[repeat(auto-fit,minmax(9.5rem,1fr))] gap-3">
      <StatTile value={String(documents.length)} label="Documents" icon={<FileText size={13} />} />
      <StatTile value={pages.toLocaleString()} label="Pages" />
      <StatTile value={passages.toLocaleString()} label="Indexed passages" />
      <StatTile
        value={meanOcr == null ? "—" : `${(meanOcr * 100).toFixed(0)}%`}
        label="Mean OCR quality"
        hint={scanned.length ? `${scanned.length} scanned source${scanned.length === 1 ? "" : "s"}` : "no scanned pages"}
        tone={meanOcr != null && meanOcr < 0.85 ? "warn" : undefined}
      />
      <div className="stat-tile col-span-full sm:col-span-2">
        <p className="section-label">By classification</p>
        <div className="mt-3 flex h-2 gap-0.5 overflow-hidden rounded-full bg-surface-sunken">
          {counts
            .filter((c) => c.count)
            .map((c) => (
              <span
                key={c.level}
                className={cn("h-full rounded-full", LADDER_BG[c.level])}
                style={{ width: `${(c.count / documents.length) * 100}%` }}
                title={`${c.level}: ${c.count}`}
              />
            ))}
        </div>
        <p className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1 text-2xs text-fg-subtle">
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
