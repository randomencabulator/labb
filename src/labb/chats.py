"""Index of the web chats (title and last update) for the chat list, the
terminal's sessions per directory, and the stats of each reply (tokens and timing).
On the server, also the terminal sessions other PCs send (see sync.py); on a PC, the
sessions still to send.

The checkpointer only stores graph state, so these tables sit next to it in
the same database (db.py: the SQLite file, or the server's Postgres).
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from labb import db
from labb.agent import CHAT_DB, checkpointer

MAX_TITLE_CHARS = 60

conn = db.connect(CHAT_DB)
lock = threading.Lock()

with lock, conn:
    # The web chats: whose (see auth.py), pinned to the top, the agent it's with (server/agents.py;
    # NULL for the default assistant), and whether a task wrote to it since its owner last opened it
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chats ("
        "thread_id TEXT PRIMARY KEY, title TEXT NOT NULL, updated_at TEXT NOT NULL, user_id TEXT, "
        "pinned INTEGER NOT NULL DEFAULT 0, agent_id TEXT, unread INTEGER NOT NULL DEFAULT 0)"
    )
    # Terminal (labb) sessions; their thread ids start with "cli-"
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cli_sessions ("
        "thread_id TEXT PRIMARY KEY, directory TEXT NOT NULL, title TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    # The working directory's files before each turn of a terminal conversation (see snapshots.py);
    # `turn` counts the user messages before it
    conn.execute(
        "CREATE TABLE IF NOT EXISTS snapshots ("
        "thread_id TEXT NOT NULL, turn INTEGER NOT NULL, tree TEXT NOT NULL, "
        "PRIMARY KEY (thread_id, turn))"
    )
    # Keyed by the id of the reply's final AI message
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stats ("
        "thread_id TEXT NOT NULL, message_id TEXT NOT NULL, data TEXT NOT NULL, "
        "PRIMARY KEY (thread_id, message_id))"
    )
    # What each reply did and when (see trace.py); message_id as in stats, or the run's own for
    # a reply that failed before writing anything
    conn.execute(
        "CREATE TABLE IF NOT EXISTS traces ("
        "thread_id TEXT NOT NULL, message_id TEXT NOT NULL, started_at TEXT NOT NULL, data TEXT NOT NULL, "
        "PRIMARY KEY (thread_id, message_id))"
    )
    # On the server: terminal sessions sent by labb on other PCs, for the web chat to show.
    # The messages and stats are JSON, not graph state: the server never runs them.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS synced_sessions ("
        "thread_id TEXT PRIMARY KEY, machine TEXT NOT NULL, directory TEXT NOT NULL, title TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, messages TEXT NOT NULL, stats TEXT NOT NULL, user_id TEXT)"
    )
    # On a PC: sessions changed since they were last sent to the server
    conn.execute("CREATE TABLE IF NOT EXISTS unsynced (thread_id TEXT PRIMARY KEY)")
    # Usage stats (see usage.py): one row per reply, web and synced alike, which only grows:
    # deleting a chat, regenerating or taking back a reply leaves it. `created_at` is UTC,
    # as YYYY-MM-DDTHH:MM:SSZ, so it compares as text.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS usage ("
        "thread_id TEXT NOT NULL, message_id TEXT NOT NULL, user_id TEXT, source TEXT NOT NULL, machine TEXT, "
        "model TEXT NOT NULL, created_at TEXT NOT NULL, duration_ms INTEGER NOT NULL DEFAULT 0, first_token_ms INTEGER, "
        "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, generation_ms INTEGER, "
        "llm_calls INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0, tools TEXT NOT NULL DEFAULT '{}', "
        "stopped INTEGER NOT NULL DEFAULT 0, error INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (thread_id, message_id))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS usage_by_time ON usage (created_at)")
    # Tool calls that needed fixing (tool_calls.py); added after the table, so older databases get them here
    for column in ("tool_repaired", "tool_constrained", "tool_failed"):
        db.add_column(conn, "usage", column, "INTEGER NOT NULL DEFAULT 0")
    # Which engine wrote the reply (engine.py); NULL for the default
    db.add_column(conn, "usage", "engine", "TEXT")
    # Of the input tokens, those read from the provider's prompt cache and those written to it;
    # and what the reply cost in dollars, at the server's prices when it was recorded (NULL: no price)
    db.add_column(conn, "usage", "cache_read_tokens", "INTEGER NOT NULL DEFAULT 0")
    db.add_column(conn, "usage", "cache_write_tokens", "INTEGER NOT NULL DEFAULT 0")
    db.add_column(conn, "usage", "cost", "DOUBLE PRECISION")
    # A chat its owner shared: everyone on the server can read it (added after the table)
    db.add_column(conn, "chats", "shared", "INTEGER NOT NULL DEFAULT 0")
    # Model calls and page reads through the gateway (gateway.py), added up per hour
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gateway_usage ("
        "hour TEXT NOT NULL, user_id TEXT NOT NULL, key_id TEXT NOT NULL, model TEXT NOT NULL, "
        "requests INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, "
        "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, eval_ms INTEGER NOT NULL DEFAULT 0, "
        "pages INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (hour, user_id, key_id, model))"
    )
    # What a conversation without a project works on (work.py): its todo list, goal and approved
    # plan, by kind; the terminal keeps them in the project's .labb folder instead
    conn.execute(
        "CREATE TABLE IF NOT EXISTS work ("
        "thread_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "PRIMARY KEY (thread_id, kind))"
    )
    # The labb version and machine each API key was last seen with (the X-Labb-Client header)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS clients ("
        "key_id TEXT PRIMARY KEY, version TEXT NOT NULL, machine TEXT NOT NULL, last_seen TEXT NOT NULL)"
    )


def make_title(message: str) -> str:
    title = " ".join(message.split())
    if len(title) > MAX_TITLE_CHARS:
        title = title[:MAX_TITLE_CHARS - 1].rstrip() + "…"
    return title or "New chat"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def touch(thread_id: str, message: str, user_id: str | None = None, agent_id: str | None = None):
    """Add the chat (for `user_id`, with `agent_id`), titled after its first message, or mark
    it as just updated; a chat keeps the agent it started with"""

    with lock, conn:
        conn.execute(
            "INSERT INTO chats (thread_id, title, updated_at, user_id, agent_id) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (thread_id) DO UPDATE SET updated_at = excluded.updated_at",
            (thread_id, make_title(message), now(), user_id, agent_id),
        )


def set_unread(thread_id: str, unread: bool):
    """A task's reply waits to be read (or was opened)"""
    with lock, conn:
        conn.execute("UPDATE chats SET unread = ? WHERE thread_id = ?", (int(unread), thread_id))


