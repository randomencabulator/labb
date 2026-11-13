import { useCallback, useEffect, useReducer, useRef } from "react";
import { type ChatEvent, type SendOptions, type Stats, followPcReply, followReply, getHistory, stopChat, streamChat } from "./api";
import { toast } from "./components/Toaster";

export type Part =
  | { kind: "text"; text: string }
  // The model's reasoning; `seconds` is known for live replies, once the reasoning ends
  | { kind: "thinking"; text: string; startedAt?: number; seconds?: number; done: boolean }
  // `firstLine`: the start of its result (e.g. "The build failed…"), to tell a failure that isn't an error
  | { kind: "tool"; id?: string; name: string; args: Record<string, unknown>; chars?: number; firstLine?: string; error?: boolean }
  | { kind: "compact" }
  // The reply went round again (it ended too early), or a model call is tried again
  | { kind: "note"; text: string }
  // A tool call waiting for a yes; "expired" once the reply ended (the card keeps the answer given).
  // "allowed" and "denied": replaying a reply on a PC, whose answers are among its events
  | { kind: "approval"; id: string; title: string; detail: string; view?: string; handover?: boolean; answer?: "expired" | "allowed" | "denied" }
  // An agent on a PC asks something
  | { kind: "question"; id: string; question: string; options: string[]; answer?: string; expired?: boolean }
  | { kind: "error"; message: string };

export type Turn = {
  user: string;
  // Names of the attached files, and the attached images as data URLs
  files: string[];
  images: string[];
  parts: Part[];
  status: "streaming" | "done" | "stopped" | "error";
  stats?: Stats;
  // When a live reply started, for the elapsed timer
  startedAt?: number;
};

export type TurnAction =
  | { type: "reset" }
  | { type: "event"; event: ChatEvent; now?: number }
  | { type: "aborted" }
  | { type: "replayed" }
  // Keep only the first `count` turns (editing a message, or regenerating)
  | { type: "truncate"; count: number };

// Ends the reasoning in progress when the reply moves on to text or tools
function endThinking(parts: Part[], now?: number): Part[] {
  const last = parts.at(-1);
  if (last?.kind !== "thinking" || last.done) return parts;
  const seconds = last.startedAt && now ? (now - last.startedAt) / 1000 : undefined;
  return [...parts.slice(0, -1), { ...last, done: true, seconds }];
}

// Questions still open when the reply ends weren't answered here
function expireApprovals(parts: Part[]): Part[] {
  return parts.map(part => {
    if (part.kind === "approval" && !part.answer) return { ...part, answer: "expired" };
    if (part.kind === "question" && part.answer === undefined) return { ...part, expired: true };
    return part;
  });
}

// Why a reply went round again (the server's continuation.py)
const CONTINUE_REASONS: Record<string, string> = {
  empty: "The reply was empty: asking again",
  announced: "It said what it'd do next and stopped: telling it to go on",
  open_steps: "Its todo list still has open steps: back to work",
  loop_unscheduled: "It didn't say when to run again: asking",
  goal: "The goal isn't done yet: going on",
  goal_budget: "The goal used its budget: wrapping up",
  hook: "A check said the work isn't done: going on",
  audit: "Checking the evidence before the report",
};

