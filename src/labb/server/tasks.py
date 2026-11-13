"""Tasks: an agent (or labb) doing a job on its own, now or on a schedule ("weekdays at
8:00, summarize the top news"), with the result waiting in a chat.

- `tasks` holds what to do (the prompt, the agent), when (a schedule in the owner's time
  zone, so 8:00 stays 8:00 across daylight saving time) and `next_run` in UTC. Each run is a
  row in `task_runs` and a normal chat turn through `run_reply`, in a new chat each time or
  always the same one; the chat is marked unread until its owner opens it.
- The scheduler runs inside the server: a thread that wakes every TICK_SECONDS and queues the
  due tasks (`due`), and TASK_WORKERS workers that run them (`execute`), one at a time by
  default since Ollama answers one request at a time. A task waits while someone's web
  reply is being written (`busy`, set by server/app.py).
- Nobody is watching a run, so it has limits (Settings → Limits): time, model calls, and only its
  agent's tools (the web chat has no files or shell anyway). After task_failures_to_pause
  failures in a row a task turns itself off, with the reason.
- After a restart, runs left queued or running count as failed, and a task that was due in
  the last MISSED_GRACE runs once; one due longer ago waits for its next time (`recover`).
"""

import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from labb import chats, db
from labb.chats import conn, lock
from labb.runner import run_reply
from labb.tools import goal as goals
from labb.server import agents
from labb.server import inbox
from labb.server import limits

log = logging.getLogger(__name__)

TASK_WORKERS = max(1, int(os.getenv("TASK_WORKERS", "1")))
TICK_SECONDS = 30
MAX_NAME = 80
MAX_MINUTES = 24 * 60
MAX_HOURS = 24 * 7
MAX_DAYS = 365
MISSED_GRACE = timedelta(hours=1)
# How long a due task waits for web replies to finish before it runs anyway
BUSY_WAIT_SECONDS = 10 * 60
KINDS = ("once", "minutes", "hours", "daily", "weekdays", "weekly", "days", "monthly")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tasks ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, prompt TEXT NOT NULL, agent_id TEXT, "
        "schedule TEXT NOT NULL, tz TEXT NOT NULL, mode TEXT NOT NULL, thread_id TEXT, enabled INTEGER NOT NULL, "
        "next_run TEXT, failures INTEGER NOT NULL DEFAULT 0, disabled_reason TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS tasks_by_user ON tasks (user_id)")
    # "Keep going until done": each run is a goal (tools/goal.py) with the run's model calls as its budget
    db.add_column(conn, "tasks", "goal", "INTEGER NOT NULL DEFAULT 0")
    # "task", or "heartbeat": someone's heartbeat (server/heartbeat.py), which the task pages leave out
    db.add_column(conn, "tasks", "kind", "TEXT NOT NULL DEFAULT 'task'")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS task_runs ("
        "id TEXT PRIMARY KEY, task_id TEXT NOT NULL, user_id TEXT NOT NULL, thread_id TEXT, status TEXT NOT NULL, "
        "error TEXT, trigger TEXT NOT NULL, queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, "
        "input_tokens INTEGER, output_tokens INTEGER" + (", seq BIGSERIAL)" if db.POSTGRES else ")")
    )
    conn.execute("CREATE INDEX IF NOT EXISTS task_runs_by_task ON task_runs (task_id, queued_at)")
    # How a run's goal ended ("Keep going until done"): its status, its note, and its model calls of its budget
    for column, declaration in (("goal_status", "TEXT"), ("goal_note", "TEXT"), ("goal_used", "INTEGER"), ("goal_budget", "INTEGER")):
        db.add_column(conn, "task_runs", column, declaration)

# Runs queued in the same second come in the order they were added: SQLite's rowid, a serial column in Postgres
NEWEST_RUNS = "queued_at DESC, " + ("seq DESC" if db.POSTGRES else "rowid DESC")

TASK_COLUMNS = ("id, user_id, name, prompt, agent_id, schedule, tz, mode, thread_id, enabled, next_run, "
                "failures, disabled_reason, created_at, updated_at, goal")
