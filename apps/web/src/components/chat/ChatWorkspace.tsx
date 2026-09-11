"use client";

import { ArrowUp, Square } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Answer, CitationList } from "@/components/chat/Answer";
import { Welcome } from "@/components/chat/Welcome";
import { ApprovalBanner } from "@/components/chat/ApprovalBanner";
import { ArtifactCard } from "@/components/chat/ArtifactCard";
import { Inspector } from "@/components/chat/Inspector";
import { RunFooter } from "@/components/chat/RunFooter";
import { RunStepper } from "@/components/chat/RunStepper";
import { ReasoningPanel } from "@/components/trace/TraceTimeline";
import { Spinner } from "@/components/ui/primitives";
import { useAgentStream } from "@/hooks/useAgentStream";
import { cn, formatDuration } from "@/lib/utils";
import { useInspector } from "@/stores/inspector";
import { useRun, type ChatMessage } from "@/stores/run";

export function ChatWorkspace() {
  const { messages, running, thinking, conversationId } = useRun();
  const { send, cancel } = useAgentStream();
  const setTab = useInspector((s) => s.setTab);
  const router = useRouter();
  const pathname = usePathname();

  // The first run of a new chat is what names the conversation. Once it has
  // an id, the address bar gets it too, so a refresh or a shared link comes
  // back to the same saved session rather than a blank one.
  useEffect(() => {
    if (conversationId && !pathname.includes(conversationId)) {
      router.replace(`/chat/${conversationId}`);
    }
  }, [conversationId, pathname, router]);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const switchedFor = useRef<string | null>(null);

  // "/" focuses the composer from anywhere, as in every tool people already use.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        textareaRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const active = messages.at(-1);
  const question = [...messages].reverse().find((m) => m.role === "user")?.text ?? "";

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, active?.text, active?.artifacts.length, active?.approval?.status]);

  // A new run shows its trace; a finished run that read something shows the
  // map. Once per message, so a person who has moved to a source is not
  // yanked back.
  useEffect(() => {
    if (!active || active.role !== "assistant") return;
    if (active.status === "streaming" && switchedFor.current !== `${active.id}:start`) {
      switchedFor.current = `${active.id}:start`;
      setTab("trace");
    }
    if (active.status === "done" && active.citations.length && switchedFor.current !== `${active.id}:done`) {
      switchedFor.current = `${active.id}:done`;
      setTab("evidence");
    }
  }, [active, setTab]);

  function submit(event?: React.FormEvent) {
    event?.preventDefault();
    const q = input.trim();
    if (!q || running) return;
    setInput("");
    void send(q);
  }

  return (
    <div className="flex h-full">
      <section className="flex min-w-0 flex-1 flex-col">
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-5 py-6">
            {!messages.length ? (
              <Welcome
                onPick={(q) => {
                  setInput(q);
                  textareaRef.current?.focus();
                }}
              />
            ) : (
              <ol className="space-y-6">
                {messages.map((message) =>
                  message.role === "user" ? (
                    <li key={message.id} className="flex justify-end">
                      <p className="max-w-[85%] rounded-2xl rounded-br-md bg-surface-raised px-3.5 py-2 text-sm leading-relaxed shadow-card">
                        {message.text}
                      </p>
                    </li>
                  ) : (
                    <li key={message.id}>
                      <AssistantTurn message={message} running={running && message === active} thinking={thinking} />
                    </li>
                  ),
                )}
              </ol>
            )}
          </div>
        </div>

        <form onSubmit={submit} className="shrink-0 border-t border-border bg-surface/80 px-5 py-3 backdrop-blur">
          <div
            className={cn(
              "mx-auto flex max-w-3xl items-end gap-2 rounded-xl border bg-bg px-3 py-2 transition-shadow",
              "border-border focus-within:border-accent/50 focus-within:shadow-glow-sm",
            )}
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) submit(event);
              }}
              rows={1}
              placeholder="Ask about an SOP, an inspection reading, a drawing — or ask for a report   ( / to focus )"
              className="max-h-40 min-h-[1.75rem] flex-1 resize-none bg-transparent py-1 text-sm placeholder:text-fg-subtle focus:outline-none"
            />
            {running ? (
              <button
                type="button"
                onClick={cancel}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-danger/40 bg-danger/15 text-danger hover:bg-danger/25"
                title="Stop this run"
              >
                <Square size={13} />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-fg transition-colors hover:bg-accent/90 disabled:opacity-30"
                title="Send (Enter)"
              >
                <ArrowUp size={14} />
              </button>
            )}
          </div>
          <p className="mx-auto mt-1.5 max-w-3xl text-[10px] text-fg-subtle">
            Every answer is grounded in documents you are cleared to see. Nothing leaves this machine.
          </p>
        </form>
      </section>

      <Inspector message={active?.role === "assistant" ? active : null} question={question} running={running} />
    </div>
  );
}

