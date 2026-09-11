"use client";

import { useCallback, useRef } from "react";

import { getAccessToken } from "@/lib/api";
import { streamRequest } from "@/lib/sse";
import type {
  ApprovalState,
  Citation,
  GeneratedArtifact,
  PlanStep,
  RouteDecision,
  RunSummary,
  ValidationReport,
} from "@/lib/types";
import { useRun } from "@/stores/run";

/** How long silence has to last before the stream is treated as dead.
 *
 *  Six missed heartbeats. The threshold only means anything because the server
 *  heartbeats through the *whole* run — an earlier version of this assumed it
 *  did while only the replay endpoint actually did, so any genuine pause past
 *  90s aborted a healthy run. The approval wait, silent for three minutes by
 *  design, hit it every time. */
const STALL_AFTER_MS = 90_000;
const STALL_CHECK_MS = 5_000;

/**
 * Drives one agent run and reduces its trace into the run store.
 *
 * Token events are buffered and flushed on an animation frame. A local model
 * emits hundreds of fragments, and re-rendering the markdown on every one of
 * them makes the whole page stutter — batching to the frame rate makes it
 * smooth without any visible delay.
 */
export function useAgentStream() {
  const controller = useRef<AbortController | null>(null);
  const buffer = useRef("");
  const reasoningBuffer = useRef("");
  const frame = useRef<number | null>(null);

  const flush = useCallback(() => {
    frame.current = null;
    const store = useRun.getState();
    if (buffer.current) {
      store.appendToken(buffer.current);
      buffer.current = "";
    }
    if (reasoningBuffer.current) {
      store.appendReasoning(reasoningBuffer.current);
      reasoningBuffer.current = "";
    }
  }, []);

  const schedule = useCallback(() => {
    if (frame.current === null) frame.current = requestAnimationFrame(flush);
  }, [flush]);

  const cancel = useCallback(() => {
    controller.current?.abort();
    controller.current = null;
    const store = useRun.getState();
    store.setRunning(false);
    store.setThinking(false);
    store.patchLast({ status: "cancelled" });
  }, []);

  const send = useCallback(
    async (message: string) => {
      const store = useRun.getState();
      if (store.running) return;

      store.append({
        id: `u-${Date.now()}`,
        role: "user",
        text: message,
        citations: [],
        trace: [],
        reasoning: "",
        validation: null,
        summary: null,
        status: "done",
        limitations: [],
        steps: [],
        artifacts: [],
        approval: null,
        startedAt: Date.now(),
      });
      store.append({
        id: `a-${Date.now()}`,
        role: "assistant",
        text: "",
        citations: [],
        trace: [],
        reasoning: "",
        validation: null,
        summary: null,
        status: "streaming",
        limitations: [],
        steps: [],
        artifacts: [],
        approval: null,
        startedAt: Date.now(),
      });
      store.setRunning(true);
      store.setThinking(true);

      controller.current = new AbortController();

      // Whether the run told us it ended. Without this the `finally` below
      // marked every stream that stopped as "done", so a run cut off halfway
      // was presented as one that had completed.
      let finished = false;
      let lastEventAt = Date.now();

      // The server heartbeats every 15s through the whole run, so silence
      // this long means the connection is gone rather than the run being
      // slow. Saying so beats a spinner that never resolves.
      const watchdog = window.setInterval(() => {
        if (Date.now() - lastEventAt < STALL_AFTER_MS) return;
        window.clearInterval(watchdog);
        controller.current?.abort();
      }, STALL_CHECK_MS);

      try {
        for await (const event of streamRequest("/api/v1/chat/stream", {
          body: { message },
          token: getAccessToken(),
          signal: controller.current.signal,
          // Heartbeats count. The run is legitimately silent for minutes
          // while a document waits on a human approver, and treating that as
          // a dead connection killed a run that was working.
          onActivity: () => {
            lastEventAt = Date.now();
          },
        })) {
          const at = Date.now();
          lastEventAt = at;
          let payload: Record<string, unknown>;
          try {
            payload = JSON.parse(event.data);
          } catch {
            continue;
          }
          const run = useRun.getState();

          switch (event.event) {
            case "token":
              buffer.current += String(payload.text ?? "");
              schedule();
              break;

            case "reasoning":
              reasoningBuffer.current += String(payload.text ?? "");
              schedule();
              break;

            case "route_decision":
              run.addTrace({ kind: "route", at, data: payload as unknown as RouteDecision });
              break;

            case "plan_created": {
              const steps = (payload.steps ?? []) as PlanStep[];
              run.setPlan(steps);
              run.addTrace({ kind: "plan", at, steps, rationale: String(payload.rationale ?? "") });
              break;
            }

            case "step_started":
              run.markStep(String(payload.step_id ?? ""), "active", at);
              run.addTrace({
                kind: "step",
                at,
                stepId: String(payload.step_id ?? ""),
                intent: String(payload.intent ?? ""),
                description: String(payload.description ?? ""),
              });
              break;

            case "step_finished":
              run.markStep(String(payload.step_id ?? ""), "done", at);
              break;

            case "artifact_created": {
              // A produced document is a first-class result, not a line in
              // the trace. It goes on the message as a card.
              const artifact = payload as unknown as GeneratedArtifact;
              run.addArtifact(artifact);
              run.addTrace({ kind: "artifact", at, artifact });
              break;
            }

            case "approval_required": {
              // Two events share this name: the request, and the decision.
              // Both update one state so the banner moves from "waiting"
              // to "approved by …" in place rather than stacking.
              const previous = run.messages.at(-1)?.approval;
              const status = String(payload.status ?? "pending") as ApprovalState["status"];
              const approval: ApprovalState = {
                approval_id: String(payload.approval_id ?? previous?.approval_id ?? ""),
                tool: String(payload.tool ?? previous?.tool ?? ""),
                status,
                requested_at: previous?.requested_at ?? at,
                expires_at: (payload.expires_at as string | null) ?? previous?.expires_at ?? null,
                waiting_s: Number(payload.waiting_s ?? previous?.waiting_s ?? 240),
                decided_by: (payload.decided_by as string | null) ?? null,
                decided_at: (payload.decided_at as string | null) ?? null,
                comment: (payload.comment as string | null) ?? null,
              };
              run.setApproval(approval);
              run.addTrace({ kind: "approval", at, approval });
              break;
            }

            case "retrieval_result":
              run.addTrace({
                kind: "retrieval",
                at,
                query: String(payload.query ?? ""),
                hits: (payload.hits ?? []) as never[],
                total: Number(payload.total_evidence ?? 0),
              });
              break;

            case "tool_call":
              run.addTrace({
                kind: "tool",
                at,
                tool: String(payload.tool ?? ""),
                args: payload.args as Record<string, unknown> | undefined,
              });
              break;

            case "tool_result":
              run.addTrace({
                kind: "tool",
                at,
                tool: String(payload.tool ?? ""),
                ok: Boolean(payload.ok),
                error: payload.error ? String(payload.error) : undefined,
                metrics: payload.metrics as Record<string, unknown> | undefined,
              });
              break;

            case "citation":
              run.addCitation(payload as unknown as Citation);
              break;

            case "answer": {
              // Tokens streamed live still carry raw [[cite:...]] markers,
              // because a marker cannot be renumbered until the surrounding
              // text exists. This is the resolved version.
              flush();
              run.patchLast({
                text: String(payload.text ?? ""),
                citations: (payload.citations ?? []) as Citation[],
              });
              break;
            }

            case "validation":
              run.patchLast({ validation: payload as unknown as ValidationReport });
              run.addTrace({ kind: "validation", at, data: payload as unknown as ValidationReport });
              break;

            case "error":
              run.addTrace({
                kind: "error",
                at,
                code: String(payload.code ?? "error"),
                message: String(payload.message ?? ""),
                recoverable: Boolean(payload.recoverable),
              });
              break;

            case "limitation": {
              const message = String(payload.message ?? "");
              const existing = run.messages.at(-1)?.limitations ?? [];
              if (message && !existing.includes(message)) {
                run.patchLast({ limitations: [...existing, message] });
              }
              break;
            }

            case "run_finished":
              finished = true;
              flush();
              run.patchLast({
                summary: payload as unknown as RunSummary,
                status: payload.status === "failed" ? "error" : "done",
              });
              break;
          }
        }
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          useRun.getState().patchLast({
            status: "error",
            error: error instanceof Error ? error.message : "The request failed.",
          });
        }
      } finally {
        window.clearInterval(watchdog);
        flush();
        const done = useRun.getState();
        done.setRunning(false);
        done.setThinking(false);
        if (done.messages.at(-1)?.status === "streaming") {
          done.patchLast(
            finished
              ? { status: "done" }
              : {
                  status: "interrupted",
                  error:
                    "The connection ended before this run finished. Anything above " +
                    "arrived before the cut and may be incomplete — the run itself " +
                    "may also have stopped on the server.",
                },
          );
        }
        controller.current = null;
      }
    },
    [flush, schedule],
  );

  return { send, cancel };
}