RUN_COLUMNS = ("id, task_id, user_id, thread_id, status, error, trigger, queued_at, started_at, finished_at, "
               "input_tokens, output_tokens, goal_status, goal_note, goal_used, goal_budget")

# Set by server/app.py: whether someone's web reply is being written
busy: Callable[[], bool] = lambda: False

_queue: queue.Queue[str] = queue.Queue()
_tick = threading.Event()
_started = False
# The stop flag of each run in progress
_active: dict[str, tuple[str | None, threading.Event]] = {}
_active_lock = threading.Lock()


class TaskError(ValueError):
    """A task that can't be saved, said for the person who's setting it up"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    """UTC, to the second, so the stored times compare as strings"""
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


# --- Schedules -------------------------------------------------------------------------


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise TaskError(f"Unknown time zone: {name}")


def _clock(text) -> tuple[int, int]:
    try:
        hour, minute = (int(part) for part in str(text).split(":"))
    except ValueError:
        raise TaskError("Times are like 08:00")
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise TaskError("Times are like 08:00")
    return hour, minute


def _whole(value, low: int, high: int, what: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise TaskError(f"Every how many {what}?")
    if not low <= number <= high:
        raise TaskError(f"Every {low} to {high:,} {what}")
    return number


def clean_schedule(schedule: dict, now: datetime, tz: str = "UTC") -> dict:
    """Check a schedule and keep only what its kind uses; raises TaskError

    - {"kind": "once", "at": "2026-10-25T08:00"}, in the task's time zone
    - {"kind": "minutes" or "hours", "every": 15}: every N, counted from when it was set (`start`, UTC)
    - {"kind": "daily" or "weekdays", "time": "08:00"}
    - {"kind": "weekly", "days": [0, 2, 4], "time": "08:00"}, Monday is 0
    - {"kind": "days", "every": 3, "time": "08:00"}: every N days from the day it was set (`start`, local)
    - {"kind": "monthly", "day": 1, "time": "08:00"}: day 29 to 31 is the last day in shorter months
    """
    kind = schedule.get("kind")
    if kind not in KINDS:
        raise TaskError(f"A schedule is one of: {', '.join(KINDS)}")
    if kind == "once":
        try:
            at = datetime.fromisoformat(str(schedule.get("at")))
        except ValueError:
            raise TaskError("When should it run? A date and time, like 2026-10-25T08:00")
        return {"kind": kind, "at": at.replace(tzinfo=None, second=0, microsecond=0).isoformat(timespec="minutes")}
    if kind in ("minutes", "hours"):
        every = _whole(schedule.get("every"), 1, MAX_MINUTES if kind == "minutes" else MAX_HOURS, kind)
        try:
            start = iso(parse(schedule["start"])) if schedule.get("start") else iso(now)
        except ValueError:
            start = iso(now)
        return {"kind": kind, "every": every, "start": start}
    hour, minute = _clock(schedule.get("time"))
    cleaned = {"kind": kind, "time": f"{hour:02d}:{minute:02d}"}
    if kind == "weekly":
        try:
            days = sorted({int(d) for d in schedule.get("days") or []})
        except (TypeError, ValueError):
            days = []
        if not days or not all(0 <= d <= 6 for d in days):
            raise TaskError("Pick at least one day")
        cleaned["days"] = days
    elif kind == "days":
        cleaned["every"] = _whole(schedule.get("every"), 1, MAX_DAYS, "days")
        try:
            cleaned["start"] = date.fromisoformat(str(schedule["start"])).isoformat()
        except (KeyError, ValueError):
            cleaned["start"] = now.astimezone(zone(tz)).date().isoformat()
    elif kind == "monthly":
        try:
            day = int(schedule.get("day"))
        except (TypeError, ValueError):
            day = 0
        if not 1 <= day <= 31:
            raise TaskError("Which day of the month? 1 to 31")
        cleaned["day"] = day
    return cleaned


def _local(day: date, hour: int, minute: int, place: ZoneInfo) -> datetime:
    # Built from the local date and time, so the UTC time moves with daylight saving time
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=place).astimezone(timezone.utc)


def _month_day(year: int, month: int, day: int) -> date:
    """That day of the month, or its last day if it's shorter"""
    following = date(year + month // 12, month % 12 + 1, 1)
    return date(year, month, min(day, (following - timedelta(days=1)).day))


def next_after(schedule: dict, tz: str, after: datetime) -> datetime | None:
    """The first time the schedule says to run after `after` (UTC); None when it won't again"""

    place = zone(tz)
    kind = schedule["kind"]
    if kind == "once":
        at = datetime.fromisoformat(schedule["at"]).replace(tzinfo=place)
        return at.astimezone(timezone.utc) if at > after else None
    if kind in ("minutes", "hours"):
        start, step = parse(schedule["start"]), timedelta(**{kind: schedule["every"]})
        if after < start:
            return start + step
        return start + step * ((after - start) // step + 1)
    hour, minute = _clock(schedule["time"])
    today = after.astimezone(place).date()
    if kind == "days":
        start, every = date.fromisoformat(schedule["start"]), schedule["every"]
        day = max(today, start)
        day += timedelta(days=-(day - start).days % every)
        while (moment := _local(day, hour, minute, place)) <= after:
            day += timedelta(days=every)
        return moment
    if kind == "monthly":
        for months in range(3):
            index = today.month - 1 + months
            moment = _local(_month_day(today.year + index // 12, index % 12 + 1, schedule["day"]), hour, minute, place)
            if moment > after:
                return moment
        return None
    days = {"daily": range(7), "weekdays": range(5)}.get(kind) or schedule["days"]
    for offset in range(8):
        day = today + timedelta(days=offset)
        if day.weekday() in days and (moment := _local(day, hour, minute, place)) > after:
            return moment
    return None


def _days(days: list[int]) -> str:
    names = [DAY_NAMES[d] for d in days]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def describe(schedule: dict) -> str:
    """The schedule in words: "Weekdays at 08:00" """

    kind = schedule["kind"]
    if kind == "once":
        at = datetime.fromisoformat(schedule["at"])
        return f"Once, {at:%a %-d %b %Y} at {at:%H:%M}"
    if kind in ("minutes", "hours", "days"):
        unit = kind[:-1]
        every = f"Every {unit}" if schedule["every"] == 1 else f"Every {schedule['every']} {kind}"
        return f"{every} at {schedule['time']}" if kind == "days" else every
    if kind == "daily":
        return f"Every day at {schedule['time']}"
    if kind == "weekdays":
        return f"Weekdays at {schedule['time']}"
    if kind == "monthly":
        last = " (or the last day)" if schedule["day"] > 28 else ""
        return f"Monthly on the {_ordinal(schedule['day'])}{last} at {schedule['time']}"
    return f"{_days(schedule['days'])} at {schedule['time']}"


def preview(schedule: dict, tz: str, count: int = 3, now: datetime | None = None) -> dict:
    """What the editor shows: the schedule in words and its next times"""

    now = now or utcnow()
    schedule = clean_schedule(schedule, now, tz)
    times, after = [], now
    while len(times) < count and (moment := next_after(schedule, tz, after)):
        times.append(iso(moment))
        after = moment
    return {"when": describe(schedule), "next": times}


# --- Tasks -----------------------------------------------------------------------------


def _task(row) -> dict:
    (id, user_id, name, prompt, agent_id, schedule, tz, mode, thread_id, enabled, next_run,
     failures, disabled_reason, created_at, updated_at, goal) = row
    schedule = json.loads(schedule)
    return {
        "id": id, "user_id": user_id, "name": name, "prompt": prompt, "agent_id": agent_id,
        "schedule": schedule, "when": describe(schedule), "tz": tz, "mode": mode, "thread_id": thread_id,
        "enabled": bool(enabled), "next_run": next_run, "failures": failures,
        "disabled_reason": disabled_reason, "created_at": created_at, "updated_at": updated_at, "goal": bool(goal),
    }


def _run(row) -> dict:
    (id, task_id, user_id, thread_id, status, error, trigger, queued_at, started_at, finished_at,
     input_tokens, output_tokens, goal_status, goal_note, goal_used, goal_budget) = row
    return {
        "id": id, "task_id": task_id, "user_id": user_id, "thread_id": thread_id, "status": status,
        "error": error, "trigger": trigger, "queued_at": queued_at, "started_at": started_at,
        "finished_at": finished_at, "input_tokens": input_tokens, "output_tokens": output_tokens,
        # None for a run without a goal; "active" if it ended with the goal unfinished
        "goal": {"status": goal_status, "note": goal_note, "used": goal_used, "budget": goal_budget} if goal_status else None,
    }


def _with_last_run(task: dict) -> dict:
    with lock:
        row = conn.execute(f"SELECT {RUN_COLUMNS} FROM task_runs WHERE task_id = ? ORDER BY {NEWEST_RUNS} LIMIT 1",
                           (task["id"],)).fetchone()
    return {**task, "last_run": _run(row) if row else None}


def get(user_id: str, task_id: str) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE id = ? AND user_id = ? AND kind = 'task'",
                           (task_id, user_id)).fetchone()
    return _with_last_run(_task(row)) if row else None


def list_for(user_id: str) -> list[dict]:
    """Your tasks, the next to run first, then the ones that are off"""

    with lock:
        rows = conn.execute(
            f"SELECT {TASK_COLUMNS} FROM tasks WHERE user_id = ? AND kind = 'task' "
            "ORDER BY enabled DESC, next_run IS NULL, next_run, created_at DESC", (user_id,)
        ).fetchall()
    return [_with_last_run(_task(row)) for row in rows]


def _clean(user_id: str, fields: dict, now: datetime, current: dict | None = None) -> dict:
    out = {}
    if "prompt" in fields:
        prompt = str(fields["prompt"] or "").strip()
        if not prompt:
            raise TaskError("What should it do? Write the prompt")
        if len(prompt) > (most := limits.get("task_prompt_chars")):
            raise TaskError(f"The prompt is over {most:,} characters")
        out["prompt"] = prompt
    if "name" in fields:
        name = " ".join(str(fields["name"] or "").split())
        if not name:
            prompt = out.get("prompt") or (current or {}).get("prompt", "")
            name = chats.make_title(prompt)
        out["name"] = name[:MAX_NAME]
    if "agent_id" in fields:
        agent_id = fields["agent_id"] or None
        if agent_id and agents.visible(agent_id, user_id) is None:
            raise TaskError("No such agent")
        out["agent_id"] = agent_id
    if "tz" in fields:
        zone(str(fields["tz"]))
        out["tz"] = str(fields["tz"])
    if "schedule" in fields:
        tz = out.get("tz") or (current or {}).get("tz") or "UTC"
        out["schedule"] = clean_schedule(dict(fields["schedule"] or {}), now, tz)
    if "mode" in fields:
        if fields["mode"] not in ("new", "same"):
            raise TaskError('Each run goes to a "new" chat or the "same" one')
        out["mode"] = fields["mode"]
    if "enabled" in fields:
        out["enabled"] = bool(fields["enabled"])
    if "goal" in fields:
        out["goal"] = bool(fields["goal"])
    return out


def create(user_id: str, fields: dict, now: datetime | None = None) -> dict:
    now = now or utcnow()
    task = {"name": "", "mode": "new", "enabled": True, "agent_id": None, "tz": "UTC", "goal": False, **fields}
    if "prompt" not in task or "schedule" not in task:
        raise TaskError("A task needs a prompt and a schedule")
    task = _clean(user_id, task, now)
    next_run = next_after(task["schedule"], task["tz"], now)
    if next_run is None:
        raise TaskError("That time has passed")
    # Read before taking the lock: the first read of the limits takes it too
    most = limits.get("tasks_per_person")
    with lock, conn:
        if conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND kind = 'task'", (user_id,)).fetchone()[0] >= most:
            raise TaskError(f"You have {most} tasks already; delete one first")
        task_id, at = uuid.uuid4().hex[:12], iso(now)
        conn.execute(
            f"INSERT INTO tasks ({TASK_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, NULL, ?, ?, ?)",
            (task_id, user_id, task["name"], task["prompt"], task["agent_id"], json.dumps(task["schedule"]), task["tz"],
             task["mode"], int(task["enabled"]), iso(next_run) if task["enabled"] else None, at, at, int(task["goal"])),
        )
    return get(user_id, task_id)


def update(user_id: str, task_id: str, fields: dict, now: datetime | None = None) -> dict | None:
    """Change your task; turning it on again forgets its failures. None if there's no such task."""

    now = now or utcnow()
    current = get(user_id, task_id)
    if current is None:
        return None
    change = _clean(user_id, fields, now, current)
    merged = {**current, **change}
    columns = {k: v for k, v in change.items()}
    if "schedule" in columns:
        columns["schedule"] = json.dumps(columns["schedule"])
    if "enabled" in columns:
        columns["enabled"] = int(columns["enabled"])
    if "goal" in columns:
        columns["goal"] = int(columns["goal"])
    if merged["enabled"] and {"schedule", "tz", "enabled"} & change.keys():
        next_run = next_after(merged["schedule"], merged["tz"], now)
        if next_run is None:
            raise TaskError("That time has passed")
        columns["next_run"] = iso(next_run)
    if not merged["enabled"]:
        columns["next_run"] = None
    if change.get("enabled") and not current["enabled"]:
        columns["failures"], columns["disabled_reason"] = 0, None
    if change.get("mode") == "new":
        columns["thread_id"] = None
    columns["updated_at"] = iso(now)
    with lock, conn:
        conn.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in columns)} WHERE id = ? AND user_id = ?",
                     (*columns.values(), task_id, user_id))
    return get(user_id, task_id)