def agent_of(thread_id: str) -> str | None:
    """The agent a chat is with"""
    with lock:
        row = conn.execute("SELECT agent_id FROM chats WHERE thread_id = ?", (thread_id,)).fetchone()
    return row[0] if row else None


def shared_chat(thread_id: str) -> dict | None:
    """A chat its owner shared: its title and whose it is; None if it isn't shared (or doesn't exist)"""

    with lock:
        row = conn.execute("SELECT title, user_id FROM chats WHERE thread_id = ? AND shared = 1", (thread_id,)).fetchone()
    return {"title": row[0], "user_id": row[1]} if row else None


def owner(thread_id: str) -> tuple[bool, str | None]:
    """Whether a conversation exists on this server, and whose it is: a web chat, a session
    a PC synced, or one of the server's own terminal sessions (which belong to no one)"""

    with lock:
        for table in ("chats", "synced_sessions"):
            row = conn.execute(f"SELECT user_id FROM {table} WHERE thread_id = ?", (thread_id,)).fetchone()
            if row:
                return True, row[0]
        exists = conn.execute("SELECT 1 FROM cli_sessions WHERE thread_id = ?", (thread_id,)).fetchone()
    return exists is not None, None


def retitle(thread_id: str, first_message: str, title: str) -> bool:
    """Replace a chat's (or terminal session's) automatic title, the start of its first
    message, with a better one; a title someone chose stays. Returns whether it changed."""

    automatic = make_title(first_message)
    with lock, conn:
        changed = 0
        for table in ("chats", "cli_sessions"):
            changed += conn.execute(
                f"UPDATE {table} SET title = ? WHERE thread_id = ? AND title = ?",
                (make_title(title), thread_id, automatic),
            ).rowcount
    return changed > 0


