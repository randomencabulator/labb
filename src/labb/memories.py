"""What the agent remembers about the user across conversations.

Memories are short facts and preferences ("prefers metric units", "works on labb in
Python"). Pinned ones go into every system prompt; the others are recalled when a message
is about them (the graph's `recall` step) and attached to that message for the model only.

Where they live:
- On the server, in the `memories` table, per account: the web chat (server/app.py sets
  `on_server`) and labb on every PC share them. The server also embeds them
  (server/memory_index.py sets `index`), so recall finds them by meaning.
- labb with a server (LABB_SERVER) reads and writes the server's, with its API key.
- labb without one keeps its own, locally, as the user "local", recalled by keywords.
"""

import math
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Protocol

import httpx
from langchain_core.runnables import RunnableConfig

from labb import remote

MAX_CHARS = 500
MAX_MEMORIES = 500


# The server's come from Settings → Limits (server/app.py sets these)
def max_chars() -> int:
    return MAX_CHARS


def max_memories() -> int:
    return MAX_MEMORIES

# Pinned memories in the system prompt, at most
PROMPT_MEMORIES = 30
PROMPT_CHARS = 3000
# Recalled per message, and how close (0 to 1) they have to be
RECALL = 3
MIN_SCORE = 0.4
# How long labb keeps the server's pinned memories before asking again
PINNED_SECONDS = 30
LOCAL_USER = "local"

_ready = False


def db():
    """chats.sqlite's connection and lock, with the memories table. Imported on first use:
    the graph imports this module, and chats.py imports the graph."""

    global _ready
    from labb.chats import conn, lock
    from labb.db import add_column as db_add_column

    if not _ready:
        with lock, conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memories ("
                "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, text TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, embed_model TEXT)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS memories_by_user ON memories (user_id)")
            # Whether the user has seen it: the ones the agent saves (or changes) wait for a yes, and until
            # then aren't pinned into every conversation (added after the table: the older ones are reviewed)
            db_add_column(conn, "memories", "reviewed", "INTEGER NOT NULL DEFAULT 1")
        _ready = True
    return conn, lock


def stamp() -> str:
    """Now in UTC, as the other tables keep times"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

COLUMNS = "id, text, pinned, created_at, updated_at, reviewed"


class MemoryError(ValueError):
    """A memory that can't be saved, said for whoever tried"""


class Index(Protocol):
    """Recall by meaning (the server's, in server/memory_index.py)"""

    def add(self, memory: dict, user_id: str): ...
    def remove(self, ids: list[str]): ...
    def search(self, user_id: str, query: str, k: int) -> list[tuple[str, float]]: ...


# Set by the server: memories are its own, per account, and searched by meaning
on_server = False
index: Index | None = None


def as_dict(row: tuple) -> dict:
    memory = dict(zip([c.strip() for c in COLUMNS.split(",")], row))
    memory["pinned"] = bool(memory["pinned"])
    memory["reviewed"] = bool(memory["reviewed"])
    return memory


# --- Stored here (the server's, or labb's own) ----------------------------------------

def list_for(user_id: str) -> list[dict]:
    """A user's memories: pinned first, then newest first"""
    conn, lock = db()
    with lock:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM memories WHERE user_id = ? ORDER BY pinned DESC, updated_at DESC, id", (user_id,)
        )
        return [as_dict(row) for row in rows]


