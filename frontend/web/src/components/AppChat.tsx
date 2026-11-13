import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { type App, type AppSession, type Attachment, type ChatEvent, type SendOptions, appFreshStart, followReply, getHistory, listAppSessions, stopChat, streamChat } from "../api";
import { relativeTime } from "../format";
import { type Part, type Turn, type TurnAction, lastModel, turnsReducer } from "../useChat";
import { useModels } from "../useModels";
import { useSkills } from "../useSkills";
import { Conversation } from "./Conversation";
import { CloseIcon, NewChatIcon, RegenerateIcon } from "./Icons";
import { toast } from "./Toaster";

// What an empty app chat suggests
const STARTERS = [
  "What does this app do now, and how is it built?",
  "Turn it into a to-do list that keeps items while it runs",
  "Add a dark and light theme switch",
];

// Away from the page this long, it's loaded again when it's back
const AWAY_MS = 2000;
// A stream that said nothing for this long (the server pings every 15 s) is taken for gone
const STREAM_SILENT_MS = 20_000;
// How long Stop waits for the server to end the reply (and send its stats) before giving up on it
const STOP_TIMEOUT_MS = 1500;

// A reply is taken for one going round in circles once this many of its builds failed, or this
// many of its steps (edits, writes, builds, checks)
const STUCK_BUILDS = 3;
const STUCK_STEPS = 8;

/** Whether a tool's result says it didn't work: an error, or a build that failed */
function failed(part: Part): boolean {
  if (part.kind !== "tool") return false;
  return Boolean(part.error) || (part.name === "build_app" && Boolean(part.firstLine?.startsWith("The build failed")));
}

/** Why a reply looks stuck, or null */
export function stuckReason(turn: Turn | undefined): string | null {
  if (!turn) return null;
  const failures = turn.parts.filter(failed);
  const builds = failures.filter(part => part.kind === "tool" && part.name === "build_app").length;
  if (builds >= STUCK_BUILDS) return `${builds} builds failed`;
  if (failures.length >= STUCK_STEPS) return `${failures.length} of its steps failed`;
  return null;
}

// The session open on an app's page, kept per app in the browser
const sessionKey = (appId: string) => `appSession:${appId}`;

function storedSession(appId: string): string {
  const first = `app-${appId}`;
  try {
    const kept = localStorage.getItem(sessionKey(appId));
    // One of this app's sessions (app-<id> or app-<id>--<session>)
    if (kept && (kept === first || kept.startsWith(`${first}--`))) return kept;
  } catch {
    // No storage: the first session
  }
  return first;
}

function keepSession(appId: string, threadId: string) {
  try {
    localStorage.setItem(sessionKey(appId), threadId);
  } catch {
    // It's only remembered while the page is open
  }
}

/** A new session's thread: app-<id>--<10 random letters and digits> */
function newSessionThread(appId: string) {
  const letters = "abcdefghijklmnopqrstuvwxyz0123456789";
  const random = crypto.getRandomValues(new Uint8Array(10));
  return `app-${appId}--${Array.from(random, n => letters[n % letters.length]).join("")}`;
}

// The turns, or all of them at once (a chat's history, replayed)
const reducer = (turns: Turn[], action: TurnAction | { type: "set"; turns: Turn[] }) =>
  action.type === "set" ? action.turns : turnsReducer(turns, action);

/**
 * The chat that belongs to an app, beside it on its page: labb with the app's tools, so it can
 * change its files, build it and check it. It's kept like any chat; a reply goes on if the page
 * closes, and is joined again when it opens. An app has sessions (its first is thread app-<id>):
 * New session starts one with nothing from the others in its context, and earlier ones can be
 * picked again. Start fresh does that too, opening with what was asked and where the app is, for
 * a session that's stuck (offered by itself once a reply looks it: stuckReason). Its messages and
 * message box are the main chat's (Conversation): attachments, voice, editing a message, writing
 * the last reply again, the model (each session its own; a new one starts with the one it came from), how it
 * answers and Web.
 */