def list_chats(user_id: str | None = None) -> list[dict]:
    """A user's chats (all of them for None), newest first"""

    with lock:
        rows = conn.execute(
            "SELECT thread_id, title, updated_at, pinned, agent_id, unread, shared FROM chats "
            "WHERE ? IS NULL OR user_id = ? ORDER BY updated_at DESC",
            (user_id, user_id),
        ).fetchall()
    return [{"thread_id": t, "title": title, "updated_at": u, "pinned": bool(p), "agent_id": a, "unread": bool(r), "shared": bool(sh)}
            for t, title, u, p, a, r, sh in rows]


def update_chat(thread_id: str, *, title: str | None = None, pinned: bool | None = None, shared: bool | None = None) -> bool:
    """Rename, (un)pin or (un)share a chat; False if there's no such chat"""

    with lock, conn:
        if shared is not None:
            conn.execute("UPDATE chats SET shared = ? WHERE thread_id = ?", (int(shared), thread_id))
        if title is not None:
            conn.execute("UPDATE chats SET title = ? WHERE thread_id = ?", (make_title(title), thread_id))
        if pinned is not None:
            conn.execute("UPDATE chats SET pinned = ? WHERE thread_id = ?", (int(pinned), thread_id))
        return conn.execute("SELECT 1 FROM chats WHERE thread_id = ?", (thread_id,)).fetchone() is not None


def delete_chat(thread_id: str):
    """Remove the chat from the list and delete its saved conversation"""

    with lock, conn:
        conn.execute("DELETE FROM chats WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM stats WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM traces WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM snapshots WHERE thread_id = ?", (thread_id,))
        conn.execute("DELETE FROM work WHERE thread_id = ?", (thread_id,))
    checkpointer.delete_thread(thread_id)


def get_work(thread_id: str, kind: str) -> str | None:
    with lock:
        row = conn.execute("SELECT value FROM work WHERE thread_id = ? AND kind = ?", (thread_id, kind)).fetchone()
    return row[0] if row else None


