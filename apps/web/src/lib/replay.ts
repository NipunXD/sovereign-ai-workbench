import type {
  ApprovalState,
  Citation,
  ConversationDetail,
  GeneratedArtifact,
  PlanStep,
  RouteDecision,
  RunSummary,
  ValidationReport,
} from "@/lib/types";
import { emptyAssistant, useRun, type ChatMessage } from "@/stores/run";

/**
 * One reducer for trace events, whether they arrive live or from storage.
 *
 * The live stream and a reopened conversation must look identical — the
 * same stepper, the same evidence map, the same artifact cards — and the only
 * way to guarantee that is to build both from the same code. The server
 * stores each run's events (minus the token firehose) and the browser replays
 * them through this function; the live hook calls it per event as they land.
 *
 * Token and reasoning fragments are the one asymmetry: live, they are
 * buffered and flushed on animation frames; replayed, the final text arrives
 * whole. The caller supplies how to handle them.
 */
export interface StreamIO {
  token: (text: string) => void;
  reasoning: (text: string) => void;
}

export function applyEvent(
  name: string,
  payload: Record<string, unknown>,
  at: number,
  io: StreamIO,
): { finished: boolean } {
  const run = useRun.getState();

  switch (name) {
    case "token":
      io.token(String(payload.text ?? ""));
      return { finished: false };

    case "reasoning":
      io.reasoning(String(payload.text ?? ""));
      return { finished: false };

    case "run_started":
      if (typeof payload.conversation_id === "string" && payload.conversation_id) {
        run.setConversationId(payload.conversation_id);
      }
      return { finished: false };

    case "route_decision":
      run.addTrace({ kind: "route", at, data: payload as unknown as RouteDecision });
      return { finished: false };

    case "plan_created": {
      const steps = (payload.steps ?? []) as PlanStep[];
      run.setPlan(steps);
      run.addTrace({ kind: "plan", at, steps, rationale: String(payload.rationale ?? "") });
      return { finished: false };
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
      return { finished: false };

    case "step_finished":
      run.markStep(String(payload.step_id ?? ""), "done", at);
      return { finished: false };

    case "retrieval_result":
      run.addTrace({
        kind: "retrieval",
        at,
        query: String(payload.query ?? ""),
        hits: (payload.hits ?? []) as never[],
        total: Number(payload.total_evidence ?? 0),
      });
      return { finished: false };

    case "tool_call":
      run.addTrace({
        kind: "tool",
        at,
        tool: String(payload.tool ?? ""),
        args: payload.args as Record<string, unknown> | undefined,
      });
      return { finished: false };

    case "tool_result":
      run.addTrace({
        kind: "tool",
        at,
        tool: String(payload.tool ?? ""),
        ok: Boolean(payload.ok),
        error: payload.error ? String(payload.error) : undefined,
        refused: Boolean(payload.refused),
        metrics: payload.metrics as Record<string, unknown> | undefined,
        display: payload.display as Record<string, unknown> | undefined,
      });
      return { finished: false };

    case "artifact_created": {
      const artifact = payload as unknown as GeneratedArtifact;
      run.addArtifact(artifact);
      run.addTrace({ kind: "artifact", at, artifact });
      return { finished: false };
    }

    case "approval_required": {
      // Two events share this name: the request, and the decision. Both
      // update one state so the banner moves in place from "waiting" to
      // "approved by …" rather than stacking.
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
      return { finished: false };
    }

    case "policy_refused":
      run.patchLast({
        policy: {
          category: String(payload.category ?? ""),
          message: String(payload.message ?? ""),
        },
      });
      return { finished: false };

    case "citation":
      run.addCitation(payload as unknown as Citation);
      return { finished: false };

    case "answer":
      run.patchLast({
        text: String(payload.text ?? ""),
        citations: (payload.citations ?? []) as Citation[],
      });
      return { finished: false };

    case "validation":
      run.patchLast({ validation: payload as unknown as ValidationReport });
      run.addTrace({ kind: "validation", at, data: payload as unknown as ValidationReport });
      return { finished: false };

    case "limitation": {
      const message = String(payload.message ?? "");
      const existing = run.messages.at(-1)?.limitations ?? [];
      if (message && !existing.includes(message)) {
        run.patchLast({ limitations: [...existing, message] });
      }
      return { finished: false };
    }

    case "error":
      run.addTrace({
        kind: "error",
        at,
        code: String(payload.code ?? "error"),
        message: String(payload.message ?? ""),
        recoverable: Boolean(payload.recoverable),
      });
      return { finished: false };

    case "run_finished":
      run.patchLast({
        summary: payload as unknown as RunSummary,
        status: payload.status === "failed" ? "error" : "done",
      });
      return { finished: true };

    default:
      return { finished: false };
  }
}


/**
 * Rebuild the view from a stored conversation by replaying its runs.
 *
 * Each assistant turn is seeded empty, its run's stored events are applied in
 * order — the same calls the live stream made — and the final text and
 * citations are set from the message record, which is authoritative even for
 * a run that was cut off before it could say it had finished.
 */
export function hydrateConversation(detail: ConversationDetail): void {
  const store = useRun.getState();
  const runs = new Map(detail.runs.map((r) => [r.id, r]));
  store.reset();
  store.setConversationId(detail.id);

  for (const message of detail.messages) {
    if (message.role === "user") {
      store.append({
        ...emptyAssistant(message.id, Date.parse(message.created_at)),
        role: "user",
        text: message.content,
        status: "done",
      });
      continue;
    }
    const run = message.run_id ? runs.get(message.run_id) : undefined;
    const startedAt = run ? Date.parse(run.started_at) : Date.parse(message.created_at);
    store.append(emptyAssistant(message.id, startedAt));

    const io: StreamIO = {
      token: () => undefined,
      reasoning: (text) => useRun.getState().appendReasoning(text),
    };
    for (const event of run?.trace ?? []) {
      applyEvent(event.name, event.data, startedAt + event.at_ms, io);
    }
    // Documents produced after the run had ended — an approval that landed
    // later — are not in its trace, only in the conversation's artifacts.
    for (const artifact of detail.artifacts) {
      if (artifact.run_id === message.run_id) useRun.getState().addArtifact(artifact);
    }
    const status: ChatMessage["status"] =
      run?.status === "cancelled" ? "interrupted" : run?.status === "failed" ? "error" : "done";
    useRun.getState().patchLast({ text: message.content, citations: message.citations, status });
  }
}
