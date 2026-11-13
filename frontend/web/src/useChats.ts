import { useCallback, useEffect, useState } from "react";
import { type Chat, type CliSession, deleteSyncedSession, deleteThread, getCliSessions, getThreads, updateThread } from "./api";

/** The list of saved chats, and the terminal's sessions */
export function useChats() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [sessions, setSessions] = useState<CliSession[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setChats(await getThreads());
      setError(null);
    } catch (e) {
      setError(`Couldn't load chats: ${(e as Error).message}`);
    }
  }, []);

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await getCliSessions());
    } catch {
      // The Chats tab shows the error
    }
  }, []);

  const remove = useCallback(
    async (threadId: string) => {
      await deleteThread(threadId);
      await refresh();
    },
    [refresh],
  );

  const update = useCallback(
    async (threadId: string, change: { title?: string; pinned?: boolean; shared?: boolean }) => {
      // Show it right away; the refresh brings the server's version (e.g. a trimmed title)
      setChats(chats => chats.map(chat => (chat.thread_id === threadId ? { ...chat, ...change } : chat)));
      try {
        await updateThread(threadId, change);
      } finally {
        await refresh();
      }
    },
    [refresh],
  );

  const removeSession = useCallback(
    async (threadId: string) => {
      await deleteSyncedSession(threadId);
      await refreshSessions();
    },
    [refreshSessions],
  );

  /** Opened: no more unread dot (the server clears it when the chat is loaded) */
  const markRead = useCallback((threadId: string) => {
    setChats(chats => chats.map(chat => (chat.thread_id === threadId && chat.unread ? { ...chat, unread: false } : chat)));
  }, []);

  useEffect(() => {
    refresh();
    refreshSessions();
  }, [refresh, refreshSessions]);

  // Tasks write to chats in the background: look for them now and then, and when the page is back
  useEffect(() => {
    const timer = setInterval(() => document.visibilityState === "visible" && refresh(), 30_000);
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);

  return { chats, sessions, error, refresh, refreshSessions, remove, removeSession, update, markRead };
}