// Turns events, streamed live or replayed from /history, into turns
export function turnsReducer(turns: Turn[], action: TurnAction): Turn[] {
  if (action.type === "reset") return [];
  if (action.type === "truncate") return turns.slice(0, action.count);
  const last = turns.at(-1);
  const update = (change: (turn: Turn) => Turn) => (last ? [...turns.slice(0, -1), change(last)] : turns);

  if (action.type === "aborted") {
    return update(turn =>
      turn.status === "streaming" ? { ...turn, parts: expireApprovals(endThinking(turn.parts, Date.now())), status: "stopped" } : turn,
    );
  }
  if (action.type === "replayed") {
    // Saved turns without stats are finished too
    return turns.map(turn =>
      turn.status === "streaming" ? { ...turn, parts: endThinking(turn.parts), status: "done" } : turn,
    );
  }

  const { event, now } = action;
  switch (event.type) {
    case "user":
      return [
        ...turns,
        { user: event.content, files: event.files ?? [], images: event.images ?? [], parts: [], status: "streaming", startedAt: now },
      ];
    case "thinking":
      return update(turn => {
        const lastPart = turn.parts.at(-1);
        if (lastPart?.kind === "thinking" && !lastPart.done) {
          return { ...turn, parts: [...turn.parts.slice(0, -1), { ...lastPart, text: lastPart.text + event.content }] };
        }
        return { ...turn, parts: [...turn.parts, { kind: "thinking", text: event.content, startedAt: now, done: false }] };
      });
    case "token":
      return update(turn => {
        const parts = endThinking(turn.parts, now);
        const lastPart = parts.at(-1);
        if (lastPart?.kind === "text") {
          return { ...turn, parts: [...parts.slice(0, -1), { kind: "text", text: lastPart.text + event.content }] };
        }
        return { ...turn, parts: [...parts, { kind: "text", text: event.content }] };
      });
    case "tool_call":
      return update(turn => ({
        ...turn,
        parts: [...endThinking(turn.parts, now), { kind: "tool", id: event.id, name: event.name, args: event.args }],
      }));
    case "tool_result":
      return update(turn => {
        // Its call (by id), or the first tool line still waiting for its result
        const index = turn.parts.findIndex(
          part => part.kind === "tool" && part.chars === undefined && (event.id ? part.id === event.id : true),
        );
        if (index < 0) return turn;
        const parts = [...turn.parts];
        parts[index] = { ...(parts[index] as Extract<Part, { kind: "tool" }>), chars: event.chars, firstLine: event.first_line, error: event.error };
        return { ...turn, parts };
      });
    case "compact":
      return update(turn => ({ ...turn, parts: [...endThinking(turn.parts, now), { kind: "compact" }] }));
    case "continue":
      // A cut-off reply goes on in the same text
      if (event.reason === "truncated") return update(turn => ({ ...turn, parts: endThinking(turn.parts, now) }));
      return update(turn => ({ ...turn, parts: [...endThinking(turn.parts, now), { kind: "note", text: CONTINUE_REASONS[event.reason] ?? "Going on" }] }));
    case "retry":
      // What the failed call wrote comes again
      return update(turn => {
        const parts = endThinking(turn.parts, now);
        const kept = parts.at(-1)?.kind === "text" ? parts.slice(0, -1) : parts;
        return { ...turn, parts: [...kept, { kind: "note", text: `The model call failed, trying again in ${event.wait}s: ${event.message}` }] };
      });
    case "approval":
      return update(turn => ({
        ...turn,
        parts: [...endThinking(turn.parts, now), { kind: "approval", id: event.id, title: event.title, detail: event.detail, view: event.view, handover: event.handover }],
      }));
    case "question":
      return update(turn => ({
        ...turn,
        parts: [...endThinking(turn.parts, now), { kind: "question", id: event.id, question: event.question, options: event.options }],
      }));
    case "answered":
      return update(turn => ({
        ...turn,
        parts: turn.parts.map(part => {
          if (part.kind === "approval" && part.id === event.id) return { ...part, answer: event.allow === true ? "allowed" : "denied" };
          if (part.kind === "question" && part.id === event.id) return { ...part, answer: event.text ?? "" };
          return part;
        }),
      }));
    case "error":
      return update(turn => ({
        ...turn,
        parts: [...endThinking(turn.parts, now), { kind: "error", message: event.message }],
        status: "error",
      }));
    case "stats": {
      const { type: _, ...stats } = event;
      return update(turn => ({ ...turn, stats, status: stats.stopped ? "stopped" : "done" }));
    }
    case "done":
    case "stopped":
      return update(turn => ({
        ...turn,
        parts: expireApprovals(endThinking(turn.parts, now)),
        stats: event.stats,
        status: turn.status === "error" ? "error" : event.type,
      }));
    default:
      return turns;
  }
}

/** The model the chat's last reply used, if one says */
export function lastModel(turns: Turn[]): string | null {
  for (let i = turns.length - 1; i >= 0; i--) if (turns[i].stats?.model) return turns[i].stats!.model;
  return null;
}

/** One chat in the page. A new chat has no threadId until the server sends one. */
export type Session = {
  threadId: string | null;
  turns: Turn[];
  streaming: boolean;
  stopping: boolean;
  // Its saved conversation is being fetched
  loading: boolean;
  loadError: string | null;
};

