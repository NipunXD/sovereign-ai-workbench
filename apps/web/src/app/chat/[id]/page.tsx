"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useEffect, useRef } from "react";

import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { EmptyState, Spinner } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { hydrateConversation } from "@/lib/replay";
import { useRun } from "@/stores/run";

/**
 * A saved conversation, rebuilt from storage.
 *
 * If the store already holds this conversation — the run that created it is
 * still on screen — nothing is reloaded; replaying over a live view would
 * duplicate it. Otherwise the stored runs are replayed through the same
 * reducer the live stream used, so what comes back is what was there.
 */
export default function SavedChatPage() {
  const { id } = useParams<{ id: string }>();
  const conversationId = useRun((s) => s.conversationId);
  const running = useRun((s) => s.running);
  const needsLoad = conversationId !== id;

  const detail = useQuery({
    queryKey: ["conversation", id],
    queryFn: () => api.conversation(id),
    enabled: needsLoad && !running,
    staleTime: 30_000,
  });

  // Replayed at most once per conversation. Without this, clearing the store
  // to start a new chat — which happens while this page is still mounted —
  // flipped `needsLoad` back to true, the query cache still held this
  // conversation, and it hydrated itself straight back in. The new chat then
  // opened onto the id that had just been restored, so the New button looked
  // like it was reloading the conversation you were trying to leave.
  const replayed = useRef<string | null>(null);

  useEffect(() => {
    if (!detail.data || !needsLoad || running) return;
    if (replayed.current === id) return;
    replayed.current = id;
    hydrateConversation(detail.data);
  }, [detail.data, needsLoad, running, id]);

  if (needsLoad && !detail.isError) {
    // Cached data can arrive before the hydration effect has run; showing
    // the workspace in that gap would flash the previous conversation.
    return (
      <div className="flex h-full items-center justify-center gap-2 text-xs text-fg-subtle">
        <Spinner /> Opening the conversation…
      </div>
    );
  }
  if (detail.isError) {
    return (
      <EmptyState
        title="No such conversation"
        hint="It may have been archived, or it belongs to someone else — saved sessions are only ever visible to the person who had them."
      />
    );
  }
  return <ChatWorkspace />;
}
