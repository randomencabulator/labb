import { type FormEvent, useCallback, useEffect, useState } from "react";
import {
  type Agent,
  type HeartbeatSettings,
  type HeartbeatTodo,
  addTodo,
  beatNow,
  changeTodo,
  clearDoneTodos,
  deleteTodo,
  getHeartbeat,
  listTodos,
  saveHeartbeat,
} from "../api";
import { moment, relativeTime } from "../format";
import { toast } from "./Toaster";

const STATUS: Record<HeartbeatTodo["status"], string> = {
  open: "waiting",
  running: "working on it",
  done: "done",
  failed: "failed",
};

/** Tasks → Heartbeat: every so often, labb looks at your to-dos and works through them on its own.
 * Off until it's turned on; each to-do runs in a chat of its own. */
export function Heartbeat({
  agents,
  onOpenChat,
}: {
  agents: Agent[];
  onOpenChat: (threadId: string) => void;
}) {
  const [settings, setSettings] = useState<HeartbeatSettings | null>(null);
  const [todos, setTodos] = useState<HeartbeatTodo[]>([]);
  const [text, setText] = useState("");
  const [every, setEvery] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    Promise.all([getHeartbeat(), listTodos()]).then(
      ([found, list]) => {
        setSettings(found);
        setEvery((current) => current || String(found.every_minutes));
        setTodos(list);
        setError(null);
      },
      (e) => setError((e as Error).message),
    );
  }, []);
  useEffect(reload, [reload]);

  // Follow to-dos as they're worked on
  const working = todos.some((t) => t.status === "running");
  useEffect(() => {
    const timer = setInterval(reload, working ? 3000 : 30_000);
    return () => clearInterval(timer);
  }, [working, reload]);

  if (!settings) return error ? <div className="problem">{error}</div> : null;

  const save = (fields: Parameters<typeof saveHeartbeat>[0]) =>
    saveHeartbeat(fields).then(
      (found) => {
        setSettings(found);
        setEvery(String(found.every_minutes));
        setError(null);
      },
      (e) => setError((e as Error).message),
    );
  const act = (action: () => Promise<unknown>) =>
    action().then(reload, (e) => setError((e as Error).message));

  const add = async (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim()) return;
    try {
      await addTodo(text);
      setText("");
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const open = todos.filter(
    (t) => t.status === "open" || t.status === "running",
  );
  const finished = todos.filter(
    (t) => t.status === "done" || t.status === "failed",
  );

  return (
    <section
      className={`heartbeat${settings.enabled ? " on" : ""}`}
      aria-label="Heartbeat"
    >
      <div className="heartbeat-head">
        <div>
          <h3>
            <span className="heartbeat-mark" aria-hidden>
              ♥
            </span>{" "}
            Heartbeat
          </h3>
          <p className="hint">
            {settings.enabled
              ? `On: every ${settings.every_minutes} minutes labb takes the oldest ${settings.per_beat === 1 ? "to-do" : `${settings.per_beat} to-dos`} and does ${settings.per_beat === 1 ? "it" : "them"}, each in a chat of its own.${settings.next_beat ? ` Next look ${moment(settings.next_beat)}.` : ""}`
              : "Leave labb to-dos, and every so often it picks them up and does them on its own, each in a chat of its own you'll find marked unread. It's off until you turn it on."}
          </p>
        </div>
        <label
          className="switch"
          title={settings.enabled ? "Turn it off" : "Turn it on"}
        >
          <input
            type="checkbox"
            role="switch"
            aria-label="Heartbeat"
            checked={settings.enabled}
            onChange={(e) => save({ enabled: e.target.checked })}
          />
          <span />
        </label>
      </div>
      {settings.disabled_reason && !settings.enabled && (
        <div className="problem">
          It turned itself off: {settings.disabled_reason}
        </div>
      )}
      {error && <div className="problem">{error}</div>}

      <div className="heartbeat-settings">
        <label>
          Every
          <input
            type="number"
            min={settings.min_minutes}
            max={1440}
            value={every}
            onChange={(e) => setEvery(e.target.value)}
            onBlur={() =>
              Number(every) !== settings.every_minutes &&
              save({ every_minutes: Number(every) })
            }
          />
          minutes
        </label>
        <label>
          With
          <select
            value={settings.agent_id ?? ""}
            onChange={(e) => save({ agent_id: e.target.value || null })}
          >
            <option value="">labb</option>
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.emoji} {agent.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <select
            value={settings.per_beat}
            onChange={(e) => save({ per_beat: Number(e.target.value) })}
          >
            {[1, 2, 3, 5, 10].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          {settings.per_beat === 1 ? "to-do" : "to-dos"} each time
        </label>
      </div>

      <form className="heartbeat-add" onSubmit={add}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Something to do or find out, e.g. “check whether the conference has posted its schedule, and list the talks on AI”"
          rows={2}
          maxLength={4000}
          aria-label="New to-do"
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) add(e);
          }}
        />
        <div className="heartbeat-add-actions">
          <button type="submit" className="primary" disabled={!text.trim()}>
            Add to-do
          </button>
          {settings.enabled && settings.open > 0 && (
            <button
              type="button"
              disabled={working}
              onClick={() =>
                beatNow().then(
                  (done) => {
                    toast(
                      `Working on ${done.queued} ${done.queued === 1 ? "to-do" : "to-dos"} now`,
                    );
                    reload();
                  },
                  (e) => setError((e as Error).message),
                )
              }
            >
              Do them now
            </button>
          )}
        </div>
      </form>

      {open.length > 0 && (
        <ul className="todo-list">
          {open.map((todo) => (
            <li key={todo.id} className={todo.status}>
              <span className="todo-text">{todo.text}</span>
              <span className={`todo-state ${todo.status}`}>
                {STATUS[todo.status]}
              </span>
              <button
                type="button"
                className="link"
                onClick={() => act(() => deleteTodo(todo.id))}
              >
                {todo.status === "running" ? "Stop" : "Remove"}
              </button>
            </li>
          ))}
        </ul>
      )}
      {!settings.enabled && open.length > 0 && (
        <p className="hint">Turn the heartbeat on and these get done.</p>
      )}

      {finished.length > 0 && (
        <>
          <div className="todo-done-head">
            <h4>Done</h4>
            <button
              type="button"
              className="link"
              onClick={() => act(clearDoneTodos)}
            >
              Clear
            </button>
          </div>
          <ul className="todo-list">
            {finished.map((todo) => (
              <li key={todo.id} className={todo.status}>
                <span className="todo-text">
                  {todo.text}
                  {todo.error && (
                    <span className="problem-text"> · {todo.error}</span>
                  )}
                </span>
                <span className={`todo-state ${todo.status}`}>
                  {STATUS[todo.status]}
                  {todo.finished_at ? ` ${relativeTime(todo.finished_at)}` : ""}
                </span>
                {todo.thread_id && (
                  <button
                    type="button"
                    className="link"
                    onClick={() => onOpenChat(todo.thread_id!)}
                  >
                    Open
                  </button>
                )}
                {todo.status === "failed" && (
                  <button
                    type="button"
                    className="link"
                    onClick={() =>
                      act(() => changeTodo(todo.id, { reopen: true }))
                    }
                  >
                    Try again
                  </button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
