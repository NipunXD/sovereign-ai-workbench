"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FileText, MessageSquarePlus, Pencil, Search, Trash2, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { ConversationSummary } from "@/lib/types";
import { cn, relativeTime } from "@/lib/utils";
import { useRun } from "@/stores/run";

/**
 * Saved sessions, for the person who had them.
 *
 * Every conversation here is the caller's own — the server never returns
 * anyone else's, and a link to one would come back "not found" rather than
 * "forbidden", so the list cannot be used to learn that a conversation exists.
 * Grouped by day because "the one from yesterday about V-1201" is how people
 * actually remember them.
 */
export function SessionsSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const reset = useRun((s) => s.reset);
  const running = useRun((s) => s.running);
  const [search, setSearch] = useState("");
  const [collapsed, setCollapsed] = useState(false);

  const sessions = useQuery({
    queryKey: ["conversations", search],
    queryFn: () => api.conversations(search),
    refetchInterval: 20_000,
  });

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => api.renameConversation(id, title),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
  const archive = useMutation({
    mutationFn: (id: string) => api.archiveConversation(id),
    onSuccess: (_, id) => {
      void queryClient.invalidateQueries({ queryKey: ["conversations"] });
      if (pathname.includes(id)) {
        reset();
        router.push("/chat");
      }
    },
  });

  const groups = useMemo(() => groupByDay(sessions.data ?? []), [sessions.data]);
  const activeId = pathname.split("/chat/")[1]?.split("/")[0] ?? null;

  function startNew() {
    if (running) return;
    reset();
    router.push("/chat");
  }

  // On a projector at 1280 wide the answer column is what matters; the list
  // is one click away. Decided after mount so the server and client agree.
  useEffect(() => {
    if (window.innerWidth < 1280) setCollapsed(true);
  }, []);

  // ⌘/Ctrl+K starts a new session from anywhere in the chat.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        startNew();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  if (collapsed) {
    return (
      <aside className="flex w-10 shrink-0 flex-col items-center gap-2 border-r border-border bg-surface py-2">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="rounded p-1.5 text-fg-subtle hover:bg-surface-raised hover:text-fg"
          title="Show sessions"
        >
          <Search size={14} />
        </button>
        <button
          type="button"
          onClick={startNew}
          className="rounded p-1.5 text-accent hover:bg-accent/15"
          title="New session (⌘K)"
        >
          <MessageSquarePlus size={14} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="flex w-[16rem] shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-header shrink-0 items-center gap-1.5 border-b border-border px-3">
        <span className="section-label">Sessions</span>
        <span className="ml-auto flex items-center gap-0.5">
          <button
            type="button"
            onClick={startNew}
            disabled={running}
            className="flex h-7 items-center gap-1.5 rounded-lg bg-accent px-2.5 text-2xs font-semibold text-accent-fg shadow-xs transition-colors hover:bg-accent-strong disabled:opacity-40"
            title="New session (⌘K)"
          >
            <MessageSquarePlus size={12} /> New
          </button>
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            className="rounded p-1 text-fg-subtle hover:text-fg"
            title="Hide sessions"
          >
            <X size={12} />
          </button>
        </span>
      </div>

      <label className="relative m-2 block">
        <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-subtle" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search sessions"
          className="h-8 w-full rounded-lg border border-border bg-surface pl-7 pr-2 text-xs shadow-xs placeholder:text-fg-subtle focus:border-accent/60 focus:outline-none focus:ring-4 focus:ring-accent/10"
        />
      </label>

      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {!sessions.data?.length ? (
          <p className="px-2 py-6 text-center text-2xs leading-relaxed text-fg-subtle">
            {sessions.isLoading ? "Loading…" : search ? "Nothing matches." : "No saved sessions yet. Ask something and it will be kept here — for you only."}
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.label} className="mb-2">
              <p className="section-label px-2.5 pb-1.5 pt-2">
                {group.label}
              </p>
              <ul className="space-y-0.5">
                {group.items.map((item) => (
                  <SessionRow
                    key={item.id}
                    item={item}
                    active={item.id === activeId}
                    busy={rename.isPending || archive.isPending}
                    onRename={(title) => rename.mutate({ id: item.id, title })}
                    onArchive={() => archive.mutate(item.id)}
                  />
                ))}
              </ul>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}

function SessionRow({
  item,
  active,
  busy,
  onRename,
  onArchive,
}: {
  item: ConversationSummary;
  active: boolean;
  busy: boolean;
  onRename: (title: string) => void;
  onArchive: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(item.title);
  const [confirming, setConfirming] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) inputRef.current?.select();
  }, [editing]);

  function commit() {
    const next = title.trim();
    setEditing(false);
    if (next && next !== item.title) onRename(next);
    else setTitle(item.title);
  }

  return (
    <li
      className={cn(
        "group relative rounded-lg transition-colors",
        active ? "bg-accent-muted ring-1 ring-inset ring-accent/20" : "hover:bg-surface-raised",
      )}
    >
      {editing ? (
        <div className="flex items-center gap-1 px-2 py-1.5">
          <input
            ref={inputRef}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") {
                setTitle(item.title);
                setEditing(false);
              }
            }}
            onBlur={commit}
            className="h-7 min-w-0 flex-1 rounded-md border border-accent/60 bg-surface px-2 text-xs focus:outline-none focus:ring-4 focus:ring-accent/10"
          />
          <button type="button" onMouseDown={commit} className="rounded p-0.5 text-ok" title="Save">
            <Check size={12} />
          </button>
        </div>
      ) : (
        <Link href={`/chat/${item.id}`} className="block px-2.5 py-2">
          <span className={cn("block truncate text-xs", active ? "font-semibold text-accent" : "font-medium text-fg-muted group-hover:text-fg")}>
            {item.title}
          </span>
          <span className="mt-1 flex items-center gap-1.5 text-2xs text-fg-subtle">
            <span className="tnum">{relativeTime(item.updated_at)}</span>
            <span aria-hidden>·</span>
            <span className="tnum">{item.message_count} msg</span>
            {item.artifact_count ? (
              <span className="flex items-center gap-0.5 text-info" title={`${item.artifact_count} generated document(s)`}>
                <FileText size={9} /> {item.artifact_count}
              </span>
            ) : null}
          </span>
        </Link>
      )}

      {!editing ? (
        <span className="absolute right-1.5 top-1.5 hidden items-center gap-0.5 rounded-lg bg-surface p-0.5 shadow-sm ring-1 ring-border group-hover:flex">
          <button
            type="button"
            onClick={() => setEditing(true)}
            disabled={busy}
            className="rounded p-1 text-fg-subtle hover:bg-surface-raised hover:text-fg"
            title="Rename"
          >
            <Pencil size={11} />
          </button>
          {confirming ? (
            <button
              type="button"
              onClick={onArchive}
              onBlur={() => setConfirming(false)}
              disabled={busy}
              className="rounded bg-danger/20 px-1.5 py-0.5 text-[10px] font-medium text-danger"
              title="Click again to archive"
            >
              archive?
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              disabled={busy}
              className="rounded p-1 text-fg-subtle hover:bg-danger/15 hover:text-danger"
              title="Archive this session"
            >
              <Trash2 size={11} />
            </button>
          )}
        </span>
      ) : null}
    </li>
  );
}

function groupByDay(items: ConversationSummary[]): Array<{ label: string; items: ConversationSummary[] }> {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 86_400_000;
  const buckets: Record<string, ConversationSummary[]> = {};
  for (const item of items) {
    const t = Date.parse(item.updated_at);
    const label =
      t >= startOfToday
        ? "Today"
        : t >= startOfToday - day
          ? "Yesterday"
          : t >= startOfToday - 6 * day
            ? "This week"
            : "Earlier";
    (buckets[label] ??= []).push(item);
  }
  return ["Today", "Yesterday", "This week", "Earlier"]
    .filter((label) => buckets[label]?.length)
    .map((label) => ({ label, items: buckets[label] }));
}