def delete(user_id: str, task_id: str) -> bool:
    """Delete your task and its runs; the chats it wrote stay"""

    with lock, conn:
        deleted = conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)).rowcount
        running = [row[0] for row in conn.execute("SELECT id FROM task_runs WHERE task_id = ? AND status = 'running'", (task_id,))]
        if deleted:
            conn.execute("DELETE FROM task_runs WHERE task_id = ?", (task_id,))
    for run_id in running if deleted else []:
        stop_run(run_id)
    return bool(deleted)


def run_seconds(minutes: int) -> float:
    """How long a run may take (the tests shorten it)"""
    return minutes * 60


def runs_for(user_id: str, task_id: str, limit: int | None = None) -> list[dict]:
    limit = limit or limits.get("task_runs_kept")
    with lock:
        rows = conn.execute(
            f"SELECT {RUN_COLUMNS} FROM task_runs WHERE task_id = ? AND user_id = ? ORDER BY {NEWEST_RUNS} LIMIT ?",
            (task_id, user_id, limit),
        ).fetchall()
    return [_run(row) for row in rows]


def get_run(run_id: str) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {RUN_COLUMNS} FROM task_runs WHERE id = ?", (run_id,)).fetchone()
    return _run(row) if row else None



# --- Running ---------------------------------------------------------------------------


