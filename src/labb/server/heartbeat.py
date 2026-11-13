"""The heartbeat: every so often, labb looks at your to-do list and works through it on its own.

Off until you turn it on (Tasks → Heartbeat). You leave to-dos, things to do or find out, and
at each beat it takes the oldest open ones (`per_beat` at most) and runs each as a task run of
its own, in a new chat marked unread, with your agent if you picked one; the to-do then says
done or failed, with its chat. A beat with nothing open does nothing, and costs nothing.

It's built on tasks (server/tasks.py): a hidden task of kind "heartbeat" holds when (every N
minutes), whether it's on, and the agent; the task pages leave it out. Its runs are task runs, so
they have the tasks' limits (time, model calls), can be stopped, wait while someone's chatting,
and after task_failures_to_pause failures in a row the heartbeat turns itself off and says why.
`to_take_locked` is how many runs a beat queues; `claim` gives each run its to-do."""

import json
import uuid
from datetime import datetime

from labb.chats import conn, lock
from labb.server import agents, limits, tasks

MAX_TEXT = 4_000
# Done and failed to-dos kept per person, newest first
DONE_KEPT = 50
TITLE_CHARS = 60

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS heartbeats (user_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, per_beat INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS heartbeat_todos ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, run_id TEXT, "
        "thread_id TEXT, error TEXT, created_at TEXT NOT NULL, finished_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS heartbeat_todos_by_user ON heartbeat_todos (user_id, status, created_at)")

TODO_COLUMNS = "id, text, status, run_id, thread_id, error, created_at, finished_at"


def _stamp(moment: datetime | None = None) -> str:
    """When a to-do was added, to the microsecond: the order they're taken in, also for several added at once"""
    return (moment or tasks.utcnow()).isoformat(timespec="microseconds")


class HeartbeatError(ValueError):
    """Something that can't be saved, said for the person"""


def title_of(text: str) -> str:
    """A to-do's chat's title: its first line, cut short"""
    line = " ".join(text.strip().splitlines()[0].split()) if text.strip() else "To-do"
    return line if len(line) <= TITLE_CHARS else line[:TITLE_CHARS - 1].rstrip() + "…"


def _todo(row) -> dict:
    return dict(zip(("id", "text", "status", "run_id", "thread_id", "error", "created_at", "finished_at"), row))


# --- Settings ----------------------------------------------------------------------------------

def _row(user_id: str) -> tuple[str, int] | None:
    with lock:
        return conn.execute("SELECT task_id, per_beat FROM heartbeats WHERE user_id = ?", (user_id,)).fetchone()


def _task_row(task_id: str):
    with lock:
        return conn.execute(f"SELECT {tasks.TASK_COLUMNS} FROM tasks WHERE id = ? AND kind = 'heartbeat'", (task_id,)).fetchone()


def settings(user_id: str) -> dict:
    """Your heartbeat: on or not, how often, with which agent, how many to-dos a beat, when the next is"""

    found = _row(user_id)
    task = tasks._task(row) if found and (row := _task_row(found[0])) else None
    with lock:
        open_count = conn.execute("SELECT COUNT(*) FROM heartbeat_todos WHERE user_id = ? AND status = 'open'",
                                  (user_id,)).fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM heartbeat_todos WHERE user_id = ? AND status = 'running'",
                               (user_id,)).fetchone()[0]
    return {
        "enabled": bool(task and task["enabled"]),
        "every_minutes": task["schedule"]["every"] if task else 30,
        "agent_id": task["agent_id"] if task else None,
        "per_beat": found[1] if found else 1,
        "next_beat": task["next_run"] if task and task["enabled"] else None,
        "disabled_reason": task["disabled_reason"] if task else None,
        "open": open_count, "running": running,
        "min_minutes": limits.get("heartbeat_min_minutes"),
    }


