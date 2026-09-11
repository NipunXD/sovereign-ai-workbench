import { create } from "zustand";

import type {
  ApprovalState,
  Citation,
  GeneratedArtifact,
  PlanStep,
  RunSummary,
  StepProgress,
  TraceItem,
  ValidationReport,
} from "@/lib/types";

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
  /** Things the run understood and declined to do, with the reason. Shown
   *  next to the answer rather than in the trace: a refusal the person
   *  never sees is indistinguishable from a failure. */
  limitations: string[];
  /** The plan, with each step's live state. Rendered as a stepper above the
   *  answer so "what is it doing right now" is answerable at a glance rather
   *  than by reading the trace. */
  steps: StepProgress[];
  /** Documents this run produced. Shown as cards in the message, because a
   *  file that only appears on another page is a file nobody finds. */
  artifacts: GeneratedArtifact[];
  /** The human gate, if this run hit one. */
  approval: ApprovalState | null;
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
  setPlan: (steps: PlanStep[]) => void;
  markStep: (stepId: string, state: "active" | "done", at: number) => void;
  addArtifact: (artifact: GeneratedArtifact) => void;
  setApproval: (approval: ApprovalState) => void;
  setRunning: (running: boolean) => void;
  setThinking: (thinking: boolean) => void;
  reset: () => void;
}

/** Apply a change to the most recent message, which is the one being streamed. */
function patchLastMessage(
  messages: ChatMessage[],
  patch: (last: ChatMessage) => ChatMessage,
): ChatMessage[] | null {
  if (!messages.length) return null;
  const next = [...messages];
  next[next.length - 1] = patch(next[next.length - 1]);
  return next;
}

export const useRun = create<RunState>((set) => ({
  messages: [],
  running: false,
  thinking: false,

  append: (message) => set((state) => ({ messages: [...state.messages, message] })),

  patchLast: (patch) =>
    set((state) => {
      const messages = patchLastMessage(state.messages, (last) => ({ ...last, ...patch }));
      return messages ? { messages } : state;
    }),

  appendToken: (text) =>
    set((state) => {
      const messages = patchLastMessage(state.messages, (last) => ({
        ...last,
        text: last.text + text,
      }));
      return messages ? { messages, thinking: false } : state;
    }),

  appendReasoning: (text) =>
    set((state) => {
      const messages = patchLastMessage(state.messages, (last) => ({
        ...last,
        reasoning: last.reasoning + text,
      }));
      return messages ? { messages } : state;
    }),

  addTrace: (item) =>
    set((state) => {
      const messages = patchLastMessage(state.messages, (last) => ({
        ...last,
        trace: [...last.trace, item],
      }));
      return messages ? { messages } : state;
    }),

  addCitation: (citation) =>
    set((state) => {
      const messages = patchLastMessage(state.messages, (last) =>
        last.citations.some((c) => c.chunk_id === citation.chunk_id)
          ? last
          : { ...last, citations: [...last.citations, citation] },
      );
      return messages ? { messages } : state;
    }),

  setPlan: (steps) =>
    set((state) => {
      const messages = patchLastMessage(state.messages, (last) => ({
        ...last,
        steps: steps.map((step) => ({ ...step, state: "pending" as const })),
      }));
      return messages ? { messages } : state;
    }),

  markStep: (stepId, stepState, at) =>
    set((state) => {
      const messages = patchLastMessage(state.messages, (last) => ({
        ...last,
        steps: last.steps.map((step) =>
          step.id !== stepId
            ? step
            : stepState === "active"
              ? { ...step, state: "active" as const, startedAt: at }
              : { ...step, state: "done" as const, finishedAt: at },
        ),
      }));
      return messages ? { messages } : state;
    }),

  addArtifact: (artifact) =>
    set((state) => {
      const messages = patchLastMessage(state.messages, (last) =>
        last.artifacts.some((a) => a.sha256 === artifact.sha256)
          ? last
          : { ...last, artifacts: [...last.artifacts, artifact] },
      );
      return messages ? { messages } : state;
    }),

  setApproval: (approval) =>
    set((state) => {
      const messages = patchLastMessage(state.messages, (last) => ({ ...last, approval }));
      return messages ? { messages } : state;
    }),

  setRunning: (running) => set({ running }),
  setThinking: (thinking) => set({ thinking }),
  reset: () => set({ messages: [], running: false, thinking: false }),
}));