def _queue_locked(task_id: str, user_id: str, trigger: str, now: datetime) -> str:
    run_id = uuid.uuid4().hex[:12]
    conn.execute(
        f"INSERT INTO task_runs ({RUN_COLUMNS}) VALUES (?, ?, ?, NULL, 'queued', NULL, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
        (run_id, task_id, user_id, trigger, iso(now)),
    )
    return run_id


def _pending_locked(task_id: str) -> bool:
    return conn.execute("SELECT 1 FROM task_runs WHERE task_id = ? AND status IN ('queued', 'running')",
                        (task_id,)).fetchone() is not None


def run_now(user_id: str, task_id: str, now: datetime | None = None) -> dict | None:
    """Queue a run of your task now, whatever its schedule; None if there's no such task"""

    now = now or utcnow()
    with lock, conn:
        if not conn.execute("SELECT 1 FROM tasks WHERE id = ? AND user_id = ? AND kind = 'task'", (task_id, user_id)).fetchone():
            return None
        if _pending_locked(task_id):
            raise TaskError("It's running already")
        run_id = _queue_locked(task_id, user_id, "manual", now)
    _queue.put(run_id)
    return get_run(run_id)


def due(now: datetime) -> list[str]:
    """Queue the tasks whose time has come (once each, also if one is still running) and move
    their next run on; the queued runs' ids"""

    from labb.server import heartbeat
    queued = []
    with lock, conn:
        rows = conn.execute(f"SELECT {TASK_COLUMNS}, kind FROM tasks WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?",
                            (iso(now),)).fetchall()
        for row in rows:
            task, kind = _task(row[:-1]), row[-1]
            if kind == "heartbeat":
                # A beat: a run for each open to-do it takes this time, none (and no cost) when there are none
                if not _pending_locked(task["id"]):
                    for _ in range(heartbeat.to_take_locked(task["user_id"])):
                        queued.append(_queue_locked(task["id"], task["user_id"], "heartbeat", now))
            elif not _pending_locked(task["id"]):
                queued.append(_queue_locked(task["id"], task["user_id"], "schedule", now))
            next_run = next_after(task["schedule"], task["tz"], now)
            conn.execute("UPDATE tasks SET next_run = ?, enabled = ? WHERE id = ?",
                         (iso(next_run) if next_run else None, int(next_run is not None), task["id"]))
    return queued


