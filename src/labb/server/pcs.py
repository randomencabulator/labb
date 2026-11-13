"""PCs that are connected with `labb connect`, and the runs started on them from here.

A session (`pc_sessions`) is a chat that runs on a PC, in one of its folders; its thread is `cli-…`, and
the PC sends the conversation with `/sync` like any terminal session. Each reply is a run. A PC holds a
request open (`Line`), which brings it messages, answers and stops; it posts each run's events back, and
they're kept in `pc_run_events`, so a reply outlives the page that started it and can be followed from any
tab. Who's connected is in memory, like `app.runs`: the server is one process.

Everything is per user: a PC belongs to whoever's key connected it, and every lookup here says
whose. What a PC may do is decided on the PC (cli/connect.py); the server only asks."""

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

from labb import db
from labb.chats import conn, lock

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 100_000
MAX_FOLDERS = 50
MAX_RESULT_CHARS = 50_000
MAX_EVENTS_PER_CALL = 500
# A job the PC didn't pick up by then isn't started later, when you've forgotten about it
JOB_MINUTES = 5
# Runs kept per person, the newest
KEEP_RUNS = 100
FINISHED = ("done", "failed", "stopped", "lost")

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pcs ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, folders TEXT NOT NULL, version TEXT, "
        "allow_all INTEGER NOT NULL DEFAULT 0, last_seen TEXT NOT NULL, UNIQUE (user_id, name))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pc_runs ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, pc_id TEXT NOT NULL, folder TEXT NOT NULL, message TEXT NOT NULL, "
        "questions INTEGER NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL, waiting TEXT NOT NULL DEFAULT '[]', "
        "last_seq INTEGER NOT NULL DEFAULT 0, result TEXT NOT NULL DEFAULT '', error TEXT, "
        "created_at TEXT NOT NULL, finished_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pc_sessions ("
        "thread_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, pc_id TEXT NOT NULL, folder TEXT NOT NULL, title TEXT NOT NULL, "
        "source TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS pc_sessions_by_user ON pc_sessions (user_id)")
    # Which session a run is a reply in; what to send the PC, until it has it; and which turn of the chat it is
    db.add_column(conn, "pc_runs", "thread_id", "TEXT")
    db.add_column(conn, "pc_runs", "job", "TEXT")
    db.add_column(conn, "pc_runs", "turn", "INTEGER NOT NULL DEFAULT 0")
    db.add_column(conn, "pcs", "models", "TEXT NOT NULL DEFAULT '[]'")
    conn.execute("CREATE INDEX IF NOT EXISTS pc_runs_by_thread ON pc_runs (thread_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS pc_runs_by_user ON pc_runs (user_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS pc_runs_by_pc ON pc_runs (pc_id, status)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pc_run_events ("
        "run_id TEXT NOT NULL, seq INTEGER NOT NULL, event TEXT NOT NULL, PRIMARY KEY (run_id, seq))"
    )


class PcError(Exception):
    """Something that can't be done, in words for the person (or the model)"""


class PcConflict(PcError):
    """The PC refused a change of what changed since it was read"""


class Line:
    """The open request to one PC"""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue()

    def send(self, message: dict):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, message)


_lines: dict[str, Line] = {}
# Answers and stops for a PC that's reconnecting
_undelivered: dict[str, list[dict]] = {}
_lines_lock = threading.Lock()


