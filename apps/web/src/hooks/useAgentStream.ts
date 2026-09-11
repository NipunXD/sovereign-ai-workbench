"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef } from "react";

import { getAccessToken } from "@/lib/api";
import { applyEvent } from "@/lib/replay";
import { streamRequest } from "@/lib/sse";
import { emptyAssistant, useRun } from "@/stores/run";

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
 *
 * The events themselves are reduced by `applyEvent`, shared with the code that
 * replays a saved conversation, so live and reopened runs are built the same
 * way.
 */
export function useAgentStream() {
  const queryClient = useQueryClient();
  const controller = useRef<AbortController | null>(null);
  const buffer = useRef("");
  const reasoningBuffer = useRef("");
  const frame = useRef<number | null>(null);

  const flush = useCallback(() => {
    if (buffer.current) {
      useRun.getState().appendToken(buffer.current);
      buffer.current = "";
    }
    if (reasoningBuffer.current) {
      useRun.getState().appendReasoning(reasoningBuffer.current);
      reasoningBuffer.current = "";
    }
    frame.current = null;
  }, []);

  const schedule = useCallback(() => {
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(flush);
  }, [flush]);

  const cancel = useCallback(() => {
    controller.current?.abort();
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
        ...emptyAssistant(`u-${Date.now()}`, Date.now()),
        role: "user",
        text: message,
        status: "done",
      });
      store.append(emptyAssistant(`a-${Date.now()}`, Date.now()));
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

      const io = {
        token: (text: string) => {
          buffer.current += text;
          schedule();
        },
        reasoning: (text: string) => {
          reasoningBuffer.current += text;
          schedule();
        },
      };

      try {
        for await (const event of streamRequest("/api/v1/chat/stream", {
          // Continuing a saved conversation, or starting one the server names.
          body: { message, conversation_id: store.conversationId },
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
          // The resolved answer replaces the token stream; flush first so a
          // late fragment cannot land on top of the final text.
          if (event.event === "answer" || event.event === "run_finished") flush();
          const result = applyEvent(event.event, payload, at, io);
          if (result.finished) finished = true;
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
        // The sidebar's list: a new conversation appears, an old one moves up.
        void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      }
    },
    [flush, schedule, queryClient],
  );

  return { send, cancel };
}
