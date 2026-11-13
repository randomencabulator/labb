import { type KeyboardEvent, type PointerEvent, type ReactNode, type Ref, useRef, useState } from "react";
import type { Chat, CliSession } from "../api";
import { relativeTime } from "../format";
import { Link, chatPath, pathOf } from "../router";
import { DEFAULT_LAYOUT, type SidebarLayout, arrange, useSidebarLayout } from "../sidebarLayout";
import { AgentIcon, AppsIcon, ArtifactIcon, BellIcon, ChartIcon, ClockIcon, PcIcon, EngineIcon, GearIcon, GripIcon, SkillIcon, ChatsIcon, ChevronIcon, CloseIcon, DocumentIcon, Logo, Wordmark, StudioIcon, MemoryIcon, NoteIcon, NewChatIcon, PencilIcon, PinIcon, TerminalIcon } from "./Icons";

// What the main area shows: a conversation, or one of the pages
export type View = "chat" | "inbox" | "apps" | "artifacts" | "studio" | "agents" | "engines" | "pcs" | "tasks" | "skills" | "documents" | "memories" | "notes" | "usage";
// The sidebar's lists, each folded or open
export type Section = "chats" | "terminal";

export const PAGES: { id: Exclude<View, "chat">; label: string; icon: ReactNode }[] = [
  { id: "inbox", label: "Inbox", icon: <BellIcon /> },
  { id: "apps", label: "Apps", icon: <AppsIcon /> },
  { id: "artifacts", label: "Artifacts", icon: <ArtifactIcon /> },
  { id: "studio", label: "Studio", icon: <StudioIcon /> },
  { id: "agents", label: "Agents", icon: <AgentIcon /> },
  { id: "engines", label: "Engines", icon: <EngineIcon /> },
  { id: "pcs", label: "PCs", icon: <PcIcon /> },
  { id: "tasks", label: "Tasks", icon: <ClockIcon /> },
  { id: "skills", label: "Skills", icon: <SkillIcon /> },
  { id: "documents", label: "Documents", icon: <DocumentIcon /> },
  { id: "memories", label: "Memories", icon: <MemoryIcon /> },
  { id: "notes", label: "Notes", icon: <NoteIcon /> },
  { id: "usage", label: "Usage", icon: <ChartIcon /> },
];

type Props = {
  view: View;
  onView: (view: Exclude<View, "chat">) => void;
  open: Record<Section, boolean>;
  onToggle: (section: Section) => void;
  chats: Chat[];
  sessions: CliSession[];
  error: string | null;
  activeId: string | null;
  // Chats with a reply being written
  answering: Set<string>;
  searchRef: Ref<HTMLInputElement>;
  onNew: () => void;
  onOpen: (threadId: string) => void;
  onDelete: (chat: Chat) => void;
  onRename: (chat: Chat, title: string) => void;
  onPin: (chat: Chat, pinned: boolean) => void;
  // A count next to a page, e.g. the chats tasks wrote to that you haven't read
  badges: Partial<Record<View, number>>;
  // The emoji of a chat's agent
  agentEmoji: (agentId: string | null) => string | undefined;
  // Delete the server's copy of a synced terminal session
  onDeleteSession: (session: CliSession) => void;
  // At the bottom: the account menu
  footer: ReactNode;
};

const DAY = 24 * 60 * 60 * 1000;

/** "Today", "Yesterday", "Previous 7 days", "Previous 30 days" or "Older", by the local date */
function dateGroup(iso: string) {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const time = new Date(iso).getTime();
  if (time >= startOfToday.getTime()) return "Today";
  if (time >= startOfToday.getTime() - DAY) return "Yesterday";
  if (time >= startOfToday.getTime() - 7 * DAY) return "Previous 7 days";
  if (time >= startOfToday.getTime() - 30 * DAY) return "Previous 30 days";
  return "Older";
}

function groupBy<T>(items: T[], key: (item: T) => string): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const item of items) groups.set(key(item), [...(groups.get(key(item)) ?? []), item]);
  return [...groups];
}