def recover(now: datetime) -> int:
    """At start: runs a restart cut off failed, and tasks missed by more than MISSED_GRACE wait
    for their next time (the ones missed by less run at the first tick). How many runs failed."""

    with lock, conn:
        failed = conn.execute(
            "UPDATE task_runs SET status = 'failed', error = 'The server restarted while it ran', finished_at = ? "
            "WHERE status IN ('queued', 'running')", (iso(now),)
        ).rowcount
        rows = conn.execute(f"SELECT {TASK_COLUMNS} FROM tasks WHERE enabled = 1 AND next_run < ?",
                            (iso(now - MISSED_GRACE),)).fetchall()
        for row in rows:
            task = _task(row)
            next_run = next_after(task["schedule"], task["tz"], now)
            conn.execute("UPDATE tasks SET next_run = ?, enabled = ? WHERE id = ?",
                         (iso(next_run) if next_run else None, int(next_run is not None), task["id"]))
    # To-dos a restart cut off go back on the list, to be taken at the next beat
    from labb.server import heartbeat
    heartbeat.reopen_cut_off()
    return failed


def _active_items() -> list[tuple[str, tuple[str | None, threading.Event]]]:
    with _active_lock:
        return list(_active.items())


def stop_run(run_id: str) -> bool:
    """Stop a run: a queued one doesn't start, a running one stops at its next step"""

    with lock, conn:
        if conn.execute("UPDATE task_runs SET status = 'stopped', finished_at = ? WHERE id = ? AND status = 'queued'",
                        (iso(utcnow()), run_id)).rowcount:
            return True
    with _active_lock:
        active = _active.get(run_id)
    if active:
        active[1].set()
        return True
    return False


