// The inbox (server/inbox.py): the list, the unread count for the sidebar's badge, and a desktop
// notification for each new one while labb isn't in front, if this browser may show them.
// It follows the server's stream, so another tab or the server marking some read shows here too.
import { useCallback, useEffect, useRef, useState } from "react";
import { type InboxItem, clearReadInbox, deleteInboxItem, followInbox, listInbox, markInboxRead } from "./api";
import { navigate } from "./router";

export type Inbox = {
  items: InboxItem[] | null;
  unread: number;
  error: string | null;
  /** Go to what it's about (if it's about a page), marking it read */
  open: (item: InboxItem) => void;
  markRead: (ids?: string[]) => Promise<void>;
  remove: (id: string) => Promise<void>;
  clearRead: () => Promise<void>;
};

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** `onNew`: told of each new one as it comes (e.g. the chat list, for a task's chat); `onRead`: after
 * some were marked read here (reading a task's result reads its chat too) */
export function useInbox(onNew?: (item: InboxItem) => void, onRead?: () => void): Inbox {
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      listInbox().then(
        found => {
          setItems(found.items);
          setUnread(found.unread);
          setError(null);
        },
        e => setError(message(e)),
      ),
    [],
  );

  const onReadRef = useRef(onRead);
  onReadRef.current = onRead;
  const markRead = useCallback(async (ids?: string[]) => {
    setItems(list => list?.map(item => (!ids || ids.includes(item.id) ? { ...item, read: true } : item)) ?? list);
    try {
      setUnread((await markInboxRead(ids)).unread);
      onReadRef.current?.();
    } catch (e) {
      setError(message(e));
    }
  }, []);

  const open = useCallback(
    (item: InboxItem) => {
      if (!item.read) markRead([item.id]);
      if (item.link) navigate(item.link);
    },
    [markRead],
  );
  // The stream's handler outlives renders: it uses the latest
  const openRef = useRef(open);
  openRef.current = open;
  const unreadRef = useRef(unread);
  unreadRef.current = unread;
  const onNewRef = useRef(onNew);
  onNewRef.current = onNew;

  useEffect(() => {
    load();
    // Each time it (re)connects, catch up on what came while it wasn't
    let connected = false;
    const stop = followInbox(
      event => {
        if (event.type === "notification") {
          const item = event.item;
          // A newer one with the same key keeps its id: it moves to the top
          setItems(list => (list ? [item, ...list.filter(other => other.id !== item.id)] : list));
          setUnread(event.unread);
          tellDesktop(item, () => openRef.current(item));
          onNewRef.current?.(item);
        } else {
          // Read or deleted elsewhere (another tab, a chat opened): the list changed too
          if (unreadRef.current !== event.unread) load();
          setUnread(event.unread);
        }
      },
      () => {
        if (connected) load();
        connected = true;
      },
    );
    return stop;
  }, [load]);

  const remove = useCallback(async (id: string) => {
    setItems(list => list?.filter(item => item.id !== id) ?? list);
    try {
      await deleteInboxItem(id);
    } catch (e) {
      setError(message(e));
    }
  }, []);

  const clearRead = useCallback(async () => {
    setItems(list => list?.filter(item => !item.read) ?? list);
    try {
      await clearReadInbox();
    } catch (e) {
      setError(message(e));
    }
  }, []);

  return { items, unread, error, open, markRead, remove, clearRead };
}

/** A desktop notification, when this browser allows them and labb isn't the window in front */
function tellDesktop(item: InboxItem, onClick: () => void) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  if (document.visibilityState === "visible" && document.hasFocus()) return;
  // The tag: one with the same key replaces the one before on the desktop too
  const notice = new Notification(item.title, { body: item.body, tag: item.key ?? item.id });
  notice.onclick = () => {
    window.focus();
    onClick();
    notice.close();
  };
}
