"use client";

import { useEffect } from "react";

import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { useRun } from "@/stores/run";

/** A new conversation. The first run names it and the URL follows. */
export default function NewChatPage() {
  const reset = useRun((s) => s.reset);
  const conversationId = useRun((s) => s.conversationId);
  const running = useRun((s) => s.running);

  useEffect(() => {
    // Arriving here from a saved session means "start fresh" — but not while
    // a run is streaming, and not while the workspace is mid-navigation to
    // the id it was just given.
    if (conversationId && !running) reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <ChatWorkspace />;
}