function ChatItem({ chat, emoji, active, busy, onOpen, onDelete, onRename, onPin }: {
  chat: Chat;
  emoji?: string;
  active: boolean;
  busy: boolean;
  onOpen: () => void;
  onDelete: () => void;
  onRename: (title: string) => void;
  onPin: () => void;
}) {
  const [renaming, setRenaming] = useState(false);

  if (renaming) {
    const done = (title: string | null) => {
      setRenaming(false);
      if (title?.trim() && title.trim() !== chat.title) onRename(title.trim());
    };
    return (
      <li className="active">
        <input
          className="rename"
          aria-label="Chat title"
          defaultValue={chat.title}
          autoFocus
          onFocus={event => event.target.select()}
          onBlur={event => done(event.target.value)}
          onKeyDown={event => {
            if (event.key === "Enter") done(event.currentTarget.value);
            if (event.key === "Escape") done(null);
          }}
        />
      </li>
    );
  }

  return (
    <li className={active ? "active" : undefined}>
      <Link to={chatPath(chat.thread_id)} className="open" title={chat.title} onClick={onOpen} onDoubleClick={() => setRenaming(true)}>
        {emoji && (
          <span className="chat-emoji" aria-hidden>
            {emoji}
          </span>
        )}
        <span className="title">{chat.title}</span>
        {chat.unread && !active && <span className="unread-dot" aria-label="Unread" title="A task wrote here" />}
        {busy ? (
          <span className="answering" aria-label="Answering" title="Answering…" />
        ) : (
          <span className="time">{relativeTime(chat.updated_at)}</span>
        )}
      </Link>
      <span className="actions">
        <button type="button" aria-label={chat.pinned ? "Unpin" : "Pin"} title={chat.pinned ? "Unpin" : "Pin"} onClick={onPin}>
          <PinIcon filled={chat.pinned} />
        </button>
        <button type="button" aria-label="Rename" title="Rename" onClick={() => setRenaming(true)}>
          <PencilIcon />
        </button>
        <button
          type="button"
          className="delete"
          aria-label={`Delete “${chat.title}”`}
          title={busy ? "Stop the reply first" : "Delete"}
          disabled={busy}
          onClick={onDelete}
        >
          <CloseIcon />
        </button>
      </span>
    </li>
  );
}

type Page = (typeof PAGES)[number];

/**
 * Customize: every page in two groups, pinned (at the top of the sidebar) and the rest. Drag a row by
 * its handle to order it within its group (or focus the handle and use the arrow keys), the star pins
 * it, and the switch shows or hides it; the Terminal list can be hidden too. Changes apply as they're
 * made; Done (or Escape) goes back to the sidebar.
 */