export function AppChat({ app, onReplyEnded }: { app: App; onReplyEnded: () => void }) {
  const [threadId, setThreadId] = useState(() => storedSession(app.id));
  const [sessions, setSessions] = useState<AppSession[]>([]);
  const [turns, dispatch] = useReducer(reducer, [] as Turn[]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [stopping, setStopping] = useState(false);
  const { models, choose, modelFor, settingsFor, rememberSettings } = useModels();
  // Each session keeps its own model and settings (picking one changes no other chat)
  const model = modelFor(threadId, lastModel(turns));
  const { answerMode, web } = settingsFor(threadId);
  const { skills } = useSkills();
  const controller = useRef<AbortController | null>(null);
  // Each load and stream counts up, so a stream that was replaced can't change the turns when it ends
  const generation = useRef(0);
  // When the stream last said anything (the server pings every PING_SECONDS while a reply is quiet)
  const lastHeard = useRef(0);
  const streamingRef = useRef(false);
  streamingRef.current = streaming;
  const ended = useRef(onReplyEnded);
  ended.current = onReplyEnded;
  // The first message of a session opened by Start fresh, sent once it's loaded
  const startWith = useRef<string | null>(null);
  const [starting, setStarting] = useState(false);
  // The reply whose "going round in circles" notice was closed (its session and place), so the next one shows again
  const [dismissedStuck, setDismissedStuck] = useState<string | null>(null);

  const loadSessions = useCallback(() => {
    listAppSessions(app.id).then(setSessions, () => {});
  }, [app.id]);
  useEffect(loadSessions, [loadSessions]);

  // Another app on the same page: its own session
  useEffect(() => {
    setThreadId(storedSession(app.id));
  }, [app.id]);

  const openSession = (thread: string) => {
    keepSession(app.id, thread);
    setThreadId(thread);
  };

  /** A new session, with the model and settings this one has */
  const openNewSession = () => {
    const thread = newSessionThread(app.id);
    if (model) choose(thread, model);
    rememberSettings(thread, settingsFor(threadId));
    openSession(thread);
  };

  const onEvent = (event: ChatEvent) => {
    lastHeard.current = Date.now();
    if (event.type !== "ping") dispatch({ type: "event", event, now: Date.now() });
  };

  /** Follow a stream of events into the turns until it ends; the error, if it broke off with one */
  const follow = async (run: (signal: AbortSignal) => Promise<void>): Promise<string | null> => {
    const mine = ++generation.current;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    lastHeard.current = Date.now();
    setStreaming(true);
    setStopping(false);
    let failure: string | null = null;
    try {
      await run(abort.signal);
    } catch (e) {
      if (!abort.signal.aborted && generation.current === mine) {
        failure = (e as Error).message;
        dispatch({ type: "event", event: { type: "error", message: failure } });
      }
    } finally {
      if (generation.current === mine) {
        dispatch({ type: "aborted" });
        setStreaming(false);
        setStopping(false);
        ended.current();
        // A new session is saved, or got its title
        loadSessions();
      }
    }
    return failure;
  };

  /** Its history, then a reply being written, if there is one. `quiet` keeps what's shown while it loads. */
  const load = (quiet: boolean) => {
    const mine = ++generation.current;
    controller.current?.abort();
    if (!quiet) {
      dispatch({ type: "reset" });
      setLoading(true);
    }
    setError(null);
    getHistory(threadId).then(
      events => {
        if (generation.current !== mine) return;
        const replayed = events.reduce((all, event) => turnsReducer(all, { type: "event", event }), [] as Turn[]);
        dispatch({ type: "set", turns: turnsReducer(replayed, { type: "replayed" }) });
        setLoading(false);
        setStreaming(false);
        const first = startWith.current;
        startWith.current = null;
        if (first) {
          dispatch({ type: "event", event: { type: "user", content: first }, now: Date.now() });
          follow(signal => streamChat(first, threadId, sendOptions(), onEvent, signal));
        } else {
          follow(signal => followReply(threadId, onEvent, signal));
        }
      },
      e => {
        if (generation.current !== mine) return;
        // Coming back to it offline keeps what was shown
        if (!quiet) setError((e as Error).message);
        setLoading(false);
      },
    );
  };

  useEffect(() => {
    load(false);
    return () => {
      generation.current++;
      controller.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  // Back to the page (another tab, another app, the phone woke up, back online): a phone stops a
  // hidden page's streams without saying so, and replies go on on the server meanwhile, so load it
  // again, unless its stream is still clearly alive
  useEffect(() => {
    let hiddenAt: number | null = document.visibilityState === "hidden" ? Date.now() : null;
    const resync = () => {
      const alive = streamingRef.current && Date.now() - lastHeard.current < STREAM_SILENT_MS;
      if (!alive) load(true);
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        hiddenAt = Date.now();
        return;
      }
      const away = hiddenAt === null ? 0 : Date.now() - hiddenAt;
      hiddenAt = null;
      if (away >= AWAY_MS) resync();
    };
    // Restored from the back/forward cache, with whatever it had then
    const onPageShow = (event: PageTransitionEvent) => event.persisted && resync();
    window.addEventListener("online", resync);
    window.addEventListener("pageshow", onPageShow);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("online", resync);
      window.removeEventListener("pageshow", onPageShow);
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  // What a message goes with: this app, and the message box's choices
  const sendOptions = (): SendOptions => ({ appId: app.id, model: model ?? undefined, answerMode, web });

  const send = (message: string, attachments: Attachment[]) => {
    if (!message.trim() || streaming || app.legacy) return;
    dispatch({
      type: "event",
      event: {
        type: "user",
        content: message,
        files: attachments.filter(a => a.kind === "text").map(a => a.name),
        images: attachments.filter(a => a.kind === "image").map(a => `data:${a.mime_type};base64,${a.data}`),
      },
      now: Date.now(),
    });
    follow(signal => streamChat(message, threadId, { ...sendOptions(), attachments }, onEvent, signal));
  };

  /** Message `index` and everything after it replaced, keeping its attachments: editing it, or
   * writing the last reply again. The app's files stay as the replaced replies left them. */
  const replace = async (index: number, message: string) => {
    const turn = turns[index];
    if (!turn || streaming) return;
    dispatch({ type: "truncate", count: index });
    dispatch({ type: "event", event: { type: "user", content: message, files: turn.files, images: turn.images }, now: Date.now() });
    const failure = await follow(signal =>
      streamChat(message, threadId, { ...sendOptions(), replaceFrom: index, keepAttachments: true }, onEvent, signal),
    );
    // The server refused it: the conversation is as it was
    if (failure) {
      load(true);
      toast(failure);
    }
  };

  const stop = () => {
    if (!controller.current || stopping) return;
    const abort = controller.current;
    setStopping(true);
    // The server stops at its next step and ends the stream with the stats; a tool can take longer
    stopChat(threadId).catch(() => {});
    setTimeout(() => abort.abort(), STOP_TIMEOUT_MS);
  };

  /** A new session that's told what was asked in this one and where the app is now, but not what
   * this one concluded: for when labb goes round in circles */
  const startFresh = async () => {
    if (starting) return;
    setStarting(true);
    if (streaming) stop();
    let message: string | null = null;
    try {
      message = (await appFreshStart(app.id, threadId)).message;
    } catch {
      // A session that was never saved has nothing to carry over: a plain new one
    }
    startWith.current = message;
    setStarting(false);
    openNewSession();
  };

  const stuckKey = `${threadId}:${turns.length}`;
  const stuck = dismissedStuck === stuckKey ? null : stuckReason(turns.at(-1));

  return (
    <aside className="try-chat app-chat" aria-label={`Chat about ${app.name}`}>
      <div className="try-head app-chat-head">
        <b>Build with labb</b>
        <select
          className="app-sessions"
          value={threadId}
          onChange={e => openSession(e.target.value)}
          aria-label="Session"
          title="Earlier sessions with labb about this app"
        >
          {!sessions.some(s => s.thread_id === threadId) && <option value={threadId}>New session</option>}
          {sessions.map(s => (
            <option key={s.thread_id} value={s.thread_id}>
              {s.title} · {relativeTime(s.updated_at)}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="icon-button"
          onClick={openNewSession}
          disabled={!turns.length && !sessions.some(s => s.thread_id === threadId)}
          aria-label="New session"
          title="New session: start again with nothing from this one in its context"
        >
          <NewChatIcon />
        </button>
        <button
          type="button"
          className="icon-button"
          onClick={startFresh}
          disabled={starting || !turns.length || app.legacy}
          aria-label="Start fresh"
          title="Start fresh: a new session told what you asked for here and where the app is now, without this session's history"
        >
          <RegenerateIcon />
        </button>
      </div>
      <Conversation
        turns={turns}
        loading={loading}
        error={error}
        threadId={threadId}
        streaming={streaming}
        stopping={stopping}
        name=""
        agent={{ emoji: "🛠️", name: app.name, description: "Say what this app should do or change, and labb builds it here.", starters: STARTERS }}
        canChange={!streaming && !app.legacy && turns.length > 0}
        onSend={send}
        onReplace={replace}
        onStop={stop}
        disabled={app.legacy}
        settings={{
          models,
          model,
          onModel: spec => choose(threadId, spec),
          answerMode,
          onAnswerMode: mode => rememberSettings(threadId, { answerMode: mode }),
          web,
          onWeb: on => rememberSettings(threadId, { web: on }),
          webAvailable: true,
          skills,
          placeholder: app.legacy ? "This app can't be changed: make a new one" : "What should it do?",
        }}
        above={stuck && (
          <div className="app-stuck" role="status">
            <span>labb seems to be going round in circles: {stuck} in this reply.</span>
            <button type="button" onClick={startFresh} disabled={starting}>
              {streaming ? "Stop and start fresh" : "Start fresh"}
            </button>
            <button type="button" className="app-stuck-close" onClick={() => setDismissedStuck(stuckKey)} aria-label="Close" title="Keep going in this session">
              <CloseIcon />
            </button>
          </div>
        )}
      />
    </aside>
  );
}