def stamp(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pc(row) -> dict:
    return {"id": row[0], "name": row[1], "folders": json.loads(row[2]), "version": row[3], "allow_all": bool(row[4]),
            "last_seen": row[5], "models": json.loads(row[6]), "online": row[0] in _lines}


PC_COLUMNS = "id, name, folders, version, allow_all, last_seen, models"
RUN_COLUMNS = ("r.id, r.pc_id, p.name, r.folder, r.message, r.source, r.status, r.waiting, r.last_seq, r.result, r.error, r.created_at, "
               "r.finished_at, r.thread_id, r.turn")


def _run(row) -> dict:
    return {"id": row[0], "pc_id": row[1], "pc": row[2], "folder": row[3], "message": row[4], "source": row[5], "status": row[6],
            "waiting": json.loads(row[7]), "last_seq": row[8], "result": row[9], "error": row[10], "created_at": row[11],
            "finished_at": row[12], "thread_id": row[13], "turn": row[14], "online": row[1] in _lines}


def register(user_id: str, name: str, folders: list[str], version: str | None, allow_all: bool, models: list[str] = ()) -> str:
    """Add this person's PC, or update what it serves; its id"""

    name = " ".join(name.split())[:64] or "a PC"
    with lock, conn:
        conn.execute(
            "INSERT INTO pcs (id, user_id, name, folders, version, allow_all, last_seen, models) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id, name) DO UPDATE SET folders = excluded.folders, version = excluded.version, "
            "allow_all = excluded.allow_all, last_seen = excluded.last_seen, models = excluded.models",
            (str(uuid.uuid4()), user_id, name, json.dumps(folders), version, int(allow_all), stamp(), json.dumps(list(models))),
        )
        return conn.execute("SELECT id FROM pcs WHERE user_id = ? AND name = ?", (user_id, name)).fetchone()[0]


def list_for(user_id: str) -> list[dict]:
    """This person's PCs, connected ones first"""

    with lock:
        rows = conn.execute(f"SELECT {PC_COLUMNS} FROM pcs WHERE user_id = ? ORDER BY name", (user_id,)).fetchall()
    return sorted((_pc(row) for row in rows), key=lambda pc: not pc["online"])


def any_online(user_id: str) -> bool:
    return any(pc["online"] for pc in list_for(user_id))


def get(pc_id: str, user_id: str) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {PC_COLUMNS} FROM pcs WHERE id = ? AND user_id = ?", (pc_id, user_id)).fetchone()
    return _pc(row) if row else None


def forget(pc_id: str, user_id: str) -> bool:
    """Take a PC off the list, with its runs. It comes back if it connects again, and what it
    synced stays, read-only."""

    with lock, conn:
        if not conn.execute("SELECT id FROM pcs WHERE id = ? AND user_id = ?", (pc_id, user_id)).fetchone():
            return False
        if pc_id in _lines:
            raise PcError("That PC is connected: press Ctrl-C in its labb connect first.")
        conn.execute("DELETE FROM pc_run_events WHERE run_id IN (SELECT id FROM pc_runs WHERE pc_id = ?)", (pc_id,))
        conn.execute("DELETE FROM pc_runs WHERE pc_id = ?", (pc_id,))
        conn.execute("DELETE FROM pc_sessions WHERE pc_id = ?", (pc_id,))
        conn.execute("DELETE FROM pcs WHERE id = ?", (pc_id,))
    return True


def _session(row) -> dict:
    return {"thread_id": row[0], "pc_id": row[1], "machine": row[2], "directory": row[3], "title": row[4], "updated_at": row[5], "source": row[6]}


SESSION_COLUMNS = "s.thread_id, s.pc_id, p.name, s.folder, s.title, s.created_at, s.source"


def session(thread_id: str, user_id: str) -> dict | None:
    """This person's session on a PC"""

    with lock:
        row = conn.execute(f"SELECT {SESSION_COLUMNS} FROM pc_sessions s JOIN pcs p ON p.id = s.pc_id WHERE s.thread_id = ? AND s.user_id = ?",
                           (thread_id, user_id)).fetchone()
    return _session(row) if row else None


def owned_by_someone_else(thread_id: str, user_id: str) -> bool:
    with lock:
        row = conn.execute("SELECT user_id FROM pc_sessions WHERE thread_id = ?", (thread_id,)).fetchone()
    return row is not None and row[0] != user_id


def sessions_for(user_id: str) -> dict[str, dict]:
    """This person's sessions on PCs, by thread"""

    with lock:
        rows = conn.execute(f"SELECT {SESSION_COLUMNS} FROM pc_sessions s JOIN pcs p ON p.id = s.pc_id WHERE s.user_id = ?", (user_id,)).fetchall()
    return {row[0]: _session(row) for row in rows}


def new_session(user_id: str, pc_id: str, folder: str, title: str, source: str = "web") -> dict:
    """A new chat on a connected PC, in one of the folders it serves"""

    pc = get(pc_id, user_id)
    if pc is None:
        raise LookupError("No such PC")
    if not pc["online"]:
        raise PcError(f"{pc['name']} isn't connected: run labb connect on it.")
    if folder not in pc["folders"]:
        raise PcError(f"{pc['name']} doesn't serve {folder}.")
    thread_id = f"cli-{uuid.uuid4()}"
    with lock, conn:
        conn.execute("INSERT INTO pc_sessions (thread_id, user_id, pc_id, folder, title, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (thread_id, user_id, pc_id, folder, " ".join(title.split())[:80] or "A session", source, stamp()))
    return session(thread_id, user_id)


def delete_session(thread_id: str, user_id: str) -> bool:
    """Forget a session here, with its replies' events; the PC keeps its own. Not while it's answering."""

    if active_run(thread_id, user_id):
        raise PcError("It's still answering: stop it first.")
    with lock, conn:
        if not conn.execute("SELECT 1 FROM pc_sessions WHERE thread_id = ? AND user_id = ?", (thread_id, user_id)).fetchone():
            return False
        conn.execute("DELETE FROM pc_run_events WHERE run_id IN (SELECT id FROM pc_runs WHERE thread_id = ?)", (thread_id,))
        conn.execute("DELETE FROM pc_runs WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM pc_sessions WHERE thread_id = ?", (thread_id,))
    return True


def active_run(thread_id: str, user_id: str) -> dict | None:
    """The reply being written in this session, if any"""

    with lock:
        row = conn.execute(f"SELECT {RUN_COLUMNS} FROM pc_runs r JOIN pcs p ON p.id = r.pc_id WHERE r.thread_id = ? AND r.user_id = ? "
                           "AND r.status NOT IN ('done', 'failed', 'stopped', 'lost') ORDER BY r.created_at DESC", (thread_id, user_id)).fetchone()
    return _run(row) if row else None


def connected(pc_id: str, line: Line, active: list[str]) -> Line | None:
    """A PC's request came in: this is its line now; the line it replaces, if any. What it missed is
    sent, and what it says it isn't doing any more is marked lost."""

    with _lines_lock:
        old = _lines.get(pc_id)
        _lines[pc_id] = line
        missed = _undelivered.pop(pc_id, [])
    if old:
        old.send({"type": "replaced"})
    cutoff = stamp(datetime.now(timezone.utc) - timedelta(minutes=JOB_MINUTES))
    with lock, conn:
        conn.execute("UPDATE pc_runs SET status = 'failed', error = ?, finished_at = ? WHERE pc_id = ? AND status = 'queued' AND created_at < ?",
                     ("The PC didn't pick it up in time.", stamp(), pc_id, cutoff))
        for row in conn.execute("SELECT id FROM pc_runs WHERE pc_id = ? AND status IN ('running', 'waiting')", (pc_id,)).fetchall():
            if row[0] not in active:
                conn.execute("UPDATE pc_runs SET status = 'lost', waiting = '[]', error = ?, finished_at = ? WHERE id = ?",
                             ("The PC stopped this run without saying how it ended (it was closed or restarted).", stamp(), row[0]))
        jobs = conn.execute("SELECT job FROM pc_runs WHERE pc_id = ? AND status = 'queued' AND job IS NOT NULL ORDER BY created_at", (pc_id,)).fetchall()
    for message in missed:
        line.send(message)
    for job in jobs:
        line.send(json.loads(job[0]))
    return old


def disconnected(pc_id: str, line: Line, user_id: str):
    """A PC's request ended; what it didn't get waits for it to come back (jobs are read from the table)"""

    with _lines_lock:
        if _lines.get(pc_id) is not line:
            return
        del _lines[pc_id]
        left = []
        while not line.queue.empty():
            message = line.queue.get_nowait()
            if message.get("type") in ("answer", "stop"):
                left.append(message)
        if left:
            _undelivered.setdefault(pc_id, []).extend(left)
    with lock, conn:
        conn.execute("UPDATE pcs SET last_seen = ? WHERE id = ? AND user_id = ?", (stamp(), pc_id, user_id))


def _send(pc_id: str, message: dict):
    with _lines_lock:
        line = _lines.get(pc_id)
        if line:
            line.send(message)
        else:
            _undelivered.setdefault(pc_id, []).append(message)


def start(user_id: str, thread_id: str, message: str, content: str | list | None = None, *, questions: bool = True, source: str = "web",
          model: str | None = None, answer_mode: str | None = None, replace_from: int | None = None, keep_attachments: bool = False,
          turn: int = 0) -> dict:
    """Send a message in a session on its PC; the run that is its reply. `content` is what the PC gets
    if it's more than the message (attachments), and `turn` which turn of the chat this will be."""

    found = session(thread_id, user_id)
    if found is None:
        raise LookupError("No such session")
    pc = get(found["pc_id"], user_id)
    if not pc["online"]:
        raise PcError(f"{pc['name']} isn't connected: run labb connect on it.")
    if found["directory"] not in pc["folders"]:
        raise PcError(f"{pc['name']} doesn't serve {found['directory']} any more.")
    if model is not None and model not in pc["models"]:
        raise PcError(f"{pc['name']} doesn't have the model {model}.")
    message = message.strip()
    if not message and not content:
        raise PcError("Say what to do.")
    if len(message) > MAX_MESSAGE_CHARS:
        raise PcError("That message is too long.")
    if active_run(thread_id, user_id):
        raise PcError("It's still answering the last message.")
    run_id = str(uuid.uuid4())
    job = {"type": "job", "run_id": run_id, "thread_id": thread_id, "folder": found["directory"], "content": content or message,
           "title": message[:200], "questions": questions, "model": model, "answer_mode": answer_mode,
           "replace_from": replace_from, "keep_attachments": keep_attachments}
    with lock, conn:
        conn.execute(
            "INSERT INTO pc_runs (id, user_id, pc_id, folder, message, questions, source, status, created_at, thread_id, job, turn) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
            (run_id, user_id, found["pc_id"], found["directory"], message, int(questions), source, stamp(), thread_id, json.dumps(job), turn),
        )
        _prune(user_id)
    _send(found["pc_id"], job)
    return get_run(run_id, user_id)


def _prune(user_id: str):
    old = conn.execute("SELECT id FROM pc_runs WHERE user_id = ? AND status IN ('done', 'failed', 'stopped', 'lost') "
                       "ORDER BY created_at DESC LIMIT 100000 OFFSET ?", (user_id, KEEP_RUNS)).fetchall()
    for row in old:
        conn.execute("DELETE FROM pc_run_events WHERE run_id = ?", (row[0],))
        conn.execute("DELETE FROM pc_runs WHERE id = ?", (row[0],))


def get_run(run_id: str, user_id: str) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {RUN_COLUMNS} FROM pc_runs r JOIN pcs p ON p.id = r.pc_id WHERE r.id = ? AND r.user_id = ?",
                           (run_id, user_id)).fetchone()
    return _run(row) if row else None


def events_after(run_id: str, after: int) -> list[dict]:
    with lock:
        rows = conn.execute("SELECT seq, event FROM pc_run_events WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after)).fetchall()
    return [{"seq": row[0], "event": json.loads(row[1])} for row in rows]


def add_events(run_id: str, user_id: str, events: list[dict]):
    """What the PC says happened in its run. Numbers it sent before are ignored, so it can send
    a batch again. The run's status follows from the events."""

    with lock, conn:
        row = conn.execute("SELECT status, waiting, last_seq, result, error FROM pc_runs WHERE id = ? AND user_id = ?",
                           (run_id, user_id)).fetchone()
        if row is None:
            raise LookupError("No such run")
        status, last_seq = row[0], row[2]
        waiting, result, error = json.loads(row[1]), row[3], row[4]
        finished = False
        for item in events:
            seq, event = item["seq"], item["event"]
            if seq <= last_seq:
                continue
            last_seq = seq
            conn.execute("INSERT INTO pc_run_events (run_id, seq, event) VALUES (?, ?, ?)", (run_id, seq, json.dumps(event)))
            match event.get("type"):
                case "token":
                    result += str(event.get("content", "")).replace("\x00", "")
                case "approval" | "question":
                    waiting.append(event.get("id"))
                case "answered":
                    waiting = [i for i in waiting if i != event.get("id")]
                case "error":
                    error = str(event.get("message", "")).replace("\x00", "")[:2000]
                case "done" | "stopped":
                    status = "stopped" if event["type"] == "stopped" else "failed" if error else "done"
                    waiting, finished = [], True
        if status not in FINISHED:
            status = "waiting" if waiting else "running"
        # Once it's started, the PC has what it needs
        conn.execute("UPDATE pc_runs SET status = ?, waiting = ?, last_seq = ?, result = ?, error = ?, job = NULL, "
                     "finished_at = CASE WHEN ? = 1 THEN ? ELSE finished_at END WHERE id = ?",
                     (status, json.dumps(waiting), last_seq, result[-MAX_RESULT_CHARS:], error, int(finished), stamp(), run_id))


def answer(run_id: str, user_id: str, question_id: str, allow: bool | None = None, reason: str | None = None, text: str | None = None):
    """Pass on the answer to a question the run is waiting on. Until the PC confirms it, it can be
    answered again (it may not have got there), and the PC takes the first."""

    run = get_run(run_id, user_id)
    if run is None:
        raise LookupError("No such run")
    if question_id not in run["waiting"]:
        raise PcError("That question isn't waiting any more.")
    _send(run["pc_id"], {"type": "answer", "run_id": run_id, "id": question_id, "allow": allow, "reason": reason, "text": text})


def answer_question(user_id: str, question_id: str, allow: bool | None = None, reason: str | None = None, text: str | None = None) -> bool:
    """Answer a question by its id, as the chat knows it; False if none of this person's replies is waiting on it"""

    with lock:
        rows = conn.execute("SELECT id, waiting FROM pc_runs WHERE user_id = ? AND status = 'waiting'", (user_id,)).fetchall()
    for run_id, waiting in rows:
        if question_id in json.loads(waiting):
            answer(run_id, user_id, question_id, allow, reason, text)
            return True
    return False


def stop(run_id: str, user_id: str):
    run = get_run(run_id, user_id)
    if run is None:
        raise LookupError("No such run")
    if run["status"] in FINISHED:
        return
    _send(run["pc_id"], {"type": "stop", "run_id": run_id})
    if run["pc_id"] not in _lines:
        # Nobody's there to say how it ended, and the chat shouldn't wait. If the PC comes back with it
        # still going, the stop reaches it then, and its events still count.
        with lock, conn:
            conn.execute("UPDATE pc_runs SET status = 'stopped', waiting = '[]', job = NULL, finished_at = ? WHERE id = ?", (stamp(), run_id))


# --- Questions for a PC: a folder's notes (server/notes.py), a session's context (app.py) ----

# How long the page waits for the PC's answer; a session's context may start its process first
NOTES_SECONDS = 15
CONTEXT_SECONDS = 60
# Questions waiting for the PC's answer, by id: whose, and the answer when it comes
_asks: dict[str, dict] = {}
_asks_lock = threading.Lock()


def ask(user_id: str, pc_id: str, question: dict, seconds: float) -> dict:
    """Send a question down the PC's line and wait for its answer (POST /pcs/answers/{id})"""

    pc = get(pc_id, user_id)
    if pc is None:
        raise LookupError(pc_id)
    if pc_id not in _lines:
        raise PcError(f"{pc['name']} isn't connected: run labb connect there.")
    ask_id = str(uuid.uuid4())
    waiting = {"user_id": user_id, "done": threading.Event(), "answer": None}
    with _asks_lock:
        _asks[ask_id] = waiting
    try:
        with _lines_lock:
            line = _lines.get(pc_id)
            if line:
                line.send({**question, "id": ask_id})
        if line is None or not waiting["done"].wait(seconds):
            raise PcError(f"{pc['name']} didn't answer. If its labb is older than this server's, update it with labb --update.")
    finally:
        with _asks_lock:
            _asks.pop(ask_id, None)
    answer = waiting["answer"] or {}
    if error := answer.get("error"):
        raise (PcConflict if answer.get("conflict") else PcError)(str(error)[:500])
    return answer


def ask_notes(user_id: str, pc_id: str, folder: str, **change) -> str:
    """A folder's notes (.labb/notes.md) on a connected PC, after a change if one is given
    (action, text, topic, base: see tools/notes.changed). The PC checks the folder again."""

    pc = get(pc_id, user_id)
    if pc is not None and folder not in pc["folders"]:
        raise PcError(f"{pc['name']} doesn't serve {folder}.")
    answer = ask(user_id, pc_id, {"type": "notes", "folder": folder, "action": "read", **change}, NOTES_SECONDS)
    return str(answer.get("text") or "")


def ask_context(user_id: str, session: dict, question: dict) -> dict:
    """What the context panel asks about a session (`what`: seen, raw, message, traces, summarize),
    answered by its process on the PC: {"result"} or {"missing"}"""
    return ask(user_id, session["pc_id"], {"type": "context", "thread_id": session["thread_id"], "folder": session["directory"],
                                          **question}, CONTEXT_SECONDS)


def answered(user_id: str, ask_id: str, answer: dict) -> bool:
    """The PC's answer to ask; False if nobody of this user's is waiting for it"""
    with _asks_lock:
        waiting = _asks.get(ask_id)
    if waiting is None or waiting["user_id"] != user_id:
        return False
    waiting["answer"] = answer
    waiting["done"].set()
    return True
