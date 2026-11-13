"""The inbox: what happened while you weren't looking, in one list per person. A task's result or
failure, a workflow waiting for your approval, an app that crashed, and for admins, a service
that stopped answering (see `watch.py`) and people joining.

- Each notification is a row in `notifications`: who it's for, a `kind` (task, studio, app,
  document, account, health), a title and a line of detail, a `link` to the page it's about
  and a `level` (info, success, warning, error). Opening it in the web chat marks it read.
- `key` names what a notification is about ("task:<id>:done"): a new one with the key of one
  still unread replaces it, so a task that runs every 5 minutes leaves one unread result,
  not a pile, and "the crawler is working again" replaces "the crawler isn't working".
- Pages follow their person's inbox live (`follow`, `GET /inbox/stream`): this lives in
  memory, so the server must stay one process (like live.py).
- Only the newest `inbox_kept` (Settings → Limits) are kept for each person.

`add` never raises: telling someone must never break what it tells them about.
"""

import asyncio
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone

from labb import chats, db
from labb.chats import conn, lock
from labb.server import limits

log = logging.getLogger(__name__)

LEVELS = ("info", "success", "warning", "error")
MAX_TITLE = 200
MAX_BODY = 1000
# How often a quiet stream says it's still there, so proxies and browsers keep it open
PING_SECONDS = 15

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS notifications ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, "
        "link TEXT, key TEXT, level TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT"
        + (", seq BIGSERIAL)" if db.POSTGRES else ")")
    )
    conn.execute("CREATE INDEX IF NOT EXISTS notifications_by_user ON notifications (user_id, created_at)")

# Added in the same second come in the order they were added: SQLite's rowid, a serial column in Postgres
NEWEST = "created_at DESC, " + ("seq DESC" if db.POSTGRES else "rowid DESC")
COLUMNS = "id, kind, title, body, link, key, level, created_at, read_at"


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _item(row) -> dict:
    nid, kind, title, body, link, key, level, created_at, read_at = row
    return {"id": nid, "kind": kind, "title": title, "body": body, "link": link, "key": key, "level": level,
            "created_at": created_at, "read": read_at is not None}


# --- Following an inbox live -------------------------------------------------------------

# Each person's open streams: a function that hands an event to one
_followers: dict[str, set[Callable[[dict], None]]] = {}
_followers_lock = threading.Lock()


def _publish(user_id: str, event: dict):
    with _followers_lock:
        pushes = list(_followers.get(user_id, ()))
    for push in pushes:
        push(event)


async def follow(user_id: str) -> AsyncIterator[dict]:
    """The person's unread count, then each change as it comes: `{"type": "notification", "item",
    "unread"}` for a new one, `{"type": "unread", "unread"}` when some were read or deleted, and
    `{"type": "ping"}` when it's quiet"""

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def push(event: dict):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except RuntimeError:
            # The page's stream has gone and its loop with it
            pass

    with _followers_lock:
        _followers.setdefault(user_id, set()).add(push)
    try:
        yield {"type": "unread", "unread": unread_count(user_id)}
        while True:
            try:
                yield await asyncio.wait_for(queue.get(), PING_SECONDS)
            except asyncio.TimeoutError:
                yield {"type": "ping"}
    finally:
        with _followers_lock:
            followers = _followers.get(user_id)
            if followers is not None:
                followers.discard(push)
                if not followers:
                    _followers.pop(user_id, None)


def _changed(user_id: str):
    _publish(user_id, {"type": "unread", "unread": unread_count(user_id)})


# --- Adding ------------------------------------------------------------------------------

def add(user_id: str, kind: str, title: str, body: str = "", *, link: str | None = None, key: str | None = None,
        level: str = "info") -> dict | None:
    """Tell someone something; it replaces their unread one with the same `key`. The notification,
    or None if it couldn't be saved (which is logged, never raised)"""

    try:
        level = level if level in LEVELS else "info"
        title, body = title.strip()[:MAX_TITLE], (body or "").strip()[:MAX_BODY]
        # Before the lock: reading a limit takes it too
        at, kept = stamp(), limits.get("inbox_kept")
        with lock, conn:
            replaced = None
            if key:
                found = conn.execute("SELECT id FROM notifications WHERE user_id = ? AND key = ? AND read_at IS NULL",
                                     (user_id, key)).fetchone()
                replaced = found[0] if found else None
            if replaced:
                # Its id stays, so a page showing it updates it in place
                conn.execute("UPDATE notifications SET kind = ?, title = ?, body = ?, link = ?, level = ?, created_at = ? WHERE id = ?",
                             (kind, title, body, link, level, at, replaced))
                nid = replaced
            else:
                nid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO notifications (id, user_id, kind, title, body, link, key, level, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (nid, user_id, kind, title, body, link, key, level, at),
                )
                # Only the newest are kept
                conn.execute(
                    "DELETE FROM notifications WHERE user_id = ? AND id NOT IN "
                    f"(SELECT id FROM notifications WHERE user_id = ? ORDER BY {NEWEST} LIMIT ?)",
                    (user_id, user_id, kept),
                )
            row = conn.execute(f"SELECT {COLUMNS} FROM notifications WHERE id = ?", (nid,)).fetchone()
        item = _item(row)
        _publish(user_id, {"type": "notification", "item": item, "unread": unread_count(user_id)})
        return item
    except Exception:
        log.exception("Couldn't add a notification for %s: %s", user_id, title)
        return None


