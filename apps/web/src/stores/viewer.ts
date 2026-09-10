import { create } from "zustand";

import type { Citation } from "@/lib/types";

/**
 * What the document viewer is currently showing.
 *
 * This store is the link between a citation in an answer and the highlighted
 * region on the page it came from — clicking `[3]` in the chat opens the right
 * document at the right page and flashes the right rectangle. Keeping it
 * separate from the chat store means the viewer can also be driven from the
 * search page without either knowing about the other.
 */
interface ViewerState {
  docId: string | null;
  page: number;
  /** The citation being shown, if the viewer was opened from one. */
  activeCitation: Citation | null;
  /** Bumped on every open so the highlight re-runs its flash animation even
   *  when the same citation is clicked twice. */
  flashToken: number;
  open: (citation: Citation) => void;
  openDocument: (docId: string, page?: number) => void;
  setPage: (page: number) => void;
  close: () => void;
}

export const useViewer = create<ViewerState>((set) => ({
  docId: null,
  page: 1,
  activeCitation: null,
  flashToken: 0,

  open: (citation) =>
    set((state) => ({
      docId: citation.doc_id,
      page: citation.page_no,
      activeCitation: citation,
      flashToken: state.flashToken + 1,
    })),

  openDocument: (docId, page = 1) =>
    set((state) => ({
      docId,
      page,
      activeCitation: null,
      flashToken: state.flashToken + 1,
    })),

  setPage: (page) => set({ page, activeCitation: null }),
  close: () => set({ docId: null, activeCitation: null }),
}));