function SidebarEditor({ layout, onChange, onDone }: { layout: SidebarLayout; onChange: (next: SidebarLayout) => void; onDone: () => void }) {
  const shown = arrange(PAGES, layout);
  const pinnedPages = shown.filter(p => layout.pinned.includes(p.id));
  const otherPages = shown.filter(p => !layout.pinned.includes(p.id));
  const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter(i => i !== id) : [...list, id]);
  const changed = JSON.stringify(layout) !== JSON.stringify(DEFAULT_LAYOUT);
  const hidden = PAGES.filter(page => layout.hidden.includes(page.id)).length + (layout.terminal ? 0 : 1);
  // The row being dragged, and where each row is, to find where the pointer is over
  const [dragging, setDragging] = useState<string | null>(null);
  const rows = useRef(new Map<string, HTMLLIElement>());

  /** A group in a new order, saved */
  const reorder = (pinned: boolean, ids: string[]) => {
    if (pinned) onChange({ ...layout, pinned: ids });
    else onChange({ ...layout, order: [...pinnedPages.map(p => p.id), ...ids] });
  };
  const moveTo = (page: Page, to: number) => {
    const pinned = layout.pinned.includes(page.id);
    const ids = (pinned ? pinnedPages : otherPages).map(p => p.id);
    const from = ids.indexOf(page.id);
    to = Math.max(0, Math.min(ids.length - 1, to));
    if (from === to) return;
    ids.splice(from, 1);
    ids.splice(to, 0, page.id);
    reorder(pinned, ids);
  };

  const grab = (event: PointerEvent<HTMLButtonElement>, page: Page) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(page.id);
  };
  const drag = (event: PointerEvent<HTMLButtonElement>, page: Page) => {
    if (dragging !== page.id) return;
    const group = layout.pinned.includes(page.id) ? pinnedPages : otherPages;
    // The place in its group: past every other row whose middle is above the pointer
    const to = group.filter(p => p.id !== page.id).filter(p => {
      const box = rows.current.get(p.id)?.getBoundingClientRect();
      return box && box.top + box.height / 2 < event.clientY;
    }).length;
    moveTo(page, to);
  };
  const keys = (event: KeyboardEvent<HTMLButtonElement>, page: Page, at: number) => {
    const by = event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
    if (!by) return;
    event.preventDefault();
    moveTo(page, at + by);
  };

  const row = (page: Page, at: number, group: Page[]) => {
    const isHidden = layout.hidden.includes(page.id);
    const pinned = layout.pinned.includes(page.id);
    return (
      <li
        key={page.id}
        ref={element => {
          if (element) rows.current.set(page.id, element);
          else rows.current.delete(page.id);
        }}
        className={[isHidden && "hidden-page", dragging === page.id && "dragging"].filter(Boolean).join(" ") || undefined}
      >
        <button
          type="button"
          className="grip"
          aria-label={`Move ${page.label}: ${at + 1} of ${group.length}. Use the up and down arrow keys`}
          title="Drag to reorder"
          disabled={group.length < 2}
          onPointerDown={event => grab(event, page)}
          onPointerMove={event => drag(event, page)}
          onPointerUp={() => setDragging(null)}
          onPointerCancel={() => setDragging(null)}
          onKeyDown={event => keys(event, page, at)}
        >
          <GripIcon />
        </button>
        {page.icon}
        <span className="page-label">{page.label}</span>
        <button
          type="button"
          className="star"
          aria-pressed={pinned}
          aria-label={pinned ? `Unpin ${page.label}` : `Pin ${page.label} to the top`}
          title={pinned ? "Unpin" : "Pin to the top"}
          onClick={() => onChange({ ...layout, pinned: toggle(layout.pinned, page.id) })}
        >
          <PinIcon filled={pinned} />
        </button>
        <label className="switch" title={isHidden ? "Hidden: it still opens from its address" : "Shown in the sidebar"}>
          <input
            type="checkbox"
            role="switch"
            checked={!isHidden}
            aria-label={`Show ${page.label}`}
            onChange={() => onChange({ ...layout, hidden: toggle(layout.hidden, page.id) })}
          />
          <span />
        </label>
      </li>
    );
  };

  return (
    <div
      className="sidebar-editor"
      role="group"
      aria-label="Customize the sidebar"
      onKeyDown={event => event.key === "Escape" && !event.defaultPrevented && onDone()}
    >
      <div className="sidebar-editor-head">
        <b>Customize sidebar</b>
        <button type="button" className="link" disabled={!changed} onClick={() => onChange(DEFAULT_LAYOUT)} title="Every page shown, none pinned, in the usual order">
          Reset
        </button>
        <button type="button" className="primary" onClick={onDone} autoFocus>
          Done
        </button>
      </div>
      <p className="sidebar-editor-hint">
        Drag <GripIcon /> to reorder, <PinIcon filled={false} /> to pin{hidden > 0 ? ` · ${hidden} hidden` : ""}
      </p>
      <div className="sidebar-editor-group">Pinned</div>
      {pinnedPages.length > 0 ? (
        <ul>{pinnedPages.map((page, i) => row(page, i, pinnedPages))}</ul>
      ) : (
        <p className="sidebar-editor-empty">Star a page to keep it at the top.</p>
      )}
      <div className="sidebar-editor-group">Pages</div>
      <ul>{otherPages.map((page, i) => row(page, i, otherPages))}</ul>
      <div className="sidebar-editor-group">Lists</div>
      <ul>
        <li className={layout.terminal ? undefined : "hidden-page"}>
          <span className="grip-space" />
          <TerminalIcon />
          <span className="page-label">Terminal sessions</span>
          <label className="switch" title={layout.terminal ? "Shown in the sidebar" : "Hidden"}>
            <input
              type="checkbox"
              role="switch"
              checked={layout.terminal}
              aria-label="Show the Terminal list"
              onChange={() => onChange({ ...layout, terminal: !layout.terminal })}
            />
            <span />
          </label>
        </li>
      </ul>
    </div>
  );
}