def add_admins(kind: str, title: str, body: str = "", **fields) -> int:
    """Tell every admin whose account is enabled; how many were told"""

    try:
        with lock:
            admins = [row[0] for row in conn.execute("SELECT id FROM users WHERE role = 'admin' AND disabled = 0")]
    except Exception:
        log.exception("Couldn't find the admins to tell: %s", title)
        return 0
    return sum(add(admin, kind, title, body, **fields) is not None for admin in admins)


# --- Reading -----------------------------------------------------------------------------

def list_for(user_id: str, limit: int = 100, unread_only: bool = False) -> list[dict]:
    """The person's notifications, newest first"""

    where = "user_id = ?" + (" AND read_at IS NULL" if unread_only else "")
    with lock:
        rows = conn.execute(f"SELECT {COLUMNS} FROM notifications WHERE {where} ORDER BY {NEWEST} LIMIT ?",
                            (user_id, max(1, min(limit, 500)))).fetchall()
    return [_item(row) for row in rows]


def unread_count(user_id: str) -> int:
    with lock:
        return conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL", (user_id,)).fetchone()[0]


def mark_read(user_id: str, ids: list[str] | None = None) -> int:
    """Mark some of the person's notifications read (all of them without `ids`); how many changed.
    A task's result read here reads its chat too (the dot in the chat list and the Tasks badge), as
    opening the chat reads what the inbox said about it (`mark_read_about`)."""

    if ids is not None and not ids:
        return 0
    which = "user_id = ? AND read_at IS NULL"
    values: tuple = (user_id,)
    if ids is not None:
        which += f" AND id IN ({', '.join('?' for _ in ids)})"
        values += tuple(ids)
    with lock, conn:
        threads = [row[0][len(CHAT_LINK):] for row in conn.execute(
            f"SELECT DISTINCT link FROM notifications WHERE {which} AND kind = 'task' AND link LIKE ?",
            (*values, CHAT_LINK + "%"))]
        changed = conn.execute(f"UPDATE notifications SET read_at = ? WHERE {which}", (stamp(), *values)).rowcount
    for thread_id in threads:
        # Only the person's own chat
        if chats.owner(thread_id)[1] == user_id:
            chats.set_unread(thread_id, False)
    if changed:
        _changed(user_id)
    return changed


# A notification's link to a chat: /chat/<thread id>
CHAT_LINK = "/chat/"


def mark_read_about(*, link: str | None = None, key: str | None = None, key_prefix: str | None = None,
                    user_id: str | None = None) -> int:
    """Mark read what's about something dealt with elsewhere: a chat that was opened (its `link`), an
    approval that was answered (its `key`), a run that was stopped (keys starting `key_prefix`), for
    one person or everyone; how many"""

    if not link and not key and not key_prefix:
        return 0
    clauses, values = ["read_at IS NULL"], []
    if link:
        clauses.append("link = ?")
        values.append(link)
    if key:
        clauses.append("key = ?")
        values.append(key)
    if key_prefix:
        # The prefix taken literally
        clauses.append("key LIKE ? ESCAPE '!'")
        values.append(key_prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%")
    if user_id:
        clauses.append("user_id = ?")
        values.append(user_id)
    where = " AND ".join(clauses)
    try:
        with lock, conn:
            users = [row[0] for row in conn.execute(f"SELECT DISTINCT user_id FROM notifications WHERE {where}", values)]
            if not users:
                return 0
            changed = conn.execute(f"UPDATE notifications SET read_at = ? WHERE {where}", (stamp(), *values)).rowcount
    except Exception:
        log.exception("Couldn't mark notifications read")
        return 0
    for user in users:
        _changed(user)
    return changed


def delete(user_id: str, notification_id: str) -> bool:
    with lock, conn:
        deleted = conn.execute("DELETE FROM notifications WHERE id = ? AND user_id = ?", (notification_id, user_id)).rowcount
    if deleted:
        _changed(user_id)
    return bool(deleted)


def delete_read(user_id: str) -> int:
    """Clear the ones already read; how many"""
    with lock, conn:
        deleted = conn.execute("DELETE FROM notifications WHERE user_id = ? AND read_at IS NOT NULL", (user_id,)).rowcount
    return deleted