def save(user_id: str, fields: dict, now: datetime | None = None) -> dict:
    """Change your heartbeat (any of enabled, every_minutes, agent_id, per_beat); made the first time"""

    now = now or tasks.utcnow()
    current = settings(user_id)
    merged = {**current, **{k: v for k, v in fields.items() if k in ("enabled", "every_minutes", "agent_id", "per_beat")}}
    least = limits.get("heartbeat_min_minutes")
    every, per_beat = merged["every_minutes"], merged["per_beat"]
    if not isinstance(every, int) or isinstance(every, bool) or not least <= every <= tasks.MAX_MINUTES:
        raise HeartbeatError(f"It beats every {least} to {tasks.MAX_MINUTES:,} minutes")
    if not isinstance(per_beat, int) or isinstance(per_beat, bool) or not 1 <= per_beat <= 10:
        raise HeartbeatError("It takes 1 to 10 to-dos a beat")
    if merged["agent_id"] and agents.visible(merged["agent_id"], user_id) is None:
        raise HeartbeatError("There's no such agent")
    schedule = tasks.clean_schedule({"kind": "minutes", "every": every}, now)
    enabled = bool(merged["enabled"])
    next_run = tasks.iso(tasks.next_after(schedule, "UTC", now)) if enabled else None
    found = _row(user_id)
    with lock, conn:
        if found is None:
            task_id = uuid.uuid4().hex[:12]
            conn.execute(
                f"INSERT INTO tasks ({tasks.TASK_COLUMNS}, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, 0, 'heartbeat')",
                (task_id, user_id, "Heartbeat", "", merged["agent_id"], json.dumps(schedule), "UTC", "new", None,
                 int(enabled), next_run, tasks.iso(now), tasks.iso(now)),
            )
            conn.execute("INSERT INTO heartbeats (user_id, task_id, per_beat) VALUES (?, ?, ?)", (user_id, task_id, per_beat))
        else:
            # Turning it on again forgets the failures that turned it off
            reset = ", failures = 0, disabled_reason = NULL" if enabled and not current["enabled"] else ""
            conn.execute(f"UPDATE tasks SET agent_id = ?, schedule = ?, enabled = ?, next_run = ?, updated_at = ?{reset} WHERE id = ?",
                         (merged["agent_id"], json.dumps(schedule), int(enabled), next_run, tasks.iso(now), found[0]))
            conn.execute("UPDATE heartbeats SET per_beat = ? WHERE user_id = ?", (per_beat, user_id))
    return settings(user_id)


def beat_now(user_id: str, now: datetime | None = None) -> dict:
    """A beat now, whatever the time: its runs are queued (none when there's nothing open)"""

    now = now or tasks.utcnow()
    found = _row(user_id)
    if found is None:
        raise HeartbeatError("Turn the heartbeat on first")
    queued = []
    with lock, conn:
        if tasks._pending_locked(found[0]):
            raise HeartbeatError("It's working through to-dos already")
        for _ in range(to_take_locked(user_id)):
            queued.append(tasks._queue_locked(found[0], user_id, "manual", now))
    for run_id in queued:
        tasks._queue.put(run_id)
    return {"queued": len(queued)}


# --- To-dos --------------------------------------------------------------------------------------

def todos(user_id: str) -> list[dict]:
    """Open and running ones first, oldest first (the order they're taken in); then the latest done"""
    with lock:
        waiting = conn.execute(f"SELECT {TODO_COLUMNS} FROM heartbeat_todos WHERE user_id = ? AND status IN ('open', 'running') "
                               "ORDER BY created_at, id", (user_id,)).fetchall()
        done = conn.execute(f"SELECT {TODO_COLUMNS} FROM heartbeat_todos WHERE user_id = ? AND status NOT IN ('open', 'running') "
                            "ORDER BY finished_at DESC LIMIT ?", (user_id, DONE_KEPT)).fetchall()
    return [_todo(row) for row in waiting + done]


