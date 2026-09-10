"use client";

import { CornerDownLeft, Square, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Answer, CitationList } from "@/components/chat/Answer";
import { DocumentViewer } from "@/components/documents/DocumentViewer";
import { ReasoningPanel, TraceTimeline } from "@/components/trace/TraceTimeline";
import { Button, Chip, Spinner } from "@/components/ui/primitives";
import { useAgentStream } from "@/hooks/useAgentStream";
import { cn, formatDuration } from "@/lib/utils";
import { useRun } from "@/stores/run";
import { useSession } from "@/stores/session";
import { useViewer } from "@/stores/viewer";

/** Questions that exercise the parts of the system worth showing. */
const SUGGESTIONS = [
  "What is the depressurisation rate limit for V-1201, and what hold time does the SOP require?",
  "Using the 2023 and 2029 CML-04 readings for V-1201, compute the corrosion rate and remaining life against the 8.0 mm minimum.",
  "What is the vibration alert threshold for P-101A, and has it been exceeded?",
  "What was the purge duration used during the 2019 turnaround?",
];

export default function ChatPage() {
  const { messages, running, thinking } = useRun();
  const { send, cancel } = useAgentStream();
  const { principal } = useSession();
  const viewerOpen = useViewer((state) => state.docId !== null);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const active = messages.at(-1);
  const showTrace = active?.role === "assistant" && active.trace.length > 0;

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, active?.text]);

  function submit(event?: React.FormEvent) {
    event?.preventDefault();
    const question = input.trim();
    if (!question || running) return;
    setInput("");
    void send(question);
  }

  return (
    <div className="flex h-full">
      {/* --- conversation --- */}
      <section className="flex min-w-0 flex-1 flex-col">
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-4">
            {!messages.length ? (
              <div className="mt-8">
                <div className="mb-4 flex items-center gap-2">
                  <Sparkles size={15} className="text-accent" />
                  <h2 className="text-sm font-semibold">
                    Ask about the indexed plant documents
                  </h2>
                </div>
                <p className="mb-4 max-w-xl text-xs leading-relaxed text-fg-muted">
                  Answers are grounded in documents you are cleared to see, and every
                  claim carries a citation you can open. If the corpus does not cover
                  something, the workbench says so rather than guessing.
                </p>
                <div className="grid gap-1.5">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => {
                        setInput(suggestion);
                        textareaRef.current?.focus();
                      }}
                      className="rounded border border-border bg-surface px-3 py-2 text-left text-xs text-fg-muted transition-colors hover:border-border-strong hover:text-fg"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
                <p className="mt-4 text-2xs text-fg-subtle">
                  Signed in as{" "}
                  <span className="font-mono text-fg-muted">{principal?.username}</span> with{" "}
                  <span className="text-fg-muted">{principal?.clearance}</span> clearance —
                  the last question above is deliberately unanswerable from the corpus.
                </p>
              </div>
            ) : (
              <ol className="space-y-5">
                {messages.map((message) => (
                  <li key={message.id}>
                    {message.role === "user" ? (
                      <div className="flex justify-end">
                        <p className="max-w-[85%] rounded-lg rounded-br-sm bg-surface-raised px-3 py-2 text-sm">
                          {message.text}
                        </p>
                      </div>
                    ) : (
                      <div>
                        {message.status === "streaming" && !message.text ? (
                          <div className="flex items-center gap-2 text-xs text-fg-subtle">
                            <Spinner />
                            {thinking ? "Thinking…" : "Working…"}
                          </div>
                        ) : null}

                        {message.text ? (
                          <Answer
                            text={message.text}
                            citations={message.citations}
                            streaming={message.status === "streaming"}
                          />
                        ) : null}

                        <ReasoningPanel
                          reasoning={message.reasoning}
                          streaming={message.status === "streaming"}
                        />

                        {message.status !== "streaming" ? (
                          <CitationList citations={message.citations} />
                        ) : null}

                        {message.error ? (
                          <p className="mt-2 rounded border border-danger/40 bg-danger/10 px-2 py-1.5 text-xs text-danger">
                            {message.error}
                          </p>
                        ) : null}

                        {message.summary ? (
                          <div className="mt-2 flex flex-wrap items-center gap-1.5">
                            <Chip>{formatDuration(message.summary.wall_ms)}</Chip>
                            <Chip>
                              {message.summary.budget.tool_calls_used} tool call
                              {message.summary.budget.tool_calls_used === 1 ? "" : "s"}
                            </Chip>
                            <Chip>{message.summary.evidence_used} passages</Chip>
                            {message.validation?.is_refusal ? (
                              <Chip tone="warn">declined — not in the corpus</Chip>
                            ) : message.validation ? (
                              <Chip
                                tone={message.validation.grounded_ratio >= 0.8 ? "ok" : "warn"}
                              >
                                {Math.round(message.validation.grounded_ratio * 100)}% grounded
                              </Chip>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>

        <form
          onSubmit={submit}
          className="shrink-0 border-t border-border bg-surface px-4 py-3"
        >
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) submit(event);
              }}
              rows={1}
              placeholder="Ask about an SOP, an inspection reading, a drawing…"
              className="max-h-40 min-h-[2.25rem] flex-1 resize-y rounded border border-border bg-bg px-2.5 py-2 text-sm placeholder:text-fg-subtle focus:border-accent/60"
            />
            {running ? (
              <Button type="button" variant="danger" onClick={cancel} title="Stop this run">
                <Square size={12} /> Stop
              </Button>
            ) : (
              <Button type="submit" variant="primary" disabled={!input.trim()}>
                <CornerDownLeft size={12} /> Send
              </Button>
            )}
          </div>
        </form>
      </section>

      {/* --- trace --- */}
      <aside
        className={cn(
          "flex w-80 shrink-0 flex-col border-l border-border bg-surface",
          "xl:w-96",
        )}
      >
        <div className="panel-header shrink-0 border-b">
          <span>Execution trace</span>
          {running ? <Spinner className="text-accent" /> : null}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {showTrace && active ? (
            <TraceTimeline trace={active.trace} startedAt={active.startedAt} />
          ) : (
            <p className="px-3 py-4 text-xs text-fg-subtle">
              Plan, routing, retrieval and tool calls appear here while the agent works.
            </p>
          )}
        </div>
      </aside>

      {/* --- source viewer, opened by a citation --- */}
      {viewerOpen ? (
        <aside className="flex w-[26rem] shrink-0 flex-col border-l border-border bg-surface 2xl:w-[32rem]">
          <DocumentViewer />
        </aside>
      ) : null}
    </div>
  );
}