const emptySession = (threadId: string | null): Session => ({
  threadId,
  turns: [],
  streaming: false,
  stopping: false,
  loading: false,
  loadError: null,
});

// Sessions are keyed by threadId, or by a temporary key ("new-1") until a new chat gets one
type State = { sessions: Record<string, Session>; activeKey: string };

type Action =
  | { type: "turns"; key: string; action: TurnAction }
  | { type: "loaded"; key: string; events: ChatEvent[] }
  | { type: "set"; key: string; change: Partial<Session> }
  | { type: "create"; key: string; threadId: string | null }
  | { type: "activate"; key: string }
  | { type: "rekey"; from: string; to: string }
  | { type: "remove"; key: string };

function reducer(state: State, action: Action): State {
  const { sessions } = state;
  switch (action.type) {
    case "turns": {
      const session = sessions[action.key];
      if (!session) return state;
      return { ...state, sessions: { ...sessions, [action.key]: { ...session, turns: turnsReducer(session.turns, action.action) } } };
    }
    case "loaded": {
      const session = sessions[action.key];
      if (!session) return state;
      const turns = turnsReducer(
        action.events.reduce((turns, event) => turnsReducer(turns, { type: "event", event }), [] as Turn[]),
        { type: "replayed" },
      );
      return { ...state, sessions: { ...sessions, [action.key]: { ...session, turns, loading: false, loadError: null } } };
    }
    case "set": {
      const session = sessions[action.key];
      if (!session) return state;
      return { ...state, sessions: { ...sessions, [action.key]: { ...session, ...action.change } } };
    }
    case "create":
      return { ...state, sessions: { ...sessions, [action.key]: emptySession(action.threadId) } };
    case "activate":
      return { ...state, activeKey: action.key };
    case "rekey": {
      const { [action.from]: session, ...others } = sessions;
      if (!session) return state;
      return {
        sessions: { ...others, [action.to]: { ...session, threadId: action.to } },
        activeKey: state.activeKey === action.from ? action.to : state.activeKey,
      };
    }
    case "remove": {
      const { [action.key]: _, ...others } = sessions;
      return { ...state, sessions: others };
    }
  }
}

function initialState(threadId: string | null): State {
  const key = threadId ?? "new-0";
  return { sessions: { [key]: emptySession(threadId) }, activeKey: key };
}

// How long Stop waits for the server to end the reply (and send its stats) before giving up on it
const STOP_TIMEOUT_MS = 1500;
// Waits between tries to join a reply again after losing its stream (about two minutes in all);
// going back online cuts a wait short
const RECONNECT_WAITS_MS = [500, 1000, 2000, 4000, 8000, 15000, 30000, 30000, 30000];

