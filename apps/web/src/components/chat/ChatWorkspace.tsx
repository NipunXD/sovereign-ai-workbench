"use client";

import { ArrowUp, ShieldAlert, Square } from "lucide-react";
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
  const { messages, running, thinking } = useRun();
  const { send, cancel } = useAgentStream();
  const setTab = useInspector((s) => s.setTab);

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
          <div className="mx-auto max-w-3xl px-5 py-7">
            {!messages.length ? (
              <Welcome
                onPick={(q) => {
                  setInput(q);
                  textareaRef.current?.focus();
                }}
              />
            ) : (
              <ol className="space-y-7">
                {messages.map((message) =>
                  message.role === "user" ? (
                    <li key={message.id} className="flex justify-end">
                      <p className="ask-bubble max-w-[85%]">{message.text}</p>
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

        <form onSubmit={submit} className="shrink-0 border-t border-border bg-surface px-5 py-4">
          <div
            className={cn(
              "mx-auto flex max-w-3xl items-end gap-2 rounded-xl border bg-surface px-3.5 py-2.5",
              "shadow-card transition-[border-color,box-shadow] duration-150",
              "border-border focus-within:border-accent/50 focus-within:shadow-glow",
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
              className="max-h-40 min-h-[1.75rem] flex-1 resize-none bg-transparent py-1 text-md leading-relaxed placeholder:text-fg-subtle focus:outline-none"
            />
            {running ? (
              <button
                type="button"
                onClick={cancel}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-danger/30 bg-danger/10 text-danger transition-colors hover:bg-danger/20"
                title="Stop this run"
              >
                <Square size={13} />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent text-accent-fg shadow-xs transition-colors hover:bg-accent-strong disabled:opacity-30"
                title="Send (Enter)"
              >
                <ArrowUp size={14} />
              </button>
            )}
          </div>
          <p className="mx-auto mt-2 max-w-3xl text-2xs text-fg-subtle">
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
    <div className="turn-card animate-fade-in-up px-5 py-4">
      <div className="mb-2.5 flex items-center gap-2 text-2xs text-fg-subtle">
        <span className="brand-mark flex h-6 w-6 items-center justify-center rounded-md text-[9px] font-bold text-white" aria-hidden>
          MW
        </span>
        <span className="text-xs font-semibold text-fg">Workbench</span>
        {model && model.kind === "route" ? (
          <span className="rounded-full bg-surface-raised px-2 py-0.5 font-mono text-2xs text-fg-muted ring-1 ring-inset ring-border">
            {model.data.physical_model || model.data.model}
          </span>
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

      {message.policy ? (
        <PolicyRefusal policy={message.policy} />
      ) : message.text ? (
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

      {message.summary && !message.policy ? (
        <RunFooter summary={message.summary} validation={message.validation} />
      ) : null}
    </div>
  );
}

/**
 * A request the workbench declined to take.
 *
 * Deliberately not styled like an answer and not like an error. Nothing went
 * wrong and nothing was searched: a rule was applied, and saying which one —
 * and that it was written down — is the honest version of a refusal. The
 * category is shown because "recorded in the audit log" is checkable, and the
 * auditor can find this exact event.
 */
function PolicyRefusal({ policy }: { policy: { category: string; message: string } }) {
  return (
    <div className="mt-1 flex gap-3 rounded-xl border border-danger/25 bg-danger/[0.05] p-4">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-danger/10 text-danger">
        <ShieldAlert size={18} />
      </span>
      <div className="min-w-0">
        <p className="text-sm font-semibold text-danger">Request refused</p>
        <p className="mt-1 text-md leading-relaxed text-fg">{policy.message}</p>
        <p className="mt-2.5 flex flex-wrap items-center gap-2 text-2xs text-fg-subtle">
          <span className="rounded-full bg-danger/10 px-2 py-0.5 font-mono font-medium text-danger">
            {policy.category.replace(/_/g, " ")}
          </span>
          Checked before any model ran · written to the audit log
        </p>
      </div>
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
