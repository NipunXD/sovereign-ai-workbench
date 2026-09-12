"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { useRun } from "@/stores/run";

/** A new conversation. The first run names it and the URL follows. */
export default function NewChatPage() {
  const router = useRouter();
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

  // The first run names the conversation; the address bar follows, so a
  // refresh or a shared link comes back to the same saved session. Only this
  // page steers the URL — the saved-session page doing the same thing while
  // it was still loading the *other* conversation made the two fight, and
  // the screen flipped between them.
  //
  // Only an id that appears *after* this page mounts is followed. An id that
  // was already in the store belongs to the conversation being left behind,
  // and redirecting to it sent you straight back to it.
  const arrivedWith = useRef(conversationId);

  useEffect(() => {
    if (conversationId && conversationId !== arrivedWith.current) {
      router.replace(`/chat/${conversationId}`);
    }
  }, [conversationId, router]);

  return <ChatWorkspace />;
}
