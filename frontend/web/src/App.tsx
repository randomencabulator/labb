import { type ReactNode, Suspense, lazy, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { type Agent, type Attachment, type Chat, type ContextInspection, type ContextOptions, type Model, type Pc, type User, exportUrl, getContext, engineApi, listPcs, logOut } from "./api";
import { AccountMenu } from "./components/AccountMenu";
import { AgentsTab } from "./components/Agents";
import { Conversation } from "./components/Conversation";
import type { ComposerHandle } from "./components/Composer";
import { BrowserLive } from "./components/BrowserLive";
import { ContextPanel, TABS as CONTEXT_TABS, type ContextTab } from "./components/context/ContextPanel";
import { DocumentsTab } from "./components/Documents";
import { ChevronDownIcon, CursorIcon, ClockIcon, DownloadIcon, MenuIcon, MoreIcon, ShareIcon, SidebarIcon } from "./components/Icons";
import { SharedChat } from "./components/SharedChat";
import { InboxTab } from "./components/Inbox";
import { MemoriesTab } from "./components/Memories";
import { AppView, AppsTab } from "./components/Apps";
import { ArtifactsTab } from "./components/Artifacts";
import { NotesTab } from "./components/Notes";
import { Settings } from "./components/Settings";
import { PAGES, type Section, Sidebar, type View } from "./components/Sidebar";
import { Toaster, toast } from "./components/Toaster";
import { SkillsTab } from "./components/Skills";
import { PcsTab } from "./components/Pcs";
import { WherePicker } from "./components/WherePicker";
import { type TaskDraft, TasksTab } from "./components/Tasks";
import { UsageTab } from "./components/Usage";
import { useTheme } from "./theme";
import { useAgents } from "./useAgents";
import { useSkills } from "./useSkills";
import { useEngines } from "./useEngines";
import { EnginePicker } from "./components/EnginePicker";
import { lastModel, useChat } from "./useChat";
import { useChats } from "./useChats";
import { useInbox } from "./useInbox";
import { type ChatOptions, useModels } from "./useModels";
import { studio } from "./studioApi";
import { chatPath, navigate, pathOf, segments, usePath } from "./router";

// Studio is big (the canvas); it's loaded the first time it's opened
const Studio = lazy(() => import("./components/studio/Studio"));
const Engines = lazy(() => import("./components/engine/Engines"));

function loadCollapsed() {
  try {
    return localStorage.getItem("sidebar") === "collapsed";
  } catch {
    return false;
  }
}

/** Which of the sidebar's lists are open: Chats is, unless it was folded; Terminal when a session is open */
function loadSections(terminal: boolean): Record<Section, boolean> {
  let saved: Partial<Record<Section, boolean>> = {};
  try {
    saved = JSON.parse(localStorage.getItem("sidebarSections") ?? "{}");
  } catch {
    // Storage can be unavailable, or hold something else
  }
  return { chats: saved.chats ?? true, terminal: terminal || (saved.terminal ?? false) };
}

/**
 * What a path shows. "/" is a new chat and "/chat/<id>" a saved one; a page's path is its id,
 * followed by what's open in it ("/agents/<id>", "/studio/workflows/<id>/runs/<run id>").
 * "/chat/<id>/context/<tab>" is a saved one with its context panel open.
 * "/settings/<tab>" shows Settings over the last of the others.
 */
function routeOf(parts: string[]): { view: View; threadId: string | null; rest: string[] } {
  const page = PAGES.find(p => p.id === parts[0]);
  if (page) return { view: page.id, threadId: null, rest: parts.slice(1) };
  return { view: "chat", threadId: parts[0] === "chat" ? (parts[1] ?? null) : null, rest: parts.slice(2) };
}

// Phones (where the sidebar slides over the chat): a page opens as a sheet over the chat, as an
// app opens a screen over the last one, instead of taking the chat's place
const PHONE = "(max-width: 800px)";
// How long the sheet takes to slide away (styles.css's page-sheet-down)
const SHEET_OUT_MS = 240;

function useMedia(query: string) {
  return useSyncExternalStore(
    listener => {
      const list = window.matchMedia(query);
      list.addEventListener("change", listener);
      return () => list.removeEventListener("change", listener);
    },
    () => window.matchMedia(query).matches,
  );
}

type Route = ReturnType<typeof routeOf>;

/**
 * A page over the chat, on a phone: it slides up when it opens, and down when it's left (its ✕,
 * or the address going back to a chat), with the chat that was open still there under it
 */
function PageSheet({ route, render }: { route: Route | null; render: (route: Route) => ReactNode }) {
  // What it shows, kept while it slides away
  const [shown, setShown] = useState(route);
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    if (route) {
      setShown(route);
      setLeaving(false);
      return;
    }
    if (!shown) return;
    setLeaving(true);
    const timer = setTimeout(() => {
      setShown(null);
      setLeaving(false);
    }, window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : SHEET_OUT_MS);
    return () => clearTimeout(timer);
  }, [route]);
  const current = route ?? shown;
  if (!current) return null;
  return <div className={`page-sheet${leaving ? " leaving" : ""}`} aria-hidden={leaving || undefined}>{render(current)}</div>;
}

