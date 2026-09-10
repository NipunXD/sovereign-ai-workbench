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
            <table className="w-full border-collapse text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="border-b border-border text-2xs uppercase tracking-wider text-fg-subtle">
                  <th className="px-4 py-2 text-left font-semibold">Title</th>
                  <th className="px-2 py-2 text-left font-semibold">Type</th>
                  <th className="px-2 py-2 text-left font-semibold">Classification</th>
                  <th className="px-2 py-2 text-right font-semibold">Pages</th>
                  <th className="px-2 py-2 text-right font-semibold">Chunks</th>
                  <th className="px-2 py-2 text-right font-semibold">Quality</th>
                  <th className="px-4 py-2 text-right font-semibold">Ingested</th>
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
                    <td className="max-w-0 px-4 py-2">
                      <p className="truncate font-medium text-fg">{doc.title}</p>
                      {doc.tags.length ? (
                        <p className="mt-0.5 truncate font-mono text-2xs text-fg-subtle">
                          {doc.tags.slice(0, 6).join(" · ")}
                        </p>
                      ) : null}
                    </td>
                    <td className="px-2 py-2 text-fg-muted">{doc.doc_type}</td>
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