def stop_thread(thread_id: str) -> bool:
    """Stop the run writing in a chat (its Stop button)"""
    for run_id, (thread, _) in _active_items():
        if thread == thread_id:
            return stop_run(run_id)
    return False


def _finish(run_id: str, task: dict, status: str, error: str | None, stats: dict, now: datetime,
            goal: dict | None = None) -> str | None:
    """Save how a run ended; why the task was turned off, if this failure turned it off"""

    pause_after, kept = limits.get("task_failures_to_pause"), limits.get("task_runs_kept")
    goal, paused = goal or {}, None
    with lock, conn:
        conn.execute(
            "UPDATE task_runs SET status = ?, error = ?, finished_at = ?, input_tokens = ?, output_tokens = ?, "
            "goal_status = ?, goal_note = ?, goal_used = ?, goal_budget = ? WHERE id = ?",
            (status, error, iso(now), stats.get("input_tokens"), stats.get("output_tokens"),
             goal.get("status"), goal.get("note"), goal.get("used"), goal.get("budget"), run_id),
        )
        if status == "failed":
            failures = task["failures"] + 1
            if failures >= pause_after or error == AGENT_GONE:
                reason = AGENT_GONE if error == AGENT_GONE else f"Failed {failures} times in a row: {error}"
                conn.execute("UPDATE tasks SET failures = ?, enabled = 0, next_run = NULL, disabled_reason = ? WHERE id = ?",
                             (failures, reason, task["id"]))
                paused = reason
            else:
                conn.execute("UPDATE tasks SET failures = ? WHERE id = ?", (failures, task["id"]))
        elif status == "done":
            conn.execute("UPDATE tasks SET failures = 0 WHERE id = ?", (task["id"],))
        # Only the latest runs are kept
        conn.execute(
            "DELETE FROM task_runs WHERE task_id = ? AND id NOT IN "
            f"(SELECT id FROM task_runs WHERE task_id = ? ORDER BY {NEWEST_RUNS} LIMIT ?)",
            (task["id"], task["id"], kept),
        )
    return paused


