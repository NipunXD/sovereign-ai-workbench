import { create } from "zustand";

import type { Citation, RunSummary, TraceItem, ValidationReport } from "@/lib/types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  /** Live token stream while running; replaced by the citation-resolved text
   *  when the `answer` event arrives. */
  text: string;
  citations: Citation[];
  trace: TraceItem[];
  reasoning: string;
  validation: ValidationReport | null;
  summary: RunSummary | null;
  /** "interrupted" means the stream ended without the run saying it had
   *  finished — the tab was closed, the connection dropped, or the server went
   *  away mid-run. Distinct from "done" on purpose: the text on screen is
   *  whatever arrived before the cut, and presenting a half-finished answer as
   *  a complete one is how a reader ends up trusting a truncated figure. */
  status: "streaming" | "done" | "error" | "cancelled" | "interrupted";
  error?: string;
  startedAt: number;
}

interface RunState {
  messages: ChatMessage[];
  running: boolean;
  /** Whether the model is still in its thinking phase — before the first
   *  answer token. On a local model that gap is several seconds, and without
   *  an indicator the UI looks frozen. */
  thinking: boolean;
  append: (message: ChatMessage) => void;
  patchLast: (patch: Partial<ChatMessage>) => void;
  appendToken: (text: string) => void;
  appendReasoning: (text: string) => void;
  addTrace: (item: TraceItem) => void;
  addCitation: (citation: Citation) => void;
  setRunning: (running: boolean) => void;
  setThinking: (thinking: boolean) => void;
  reset: () => void;
}

export const useRun = create<RunState>((set) => ({
  messages: [],
  running: false,
  thinking: false,

  append: (message) => set((state) => ({ messages: [...state.messages, message] })),

  patchLast: (patch) =>
    set((state) => {
      if (!state.messages.length) return state;
      const messages = [...state.messages];
      messages[messages.length - 1] = { ...messages[messages.length - 1], ...patch };
      return { messages };
    }),

  appendToken: (text) =>
    set((state) => {
      if (!state.messages.length) return state;
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      messages[messages.length - 1] = { ...last, text: last.text + text };
      return { messages, thinking: false };
    }),

  appendReasoning: (text) =>
    set((state) => {
      if (!state.messages.length) return state;
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      messages[messages.length - 1] = { ...last, reasoning: last.reasoning + text };
      return { messages };
    }),

  addTrace: (item) =>
    set((state) => {
      if (!state.messages.length) return state;
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      messages[messages.length - 1] = { ...last, trace: [...last.trace, item] };
      return { messages };
    }),

  addCitation: (citation) =>
    set((state) => {
      if (!state.messages.length) return state;
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      if (last.citations.some((c) => c.chunk_id === citation.chunk_id)) return state;
      messages[messages.length - 1] = { ...last, citations: [...last.citations, citation] };
      return { messages };
    }),

  setRunning: (running) => set({ running }),
  setThinking: (thinking) => set({ thinking }),
  reset: () => set({ messages: [], running: false, thinking: false }),
}));
