import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useLimits } from "../useLimits";
import {
  type Agent,
  type Schedule,
  type Task,
  type TaskFields,
  type TaskRun,
  createTask,
  deleteTask,
  listTaskRuns,
  listTasks,
  previewSchedule,
  runTask,
  stopTaskRun,
  updateTask,
} from "../api";
import { moment } from "../format";
import { NotFound } from "./NotFound";
import { toast } from "./Toaster";
import { Heartbeat } from "./Heartbeat";
import { Logo } from "./Icons";

// What another page fills the editor with: "Schedule" on an agent, "Run this every…" in a chat
export type TaskDraft = { name?: string; prompt?: string; agent_id?: string | null };

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// The same limits as the server (tasks.py)
const EVERY_MAX = { minutes: 24 * 60, hours: 24 * 7, days: 365 };
// How a run's goal ended, for a task that keeps going until done
const GOAL_OUTCOME: Record<string, string> = {
  complete: "goal done",
  blocked: "goal blocked",
  budget_limited: "goal out of model calls",
  paused: "goal paused",
  active: "goal unfinished",
};

/** "goal done, 7 of 24 calls", with its note to hover */
function GoalOutcome({ goal }: { goal: NonNullable<TaskRun["goal"]> }) {
  return (
    <span className={`goal-status ${goal.status}`} title={goal.note ?? undefined}>
      {" "}
      · {GOAL_OUTCOME[goal.status] ?? goal.status}
      {goal.used != null && `, ${goal.used}${goal.budget ? ` of ${goal.budget}` : ""} calls`}
    </span>
  );
}

const STATUS: Record<TaskRun["status"], string> = {
  queued: "Waiting to run",
  running: "Running",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
};

function browserZone() {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    // Some browsers (and headless ones) don't know theirs
    return zone && zone !== "Etc/Unknown" ? zone : "UTC";
  } catch {
    return "UTC";
  }
}

type Props = {
  agents: Agent[];
  // What the address opens: "new", a task's id, or null for the list
  editing: string | null;
  onEditing: (id: string | null) => void;
  // What a new task's editor has filled in
  draft: TaskDraft | null;
  onOpenChat: (threadId: string) => void;
};