function pause(ms: number) {
  return new Promise<void>(resolve => {
    const done = () => {
      clearTimeout(timer);
      window.removeEventListener("online", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    window.addEventListener("online", done);
  });
}

// Joining a reply: it ended while joined, there was none, or the stream broke off before its end
type Followed = "ended" | "none" | "lost";

/**
 * The chats open in the page. Each one streams on its own, so you can switch to
 * another chat (or start a new one) while a reply is still being written.
 */
export function useChat(
  onChatsChanged: () => void,
  initialThreadId: string | null,
  // A new chat got its id: what its first message was sent with (e.g. the model, for it to keep)
  onStarted?: (threadId: string, options: SendOptions) => void,
) {
  const [state, dispatch] = useReducer(reducer, initialThreadId, initialState);
  const stateRef = useRef(state);
  stateRef.current = state;
  const started = useRef(onStarted);
  started.current = onStarted;
  // The running request of each session, and the current key of each stream:
  // a new chat's key changes from "new-N" to its threadId mid-stream
  const controllers = useRef(new Map<string, AbortController>());
  const nextNewKey = useRef(1);
  // Only the latest history request of a chat counts, if it's opened again while loading
  const loads = useRef(new Map<string, number>());

  const active = state.sessions[state.activeKey] ?? emptySession(null);

  // A reply can be going on with nobody watching (on a PC, or one this page lost the stream
  // of, or that was sent from another page): join it, from the reply's start
  const follow = useCallback(async (threadId: string): Promise<Followed> => {
    if (controllers.current.has(threadId)) return "ended";
    const controller = new AbortController();
    controllers.current.set(threadId, controller);
    let joined = false;
    let ended = false;
    let lost = false;
    try {
      await (threadId.startsWith("cli-") ? followPcReply : followReply)(
        threadId,
        event => {
          if (event.type === "user") {
            joined = true;
            dispatch({ type: "set", key: threadId, change: { streaming: true, stopping: false } });
            // What's saved may have this turn already, or the ones it replaces
            dispatch({ type: "turns", key: threadId, action: { type: "truncate", count: event.turn ?? Number.MAX_SAFE_INTEGER } });
          }
          if (event.type === "done" || event.type === "stopped") ended = true;
          dispatch({ type: "turns", key: threadId, action: { type: "event", event, now: Date.now() } });
        },
        controller.signal,
      );
      lost = joined && !ended && !controller.signal.aborted;
    } catch {
      // It goes on without us: try again, or opening the chat again joins it again
      lost = !controller.signal.aborted;
    } finally {
      controllers.current.delete(threadId);
      if (joined && !lost) {
        dispatch({ type: "turns", key: threadId, action: { type: "aborted" } });
        dispatch({ type: "set", key: threadId, change: { streaming: false, stopping: false } });
        onChatsChanged();
      }
    }
    return lost ? "lost" : joined ? "ended" : "none";
  }, [onChatsChanged]);

  // Join a reply, and again while the stream keeps breaking off
  const keepFollowing = useCallback(async (threadId: string): Promise<Followed> => {
    for (const wait of [0, ...RECONNECT_WAITS_MS]) {
      if (wait) await pause(wait);
      const followed = await follow(threadId);
      if (followed !== "lost") return followed;
    }
    if (stateRef.current.sessions[threadId]?.streaming) {
      dispatch({ type: "turns", key: threadId, action: { type: "event", event: { type: "error", message: "Lost the connection to the server. The reply goes on there: open the chat again to see it." } } });
      dispatch({ type: "turns", key: threadId, action: { type: "aborted" } });
      dispatch({ type: "set", key: threadId, change: { streaming: false, stopping: false } });
    }
    return "lost";
  }, [follow]);

  const load = useCallback(async (threadId: string) => {
    const request = (loads.current.get(threadId) ?? 0) + 1;
    loads.current.set(threadId, request);
    dispatch({ type: "set", key: threadId, change: { loading: true } });
    try {
      const events = await getHistory(threadId);
      if (loads.current.get(threadId) !== request) return;
      dispatch({ type: "loaded", key: threadId, events });
      void keepFollowing(threadId);
    } catch (e) {
      if (loads.current.get(threadId) !== request) return;
      dispatch({ type: "set", key: threadId, change: { loading: false, loadError: `Couldn't load the conversation: ${(e as Error).message}` } });
    }
  }, [keepFollowing]);

  /** Show a saved chat, or a new one (null). A chat that's still answering keeps going. */
  const open = useCallback(
    (threadId: string | null) => {
      const { sessions, activeKey } = stateRef.current;
      if (threadId === null) {
        // Reuse the current new chat if nothing was sent in it yet
        const current = sessions[activeKey];
        if (!(current && current.threadId === null && current.turns.length === 0)) {
          const key = `new-${nextNewKey.current++}`;
          dispatch({ type: "create", key, threadId: null });
          dispatch({ type: "activate", key });
        }
        return;
      }
      const session = sessions[threadId];
      if (!session) dispatch({ type: "create", key: threadId, threadId });
      dispatch({ type: "activate", key: threadId });
      // A chat that's answering is already up to date; reload the others, in case they changed since
      if (!session?.streaming) load(threadId);
    },
    [load],
  );

  // Show the conversation the page was opened at
  useEffect(() => {
    const { sessions, activeKey } = stateRef.current;
    const threadId = sessions[activeKey]?.threadId;
    if (threadId) load(threadId);
  }, [load]);

  /**
   * Send a message in the open chat. With `options.replaceFrom`, it replaces that user
   * message and everything after it (editing a message, or regenerating the last reply).
   * `kept` are the attachments the server keeps from the replaced message, to show.
   */
  const send = useCallback(
    async (message: string, options: SendOptions = {}, kept: { files: string[]; images: string[] } = { files: [], images: [] }) => {
      let key = stateRef.current.activeKey;
      const threadId = stateRef.current.sessions[key]?.threadId ?? null;
      const controller = new AbortController();
      controllers.current.set(key, controller);
      const attachments = options.attachments ?? [];
      const userEvent: ChatEvent = {
        type: "user",
        content: message,
        files: [...kept.files, ...attachments.filter(a => a.kind === "text").map(a => a.name)],
        images: [...kept.images, ...attachments.filter(a => a.kind === "image").map(a => `data:${a.mime_type};base64,${a.data}`)],
      };
      dispatch({ type: "set", key, change: { streaming: true, stopping: false } });
      if (options.replaceFrom !== undefined) dispatch({ type: "turns", key, action: { type: "truncate", count: options.replaceFrom } });
      dispatch({ type: "turns", key, action: { type: "event", event: userEvent, now: Date.now() } });
      let failed = false;
      // Whether the reply's end came, and the error if the stream broke off
      let ended = false;
      let broken: unknown = null;
      try {
        await streamChat(
          message,
          threadId,
          options,
          event => {
            if (event.type === "thread") {
              // The new chat got its id: key it by that from now on
              dispatch({ type: "rekey", from: key, to: event.thread_id });
              controllers.current.delete(key);
              controllers.current.set(event.thread_id, controller);
              key = event.thread_id;
              started.current?.(event.thread_id, options);
              onChatsChanged();
            } else if (event.type === "title") {
              onChatsChanged();
            } else {
              if (event.type === "done" || event.type === "stopped") ended = true;
              dispatch({ type: "turns", key, action: { type: "event", event, now: Date.now() } });
            }
          },
          controller.signal,
        );
      } catch (e) {
        broken = e;
      }
      // The connection went (a network error, not the server refusing): the reply goes on on the
      // server, so join it again. A new chat that never got its id can't be found again.
      const lost = !ended && !controller.signal.aborted && (broken === null || broken instanceof TypeError)
        && stateRef.current.sessions[key]?.threadId != null;
      if (broken !== null && !lost && !controller.signal.aborted) {
        failed = true;
        dispatch({ type: "turns", key, action: { type: "event", event: { type: "error", message: (broken as Error).message } } });
      }
      controllers.current.delete(key);
      if (lost) {
        if ((await keepFollowing(key)) === "none") {
          // It ended meanwhile: what it wrote is saved
          dispatch({ type: "turns", key, action: { type: "aborted" } });
          dispatch({ type: "set", key, change: { streaming: false, stopping: false } });
          await load(key);
        }
        onChatsChanged();
        return;
      }
      dispatch({ type: "turns", key, action: { type: "aborted" } });
      dispatch({ type: "set", key, change: { streaming: false, stopping: false } });
      onChatsChanged();
      // A replacement the server refused left the conversation as it was: show that again
      if (failed && options.replaceFrom !== undefined && threadId) {
        const error = stateRef.current.sessions[key]?.turns.at(-1)?.parts.at(-1);
        await load(threadId);
        if (error?.kind === "error") toast(error.message);
      }
    },
    [onChatsChanged, load, keepFollowing],
  );

  /** Stop the reply in the open chat */
  const stop = useCallback(() => {
    const { sessions, activeKey } = stateRef.current;
    const controller = controllers.current.get(activeKey);
    if (!controller) return;
    const threadId = sessions[activeKey]?.threadId;
    dispatch({ type: "set", key: activeKey, change: { stopping: true } });
    // The server stops at its next step and ends the stream with the stats; if that takes
    // longer (a tool can run for a while), stop waiting. Leaving the page doesn't stop it.
    if (threadId) stopChat(threadId).catch(() => {});
    setTimeout(() => controller.abort(), threadId ? STOP_TIMEOUT_MS : 0);
  }, []);

  /** Forget a deleted chat; show a new chat if it was open */
  const forget = useCallback(
    (threadId: string) => {
      const wasActive = stateRef.current.activeKey === threadId;
      dispatch({ type: "remove", key: threadId });
      if (wasActive) open(null);
    },
    [open],
  );

  const answering = new Set(
    Object.values(state.sessions)
      .filter(session => session.streaming && session.threadId)
      .map(session => session.threadId!),
  );

  return {
    activeKey: state.activeKey,
    threadId: active.threadId,
    turns: active.turns,
    streaming: active.streaming,
    stopping: active.stopping,
    loading: active.loading,
    loadError: active.loadError,
    answering,
    send,
    stop,
    open,
    forget,
  };
}
