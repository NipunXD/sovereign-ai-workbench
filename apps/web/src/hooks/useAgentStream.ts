"use client";

import { useCallback, useRef } from "react";

import { getAccessToken } from "@/lib/api";
import { streamRequest } from "@/lib/sse";
import type { Citation, PlanStep, RouteDecision, RunSummary, ValidationReport } from "@/lib/types";
import { useRun } from "@/stores/run";

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
        startedAt: Date.now(),
      });
      store.setRunning(true);
      store.setThinking(true);

      controller.current = new AbortController();

      try {
        for await (const event of streamRequest("/api/v1/chat/stream", {
          body: { message },
          token: getAccessToken(),
          signal: controller.current.signal,
        })) {
          const at = Date.now();
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

            case "plan_created":
              run.addTrace({
                kind: "plan",
                at,
                steps: (payload.steps ?? []) as PlanStep[],
                rationale: String(payload.rationale ?? ""),
              });
              break;

            case "step_started":
              run.addTrace({
                kind: "step",
                at,
                stepId: String(payload.step_id ?? ""),
                intent: String(payload.intent ?? ""),
                description: String(payload.description ?? ""),
              });
              break;

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

            case "run_finished":
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
        flush();
        const done = useRun.getState();
        done.setRunning(false);
        done.setThinking(false);
        if (done.messages.at(-1)?.status === "streaming") {
          done.patchLast({ status: "done" });
        }
        controller.current = null;
      }
    },
    [flush, schedule],
  );

  return { send, cancel };
}