// How often the list of PCs is looked at, for the message box
const PCS_MS = 5000;

const KNOWN_ROOTS = new Set<string>(["chat", "settings", ...PAGES.map(p => p.id)]);

// The browser's tabs before a chat has an id, e.g. to sign in to something first
const NEW_CHAT_BROWSER = "new-chat";

export function App({ user, onLogOut }: { user: User; onLogOut: () => void }) {
  const path = usePath();
  const parts = segments(path);
  const settingsTab = parts[0] === "settings" ? parts.slice(1) : null;
  // Under Settings, the page it was opened from
  const behindSettings = useRef("/");
  if (!settingsTab) behindSettings.current = path;
  const phone = useMedia(PHONE);
  // What the address shows: a chat, or a page
  const shownRoute = routeOf(settingsTab ? segments(behindSettings.current) : parts);
  // On a phone a page is a sheet over the last chat, which stays as it was under it
  const sheetRoute = phone && shownRoute.view !== "chat" ? shownRoute : null;
  const behindSheet = useRef("/");
  if (shownRoute.view === "chat") behindSheet.current = settingsTab ? behindSettings.current : path;
  // What the main column shows
  const route = sheetRoute ? routeOf(segments(behindSheet.current)) : shownRoute;
  // A chat someone shared, read-only: /shared/<thread id>
  const behind = sheetRoute ? segments(behindSheet.current) : settingsTab ? segments(behindSettings.current) : parts;
  const sharedId = behind[0] === "shared" ? (behind[1] ?? null) : null;
  const [sharedTitle, setSharedTitle] = useState("");
  const view = route.view;
  const chats = useChats();
  // A task wrote to a chat: the chat list shows it now, not at its next look; and reading its result
  // in the inbox reads the chat, so the list loses its dot (and Tasks its count)
  const inbox = useInbox(
    item => item.kind === "task" && chats.refresh(),
    () => chats.refresh(),
  );
  // A new chat joins the list, and titles and order change, as replies start and end
  const { refresh: refreshChats, refreshSessions } = chats;
  const refreshLists = useCallback(() => {
    void refreshChats();
    void refreshSessions();
  }, [refreshChats, refreshSessions]);
  const {
    models, model: defaultModel, pick, choose, modelFor,
    pickAnswerMode, pickWeb, pickBrowser,
    browserOffered,
    settingsFor, rememberSettings,
  } = useModels();
  // Don't ask in a new chat, until it gets its id and keeps it
  const [newChatAuto, setNewChatAuto] = useState(false);
  // A new chat keeps the settings it was started with, whatever the defaults become (one on a PC has the PC's)
  const chat = useChat(refreshLists, route.threadId, (threadId, options) => {
    if (options.model && !options.pc) choose(threadId, options.model);
    rememberSettings(threadId, {
      answerMode: options.answerMode,
      web: options.web,
      browser: options.browser,
      browserAuto: options.browserAuto,
    });
    setNewChatAuto(false);
  });
  const agents = useAgents();
  const skills = useSkills();
  const engines = useEngines();
  // Picked in the open chat, for its next message; the one the chat already has, as the server keeps it
  const [pickedEngine, setPickedEngine] = useState<string | null>(null);
  const [chatEngine, setChatEngine] = useState<string | null>(null);
  // Who the next new chat is with; null for labb
  const [newAgentId, setNewAgentId] = useState<string | null>(null);
  // What the Tasks page's editor opens with, at /tasks/new
  const [taskDraft, setTaskDraft] = useState<TaskDraft | null>(null);
  const [theme, setTheme] = useTheme();
  // On phones the sidebar slides over the chat; on wider screens it can be collapsed
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(loadCollapsed);
  const [sections, setSections] = useState(() => loadSections(chat.threadId?.startsWith("cli-") ?? false));
  // What the open chat's next model call gets, refreshed as a reply goes on and when it ends
  const [contextSeen, setContextSeen] = useState<ContextInspection | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const [contextUpdating, setContextUpdating] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  const composer = useRef<ComposerHandle>(null);

  // The PCs connected with labb connect, and the one the next new chat runs on; null for this server
  const [pcs, setPcs] = useState<Pc[]>([]);
  const [where, setWhere] = useState<{ id: string; folder: string } | null>(null);
  // The model picked in the new chat before it's sent; null for its agent's or the default
  const [newChatModel, setNewChatModel] = useState<string | null>(null);
  // The model picked for a chat on a PC; null for what that PC's settings say
  const [pcModel, setPcModel] = useState<string | null>(null);
  useEffect(() => {
    const reload = () => listPcs().then(setPcs, () => {});
    reload();
    const timer = setInterval(reload, PCS_MS);
    return () => clearInterval(timer);
  }, []);
  // A PC forgotten while it was picked: back to this server, not an address that's gone
  useEffect(() => {
    if (pcs.length && where && !pcs.some(pc => pc.id === where.id)) setWhere(null);
  }, [pcs, where]);

  const terminal = chat.threadId?.startsWith("cli-") ?? false;
  const session = terminal ? chats.sessions.find(s => s.thread_id === chat.threadId) : undefined;
  // A session started here runs on its PC, and goes on from here while that's connected. The others are
  // shown read-only. A chat that just started may not be in the list yet, and is answering meanwhile.
  const sessionPc = pcs.find(p => p.id === (session?.pc_id ?? (terminal ? where?.id : undefined)));
  const readOnly = terminal && !chat.streaming && !(session?.pc_id && sessionPc?.online);
  // The PC the next new chat runs on, connected or not: its model list and the warnings stay put
  // while it's away, and sending says it isn't connected rather than running on the server unseen
  const wherePc = where ? pcs.find(p => p.id === where.id) : undefined;
  const newChatPc = chat.threadId === null && chat.turns.length === 0 ? wherePc : undefined;
  const onPc = terminal ? !readOnly : Boolean(newChatPc);
  const shownPc = terminal ? sessionPc : newChatPc;
  const pcModels: Model[] = [{ spec: "", name: "This PC's default", vision: null }, ...(shownPc?.models ?? []).map(spec => ({ spec, name: spec, vision: null }))];
  const listed = chats.chats.find(c => c.thread_id === chat.threadId);
  // A chat that just started isn't in the list yet: it's with the agent it was started with
  const agentId = listed ? listed.agent_id : terminal || onPc ? null : newAgentId;
  const agent = agents.agents.find(a => a.id === agentId) ?? null;
  // The chat's own model (useModels' modelFor); a new chat's is what's picked in it, else its agent's, else the default
  const model = chat.threadId
    ? modelFor(chat.threadId, lastModel(chat.turns), agent?.model)
    : modelFor(null, newChatModel, agent?.model);
  /** Pick the model for this chat only; in a new chat without an agent, it's also the default for new chats */
  const pickModel = (spec: string) => {
    if (chat.threadId) return choose(chat.threadId, spec);
    setNewChatModel(spec);
    if (!agent) pick(spec);
  };
  // The other settings the chat keeps: its own, like its model; a new chat's are the defaults for new chats
  const settings = chat.threadId ? settingsFor(chat.threadId) : { ...settingsFor(null), browserAuto: newChatAuto };
  const { answerMode, web, browser, browserAuto: keptAuto } = settings;
  // A new chat starts with Don't ask off, whatever the last one did
  useEffect(() => {
    if (chat.threadId === null) setNewChatAuto(false);
  }, [chat.activeKey, chat.threadId]);
  // An agent without the web tools has nothing for the Web toggle to turn on or off
  const webAvailable = !agent || agent.toolsets === null || agent.toolsets.includes("web");
  // An agent has the browser if it was given it; the switch is for chats without one
  const browserAvailable = browserOffered && !agent && !onPc;
  const browserOn = browserOffered && !onPc && (agent ? (agent.toolsets ?? []).includes("browser") : browser);
  // The panel stays as it was left; until then it's open when the agent can use the browser, if there's room
  const [browserPanel, setBrowserPanel] = useState<boolean | null>(() => {
    try {
      const saved = localStorage.getItem("browserPanel");
      return saved === null ? null : saved === "open";
    } catch {
      return null;
    }
  });
  const browserOpen = browserPanel ?? (browserOn && !window.matchMedia("(max-width: 1100px)").matches);
  const toggleBrowserPanel = () => {
    setBrowserPanel(!browserOpen);
    try {
      localStorage.setItem("browserPanel", browserOpen ? "closed" : "open");
    } catch {
      // Storage can be unavailable, e.g. in a private window
    }
  };
  const agentGone = agentId !== null && agents.loaded && agent === null;
  const canChange = !readOnly && !chat.streaming && chat.threadId !== null;
  const page = PAGES.find(p => p.id === view);
  const sheetPage = sheetRoute ? PAGES.find(p => p.id === sheetRoute.view) : undefined;
  const title = sheetPage
    ? sheetPage.label
    : page
    ? page.label
    : sharedId
      ? sharedTitle || "Shared chat"
      : chat.threadId
      ? (chats.chats.find(c => c.thread_id === chat.threadId)?.title ?? session?.title ?? "")
      : "New chat";

  // The browser tab shows the open chat
  useEffect(() => {
    document.title = title ? `${title} · Labb` : "Labb";
  }, [title]);

  const logOutNow = async () => {
    try {
      await logOut();
    } catch {
      // Logged out on the server already, or it's unreachable: forget the session here anyway
    }
    // The next one to log in here starts at a new chat
    navigate("/", { replace: true });
    onLogOut();
  };

  /** Show a chat, from a link or the address (Back, Forward, a reload) */
  const showChat = (threadId: string | null) => {
    if (threadId === null) {
      setNewAgentId(null);
      setWhere(null);
      setNewChatModel(null);
    }
    if (threadId !== chat.threadId) setPcModel(null);
    // A session opened from elsewhere (e.g. Usage) shows up in its list
    if (threadId?.startsWith("cli-") && !sections.terminal) setSections({ ...sections, terminal: true });
    if (threadId === null || threadId !== chat.threadId) chat.open(threadId);
    if (threadId) chats.markRead(threadId);
  };

  const openChat = (threadId: string | null) => {
    setSidebarOpen(false);
    navigate(chatPath(threadId));
    showChat(threadId);
  };

  /** Share a chat (and copy its link), or stop sharing it */
  const toggleShared = async (shared: Chat) => {
    if (shared.shared && !confirm("Stop sharing this chat? Its link stops working.")) return;
    try {
      await chats.update(shared.thread_id, { shared: !shared.shared });
    } catch (e) {
      toast(`Couldn't change sharing: ${(e as Error).message}`);
      return;
    }
    if (shared.shared) {
      toast("Not shared any more.");
      return;
    }
    const link = `${window.location.origin}${pathOf("shared", shared.thread_id)}`;
    try {
      await navigator.clipboard.writeText(link);
      toast("Shared: the link is copied. Anyone on this server can read the chat; nobody else can change it.");
    } catch {
      toast(`Shared: anyone on this server can read it at ${link}`);
    }
  };

  // The address changed to another chat (Back, Forward): show it
  useEffect(() => {
    if (view === "chat" && route.threadId !== chat.threadId) showChat(route.threadId);
  }, [view, route.threadId]);

  // The open chat changed here (a new chat got its id, the open one was deleted): so does the address
  useEffect(() => {
    if (view === "chat" && !settingsTab && !sheetRoute && route.threadId !== chat.threadId) navigate(chatPath(chat.threadId), { replace: true });
  }, [chat.threadId]);

  // An address that isn't a page goes to a new chat
  useEffect(() => {
    if (parts.length > 0 && !KNOWN_ROOTS.has(parts[0])) navigate("/", { replace: true });
  }, [path]);

  /** The Tasks page, with its editor filled in */
  const scheduleTask = (draft: TaskDraft) => {
    setTaskDraft(draft);
    navigate("/tasks/new");
  };
  // The draft is for that one editor
  useEffect(() => {
    if (!(shownRoute.view === "tasks" && shownRoute.rest[0] === "new")) setTaskDraft(null);
  }, [shownRoute.view, shownRoute.rest[0]]);

  // Chats a task wrote to that weren't opened yet, for the Tasks badge (the inbox, useInbox, tells of them)
  const unread = chats.chats.filter(c => c.unread);

  // Approvals waiting in Studio, for its badge
  const [waiting, setWaiting] = useState(0);
  useEffect(() => {
    const check = () => studio.waiting().then(w => setWaiting(w.length), () => {});
    check();
    const timer = setInterval(() => document.visibilityState === "visible" && check(), 30_000);
    return () => clearInterval(timer);
  }, []);

  const openPage = (next: Exclude<View, "chat">) => {
    setSidebarOpen(false);
    navigate(pathOf(next));
    if (next === "agents") agents.refresh();
  };

  /** Go to a path in the open page: `pathOf(view, ...parts)` */
  const pageTo = (...rest: (string | null)[]) => navigate(pathOf(shownRoute.view, ...rest));

  /** A new chat with an agent */
  const chatWith = (chosen: Agent) => {
    openChat(null);
    setNewAgentId(chosen.id);
    requestAnimationFrame(() => composer.current?.focus());
  };

  // A reply may have saved a skill: the / suggestions get it
  useEffect(() => {
    if (!chat.streaming) skills.refresh();
  }, [chat.streaming, skills.refresh]);

  // Synced terminal sessions ran elsewhere, so the server can't show their context; a session on a
  // connected PC is asked of the PC, which has its prompt and tools
  const inspectable = chat.threadId !== null && chat.turns.length > 0 && (!terminal || Boolean(session?.pc_id && sessionPc?.online));
  // The bar's actions, for the ⋯ menu that holds them on a phone
  const [moreOpen, setMoreOpen] = useState(false);
  const hasActions = !page && !sharedId && (inspectable || Boolean(listed && !chat.streaming) || Boolean(chat.threadId && chat.turns.length > 0));
  const lastTurn = chat.turns[chat.turns.length - 1];
  useEffect(() => {
    setContextSeen(null);
    setContextError(null);
  }, [chat.threadId]);
  // The panel's address: /chat/<id>/context/<tab>
  const contextOpen = view === "chat" && !sharedId && inspectable && route.rest[0] === "context";
  const contextTab: ContextTab = CONTEXT_TABS.find(tab => tab.id === route.rest[1])?.id ?? "overview";
  const contextPath = (tab: ContextTab) => pathOf("chat", chat.threadId, "context", tab);
  // What the next message would be sent with, so the numbers are that message's
  const contextWeb = web && webAvailable;
  // On a PC: its model, and the tools it has (the message box's Web and engine are the server's)
  const contextOptions: ContextOptions = onPc
    ? { model: pcModel ?? undefined, answerMode, web: true }
    : { model, answerMode, web: contextWeb, browser: browserOn, engineId: pickedEngine ?? undefined };
  // Changes as a reply's steps are saved: only those change what the model would get
  const repliedSoFar = lastTurn ? `${lastTurn.parts.length}.${lastTurn.parts.filter(part => part.kind === "tool" && part.chars !== undefined).length}` : "";
  const liveStep = chat.streaming && contextOpen ? repliedSoFar : "";
  useEffect(() => {
    if (!inspectable || (chat.streaming && !contextOpen)) return;
    let current = true;
    // A reply can take many steps in a moment; the end of a reply or a new choice shows at once
    const timer = setTimeout(() => {
      setContextUpdating(true);
      getContext(chat.threadId!, contextOptions)
        .then(seen => {
          if (!current) return;
          setContextSeen(seen);
          setContextError(null);
        })
        .catch(e => current && setContextError(e instanceof Error ? e.message : String(e)))
        .finally(() => current && setContextUpdating(false));
    }, chat.streaming ? 500 : 0);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [inspectable, chat.threadId, chat.streaming, contextOpen, liveStep, model, answerMode, contextWeb, pickedEngine, onPc, pcModel]);

  // The agents you used last come first in the picker
  const lastUsed = new Map<string, string>();
  for (const c of chats.chats) if (c.agent_id && !lastUsed.has(c.agent_id)) lastUsed.set(c.agent_id, c.updated_at);
  const recentAgents = [...agents.agents].sort((a, b) => (lastUsed.get(b.id) ?? "").localeCompare(lastUsed.get(a.id) ?? ""));

  const toggleSection = (section: Section) => {
    const next = { ...sections, [section]: !sections[section] };
    setSections(next);
    try {
      localStorage.setItem("sidebarSections", JSON.stringify(next));
    } catch {
      // Storage can be unavailable, e.g. in a private window
    }
  };

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem("sidebar", next ? "collapsed" : "open");
    } catch {
      // Storage can be unavailable, e.g. in a private window
    }
  };

  useEffect(() => {
    if (sections.terminal) chats.refreshSessions();
  }, [sections.terminal, chats.refreshSessions]);

  // Ctrl+K: search the chats; Ctrl+Shift+O: new chat
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key.toLowerCase() === "k" && !event.shiftKey) {
        event.preventDefault();
        setCollapsed(false);
        setSidebarOpen(true);
        // After the sidebar is shown
        requestAnimationFrame(() => search.current?.focus());
      } else if (event.key.toLowerCase() === "o" && event.shiftKey) {
        event.preventDefault();
        openChat(null);
        // After the composer is back
        requestAnimationFrame(() => composer.current?.focus());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // A chat keeps its engine: what's picked goes with the next message, and the server remembers it
  const previousThread = useRef(chat.threadId);
  useEffect(() => {
    // A new chat just got its id: what was picked for it is what it has
    const justStarted = previousThread.current === null && chat.threadId !== null;
    previousThread.current = chat.threadId;

    if (justStarted && pickedEngine) return;
    setPickedEngine(null);
    setChatEngine(null);
    if (!chat.threadId || chat.threadId.startsWith("cli-")) return;
    engineApi.ofChat(chat.threadId).then(r => setChatEngine(r.engine_id), () => {});
  }, [chat.threadId]);
  const currentEngine = pickedEngine ?? chatEngine ?? agent?.engine_id ?? engines.my_default ?? engines.server_default ?? "default";
  const engineChoice = pickedEngine ?? undefined;

  // A setting picked in a chat is kept with it; in a new chat it's also the default for new chats
  const applySetting = (change: Partial<ChatOptions>) => {
    if (chat.threadId) return rememberSettings(chat.threadId, change);
    if (change.answerMode) pickAnswerMode(change.answerMode);
    if (change.web !== undefined) pickWeb(change.web);
    if (change.browser !== undefined) pickBrowser(change.browser);
    if (change.browserAuto !== undefined) setNewChatAuto(change.browserAuto);
  };
  const browserAuto = browserOn && keptAuto;
  const pickBrowserAuto = (on: boolean) => {
    if (on && !window.confirm("Stop the browser asking in this chat?\n\nIt can then send, pay, delete and upload on its own, wherever you're signed in, and a page could trick it into doing so. Passwords, cards and codes stay with you.")) return;
    applySetting({ browserAuto: on });
  };

  const send = (message: string, attachments: Attachment[]) => {
    if (onPc) {
      chat.send(message, { model: pcModel ?? undefined, answerMode, attachments, pc: chat.threadId === null ? (where ?? undefined) : undefined });
      return;
    }
    chat.send(message, { model: model ?? undefined, answerMode, web: web && webAvailable, browser: browserOn, browserAuto, attachments, agentId: chat.threadId ? undefined : (newAgentId ?? undefined), engineId: engineChoice });
  };

  // Replace message `index` and everything after it, keeping its attachments
  const replace = (index: number, message: string) => {
    const turn = chat.turns[index];
    if (onPc) {
      chat.send(message, { model: pcModel ?? undefined, answerMode, replaceFrom: index, keepAttachments: true }, { files: turn.files, images: turn.images });
      return;
    }
    chat.send(
      message,
      { model: model ?? undefined, answerMode, web: web && webAvailable, browser: browserOn, browserAuto, replaceFrom: index, keepAttachments: true, engineId: engineChoice },
      { files: turn.files, images: turn.images },
    );
  };

  /** A page, at `route` (inline in the main column, or in the sheet on a phone) */
  const renderPage = ({ view, rest }: Route) => {
    const page = PAGES.find(p => p.id === view)!;
    const close = sheetRoute ? (
      <button type="button" className="icon-button sheet-close" aria-label="Back to the chat" title="Back to the chat" onClick={() => navigate(behindSheet.current)}>
        <ChevronDownIcon />
      </button>
    ) : null;
    const main = (view === "studio" ? (
      <main className="page studio-page">
        <Suspense fallback={<div className="studio-loading muted">Loading Studio…</div>}>
          <Studio
            route={rest}
            onRoute={(rest, replace) => navigate(pathOf("studio", ...rest), { replace })}
            agents={agents.agents}
            models={models}
            skills={skills.skills}
            onOpenChat={openChat}
            onWaitingChanged={setWaiting}
          />
        </Suspense>
      </main>
    ) : view === "engines" ? (
      <main className="page engine-page">
        <Suspense fallback={<div className="studio-loading muted">Loading…</div>}>
          <Engines route={rest} onRoute={rest => navigate(pathOf("engines", ...rest))} models={models} model={defaultModel} user={user} onChanged={engines.refresh} />
        </Suspense>
      </main>
    ) : view === "apps" && rest[0] ? (
      <main className="page app-page">
        <AppView id={rest[0]} />
      </main>
    ) : (
      <main className="page">
        <div className="page-inner">
          <div className="page-head">
            <h2>
              {page.icon} {page.label}
            </h2>
          </div>
          {view === "inbox" && <InboxTab inbox={inbox} />}
          {view === "apps" && <AppsTab />}
          {view === "artifacts" && <ArtifactsTab opened={rest[0] ?? null} onOpen={name => pageTo(name)} onOpenChat={openChat} />}
          {view === "agents" && (
            <AgentsTab
              agents={agents.agents}
              loaded={agents.loaded}
              editing={rest[0] ?? null}
              onEditing={id => pageTo(id)}
              models={models}
              onChanged={agents.refresh}
              onChat={chatWith}
              onSchedule={a => scheduleTask({ agent_id: a.id, name: a.name })}
            />
          )}
          {view === "skills" && <SkillsTab editing={rest[0] ?? null} onEditing={id => pageTo(id)} onChanged={skills.refresh} />}
          {view === "tasks" && (
            <TasksTab
              agents={agents.agents}
              editing={rest[0] ?? null}
              onEditing={id => pageTo(id)}
              draft={taskDraft}
              onOpenChat={openChat}
            />
          )}
          {view === "pcs" && <PcsTab />}
          {view === "documents" && <DocumentsTab opened={rest[0] ?? null} onOpen={id => pageTo(id)} />}
          {view === "memories" && <MemoriesTab />}
          {view === "notes" && <NotesTab route={rest} onRoute={rest => navigate(pathOf("notes", ...rest), { replace: true })} />}
          {view === "usage" && <UsageTab onOpenChat={openChat} />}
        </div>
      </main>
    ));
    if (!close) return main;
    return (
      <>
        <header className="sheet-head">
          {close}
          <h1 title={page.label}>{page.label}</h1>
        </header>
        {main}
      </>
    );
  };

  return (
    <div className={["app", sidebarOpen && "sidebar-open", collapsed && "sidebar-collapsed"].filter(Boolean).join(" ")}>
      <Sidebar
        view={view}
        onView={openPage}
        open={sections}
        onToggle={toggleSection}
        footer={<AccountMenu user={user} theme={theme} onTheme={setTheme} onSettings={() => navigate("/settings")} onLogOut={logOutNow} />}
        chats={chats.chats}
        sessions={chats.sessions}
        error={chats.error}
        activeId={chat.threadId}
        answering={chat.answering}
        searchRef={search}
        onNew={() => openChat(null)}
        onOpen={openChat}
        onDelete={async deleted => {
          if (!confirm(`Delete “${deleted.title}”?`)) return;
          try {
            await chats.remove(deleted.thread_id);
          } catch (e) {
            toast(`Couldn't delete the chat: ${(e as Error).message}`);
            return;
          }
          chat.forget(deleted.thread_id);
        }}
        onDeleteSession={async deleted => {
          if (!confirm(`Delete the server's copy of “${deleted.title}”? It stays on ${deleted.machine}.`)) return;
          try {
            await chats.removeSession(deleted.thread_id);
          } catch (e) {
            toast(`Couldn't delete it: ${(e as Error).message}`);
            return;
          }
          chat.forget(deleted.thread_id);
        }}
        onRename={(renamed, title) =>
          chats.update(renamed.thread_id, { title }).catch(e => toast(`Couldn't rename the chat: ${(e as Error).message}`))
        }
        agentEmoji={id => agents.agents.find(a => a.id === id)?.emoji}
        badges={{ inbox: inbox.unread, tasks: unread.length, studio: waiting }}
        onPin={(pinned, value) =>
          chats.update(pinned.thread_id, { pinned: value }).catch(e => toast(`Couldn't pin the chat: ${(e as Error).message}`))
        }
      />
      <div className="backdrop" onClick={() => setSidebarOpen(false)} />
      <div className="chat">
        <header>
          <button type="button" id="menu" className="icon-button" aria-label="Chats" onClick={() => setSidebarOpen(true)}>
            <MenuIcon />
          </button>
          <button
            type="button"
            id="collapse"
            className="icon-button"
            aria-label={collapsed ? "Show the sidebar" : "Hide the sidebar"}
            title={collapsed ? "Show the sidebar" : "Hide the sidebar"}
            onClick={toggleCollapsed}
          >
            <SidebarIcon />
          </button>
          <h1 title={title}>
            {!page && !sharedId && agent && (
              <span className="title-emoji" aria-hidden>
                {agent.emoji}
              </span>
            )}
            {title}
          </h1>
          {!page && !sharedId && agentGone && (
            <span className="model-tag" title="The agent this chat was with was deleted, or isn't shared any more; it goes on with labb">
              agent deleted
            </span>
          )}
          {hasActions && (
            <button type="button" className="icon-button more-button" aria-label="More" aria-haspopup="menu" aria-expanded={moreOpen}
              onClick={() => setMoreOpen(!moreOpen)}>
              <MoreIcon />
            </button>
          )}
          {moreOpen && <div className="more-backdrop" onClick={() => setMoreOpen(false)} />}
          {/* The chat's actions: buttons in the bar, a menu behind ⋯ on a phone */}
          <div className={`chat-actions${moreOpen ? " open" : ""}`} role={moreOpen ? "menu" : undefined} onClick={() => setMoreOpen(false)}>
          {!page && !sharedId && !terminal && chat.threadId && chat.turns.length > 0 && (
            <button
              type="button"
              className="icon-button bordered"
              aria-label="Run this every…"
              title="Run this every… (a task with this chat's agent and first message)"
              onClick={() => scheduleTask({ prompt: chat.turns[0].user, agent_id: agent?.id ?? null, name: title.split(" · ")[0] })}
            >
              <ClockIcon /><span className="action-label">Run this every…</span>
            </button>
          )}
          {!page && !sharedId && browserOffered && (
            <button
              type="button"
              className={browserOpen ? "icon-button bordered shared-on" : "icon-button bordered"}
              aria-label="The browser"
              aria-expanded={browserOpen}
              title="Keep the browser in view: watch it, or use it yourself"
              onClick={toggleBrowserPanel}
            >
              <CursorIcon />
            </button>
          )}
          {!page && !sharedId && inspectable && (
            <button
              type="button"
              className={`bordered context-button${contextSeen?.will_compact ? " warn" : ""}`}
              title="What the model gets: the system prompt, tools, summary and messages, with their tokens"
              aria-expanded={contextOpen}
              onClick={() => navigate(contextOpen ? chatPath(chat.threadId) : contextPath(contextTab))}
            >
              <span className="context-short">{contextSeen ? `${Math.round(contextSeen.share * 100)}% context` : "Context"}</span>
              <span className="action-label">What the model gets{contextSeen ? ` · ${Math.round(contextSeen.share * 100)}%` : ""}</span>
            </button>
          )}
          {!page && !sharedId && listed && !chat.streaming && (
            <button
              type="button"
              className={listed.shared ? "icon-button bordered shared-on" : "icon-button bordered"}
              aria-label={listed.shared ? "Shared: stop sharing" : "Share"}
              title={listed.shared ? "Shared with everyone on this server; click to stop sharing" : "Share a read-only link with everyone on this server"}
              onClick={() => toggleShared(listed)}
            >
              <ShareIcon /><span className="action-label">{listed.shared ? "Stop sharing" : "Share a link"}</span>
            </button>
          )}
          {!page && !sharedId && chat.threadId && chat.turns.length > 0 && (
            <a
              className="button icon-button"
              href={exportUrl(chat.threadId)}
              download
              aria-label="Export"
              title="Download this conversation as Markdown"
            >
              <DownloadIcon /><span className="action-label">Download as Markdown</span>
            </a>
          )}
          </div>
        </header>
        {page ? (
          renderPage(route)
        ) : sharedId ? (
          <SharedChat threadId={sharedId} onTitle={setSharedTitle} onOpenChat={openChat} />
        ) : (
          <>
          <Conversation
            ref={composer}
            listKey={chat.activeKey}
            turns={chat.turns}
            loading={chat.loading}
            error={chat.loadError}
            threadId={chat.threadId}
            streaming={chat.streaming}
            stopping={chat.stopping}
            name={terminal && !onPc ? null : user.username}
            agent={agent ?? undefined}
            canChange={canChange}
            onSend={send}
            onReplace={replace}
            onStop={chat.stop}
            disabled={summarizing}
            settings={{
              models: onPc ? pcModels : models,
              model: onPc ? (pcModel ?? "") : model,
              onModel: onPc ? (spec => setPcModel(spec || null)) : pickModel,
              answerMode,
              onAnswerMode: mode => applySetting({ answerMode: mode }),
              enginePicker: !onPc && engines.engines.length > 1 ? (
                <EnginePicker engines={engines.engines} current={currentEngine} onPick={setPickedEngine} onManage={() => openPage("engines")} />
              ) : undefined,
              web,
              onWeb: on => applySetting({ web: on }),
              webAvailable: webAvailable && !onPc,
              browser,
              onBrowser: on => applySetting({ browser: on }),
              browserAvailable,
              browserOn,
              browserAuto,
              onBrowserAuto: pickBrowserAuto,
              agents: onPc ? [] : recentAgents,
              agent,
              onAgent: chosen => setNewAgentId(chosen?.id ?? null),
              agentLocked: onPc || chat.threadId !== null || chat.turns.length > 0,
              skills: onPc ? [] : skills.skills,
            }}
            above={
              <>
                {chat.threadId === null && chat.turns.length === 0 && pcs.length > 0 && (
                  <div className="where-bar">
                    <span className="where-label">Runs on</span>
                    <WherePicker
                      pcs={pcs}
                      where={where}
                      onWhere={next => {
                        setWhere(next);
                        setPcModel(null);
                      }}
                      onManage={() => openPage("pcs")}
                    />
                    {wherePc && !wherePc.online && (
                      <span className="problem-text">
                        {wherePc.name} isn't connected right now: run <code>labb connect</code> there, or pick another place.
                      </span>
                    )}
                    {wherePc?.allow_all && <span className="problem-text">Nothing there asks first (--allow-all)</span>}
                  </div>
                )}
                {terminal && (
                  <div className="where-bar">
                    <span>On {session?.machine ?? sessionPc?.name ?? "your PC"}{session ? `, in ${session.directory}` : ""}</span>
                    {sessionPc && !sessionPc.online && <span className="problem-text">It isn't connected right now: answers and stops reach it when it's back</span>}
                    {sessionPc?.allow_all && <span className="problem-text">Nothing there asks first (--allow-all)</span>}
                  </div>
                )}
              </>
            }
            readOnly={readOnly ? (
              <div className="read-only">
                <div className="inner">
                  <span>
                    {session?.pc_id ? (
                      <>{session.machine} isn't connected, so this is read-only. Run <code>labb connect</code> there to go on from here, or continue it there with{" "}</>
                    ) : (
                      <>A terminal session{session ? ` on ${session.machine}, in ${session.directory}` : ""}, shown read-only. Continue it there with{" "}</>
                    )}
                    <code>labb -r {chat.threadId}</code>
                  </span>
                </div>
              </div>
            ) : undefined}
          />
          </>
        )}
      </div>
      {browserOpen && browserOffered && !page && !sharedId && !onPc && (
        <section className="browser-panel" aria-label="The browser">
          <BrowserLive
            key={chat.threadId ?? NEW_CHAT_BROWSER}
            threadId={chat.threadId ?? NEW_CHAT_BROWSER}
            onClose={toggleBrowserPanel}
            note={[
              browserAuto && "Don't ask is on: the browser won't ask before it does anything in this chat.",
              !chat.threadId && "This chat hasn't started, so these tabs stay here. What you sign in to carries over to your chats.",
            ].filter(Boolean).join(" ") || undefined}
          />
        </section>
      )}
      {contextOpen && (
        <ContextPanel
          threadId={chat.threadId!}
          seen={contextSeen}
          error={contextError}
          updating={contextUpdating}
          streaming={chat.streaming}
          options={contextOptions}
          models={onPc ? pcModels.filter(m => m.spec) : models}
          engines={onPc ? [] : engines.engines}
          currentEngine={currentEngine}
          webAvailable={webAvailable && !onPc}
          tab={contextTab}
          onTab={tab => navigate(contextPath(tab))}
          onClose={() => navigate(chatPath(chat.threadId))}
          summarizing={summarizing}
          onSummarizing={setSummarizing}
          onSeen={setContextSeen}
        />
      )}
      <PageSheet route={sheetRoute} render={renderPage} />
      {settingsTab && (
        <Settings
          user={user}
          browserOffered={browserOffered}
          route={settingsTab}
          onRoute={rest => navigate(pathOf("settings", ...rest), { replace: true })}
          onClose={() => navigate(behindSettings.current)}
          onLogOut={logOutNow}
          onDefaultsRestored={() => {
            agents.refresh();
            skills.refresh();
          }}
        />
      )}
      <Toaster />
    </div>
  );
}