/** Your tasks: what runs when, its last result and its runs; and the editor */
export function TasksTab({ agents, editing, onEditing, draft, onOpenChat }: Props) {
  const [items, setItems] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const edited = editing && editing !== "new" ? items?.find(t => t.id === editing) : null;

  const reload = useCallback(() => {
    listTasks().then(tasks => (setItems(tasks), setError(null)), e => setError((e as Error).message));
  }, []);
  useEffect(reload, [reload]);

  // Follow runs as they go: often while one runs, now and then otherwise
  const active = items?.some(t => t.last_run && ["queued", "running"].includes(t.last_run.status));
  useEffect(() => {
    const timer = setInterval(reload, active ? 3000 : 30_000);
    return () => clearInterval(timer);
  }, [active, reload]);

  if (editing && editing !== "new" && !edited) {
    if (!items) return error ? <div className="problem">{error}</div> : null;
    return <NotFound text="There's no task here: it was deleted." back="Back to the tasks" onBack={() => onEditing(null)} />;
  }

  if (editing) {
    return (
      <TaskEditor
        key={editing}
        task={edited ?? null}
        draft={edited ? null : draft}
        agents={agents}
        onDone={saved => {
          onEditing(null);
          reload();
          if (saved) toast(`Saved “${saved.name}”: ${saved.when.toLowerCase()}`);
        }}
      />
    );
  }

  const run = (action: () => Promise<unknown>, failed: string) =>
    action().then(reload, e => toast(`${failed}: ${(e as Error).message}`));

  return (
    <>
      <Heartbeat agents={agents} onOpenChat={onOpenChat} />
      <div className="agents-intro">
        <p className="hint">
          A task runs a prompt on its own, now or on a schedule, with labb or one of your agents: “every weekday at 8, summarize
          the top news”. Each run writes to a chat, marked with a dot until you read it. Runs wait while someone's
          chatting, and stop after 15 minutes or 25 model calls.
        </p>
        <button type="button" className="primary" onClick={() => onEditing("new")}>
          New task
        </button>
      </div>
      {error && <div className="problem">{error}</div>}
      {items && items.length === 0 && <p className="hint">No tasks yet.</p>}
      <ul className="task-list">
        {items?.map(task => {
          const agent = agents.find(a => a.id === task.agent_id);
          const last = task.last_run;
          const busy = last && (last.status === "queued" || last.status === "running");
          return (
            <li key={task.id} className={task.enabled ? "task" : "task off"}>
              <div className="task-main">
                <span className={agent ? "agent-emoji small" : "agent-emoji small labb-mark"} aria-hidden>
                  {agent?.emoji ?? <Logo size={20} />}
                </span>
                <div className="task-text">
                  <b>{task.name}</b>
                  <span className="muted">
                    {task.when}
                    {agent ? ` · ${agent.name}` : task.agent_id ? " · agent deleted" : ""}
                    {task.mode === "same" ? " · one chat" : ""}
                    {task.goal ? " · until done" : ""}
                  </span>
                  <span className="task-status">
                    {task.enabled && task.next_run && <span>Next {moment(task.next_run)}</span>}
                    {!task.enabled && <span>{task.disabled_reason ?? "Off"}</span>}
                    {last && (
                      <span className={`run-status ${last.status}`} title={last.error ?? undefined}>
                        {STATUS[last.status]}
                        {last.finished_at && ` ${moment(last.finished_at)}`}
                        {last.error && `: ${last.error}`}
                        {last.goal && <GoalOutcome goal={last.goal} />}
                      </span>
                    )}
                  </span>
                </div>
                <label className="switch" title={task.enabled ? "Turn it off" : "Turn it on"}>
                  <input
                    type="checkbox"
                    checked={task.enabled}
                    aria-label={`${task.name} is on`}
                    onChange={e => run(() => updateTask(task.id, { enabled: e.target.checked }), "Couldn't change it")}
                  />
                  <span />
                </label>
              </div>
              <div className="agent-actions">
                {busy ? (
                  <button type="button" onClick={() => run(() => stopTaskRun(last!.id), "Couldn't stop it")}>
                    Stop
                  </button>
                ) : (
                  <button type="button" className="primary" onClick={() => run(() => runTask(task.id), "Couldn't run it")}>
                    Run now
                  </button>
                )}
                {last?.thread_id && (
                  <button type="button" onClick={() => onOpenChat(last.thread_id!)}>
                    Open result
                  </button>
                )}
                <button type="button" onClick={() => onEditing(task.id)}>
                  Edit
                </button>
                <button type="button" aria-expanded={open === task.id} onClick={() => setOpen(open === task.id ? null : task.id)}>
                  Runs
                </button>
                <button
                  type="button"
                  className="danger"
                  onClick={() => {
                    if (confirm(`Delete “${task.name}”? The chats it wrote stay.`)) run(() => deleteTask(task.id), "Couldn't delete it");
                  }}
                >
                  Delete
                </button>
              </div>
              {open === task.id && <Runs task={task} onOpenChat={onOpenChat} />}
            </li>
          );
        })}
      </ul>
    </>
  );
}

