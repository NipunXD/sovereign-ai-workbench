"use client";

import { useMutation } from "@tanstack/react-query";
import { Search as SearchIcon } from "lucide-react";
import { useState } from "react";

import { DocumentViewer } from "@/components/documents/DocumentViewer";
import { Button, Chip, EmptyState, Input, Spinner } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { useSession } from "@/stores/session";
import { useViewer } from "@/stores/viewer";

/**
 * Retrieval, exposed without a model in the way.
 *
 * "Why did it answer that?" is the first question anyone asks of a RAG system,
 * and this answers it directly: the exact passages, their scores, which
 * retriever found each one, and the access filter that was applied. Running the
 * same query as two users is also the clearest demonstration that
 * confidentiality is enforced in the search itself.
 */
/** Queries that show the retriever off: a tag, a reading, a number, a name. */
const EXAMPLES = [
  "V-1201 depressurisation rate",
  "CML-04 thickness",
  "P-101A vibration",
  "turnaround budget 2029",
  "hold time before opening",
];

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const { principal } = useSession();
  const openDocument = useViewer((state) => state.openDocument);
  const viewerOpen = useViewer((state) => state.docId !== null);

  const search = useMutation({
    mutationFn: (q: string) => api.search(q, 12),
  });

  return (
    <div className="flex h-full">
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="shrink-0 border-b border-border bg-surface px-4 py-3">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (query.trim()) search.mutate(query.trim());
            }}
            className="flex items-center gap-2"
          >
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search the indexed corpus — try an equipment tag like V-1201"
              className="flex-1"
            />
            <Button type="submit" variant="primary" disabled={!query.trim() || search.isPending}>
              {search.isPending ? <Spinner /> : <SearchIcon size={12} />} Search
            </Button>
          </form>

          {search.data ? (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-2xs">
              <span className="text-fg-subtle">Applied to your clearance:</span>
              <Chip tone="accent">{search.data.access_filter}</Chip>
              <span className="text-fg-subtle">
                {search.data.hits.length} passage{search.data.hits.length === 1 ? "" : "s"}
              </span>
            </div>
          ) : null}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {search.isPending ? (
            <div className="flex justify-center py-10">
              <Spinner className="text-fg-subtle" />
            </div>
          ) : search.error ? (
            <p className="rounded border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
              {(search.error as Error).message}
            </p>
          ) : search.data ? (
            search.data.hits.length ? (
              <ol className="space-y-2">
                {search.data.hits.map((hit, index) => (
                  <li key={hit.chunk_id}>
                    <button
                      type="button"
                      onClick={() => openDocument(hit.doc_id, hit.page_from)}
                      className="panel w-full p-3 text-left transition-colors hover:border-border-strong"
                    >
                      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                        <span className="tnum text-2xs text-fg-subtle">#{index + 1}</span>
                        <span className="truncate text-xs font-medium text-fg">
                          {hit.doc_title}
                        </span>
                        <span className="tnum text-2xs text-fg-subtle">p{hit.page_from}</span>
                        <Chip tone={hit.retrieval_method === "hybrid" ? "accent" : "neutral"}>
                          {hit.retrieval_method}
                        </Chip>
                        <Chip>{hit.doc_type}</Chip>
                        {hit.confidence < 0.85 ? (
                          <Chip tone="warn">OCR {(hit.confidence * 100).toFixed(0)}%</Chip>
                        ) : null}
                        <span className="tnum ml-auto text-2xs text-fg-subtle">
                          {hit.score.toFixed(4)}
                        </span>
                      </div>
                      {hit.section_path.length ? (
                        <p className="mb-1 text-2xs text-fg-subtle">
                          {hit.section_path.join(" › ")}
                        </p>
                      ) : null}
                      <p className="line-clamp-3 whitespace-pre-wrap text-xs leading-snug text-fg-muted">
                        {hit.text}
                      </p>
                    </button>
                  </li>
                ))}
              </ol>
            ) : (
              <EmptyState
                title="Nothing visible to you matches that"
                hint={`Your clearance is ${principal?.clearance}. A passage in a document above it is filtered inside the search itself, so it never becomes a candidate.`}
              />
            )
          ) : (
            <div>
              <EmptyState
                icon={<SearchIcon size={20} />}
                title="Search the corpus directly"
                hint="Results show the exact passages retrieval returns, which retriever found each one, and the access filter applied — the same filter the agent works behind."
              />
              <div className="stagger mx-auto flex max-w-xl flex-wrap justify-center gap-1.5">
                {EXAMPLES.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => {
                      setQuery(example);
                      search.mutate(example);
                    }}
                    className="rounded-full border border-border bg-surface/60 px-3 py-1 font-mono text-2xs text-fg-muted transition-colors hover:border-accent/50 hover:text-accent"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>

      {viewerOpen ? (
        <aside className="flex w-[26rem] shrink-0 flex-col border-l border-border bg-surface 2xl:w-[32rem]">
          <DocumentViewer />
        </aside>
      ) : null}
    </div>
  );
}