def get_for(user_id: str, memory_id: str) -> dict | None:
    conn, lock = db()
    with lock:
        row = conn.execute(f"SELECT {COLUMNS} FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id)).fetchone()
    return as_dict(row) if row else None


def clean(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        raise MemoryError("A memory can't be empty")
    if len(text) > (most := max_chars()):
        raise MemoryError(f"A memory is at most {most} characters; keep it to one fact")
    return text


def _indexed(memory: dict, user_id: str):
    """Embed a new or changed memory; if that fails it's recalled by keywords until the
    server indexes it again at start"""
    conn, lock = db()
    if index is None:
        return
    try:
        index.add(memory, user_id)
    except Exception:
        with lock, conn:
            conn.execute("UPDATE memories SET embed_model = NULL WHERE id = ?", (memory["id"],))


def add_for(user_id: str, text: str, pinned: bool = False, reviewed: bool = True) -> dict:
    """Save a memory, or return the same one if it's saved already (with `existed`). One the
    agent saved isn't `reviewed` until the user keeps it."""

    conn, lock = db()
    text = clean(text)
    with lock, conn:
        row = conn.execute(
            f"SELECT {COLUMNS} FROM memories WHERE user_id = ? AND lower(text) = lower(?)", (user_id, text)
        ).fetchone()
        if row:
            if pinned and not row[2]:
                conn.execute("UPDATE memories SET pinned = 1 WHERE id = ?", (row[0],))
                row = (row[0], row[1], 1, *row[3:])
            return {**as_dict(row), "existed": True}
        if conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ?", (user_id,)).fetchone()[0] >= (most := max_memories()):
            raise MemoryError(f"There are {most} memories already; forget some first")
        memory_id, now = uuid.uuid4().hex[:10], stamp()
        conn.execute(
            "INSERT INTO memories (id, user_id, text, pinned, created_at, updated_at, reviewed) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (memory_id, user_id, text, int(pinned), now, now, int(reviewed)),
        )
    memory = {"id": memory_id, "text": text, "pinned": pinned, "created_at": now, "updated_at": now, "reviewed": reviewed}
    _indexed(memory, user_id)
    return memory


def update_for(user_id: str, memory_id: str, text: str | None = None, pinned: bool | None = None,
               reviewed: bool | None = None) -> dict | None:
    """Change a memory's text, whether it's pinned, or whether the user kept it. None if there's no such memory."""

    conn, lock = db()
    if text is not None:
        text = clean(text)
    with lock, conn:
        changed = conn.execute(
            # Pinning isn't a change of the memory itself (and keeps /memory's numbers)
            "UPDATE memories SET text = coalesce(?, text), pinned = coalesce(?, pinned), reviewed = coalesce(?, reviewed), "
            "updated_at = CASE WHEN ? IS NULL THEN updated_at ELSE ? END WHERE id = ? AND user_id = ?",
            (text, None if pinned is None else int(pinned), None if reviewed is None else int(reviewed), text, stamp(),
             memory_id, user_id),
        ).rowcount
    if not changed:
        return None
    memory = get_for(user_id, memory_id)
    if text is not None:
        _indexed(memory, user_id)
    return memory


def delete_for(user_id: str, memory_id: str | None = None) -> int:
    """Forget one memory, or with no id all of them. Returns how many were forgotten."""

    conn, lock = db()
    with lock, conn:
        where, args = ("user_id = ? AND id = ?", (user_id, memory_id)) if memory_id else ("user_id = ?", (user_id,))
        ids = [row[0] for row in conn.execute(f"SELECT id FROM memories WHERE {where}", args)]
        conn.execute(f"DELETE FROM memories WHERE {where}", args)
    if ids and index is not None:
        try:
            index.remove(ids)
        except Exception:
            pass
    return len(ids)


WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = set(
    "the a an and or but of to in on at for with from by about as is are was were be been am do does did "
    "i me my you your we our it its this that these those what which who how when where why can could "
    "would should will shall have has had not no yes so if then than there here please tell know".split()
)


def words(text: str) -> set[str]:
    return {w for w in WORD.findall(text.lower()) if len(w) > 2 and w not in STOPWORDS}


def keyword_scores(query: str, memories: list[dict]) -> list[tuple[str, float]]:
    """Memories that share words with the query, closest first (the words in common over
    the geometric mean of both sizes)"""

    asked = words(query)
    scored = []
    for memory in memories:
        have = words(memory["text"])
        if common := asked & have:
            scored.append((memory["id"], len(common) / math.sqrt(len(asked) * len(have))))
    return sorted(scored, key=lambda pair: -pair[1])


def recall_for(user_id: str, query: str, k: int = RECALL, min_score: float = MIN_SCORE, pinned: bool = False) -> list[dict]:
    """The memories closest to `query`, best first, each with its `score`. Pinned ones are
    left out (they're in the system prompt already) unless `pinned`."""

    if not query.strip():
        return []
    # Pinned ones waiting for review aren't in the prompt, so they're recalled like the others
    candidates = {m["id"]: m for m in list_for(user_id) if pinned or not m["pinned"] or not m["reviewed"]}
    if not candidates:
        return []
    scored = None
    if index is not None:
        try:
            scored = index.search(user_id, query, k + PROMPT_MEMORIES)
        except Exception:
            pass
    if scored is None:
        scored = keyword_scores(query, list(candidates.values()))
        # Keyword overlap scores lower than meaning does for the same match
        min_score = min(min_score, 0.2)
    found = [{**candidates[i], "score": round(score, 3)} for i, score in scored if i in candidates and score >= min_score]
    return found[:k]


# --- The right place for a run ---------------------------------------------------------

def user_for(config: RunnableConfig | None) -> str | None:
    """Whose memories a run uses here: the account on the server (None in a run that has
    none), the local user without a server. With a server, labb asks it instead."""

    if on_server:
        return (config or {}).get("configurable", {}).get("user_id")
    return LOCAL_USER


def asks_server() -> bool:
    return not on_server and remote.server() is not None


def _remote(method: str, path: str, **kwargs):
    response = httpx.request(method, f"{remote.server()}{path}", headers=remote.headers(), timeout=30, **kwargs)
    if method in ("POST", "PATCH", "DELETE") and not path.endswith("/recall"):
        forget_cached()
    if response.status_code == 401:
        raise MemoryError("The server didn't accept this PC's key; run labb --login")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise MemoryError(detail if isinstance(detail, str) else f"The server said {response.status_code}")
    return response.json() if response.content else None


def all_memories(config: RunnableConfig | None = None) -> list[dict]:
    if asks_server():
        return _remote("GET", "/memories")
    user_id = user_for(config)
    return list_for(user_id) if user_id else []


def remember(config: RunnableConfig | None, text: str, pinned: bool = False, replace_id: str | None = None,
             by_agent: bool = False) -> dict:
    """Save a memory, or with `replace_id` change that one. What the agent saves (`by_agent`)
    waits for the user's review. Raises MemoryError."""

    reviewed = not by_agent
    if replace_id:
        return update(config, replace_id, text=text, pinned=pinned or None, reviewed=False if by_agent else None)
    if asks_server():
        return _remote("POST", "/memories", json={"text": text, "pinned": pinned, "reviewed": reviewed})
    user_id = user_for(config)
    if not user_id:
        raise MemoryError("Memories need a logged-in user")
    return add_for(user_id, text, pinned, reviewed)


def update(config: RunnableConfig | None, memory_id: str, text: str | None = None, pinned: bool | None = None,
           reviewed: bool | None = None) -> dict:
    if asks_server():
        return _remote("PATCH", f"/memories/{memory_id}", json={"text": text, "pinned": pinned, "reviewed": reviewed})
    user_id = user_for(config)
    memory = update_for(user_id, memory_id, text, pinned, reviewed) if user_id else None
    if memory is None:
        raise MemoryError(f"There's no memory {memory_id}")
    return memory


def forget(config: RunnableConfig | None, memory_id: str):
    if asks_server():
        _remote("DELETE", f"/memories/{memory_id}")
        return
    user_id = user_for(config)
    if not user_id or not delete_for(user_id, memory_id):
        raise MemoryError(f"There's no memory {memory_id}")


def recall(config: RunnableConfig | None, query: str, k: int = RECALL, min_score: float = MIN_SCORE,
           pinned: bool = False) -> list[dict]:
    """The memories closest to `query`; [] if they can't be reached (recall never stops a reply)"""

    try:
        if asks_server():
            return _remote("POST", "/memories/recall", json={"query": query, "k": k, "min_score": min_score, "pinned": pinned})
        user_id = user_for(config)
        return recall_for(user_id, query, k, min_score, pinned) if user_id else []
    except (MemoryError, httpx.HTTPError, ValueError):
        return []


_pinned_cache: dict[str, tuple[float, list[dict]]] = {}
_pinned_lock = threading.Lock()


def pinned(config: RunnableConfig | None) -> list[dict]:
    """The pinned memories for the system prompt. The server's are kept for PINNED_SECONDS,
    since the prompt is built for every model call."""

    try:
        if asks_server():
            with _pinned_lock:
                # Never fetched is long ago, not 0: time.monotonic() counts from the machine's start
                at, memories = _pinned_cache.get("server", (float("-inf"), []))
            if time.monotonic() - at > PINNED_SECONDS:
                memories = [m for m in _remote("GET", "/memories") if m["pinned"] and m.get("reviewed", True)]
                with _pinned_lock:
                    _pinned_cache["server"] = (time.monotonic(), memories)
            return memories
        user_id = user_for(config)
        # Not the ones waiting for review: nothing wrong stays in every conversation
        return [m for m in list_for(user_id) if m["pinned"] and m["reviewed"]] if user_id else []
    except (MemoryError, httpx.HTTPError, ValueError):
        return []


def forget_cached():
    """After a change from this PC, ask the server again"""
    with _pinned_lock:
        _pinned_cache.clear()


def prompt_section(config: RunnableConfig | None) -> str | None:
    """The pinned memories as a part of the system prompt"""

    lines, size = [], 0
    for memory in pinned(config)[:PROMPT_MEMORIES]:
        line = f"- [{memory['id']}] {memory['text']}"
        if size + len(line) > PROMPT_CHARS:
            break
        lines.append(line)
        size += len(line)
    if not lines:
        return None
    return "What you remember about the user (pinned memories, with their ids):\n" + "\n".join(lines)


def recall_note(memories: list[dict]) -> str:
    """Recalled memories, as they're added to the user's message for the model"""
    lines = "\n".join(f"- [{m['id']}] {m['text']}" for m in memories)
    return f"\n\n<memories>\nThings you remember about the user that may be relevant (ids in brackets):\n{lines}\n</memories>"