/** A list's heading, which folds it: "▾ Chats 12" */
function SectionHead({ icon, label, count, open, onToggle }: {
  icon: ReactNode;
  label: string;
  count: number;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button type="button" className="section-head" aria-expanded={open} onClick={onToggle}>
      <ChevronIcon open={open} />
      {icon}
      <span className="label">{label}</span>
      {count > 0 && <span className="count">{count}</span>}
    </button>
  );
}

export function Sidebar(props: Props) {
  const { view, onView, chats, sessions, error, activeId, answering, searchRef, onNew, onOpen } = props;
  const [query, setQuery] = useState("");
  const [layout, setLayout] = useSidebarLayout();
  const [customizing, setCustomizing] = useState(false);
  // A hidden page still shows while it's open (it was reached from its address)
  const pages = arrange(PAGES, layout).filter(page => !layout.hidden.includes(page.id) || view === page.id);
  const hiddenCount = PAGES.filter(page => layout.hidden.includes(page.id)).length + (layout.terminal ? 0 : 1);
  // What hidden pages have waiting (a task wrote to a chat), shown on Customize so it isn't missed
  const hiddenBadges = PAGES.filter(page => layout.hidden.includes(page.id) && view !== page.id).reduce((sum, page) => sum + (props.badges[page.id] ?? 0), 0);
  const q = query.trim().toLowerCase();
  // While searching, both lists are open, to show what matches
  const open = (section: Section) => Boolean(q) || props.open[section];

  const matches = chats.filter(chat => chat.title.toLowerCase().includes(q));
  const chatGroups: [string, Chat[]][] = [
    ["Pinned", matches.filter(chat => chat.pinned)],
    ...groupBy(matches.filter(chat => !chat.pinned), chat => dateGroup(chat.updated_at)),
  ].filter(([, items]) => items.length > 0) as [string, Chat[]][];

  const sessionMatches = sessions.filter(s =>
    [s.title, s.directory, s.machine].some(text => text.toLowerCase().includes(q)),
  );
  // By machine, then folder
  const sessionGroups = groupBy(sessionMatches, s => `${s.machine}\u0000${s.directory}`);

  return (
    <aside id="sidebar">
      <div className="top">
        <div className="brand-row">
          {/* The logo is a way home too: a new chat */}
          <Link to="/" className="brand" aria-label="labb: new chat" title="New chat" onClick={onNew}>
            <Logo size={20} />
            <Wordmark />
          </Link>
          <button type="button" className="new-chat" onClick={onNew} title="New chat (Ctrl+Shift+O)">
            <NewChatIcon /> New chat
          </button>
        </div>
        <input
          ref={searchRef}
          type="search"
          className="search"
          placeholder="Search (Ctrl+K)"
          aria-label="Search chats and sessions"
          value={query}
          onChange={event => setQuery(event.target.value)}
          onKeyDown={event => event.key === "Escape" && setQuery("")}
        />
      </div>
      {/* The pages and the lists scroll as one, so the chats can have the whole height */}
      <div className="side-scroll">
      {customizing ? (
        <SidebarEditor layout={layout} onChange={setLayout} onDone={() => setCustomizing(false)} />
      ) : (
        <nav className="pages" aria-label="Pages">
          {pages.map(page => (
            <div key={page.id} className={view === page.id ? "page active" : "page"}>
              <Link to={pathOf(page.id)} aria-current={view === page.id ? "page" : undefined} onClick={() => onView(page.id)}>
                {page.icon} <span className="page-label">{page.label}</span>
                {Boolean(props.badges[page.id]) && <span className="badge">{props.badges[page.id]}</span>}
              </Link>
            </div>
          ))}
          <button
            type="button"
            className="customize"
            onClick={() => setCustomizing(true)}
            title={`Choose, pin and order what the sidebar shows${hiddenCount ? ` (${hiddenCount} hidden)` : ""}`}
          >
            <GearIcon /> <span className="page-label">Customize</span>
            {hiddenBadges > 0 ? (
              <span className="badge" title="Waiting in a hidden page">
                {hiddenBadges}
              </span>
            ) : (
              hiddenCount > 0 && <span className="hidden-count">{hiddenCount} hidden</span>
            )}
          </button>
        </nav>
      )}
      <div className="lists">
        <section>
          <SectionHead icon={<ChatsIcon />} label="Chats" count={chats.length} open={open("chats")} onToggle={() => props.onToggle("chats")} />
          {open("chats") && (
            <ul id="chats" className="chat-list">
              {error && <li className="none">{error}</li>}
              {!error && chats.length === 0 && <li className="none">No chats yet</li>}
              {!error && chats.length > 0 && matches.length === 0 && <li className="none">No chats match “{query}”</li>}
              {chatGroups.map(([group, items]) => (
                <li key={group} className="group">
                  <div className="group-name">{group}</div>
                  <ul>
                    {items.map(chat => (
                      <ChatItem
                        key={chat.thread_id}
                        chat={chat}
                        emoji={props.agentEmoji(chat.agent_id)}
                        active={view === "chat" && chat.thread_id === activeId}
                        busy={answering.has(chat.thread_id)}
                        onOpen={() => onOpen(chat.thread_id)}
                        onDelete={() => props.onDelete(chat)}
                        onRename={title => props.onRename(chat, title)}
                        onPin={() => props.onPin(chat, !chat.pinned)}
                      />
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </section>
        {layout.terminal && <section>
          <SectionHead
            icon={<TerminalIcon />}
            label="Terminal"
            count={sessions.length}
            open={open("terminal")}
            onToggle={() => props.onToggle("terminal")}
          />
          {open("terminal") && (
            <ul id="sessions" className="chat-list">
              {sessions.length === 0 && (
                <li className="none">No terminal sessions yet. They show up here from labb on this server, or on PCs that installed it from here.</li>
              )}
              {sessions.length > 0 && sessionMatches.length === 0 && <li className="none">No sessions match “{query}”</li>}
              {sessionGroups.map(([key, items]) => {
                const { machine, directory } = items[0];
                return (
                  <li key={key} className="group">
                    <div className="group-name" title={`${machine}: ${directory}`}>
                      <span className="machine">{machine}</span>
                      {/* The marks keep the path's slashes in place in the right-to-left box that cuts it at the start */}
                      <span className="dir">{`\u200e${directory}\u200e`}</span>
                    </div>
                    <ul>
                      {items.map(session => (
                        <li key={session.thread_id} className={view === "chat" && session.thread_id === activeId ? "active" : undefined}>
                          <Link to={chatPath(session.thread_id)} className="open" title={session.title} onClick={() => onOpen(session.thread_id)}>
                            <span className="title">{session.title}</span>
                            <span className="time">{relativeTime(session.updated_at)}</span>
                          </Link>
                          {(session.synced || session.pc_id) && (
                            <span className="actions">
                              <button
                                type="button"
                                className="delete"
                                aria-label={`Delete the server's copy of “${session.title}”`}
                                title="Delete the server's copy (the PC keeps its own)"
                                onClick={() => props.onDeleteSession(session)}
                              >
                                <CloseIcon />
                              </button>
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </li>
                );
              })}
            </ul>
          )}
        </section>}
      </div>
      </div>
      <div className="foot">{props.footer}</div>
    </aside>
  );
}
