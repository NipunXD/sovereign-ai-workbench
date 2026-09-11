import { create } from "zustand";

import type { BBox, Citation, RetrievalHit } from "@/lib/types";

/**
 * What the right-hand inspector is showing.
 *
 * Three views of the same run — the trace of what the agent did, the map of
 * what it read, and the source it is currently pointing at. They share one
 * panel because they answer one question from three angles: *why should I
 * believe this?* Clicking a citation switches to the source; finishing a run
 * that read something switches to the map.
 */
export type InspectorTab = "trace" | "evidence" | "source";

/** A passage the user has picked out — from a citation, or from the map. */
export interface SelectedPassage {
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  page_no: number;
  bbox: BBox;
  section_path: string[];
  snippet: string;
  retrieval_method: string;
  score: number;
  confidence: number;
  /** The citation number in the answer, when it was cited. */
  n: number | null;
}

interface InspectorState {
  tab: InspectorTab;
  passage: SelectedPassage | null;
  /** The passage under the pointer — in the answer or on the map. Both
   *  surfaces light the same chunk, so a reader can hold a claim in one hand
   *  and see where it sits in what was read with the other. */
  hoverChunk: string | null;
  setHoverChunk: (chunkId: string | null) => void;
  /** Within the source tab: the passage text, or the rendered page. */
  sourceMode: "passage" | "page";
  setTab: (tab: InspectorTab) => void;
  setSourceMode: (mode: "passage" | "page") => void;
  showCitation: (citation: Citation) => void;
  showHit: (hit: RetrievalHit, n: number | null) => void;
  clearPassage: () => void;
}

export const useInspector = create<InspectorState>((set) => ({
  tab: "trace",
  passage: null,
  sourceMode: "passage",
  hoverChunk: null,
  setHoverChunk: (hoverChunk) => set({ hoverChunk }),

  setTab: (tab) => set({ tab }),
  setSourceMode: (sourceMode) => set({ sourceMode }),

  showCitation: (citation) =>
    set({
      tab: "source",
      sourceMode: "passage",
      passage: {
        chunk_id: citation.chunk_id,
        doc_id: citation.doc_id,
        doc_title: citation.doc_title,
        page_no: citation.page_no,
        bbox: citation.bbox,
        section_path: citation.section_path,
        snippet: citation.snippet,
        retrieval_method: citation.retrieval_method,
        score: citation.score,
        confidence: citation.confidence,
        n: citation.n,
      },
    }),

  showHit: (hit, n) =>
    set({
      tab: "source",
      sourceMode: "passage",
      passage: {
        chunk_id: hit.chunk_id,
        doc_id: hit.doc_id,
        doc_title: hit.doc_title,
        page_no: hit.page,
        bbox: hit.bbox ?? { x0: 0, y0: 0, x1: 0, y1: 0 },
        section_path: hit.section_path ?? [],
        snippet: hit.snippet ?? "",
        retrieval_method: hit.method,
        score: hit.score,
        confidence: hit.confidence,
        n,
      },
    }),

  clearPassage: () => set({ passage: null }),
}));