def add(user_id: str, text: str, now: datetime | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        raise HeartbeatError("Say what to do")
    if len(text) > MAX_TEXT:
        raise HeartbeatError(f"A to-do is at most {MAX_TEXT:,} characters")
    most = limits.get("heartbeat_todos_per_person")
    todo_id = uuid.uuid4().hex[:12]
    with lock, conn:
        if conn.execute("SELECT COUNT(*) FROM heartbeat_todos WHERE user_id = ? AND status = 'open'", (user_id,)).fetchone()[0] >= most:
            raise HeartbeatError(f"You have {most} open to-dos already")
        conn.execute("INSERT INTO heartbeat_todos (id, user_id, text, status, created_at) VALUES (?, ?, ?, 'open', ?)",
                     (todo_id, user_id, text, _stamp(now)))
    return get(user_id, todo_id)


def get(user_id: str, todo_id: str) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {TODO_COLUMNS} FROM heartbeat_todos WHERE id = ? AND user_id = ?", (todo_id, user_id)).fetchone()
    return _todo(row) if row else None


def change(user_id: str, todo_id: str, text: str | None = None, reopen: bool = False) -> dict | None:
    """Change an open to-do's text, or put a done or failed one back on the list"""

    found = get(user_id, todo_id)
    if found is None:
        return None
    if found["status"] == "running":
        raise HeartbeatError("It's being worked on: stop its run first")
    with lock, conn:
        if text is not None:
            text = text.strip()
            if not text or len(text) > MAX_TEXT:
                raise HeartbeatError(f"A to-do is 1 to {MAX_TEXT:,} characters")
            conn.execute("UPDATE heartbeat_todos SET text = ? WHERE id = ?", (text, todo_id))
        if reopen:
            conn.execute("UPDATE heartbeat_todos SET status = 'open', run_id = NULL, error = NULL, finished_at = NULL, "
                         "created_at = ? WHERE id = ?", (_stamp(), todo_id))
    return get(user_id, todo_id)


def delete(user_id: str, todo_id: str) -> bool:
    """Forget a to-do (its chat, if it ran, stays); one being worked on is stopped"""
    found = get(user_id, todo_id)
    if found is None:
        return False
    if found["status"] == "running" and found["run_id"]:
        tasks.stop_run(found["run_id"])
    with lock, conn:
        conn.execute("DELETE FROM heartbeat_todos WHERE id = ? AND user_id = ?", (todo_id, user_id))
    return True


def clear_done(user_id: str) -> int:
    with lock, conn:
        return conn.execute("DELETE FROM heartbeat_todos WHERE user_id = ? AND status NOT IN ('open', 'running')",
                            (user_id,)).rowcount


# --- For the task runs ----------------------------------------------------------------------------

def to_take_locked(user_id: str) -> int:
    """How many runs a beat queues: its open to-dos, per_beat at most (with the lock held)"""
    per_beat = conn.execute("SELECT per_beat FROM heartbeats WHERE user_id = ?", (user_id,)).fetchone()
    waiting = conn.execute("SELECT COUNT(*) FROM heartbeat_todos WHERE user_id = ? AND status = 'open'", (user_id,)).fetchone()[0]
    return min(waiting, per_beat[0] if per_beat else 1)


def claim(user_id: str, run_id: str) -> dict | None:
    """The oldest open to-do, now this run's; None when there's none left"""
    with lock, conn:
        row = conn.execute(f"SELECT {TODO_COLUMNS} FROM heartbeat_todos WHERE user_id = ? AND status = 'open' "
                           "ORDER BY created_at, id LIMIT 1", (user_id,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE heartbeat_todos SET status = 'running', run_id = ? WHERE id = ?", (run_id, row[0]))
    return {**_todo(row), "status": "running", "run_id": run_id}


def finished(todo_id: str, status: str, error: str | None, thread_id: str | None):
    """Its run ended: done, or failed with why (a stopped one counts as failed: it didn't get done)"""
    outcome = "done" if status == "done" else "failed"
    why = error or ("Stopped" if status == "stopped" else None)
    with lock, conn:
        conn.execute("UPDATE heartbeat_todos SET status = ?, error = ?, thread_id = COALESCE(?, thread_id), finished_at = ? "
                     "WHERE id = ?", (outcome, why if outcome == "failed" else None, thread_id, tasks.iso(tasks.utcnow()), todo_id))


def reopen_cut_off():
    """At start: to-dos a restart cut off go back on the list"""
    with lock, conn:
        conn.execute("UPDATE heartbeat_todos SET status = 'open', run_id = NULL WHERE status = 'running'")
