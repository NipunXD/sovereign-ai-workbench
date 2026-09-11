"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, ScanLine, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button, Chip, Spinner } from "@/components/ui/primitives";
import { PageTranscript } from "@/components/documents/PageTranscript";
import { ApiError, api, fetchPageImage } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { BBox } from "@/lib/types";
import { useViewer } from "@/stores/viewer";

/**
 * The document viewer, with the highlight overlay that closes the loop between
 * an answer and its source.
 *
 * Bounding boxes are stored normalised to 0..1, which is what makes this work
 * at any zoom or render scale: positioning a highlight is a multiplication by
 * the rendered image size, with no coordinate conversion and nothing to drift
 * when the container resizes.
 */
export function DocumentViewer() {
  const { docId, page, activeCitation, flashToken, setPage, close } = useViewer();
  // Three outcomes, not two. `null` used to mean both "still fetching" and
  // "there is no image", so a source that never had a page render — a
  // spreadsheet — sat under a spinner that could never resolve.
  const [image, setImage] = useState<
    { status: "loading" } | { status: "ready"; url: string } | { status: "none" } | { status: "error"; message: string }
  >({ status: "loading" });
  const [rendered, setRendered] = useState({ width: 0, height: 0 });
  const [showBlocks, setShowBlocks] = useState(false);
  const imageRef = useRef<HTMLImageElement>(null);

  const { data: document } = useQuery({
    queryKey: ["document", docId],
    queryFn: () => api.document(docId!),
    enabled: !!docId,
  });

  const { data: blocks } = useQuery({
    queryKey: ["blocks", docId, page],
    queryFn: () => api.pageBlocks(docId!, page),
    enabled: !!docId,
  });

  // The page image is behind an authenticated endpoint, so it is fetched with
  // the bearer token and handed to <img> as an object URL.
  useEffect(() => {
    if (!docId) return;
    let revoked: string | null = null;
    let cancelled = false;

    setImage({ status: "loading" });
    fetchPageImage(docId, page)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        revoked = url;
        setImage({ status: "ready", url });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        // 404 is the ordinary case for a source with no page to render, not a
        // failure; anything else is one, and says so.
        setImage(
          error instanceof ApiError && error.status === 404
            ? { status: "none" }
            : { status: "error", message: error instanceof Error ? error.message : "The page could not be loaded." },
        );
      });

    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [docId, page]);

  // Track the rendered size so highlights follow the image through resizes.
  useEffect(() => {
    const element = imageRef.current;
    if (!element) return;
    const measure = () =>
      setRendered({ width: element.clientWidth, height: element.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [image]);

  const highlight = useMemo(() => {
    if (!activeCitation || activeCitation.page_no !== page) return null;
    const box = activeCitation.bbox;
    // A zero-area box means the source had no coordinates — a VLM transcription,
    // for instance. Highlighting the whole page would be a lie about precision,
    // so nothing is drawn and the page itself is the answer.
    if (box.x1 - box.x0 <= 0.001 || box.y1 - box.y0 <= 0.001) return null;
    return box;
  }, [activeCitation, page]);

  if (!docId) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center">
        <div>
          <ScanLine size={22} className="mx-auto mb-2 text-fg-subtle" />
          <p className="text-xs text-fg-muted">No document open</p>
          <p className="mt-1 max-w-[14rem] text-2xs text-fg-subtle">
            Click a citation in an answer to open its source at the exact page.
          </p>
        </div>
      </div>
    );
  }

  const pageCount = document?.page_count ?? 1;

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-2">
        <p className="min-w-0 flex-1 truncate text-xs font-medium" title={document?.title}>
          {document?.title ?? "Loading…"}
        </p>
        {image.status === "ready" ? (
        <button
          type="button"
          onClick={() => setShowBlocks((value) => !value)}
          className={cn(
            "rounded px-1.5 py-0.5 text-2xs transition-colors",
            showBlocks ? "bg-accent/20 text-accent" : "text-fg-subtle hover:text-fg-muted",
          )}
          title="Show every extracted text region"
        >
          regions
        </button>
        ) : null}
        <button
          type="button"
          onClick={close}
          className="rounded p-1 text-fg-subtle hover:bg-surface-raised hover:text-fg"
        >
          <X size={13} />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 items-start justify-center overflow-auto bg-bg p-3">
        {image.status === "none" ? (
          <PageTranscript blocks={blocks?.blocks ?? []} citation={activeCitation} page={page} />
        ) : image.status === "error" ? (
          <div className="max-w-sm rounded-lg border border-danger/40 bg-danger/[0.08] px-3 py-2.5 text-xs leading-relaxed text-danger">
            <span className="font-semibold">This page could not be loaded. </span>
            {image.message}
          </div>
        ) : (
        <div className="relative inline-block shadow-lg">
          {image.status === "ready" ? (
            // A plain <img>, not next/image. The page renders are served by
            // this system's own API and the citation overlay is positioned
            // against the element's natural box; next/image would route them
            // through an optimizer this deployment has no reason to run, and
            // on an air-gapped host that is a dependency with nothing behind
            // it.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              ref={imageRef}
              src={image.url}
              alt={`Page ${page}`}
              className="block max-w-full rounded-sm"
            />
          ) : (
            <div className="flex h-96 w-72 items-center justify-center rounded border border-border bg-surface">
              <Spinner className="text-fg-subtle" />
            </div>
          )}

          {/* Every extracted region, for inspecting what the pipeline saw. */}
          {showBlocks && blocks && rendered.width > 0
            ? blocks.blocks.map((block) => (
                <span
                  key={block.block_id}
                  className={cn(
                    "pointer-events-none absolute border",
                    block.source === "ocr"
                      ? "border-info/50"
                      : block.source === "vlm"
                        ? "border-accent/50"
                        : "border-ok/40",
                  )}
                  style={rectStyle(block.bbox, rendered)}
                  title={`${block.type} · ${block.source} · ${(block.confidence * 100).toFixed(0)}%`}
                />
              ))
            : null}

          {highlight && rendered.width > 0 ? (
            <span
              key={flashToken}
              className="pointer-events-none absolute animate-flash-highlight rounded-sm ring-2 ring-highlight"
              style={rectStyle(highlight, rendered)}
            />
          ) : null}
        </div>
        )}
      </div>

      <div className="flex h-9 shrink-0 items-center gap-2 border-t border-border px-2">
        <Button
          size="sm"
          variant="ghost"
          disabled={page <= 1}
          onClick={() => setPage(page - 1)}
        >
          <ChevronLeft size={13} />
        </Button>
        <span className="tnum text-2xs text-fg-muted">
          {page} / {pageCount}
        </span>
        <Button
          size="sm"
          variant="ghost"
          disabled={page >= pageCount}
          onClick={() => setPage(page + 1)}
        >
          <ChevronRight size={13} />
        </Button>

        <div className="ml-auto flex items-center gap-1.5">
          {blocks ? (
            <Chip tone={blocks.mean_confidence < 0.85 ? "warn" : "neutral"}>
              {blocks.blocks.length} regions · {(blocks.mean_confidence * 100).toFixed(0)}%
            </Chip>
          ) : null}
          {activeCitation && !highlight && image.status === "ready" ? (
            <Chip tone="warn">no region recorded</Chip>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function rectStyle(box: BBox, rendered: { width: number; height: number }) {
  return {
    left: `${box.x0 * rendered.width}px`,
    top: `${box.y0 * rendered.height}px`,
    width: `${(box.x1 - box.x0) * rendered.width}px`,
    height: `${(box.y1 - box.y0) * rendered.height}px`,
  };
}