/** One assistant turn: the plan, the answer, what it produced, and how much to trust it. */
function AssistantTurn({
  message,
  running,
  thinking,
}: {
  message: ChatMessage;
  running: boolean;
  thinking: boolean;
}) {
  const elapsed = useElapsed(message.startedAt, running);
  const model = message.trace.find((t) => t.kind === "route");
  const showSpinner = message.status === "streaming" && !message.text;

  return (
    <div className="turn-card animate-fade-in-up px-4 py-3.5">
      <div className="mb-2.5 flex items-center gap-2 text-2xs text-fg-subtle">
        <span className="flex h-5 w-5 items-center justify-center rounded bg-accent text-[9px] font-bold text-accent-fg" aria-hidden>
          MW
        </span>
        <span className="font-medium text-fg-muted">Workbench</span>
        {model && model.kind === "route" ? (
          <span className="rounded border border-border bg-bg px-1.5 py-px font-mono">{model.data.physical_model || model.data.model}</span>
        ) : null}
        <span className="tnum ml-auto">{running ? `${formatDuration(elapsed)} · live` : message.summary ? formatDuration(message.summary.wall_ms) : null}</span>
      </div>

      <RunStepper steps={message.steps} running={running} />

      {showSpinner ? (
        <div className="flex items-center gap-2 py-1 text-xs text-fg-subtle">
          <Spinner className="text-accent" />
          {thinking ? "Reading the sources…" : "Working…"}
        </div>
      ) : null}

      {message.text ? (
        <Answer text={message.text} citations={message.citations} streaming={message.status === "streaming"} />
      ) : null}

      {message.approval ? <ApprovalBanner approval={message.approval} /> : null}

      {message.artifacts.map((artifact) => (
        <ArtifactCard key={artifact.sha256} artifact={artifact} />
      ))}

      {message.limitations?.length ? (
        <div className="mt-3 space-y-1.5">
          {message.limitations.map((note) => (
            <p key={note} className="rounded-lg border border-warn/40 bg-warn/[0.08] px-3 py-2 text-xs leading-relaxed text-warn">
              <span className="font-semibold">Not generated — </span>
              {note}
            </p>
          ))}
        </div>
      ) : null}

      {message.error ? (
        <p
          className={cn(
            "mt-3 rounded-lg border px-3 py-2 text-xs leading-relaxed",
            message.status === "interrupted"
              ? "border-warn/40 bg-warn/[0.08] text-warn"
              : "border-danger/40 bg-danger/[0.08] text-danger",
          )}
        >
          {message.status === "interrupted" ? <span className="font-semibold">Incomplete — </span> : null}
          {message.error}
        </p>
      ) : null}

      <ReasoningPanel reasoning={message.reasoning} streaming={message.status === "streaming"} />

      {message.status !== "streaming" ? <CitationList citations={message.citations} /> : null}

      {message.summary ? <RunFooter summary={message.summary} validation={message.validation} /> : null}
    </div>
  );
}

function useElapsed(startedAt: number, running: boolean): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [running]);
  return now - startedAt;
}