def set_work(thread_id: str, kind: str, value: str | None):
    """Keep a conversation's work of this kind; None removes it"""

    with lock, conn:
        if value is None:
            conn.execute("DELETE FROM work WHERE thread_id = ? AND kind = ?", (thread_id, kind))
        else:
            conn.execute("INSERT INTO work (thread_id, kind, value, updated_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT (thread_id, kind) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                         (thread_id, kind, value, now()))


def touch_cli(thread_id: str, directory: str, message: str):
    """Add a terminal session, titled after its first message, or mark it as just updated"""

    with lock, conn:
        conn.execute(
            "INSERT INTO cli_sessions (thread_id, directory, title, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (thread_id) DO UPDATE SET updated_at = excluded.updated_at",
            (thread_id, directory, make_title(message), now()),
        )


def list_cli(directory: str) -> list[dict]:
    """The terminal sessions started in a directory, newest first"""

    with lock:
        rows = conn.execute(
            "SELECT thread_id, title, updated_at FROM cli_sessions WHERE directory = ? ORDER BY updated_at DESC",
            (directory,),
        ).fetchall()
    return [{"thread_id": t, "title": title, "updated_at": u} for t, title, u in rows]


def list_all_cli() -> list[dict]:
    """Every terminal session, newest first, for the web page"""

    with lock:
        rows = conn.execute(
            "SELECT thread_id, directory, title, updated_at FROM cli_sessions ORDER BY updated_at DESC"
        ).fetchall()
    return [{"thread_id": t, "directory": d, "title": title, "updated_at": u} for t, d, title, u in rows]


def save_synced(thread_id: str, machine: str, directory: str, title: str, updated_at: str,
                messages: list[dict], stats: dict, user_id: str | None = None):
    with lock, conn:
        conn.execute(
            "INSERT INTO synced_sessions (thread_id, machine, directory, title, updated_at, messages, stats, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (thread_id) DO UPDATE SET machine = excluded.machine, "
            "directory = excluded.directory, title = excluded.title, updated_at = excluded.updated_at, "
            "messages = excluded.messages, stats = excluded.stats, user_id = excluded.user_id",
            (thread_id, machine, directory, make_title(title), updated_at, json.dumps(messages), json.dumps(stats), user_id),
        )


def get_synced(thread_id: str) -> dict | None:
    """A synced session with its messages (as dicts) and stats"""

    with lock:
        row = conn.execute(
            "SELECT machine, directory, title, updated_at, messages, stats FROM synced_sessions WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    if row is None:
        return None
    machine, directory, title, updated_at, messages, stats = row
    return {"machine": machine, "directory": directory, "title": title, "updated_at": updated_at,
            "messages": json.loads(messages), "stats": json.loads(stats)}


def list_synced(user_id: str | None = None) -> list[dict]:
    """A user's synced sessions (all of them for None), newest first, without their messages"""

    with lock:
        rows = conn.execute(
            "SELECT thread_id, machine, directory, title, updated_at FROM synced_sessions "
            "WHERE ? IS NULL OR user_id = ? ORDER BY updated_at DESC",
            (user_id, user_id),
        ).fetchall()
    return [{"thread_id": t, "machine": m, "directory": d, "title": title, "updated_at": u} for t, m, d, title, u in rows]


def delete_synced(thread_id: str) -> bool:
    with lock, conn:
        return conn.execute("DELETE FROM synced_sessions WHERE thread_id = ?", (thread_id,)).rowcount > 0


def mark_unsynced(thread_id: str):
    with lock, conn:
        conn.execute("INSERT INTO unsynced (thread_id) VALUES (?) ON CONFLICT DO NOTHING", (thread_id,))


def mark_synced(thread_id: str):
    with lock, conn:
        conn.execute("DELETE FROM unsynced WHERE thread_id = ?", (thread_id,))


def list_unsynced() -> list[str]:
    with lock:
        return [row[0] for row in conn.execute("SELECT thread_id FROM unsynced")]


def save_stats(thread_id: str, message_id: str, stats: dict):
    with lock, conn:
        conn.execute(
            "INSERT INTO stats (thread_id, message_id, data) VALUES (?, ?, ?) "
            "ON CONFLICT (thread_id, message_id) DO UPDATE SET data = excluded.data",
            (thread_id, message_id, json.dumps(stats)),
        )


def save_trace(thread_id: str, message_id: str, trace: dict):
    with lock, conn:
        conn.execute(
            "INSERT INTO traces (thread_id, message_id, started_at, data) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (thread_id, message_id) DO UPDATE SET started_at = excluded.started_at, data = excluded.data",
            (thread_id, message_id, trace["started_at"], json.dumps(trace)),
        )


def get_traces(thread_id: str, limit: int) -> list[dict]:
    """The chat's latest replies' traces, oldest first"""
    with lock:
        rows = conn.execute(
            "SELECT message_id, data FROM traces WHERE thread_id = ? ORDER BY started_at DESC LIMIT ?", (thread_id, limit),
        ).fetchall()
    return [{"id": message_id, **json.loads(data)} for message_id, data in reversed(rows)]


def save_snapshot(thread_id: str, turn: int, tree: str):
    with lock, conn:
        conn.execute("INSERT INTO snapshots (thread_id, turn, tree) VALUES (?, ?, ?) "
                     "ON CONFLICT (thread_id, turn) DO UPDATE SET tree = excluded.tree", (thread_id, turn, tree))


def get_snapshot(thread_id: str, turn: int) -> str | None:
    with lock:
        row = conn.execute("SELECT tree FROM snapshots WHERE thread_id = ? AND turn = ?", (thread_id, turn)).fetchone()
    return row[0] if row else None


def delete_snapshots(thread_id: str, from_turn: int):
    """Forget the snapshots of turns taken back"""

    with lock, conn:
        conn.execute("DELETE FROM snapshots WHERE thread_id = ? AND turn >= ?", (thread_id, from_turn))


def delete_stats(thread_id: str, message_ids: list[str]):
    """Forget the stats and traces of replies taken back from a conversation"""

    with lock, conn:
        for table in ("stats", "traces"):
            conn.executemany(
                f"DELETE FROM {table} WHERE thread_id = ? AND message_id = ?",
                [(thread_id, message_id) for message_id in message_ids],
            )


def get_stats(thread_id: str) -> dict[str, dict]:
    """The saved reply stats of a chat, by message id"""

    with lock:
        rows = conn.execute("SELECT message_id, data FROM stats WHERE thread_id = ?", (thread_id,)).fetchall()
    return {message_id: json.loads(data) for message_id, data in rows}


def prune(thread_id: str | None = None) -> int:
    """Delete the checkpoints older than each thread's latest, with their writes.

    The checkpointer saves a full snapshot after every step and nothing reads the
    old ones, so they only grow the database. Prunes every thread if none is given.
    Returns the number of checkpoints deleted.
    """
    # Checkpoint ids are time-ordered, so the latest is the largest
    older = (
        "SELECT thread_id, checkpoint_ns, checkpoint_id FROM checkpoints c "
        "WHERE (? IS NULL OR thread_id = ?) AND checkpoint_id < ("
        "SELECT MAX(checkpoint_id) FROM checkpoints "
        "WHERE thread_id = c.thread_id AND checkpoint_ns = c.checkpoint_ns)"
    )
    if db.POSTGRES:
        return _prune_postgres(thread_id, older)
    with checkpointer.cursor() as cur:
        if not cur.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'checkpoints'").fetchone():
            return 0
        cur.execute(
            f"DELETE FROM writes WHERE (thread_id, checkpoint_ns, checkpoint_id) IN ({older})",
            (thread_id, thread_id),
        )
        cur.execute(
            f"DELETE FROM checkpoints WHERE (thread_id, checkpoint_ns, checkpoint_id) IN ({older})",
            (thread_id, thread_id),
        )
        return cur.rowcount


def _prune_postgres(thread_id: str | None, older: str) -> int:
    """prune() for Postgres's checkpointer, which keeps the channels' values apart, in
    checkpoint_blobs, once per version: the versions no checkpoint left uses go too, if
    they're older than the one it uses. A reply being saved writes its newer values just
    before its checkpoint, so those stay."""

    with lock, conn:
        if not db.has_table(conn, "checkpoints"):
            return 0
        conn.execute(
            f"DELETE FROM checkpoint_writes WHERE (thread_id, checkpoint_ns, checkpoint_id) IN ({older})",
            (thread_id, thread_id),
        )
        deleted = conn.execute(
            f"DELETE FROM checkpoints WHERE (thread_id, checkpoint_ns, checkpoint_id) IN ({older})",
            (thread_id, thread_id),
        ).rowcount
        conn.execute(
            # Versions are zero-padded, so they compare as text
            "DELETE FROM checkpoint_blobs b WHERE (? IS NULL OR b.thread_id = ?) AND EXISTS ("
            "SELECT 1 FROM checkpoints c WHERE c.thread_id = b.thread_id AND c.checkpoint_ns = b.checkpoint_ns "
            "AND b.version < c.checkpoint -> 'channel_versions' ->> b.channel) AND NOT EXISTS ("
            "SELECT 1 FROM checkpoints c WHERE c.thread_id = b.thread_id AND c.checkpoint_ns = b.checkpoint_ns "
            "AND c.checkpoint -> 'channel_versions' ->> b.channel = b.version)",
            (thread_id, thread_id),
        )
    return deleted


def compact():
    """Prune every thread and give the freed space back to the disk (Postgres's autovacuum
    takes care of that itself)"""

    if prune() and not db.POSTGRES:
        try:
            with checkpointer.lock:
                checkpointer.conn.execute("VACUUM")
        except sqlite3.OperationalError:
            # Another process (the server or a terminal) is using the file; the space
            # is still reused for new checkpoints, and the next start tries again
            pass


def checkpointed_threads(prefix: str) -> list[str]:
    """The threads with saved conversations whose ids start with `prefix`"""

    with lock:
        if not db.has_table(conn, "checkpoints"):
            return []
        return [row[0] for row in conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ?", (prefix + "%",))]


compact()