def _tell(task: dict, status: str, error: str | None, paused: str | None, thread_id: str | None, todo: dict | None):
    """The inbox's word on a run that ended (inbox.py): its result, why it failed, and that the
    task was turned off. A run someone stopped says nothing; they know."""

    chat = f"/chat/{thread_id}" if thread_id else None
    if todo:
        what = "To-do"
        key = f"todo:{todo['id']}"
    else:
        what = "Task"
        key = f"task:{task['id']}"
    if status == "done":
        inbox.add(task["user_id"], "task", f"{task['name']}", f"{what} finished: the result is in its chat.",
                  link=chat, key=f"{key}:done", level="success")
    elif status == "failed":
        inbox.add(task["user_id"], "task", f"{what} failed: {task['name']}", error or "", link=chat or "/tasks",
                  key=f"{key}:failed", level="error")
    if paused:
        # A heartbeat isn't on the tasks list: its page is the Tasks page's top
        title, link = ("Heartbeat paused", "/tasks") if todo else (f"Task paused: {task['name']}", f"/tasks/{task['id']}")
        inbox.add(task["user_id"], "task", title, paused, link=link, key=f"task:{task['id']}:paused", level="warning")


AGENT_GONE = "Its agent was deleted, or isn't shared any more"


def _thread_for(task: dict, agent_id: str | None, now: datetime) -> str:
    """The chat this run writes in: a new one titled with the task and the time, or the task's own"""

    if task["mode"] == "same" and task["thread_id"] and chats.owner(task["thread_id"])[0]:
        chats.touch(task["thread_id"], task["name"], user_id=task["user_id"], agent_id=agent_id)
        return task["thread_id"]
    thread_id = f"task-{uuid.uuid4()}"
    local = now.astimezone(zone(task["tz"]))
    # A to-do's chat is called after it (heartbeat.py); a task's new chats get the time too
    title = task["name"] if task["mode"] == "same" or task.get("plain_title") else f"{task['name']} · {local:%a %-d %b, %H:%M}"
    chats.touch(thread_id, title, user_id=task["user_id"], agent_id=agent_id)
    if task["mode"] == "same":
        with lock, conn:
            conn.execute("UPDATE tasks SET thread_id = ? WHERE id = ?", (thread_id, task["id"]))
    return thread_id


