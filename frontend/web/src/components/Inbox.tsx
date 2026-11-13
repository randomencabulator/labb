// The inbox page (server/inbox.py): what happened while you weren't looking, newest first, by day.
// Opening one goes to what it's about and marks it read.
import { useState } from "react";
import type { InboxItem } from "../api";
import { relativeTime } from "../format";
import type { Inbox } from "../useInbox";
import { CheckIcon, TrashIcon } from "./Icons";

const KINDS: Record<string, string> = {
  task: "Task",
  studio: "Studio",
  app: "App",
  document: "Document",
  account: "Account",
  health: "Server",
};

/** "Today", "Yesterday", or the date */
function dayOf(iso: string) {
  const day = new Date(iso);
  day.setHours(0, 0, 0, 0);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((today.getTime() - day.getTime()) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return day.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function byDay(items: InboxItem[]) {
  const groups: { day: string; items: InboxItem[] }[] = [];
  for (const item of items) {
    const day = dayOf(item.created_at);
    if (groups.at(-1)?.day !== day) groups.push({ day, items: [] });
    groups.at(-1)!.items.push(item);
  }
  return groups;
}

/** Desktop notifications: ask, or say why there are none */
function DesktopNotifications() {
  const supported = typeof Notification !== "undefined";
  const [permission, setPermission] = useState(supported ? Notification.permission : "denied");
  if (!supported) return null;
  if (permission === "granted") return <p className="hint">New ones also show on your desktop while labb is open in a tab.</p>;
  if (permission === "denied")
    return <p className="hint">Desktop notifications are blocked for this site; allow them in the browser's site settings to get them.</p>;
  return (
    <p className="hint inbox-ask">
      Get a desktop notification for each new one while labb is open in a tab.{" "}
      <button type="button" onClick={() => Notification.requestPermission().then(setPermission)}>
        Turn on
      </button>
    </p>
  );
}

function Row({ item, inbox }: { item: InboxItem; inbox: Inbox }) {
  return (
    <li className={`inbox-item level-${item.level}${item.read ? "" : " unread"}`}>
      <button type="button" className="inbox-open" onClick={() => inbox.open(item)} title={item.link ? "Open" : undefined}>
        <span className="inbox-dot" role="img" aria-label={item.read ? undefined : "Unread"} aria-hidden={item.read || undefined} />
        <span className="inbox-text">
          <span className="inbox-title">{item.title}</span>
          {item.body && <span className="inbox-body">{item.body}</span>}
        </span>
        <span className="inbox-meta">
          <span>{KINDS[item.kind] ?? item.kind}</span>
          <time dateTime={item.created_at} title={new Date(item.created_at).toLocaleString()}>
            {relativeTime(item.created_at)}
          </time>
        </span>
      </button>
      <span className="inbox-actions">
        {!item.read && (
          <button type="button" className="icon-button" title="Mark read" aria-label="Mark read" onClick={() => inbox.markRead([item.id])}>
            <CheckIcon />
          </button>
        )}
        <button type="button" className="icon-button" title="Delete" aria-label="Delete" onClick={() => inbox.remove(item.id)}>
          <TrashIcon />
        </button>
      </span>
    </li>
  );
}

export function InboxTab({ inbox }: { inbox: Inbox }) {
  const [only, setOnly] = useState<"all" | "unread">("all");
  const items = inbox.items?.filter(item => only === "all" || !item.read) ?? null;
  const anyRead = inbox.items?.some(item => item.read) ?? false;
  return (
    <>
      <p className="hint">
        Task results, approvals waiting for you, workflows and apps that failed, and documents that couldn't be indexed; for
        admins, also services that stopped answering and people who joined. Only you see yours.
      </p>
      <DesktopNotifications />
      <div className="inbox-bar">
        <div className="segmented" role="radiogroup" aria-label="Show">
          {(["all", "unread"] as const).map(choice => (
            <button key={choice} type="button" role="radio" aria-checked={only === choice} onClick={() => setOnly(choice)}>
              {choice === "all" ? "All" : `Unread${inbox.unread ? ` (${inbox.unread})` : ""}`}
            </button>
          ))}
        </div>
        <span className="inbox-bar-actions">
          <button type="button" disabled={!inbox.unread} onClick={() => inbox.markRead()}>
            Mark all read
          </button>
          <button type="button" disabled={!anyRead} onClick={() => inbox.clearRead()}>
            Clear read
          </button>
        </span>
      </div>
      {inbox.error && <div className="problem">{inbox.error}</div>}
      {items === null ? (
        !inbox.error && <p className="hint">Loading…</p>
      ) : items.length === 0 ? (
        <p className="inbox-empty">{only === "unread" ? "You're all caught up." : "Nothing here yet."}</p>
      ) : (
        byDay(items).map(group => (
          <section key={group.day} className="inbox-day">
            <h3>{group.day}</h3>
            <ul className="inbox-list">
              {group.items.map(item => (
                <Row key={item.id} item={item} inbox={inbox} />
              ))}
            </ul>
          </section>
        ))
      )}
    </>
  );
}