function Runs({ task, onOpenChat }: { task: Task; onOpenChat: (threadId: string) => void }) {
  const [runs, setRuns] = useState<TaskRun[] | null>(null);
  useEffect(() => {
    listTaskRuns(task.id).then(setRuns, () => setRuns([]));
    // Again when its last run changes
  }, [task.id, task.last_run?.id, task.last_run?.status]);

  if (!runs) return null;
  if (!runs.length) return <p className="hint runs-none">It hasn't run yet.</p>;
  return (
    <table className="settings-table runs">
      <thead>
        <tr>
          <th>When</th>
          <th>Result</th>
          <th className="num">Tokens</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {runs.map(run => (
          <tr key={run.id}>
            <td>
              {moment(run.started_at ?? run.queued_at)}
              {run.trigger === "manual" && <span className="muted"> · by you</span>}
            </td>
            <td>
              <span className={`run-status ${run.status}`}>{STATUS[run.status]}</span>
              {run.goal && <GoalOutcome goal={run.goal} />}
              {run.error && <span className="muted"> {run.error}</span>}
              {run.goal?.note && <div className="muted run-goal-note">{run.goal.note}</div>}
            </td>
            <td className="num">{run.output_tokens ? ((run.input_tokens ?? 0) + run.output_tokens).toLocaleString() : ""}</td>
            <td className="row-actions">
              {run.thread_id && (
                <button type="button" onClick={() => onOpenChat(run.thread_id!)}>
                  Open
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function defaultSchedule(): Schedule {
  return { kind: "weekdays", time: "08:00" };
}

// For the "once" field: an hour from now, local, as datetime-local wants it
function inAnHour() {
  const date = new Date(Date.now() + 3_600_000);
  date.setMinutes(0, 0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:00`;
}

function TaskEditor({ task, draft, agents, onDone }: {
  task: Task | null;
  draft: TaskDraft | null;
  agents: Agent[];
  onDone: (saved: Task | null) => void;
}) {
  const [name, setName] = useState(task?.name ?? draft?.name ?? "");
  const [prompt, setPrompt] = useState(task?.prompt ?? draft?.prompt ?? "");
  const limits = useLimits();
  const [agentId, setAgentId] = useState<string | null>(task?.agent_id ?? draft?.agent_id ?? null);
  const [schedule, setSchedule] = useState<Schedule>(task?.schedule ?? defaultSchedule());
  const [mode, setMode] = useState<Task["mode"]>(task?.mode ?? "new");
  const [goal, setGoal] = useState(task?.goal ?? false);
  const [notify, setNotify] = useState(typeof Notification !== "undefined" && Notification.permission === "granted");
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // A task keeps the time zone it was made in; a new one gets this browser's
  const tz = task?.tz ?? browserZone();

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setSaving(true);
    const fields: TaskFields = { name, prompt, agent_id: agentId, schedule, mode, goal };
    try {
      if (notify && typeof Notification !== "undefined" && Notification.permission === "default") await Notification.requestPermission();
      const saved = task ? await updateTask(task.id, fields) : await createTask({ ...fields, tz, enabled: true });
      onDone(saved);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="agent-form task-form" onSubmit={save}>
      <div className="editor-head">
        <button type="button" className="link" onClick={() => onDone(null)}>
          ← All tasks
        </button>
        <h3>{task ? `Edit “${task.name}”` : "New task"}</h3>
      </div>
      <label className="field">
        <span>What should it do?</span>
        <textarea
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          rows={5}
          maxLength={limits.task_prompt_chars}
          required
          autoFocus={!task}
          placeholder="e.g. Read the top 5 news stories and summarize each in two sentences, with links."
        />
      </label>
      <div className="field-row">
        <label className="field grow">
          <span>Name</span>
          <input value={name} onChange={e => setName(e.target.value)} maxLength={80} placeholder="The start of the prompt, if empty" />
        </label>
        <label className="field grow">
          <span>Who does it</span>
          <select value={agentId ?? ""} onChange={e => setAgentId(e.target.value || null)}>
            <option value="">● labb</option>
            {agents.map(a => (
              <option key={a.id} value={a.id}>
                {a.emoji} {a.name}
              </option>
            ))}
            {agentId && !agents.some(a => a.id === agentId) && <option value={agentId}>(a deleted agent)</option>}
          </select>
        </label>
      </div>

      <fieldset className="field">
        <legend>When</legend>
        <ScheduleFields schedule={schedule} tz={tz} onChange={setSchedule} onProblem={setProblem} />
      </fieldset>

      <fieldset className="field">
        <legend>Where the results go</legend>
        <label className="check">
          <input type="radio" name="mode" checked={mode === "new"} onChange={() => setMode("new")} />
          <span>A new chat for each run</span>
        </label>
        <label className="check">
          <input type="radio" name="mode" checked={mode === "same"} onChange={() => setMode("same")} />
          <span>
            Always the same chat <span className="muted">so it can see its earlier results</span>
          </span>
        </label>
      </fieldset>

      <label className="check">
        <input type="checkbox" checked={goal} onChange={e => setGoal(e.target.checked)} />
        <span>
          Keep going until done <span className="muted">it works until it has checked every part of the prompt, up to the run's model calls</span>
        </span>
      </label>

      {typeof Notification !== "undefined" && Notification.permission !== "denied" && (
        <label className="check">
          <input type="checkbox" checked={notify} onChange={e => setNotify(e.target.checked)} />
          <span>Notify me in this browser when a run finishes</span>
        </label>
      )}

      {error && <div className="problem">{error}</div>}
      <div className="editor-actions">
        <button type="submit" className="primary" disabled={saving || Boolean(problem)}>
          {saving ? "Saving…" : task ? "Save" : "Create"}
        </button>
        <button type="button" onClick={() => onDone(null)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** A schedule's settings, with it in words and its next times (or what's wrong with it) */
export function ScheduleFields({ schedule, tz, onChange, onProblem }: {
  schedule: Schedule;
  tz: string;
  onChange: (schedule: Schedule) => void;
  onProblem?: (problem: string | null) => void;
}) {
  const [preview, setPreview] = useState<{ when: string; next: string[] } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const setSchedule = onChange;

  // The schedule in words and its next times, as it's changed
  useEffect(() => {
    const timer = setTimeout(() => {
      previewSchedule(schedule, tz).then(
        result => (setPreview(result), setProblem(null), onProblem?.(null)),
        e => (setPreview(null), setProblem((e as Error).message), onProblem?.((e as Error).message)),
      );
    }, 250);
    return () => clearTimeout(timer);
  }, [JSON.stringify(schedule), tz]);

  const kind = schedule.kind;
  const time = "time" in schedule ? schedule.time : "08:00";
  const setKind = (next: Schedule["kind"]) => {
    if (next === "once") setSchedule({ kind: next, at: inAnHour() });
    else if (next === "minutes") setSchedule({ kind: next, every: 15 });
    else if (next === "hours") setSchedule({ kind: next, every: 1 });
    else if (next === "weekly") setSchedule({ kind: next, time, days: [0] });
    else if (next === "days") setSchedule({ kind: next, time, every: 2 });
    else if (next === "monthly") setSchedule({ kind: next, time, day: 1 });
    else setSchedule({ kind: next, time });
  };
  const toggleDay = (day: number) => {
    if (schedule.kind !== "weekly") return;
    const days = schedule.days.includes(day) ? schedule.days.filter(d => d !== day) : [...schedule.days, day].sort();
    setSchedule({ ...schedule, days });
  };

  return (
    <>
      <div className="schedule-row">
        <select value={kind} aria-label="How often" onChange={e => setKind(e.target.value as Schedule["kind"])}>
          <optgroup label="Over and over">
            <option value="minutes">Every few minutes</option>
            <option value="hours">Every few hours</option>
          </optgroup>
          <optgroup label="At a time of day">
            <option value="daily">Every day</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekly">On some days of the week</option>
            <option value="days">Every few days</option>
            <option value="monthly">Once a month</option>
          </optgroup>
          <optgroup label="Just once">
            <option value="once">Once</option>
          </optgroup>
        </select>
        {schedule.kind === "once" && (
          <input type="datetime-local" aria-label="At" value={schedule.at} onChange={e => setSchedule({ kind: "once", at: e.target.value })} />
        )}
        {(schedule.kind === "minutes" || schedule.kind === "hours" || schedule.kind === "days") && (
          <label className="inline">
            every
            <input
              type="number"
              min={1}
              max={EVERY_MAX[schedule.kind]}
              value={schedule.every}
              aria-label={`How many ${schedule.kind}`}
              onChange={e => setSchedule({ ...schedule, every: Math.max(1, Math.round(Number(e.target.value)) || 1) })}
            />
            {schedule.every === 1 ? schedule.kind.slice(0, -1) : schedule.kind}
          </label>
        )}
        {schedule.kind === "monthly" && (
          <label className="inline">
            on day
            <input
              type="number"
              min={1}
              max={31}
              value={schedule.day}
              aria-label="Day of the month"
              onChange={e => setSchedule({ ...schedule, day: Math.min(31, Math.max(1, Math.round(Number(e.target.value)) || 1)) })}
            />
          </label>
        )}
        {"time" in schedule && (
          <label className="inline">
            at
            <input type="time" value={schedule.time} aria-label="Time" onChange={e => setSchedule({ ...schedule, time: e.target.value })} />
          </label>
        )}
      </div>
      {schedule.kind === "weekly" && (
        <div className="day-picks" role="group" aria-label="Days">
          {DAYS.map((day, i) => (
            <button key={day} type="button" aria-pressed={schedule.days.includes(i)} onClick={() => toggleDay(i)}>
              {day}
            </button>
          ))}
        </div>
      )}
      <p className="schedule-preview">
        {problem ? (
          <span className="problem-text">{problem}</span>
        ) : preview ? (
          <>
            <b>{preview.when}</b>
            {preview.next.length > 0 && <> · next {preview.next.map(moment).join(", ")}</>}
            <span className="muted"> ({tz})</span>
          </>
        ) : (
          "…"
        )}
      </p>
    </>
  );
}