def execute(run_id: str, now: Callable[[], datetime] = utcnow) -> dict | None:
    """Run a queued run to its end; the run as it ended (None if it was stopped or deleted before)"""

    with lock, conn:
        claimed = conn.execute("UPDATE task_runs SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'",
                               (iso(now()), run_id)).rowcount
    run = get_run(run_id)
    if not claimed or run is None:
        return None
    with lock:
        row = conn.execute(f"SELECT {TASK_COLUMNS}, kind FROM tasks WHERE id = ?", (run["task_id"],)).fetchone()
    if row is None:
        return None
    task, todo = _task(row[:-1]), None
    if row[-1] == "heartbeat":
        # Its prompt is the oldest open to-do, taken for this run; none left (another beat took it): no run
        from labb.server import heartbeat
        todo = heartbeat.claim(task["user_id"], run_id)
        if todo is None:
            with lock, conn:
                conn.execute("DELETE FROM task_runs WHERE id = ?", (run_id,))
            return None
        task = {**task, "prompt": todo["text"], "name": heartbeat.title_of(todo["text"]), "plain_title": True}

    agent = agents.visible(task["agent_id"], task["user_id"]) if task["agent_id"] else None
    if task["agent_id"] and agent is None:
        paused = _finish(run_id, task, "failed", AGENT_GONE, {}, now())
        if todo:
            heartbeat.finished(todo["id"], "failed", AGENT_GONE, None)
        _tell(task, "failed", AGENT_GONE, paused, None, todo)
        return get_run(run_id)

    thread_id = _thread_for(task, task["agent_id"], now())
    with lock, conn:
        conn.execute("UPDATE task_runs SET thread_id = ? WHERE id = ?", (thread_id, run_id))
    configurable = {"user_id": task["user_id"], "task": {"id": task["id"], "name": task["name"]}}
    if agent:
        configurable["agent"] = agents.for_run(agent)
        if agent["model"]:
            configurable["model"] = agent["model"]

    stop = threading.Event()
    timed_out = threading.Event()
    with _active_lock:
        _active[run_id] = (thread_id, stop)
    minutes, model_calls = limits.get("task_minutes"), limits.get("task_model_calls")
    if task["goal"]:
        # Keeps going (continuation.py) until the model shows it's done, or the run's model calls run
        # out: one before the limit, so the wrap-up the budget asks for still fits
        goals.start({"configurable": {"thread_id": thread_id}}, task["prompt"], budget=max(model_calls - 1, 1) if model_calls else None)
    timer = threading.Timer(run_seconds(minutes), lambda: (timed_out.set(), stop.set()))
    timer.daemon = True
    timer.start()
    events = []
    try:
        run_reply(thread_id, task["prompt"], stop, events.append, configurable=configurable, name=False,
                  recursion_limit=limits.recursion(model_calls), source="task")
    except Exception as e:
        log.exception("Task run %s failed", run_id)
        events.append({"type": "error", "message": str(e)})
    finally:
        timer.cancel()
        with _active_lock:
            _active.pop(run_id, None)

    end = next((e for e in reversed(events) if e["type"] in ("done", "stopped")), {})
    error = next((e["message"] for e in events if e["type"] == "error"), None)
    if error and "recursion limit" in error.lower():
        error = f"Stopped after {model_calls} model calls"
    if timed_out.is_set():
        status, error = "failed", f"Took longer than {minutes} minutes"
    elif error:
        status = "failed"
    elif end.get("type") == "stopped":
        status = "stopped"
    else:
        status = "done"
    outcome = goals.get({"configurable": {"thread_id": thread_id}}) if task["goal"] else None
    paused = _finish(run_id, task, status, error, end.get("stats") or {}, now(), outcome)
    if todo:
        heartbeat.finished(todo["id"], status, error, thread_id)
    chats.set_unread(thread_id, True)
    _tell(task, status, error, paused, thread_id, todo)
    return get_run(run_id)


def wait_until_quiet(step: float = 2) -> float:
    """Someone's chatting: their reply comes first (for at most BUSY_WAIT_SECONDS). How long it waited."""

    waited = 0.0
    while busy() and waited < BUSY_WAIT_SECONDS:
        time.sleep(step)
        waited += step
    return waited


def _work():
    while True:
        run_id = _queue.get()
        try:
            wait_until_quiet()
            execute(run_id)
        except Exception:
            log.exception("Task run %s failed", run_id)


def _schedule():
    while True:
        try:
            for run_id in due(utcnow()):
                _queue.put(run_id)
        except Exception:
            log.exception("Couldn't queue the due tasks")
        _tick.wait(TICK_SECONDS)
        _tick.clear()


def start():
    """Start the scheduler and its workers (server/app.py, once the server is up)"""

    global _started
    if _started:
        return
    _started = True
    recover(utcnow())
    for _ in range(TASK_WORKERS):
        threading.Thread(target=_work, daemon=True, name="task-worker").start()
    threading.Thread(target=_schedule, daemon=True, name="task-scheduler").start()
