"""Engines on the server (see engine.py for what one is): people's own, each save an immutable
version, shareable like agents. A shared one shows up for everyone, who can see it, use it or
duplicate it, but only its owner changes it.

A run's engine: the one picked in the chat (chats.engine_id keeps it), else the agent's, else
yours, else the server's default, else the built-in. What can't run here (anything that would run
a command on the server, or Python without a sandbox) can't be picked, and one that's gone or
stops being shared falls back the same way, so a chat always goes on.
"""

import json
import re
import uuid

from labb import db, engine, remote
from labb.chats import conn, lock, now
# Made the server_settings table this uses
from labb.server import providers  # noqa: F401
from labb.server.studio import sandbox
from labb.server import limits

BUILT_IN = "default"
LOCAL = engine.LOCAL
SERVER_DEFAULT_KEY = "engine_default"
MAX_NAME = 60
MAX_DESCRIPTION = 200
MAX_NOTE = 200
MAX_OVERLAY_CHARS = 500_000
MAX_PROMPT_SETS = 50
MAX_PROMPTS = 20
MAX_PROMPT_CHARS = 8_000

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS engines ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL, "
        "shared INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS engines_by_user ON engines (user_id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS engine_versions ("
        "engine_id TEXT NOT NULL, version INTEGER NOT NULL, overlay TEXT NOT NULL, note TEXT NOT NULL, "
        "labb_version TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (engine_id, version))"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS engine_defaults (user_id TEXT PRIMARY KEY, engine_id TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS engine_prompt_sets ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, prompts TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    db.add_column(conn, "chats", "engine_id", "TEXT")

COLUMNS = ("h.id, h.user_id, u.username, h.name, h.description, h.shared, h.version, h.created_at, h.updated_at, v.overlay")
FROM = ("engines h LEFT JOIN users u ON u.id = h.user_id "
        "JOIN engine_versions v ON v.engine_id = h.id AND v.version = h.version")


class EngineError(ValueError):
    """An engine that can't be saved or used, said for the person setting it up"""


def slug_of(name: str) -> str:
    """A name as the terminal types it"""
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")[:48] or "engine"


def web_problems(overlay: dict) -> list[str]:
    """Why the web chat can't use this: it would run something on the server"""

    found = [f"Terminal only: {what}" for what in engine.terminal_only(overlay)]
    if engine.runs_python(overlay) and not sandbox.available():
        found.append("It runs Python, and this server has no sandbox")
    return found


def _built_in(id: str, name: str, description: str, overlay: dict) -> dict:
    return {"id": id, "slug": id, "owner_id": None, "owner": None, "mine": False, "built_in": True, "shared": True,
            "name": name, "description": description, "version": 0, "created_at": None, "updated_at": None,
            "overlay": overlay, "changes": [where for where, _, _ in engine.changes(overlay)], "web_problems": [], "runs_code": False}


# The ones that come with labb, read-only, in the order they're offered (the terminal has the same
# ones from engine.entries)
BUILT_INS = {
    BUILT_IN: _built_in(BUILT_IN, "labb default", "What labb does as it comes.", {}),
    LOCAL: _built_in(LOCAL, "labb local", engine.LOCAL_DESCRIPTION, engine.LOCAL_OVERLAY),
}


def as_dict(row, viewer: str | None = None) -> dict:
    (id, user_id, username, name, description, shared, version, created_at, updated_at, overlay) = row
    overlay = json.loads(overlay)
    return {
        "id": id, "slug": slug_of(name), "owner_id": user_id, "owner": username, "mine": viewer == user_id, "built_in": False,
        "shared": bool(shared), "name": name, "description": description, "version": version,
        "created_at": created_at, "updated_at": updated_at, "overlay": overlay,
        "changes": [where for where, _, _ in engine.changes(overlay)],
        "web_problems": web_problems(overlay), "runs_code": engine.runs_code(overlay),
    }


def get(engine_id: str, viewer: str | None = None) -> dict | None:
    if engine_id in BUILT_INS:
        return BUILT_INS[engine_id]
    with lock:
        row = conn.execute(f"SELECT {COLUMNS} FROM {FROM} WHERE h.id = ?", (engine_id,)).fetchone()
    return as_dict(row, viewer) if row else None


def visible(engine_id: str, user_id: str) -> dict | None:
    found = get(engine_id, user_id)
    return found if found and (found["mine"] or found["shared"]) else None


def list_for(user_id: str) -> list[dict]:
    """The built-in, your engines, then the ones others shared, each newest first"""

    with lock:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM {FROM} WHERE h.user_id = ? OR h.shared = 1 ORDER BY h.user_id != ?, h.updated_at DESC",
            (user_id, user_id),
        ).fetchall()
    return [*BUILT_INS.values(), *(as_dict(row, user_id) for row in rows)]


def _clean_overlay(overlay) -> dict:
    if len(json.dumps(overlay)) > MAX_OVERLAY_CHARS:
        raise EngineError(f"It's over {MAX_OVERLAY_CHARS:,} characters")
    if problems := engine.check(overlay):
        raise EngineError("; ".join(problems[:5]))
    return overlay


def _clean_name(name) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise EngineError("Give the engine a name")
    if len(name) > MAX_NAME:
        raise EngineError(f"The name is over {MAX_NAME} characters")
    return name


def _clean_description(description) -> str:
    description = " ".join(str(description or "").split())
    if len(description) > MAX_DESCRIPTION:
        raise EngineError(f"The description is over {MAX_DESCRIPTION} characters")
    return description


def create(user_id: str, name, description="", overlay: dict | None = None) -> dict:
    name, description, overlay = _clean_name(name), _clean_description(description), _clean_overlay(overlay or {})
    with lock, conn:
        if conn.execute("SELECT COUNT(*) FROM engines WHERE user_id = ?", (user_id,)).fetchone()[0] >= (most := limits.get("engines_per_person")):
            raise EngineError(f"You have {most} engines already; delete one first")
        engine_id, at = uuid.uuid4().hex[:12], now()
        conn.execute("INSERT INTO engines (id, user_id, name, description, shared, version, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, 0, 1, ?, ?)", (engine_id, user_id, name, description, at, at))
        _add_version(engine_id, 1, overlay, "Created", at)
    return get(engine_id, user_id)


def _add_version(engine_id: str, version: int, overlay: dict, note: str, at: str):
    conn.execute("INSERT INTO engine_versions (engine_id, version, overlay, note, labb_version, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (engine_id, version, json.dumps(overlay, ensure_ascii=False), note[:MAX_NOTE], remote.version(), at))
    # The oldest go once there are too many; the current one always stays
    conn.execute("DELETE FROM engine_versions WHERE engine_id = ? AND version <= ?", (engine_id, version - limits.get("engine_versions_kept")))


def update(user_id: str, engine_id: str, fields: dict) -> dict | None:
    """Change your engine; a changed overlay is a new version. None if you have no such engine."""

    current = get(engine_id, user_id)
    if current is None or not current["mine"]:
        return None
    sets, values = {}, []
    if "name" in fields:
        sets["name"] = _clean_name(fields["name"])
    if "description" in fields:
        sets["description"] = _clean_description(fields["description"])
    if "shared" in fields:
        sets["shared"] = int(bool(fields["shared"]))
    overlay = _clean_overlay(fields["overlay"]) if "overlay" in fields else None
    at = now()
    with lock, conn:
        if overlay is not None and overlay != current["overlay"]:
            sets["version"] = conn.execute("SELECT version FROM engines WHERE id = ?", (engine_id,)).fetchone()[0] + 1
            _add_version(engine_id, sets["version"], overlay, str(fields.get("note") or "Saved"), at)
        if sets:
            values = [*sets.values(), at, engine_id]
            conn.execute(f"UPDATE engines SET {', '.join(f'{k} = ?' for k in sets)}, updated_at = ? WHERE id = ?", values)
    return get(engine_id, user_id)


def delete(user_id: str, engine_id: str) -> bool:
    """Delete your engine; the chats and agents that used it fall back as the top of this file says"""
    with lock, conn:
        if not conn.execute("DELETE FROM engines WHERE id = ? AND user_id = ?", (engine_id, user_id)).rowcount:
            return False
        conn.execute("DELETE FROM engine_versions WHERE engine_id = ?", (engine_id,))
        conn.execute("DELETE FROM engine_defaults WHERE engine_id = ?", (engine_id,))
    return True


def duplicate(user_id: str, engine_id: str) -> dict | None:
    """Your own copy of an engine you can see, the built-in too"""

    found = visible(engine_id, user_id)
    if found is None:
        return None
    blank = found["id"] == BUILT_IN
    name = "My engine" if blank else f"{found['name']} (copy)"[:MAX_NAME]
    return create(user_id, name, "" if blank else found["description"], found["overlay"])


def versions(user_id: str, engine_id: str) -> list[dict] | None:
    """Each saved version of an engine you can see, newest first"""

    if engine_id in BUILT_INS or visible(engine_id, user_id) is None:
        return None
    with lock:
        rows = conn.execute("SELECT version, overlay, note, labb_version, created_at FROM engine_versions "
                            "WHERE engine_id = ? ORDER BY version DESC", (engine_id,)).fetchall()
    return [{"version": v, "overlay": json.loads(o), "note": n, "labb_version": l, "created_at": at} for v, o, n, l, at in rows]


def restore(user_id: str, engine_id: str, version: int) -> dict | None:
    """Make an old version the current one, as a new version: history only grows"""

    current = get(engine_id, user_id)
    if current is None or not current["mine"]:
        return None
    with lock:
        row = conn.execute("SELECT overlay FROM engine_versions WHERE engine_id = ? AND version = ?", (engine_id, version)).fetchone()
    if row is None:
        raise EngineError(f"There's no version {version}")
    return update(user_id, engine_id, {"overlay": json.loads(row[0]), "note": f"Restored version {version}"})


def to_run(found: dict | None) -> engine.Engine | None:
    """What a run uses of an engine: the built-in, or None if it can't run here"""

    if found is None or found["web_problems"]:
        return None
    if found["id"] == BUILT_IN:
        return engine.DEFAULT
    if found["id"] == LOCAL:
        return engine.LOCAL_ENGINE
    return engine.resolve(found["overlay"], found["slug"], found["description"])


def for_owner(engine_id: str | None, owner_id: str) -> engine.Engine | None:
    """An agent's engine, as its owner sees it"""
    return to_run(visible(engine_id, owner_id)) if engine_id else None


def picked(engine_id: str, user_id: str) -> engine.Engine:
    """An engine someone chose for a chat; raises EngineError if they can't use it"""

    found = visible(engine_id, user_id)
    if found is None:
        raise EngineError("There's no such engine")
    if found["web_problems"]:
        raise EngineError(f"{found['name']} can't run here. " + " ".join(found["web_problems"]))
    return to_run(found)


def draft(overlay: dict, name: str = "draft") -> engine.Engine:
    """An engine as an editor has it, not saved, for trying it; raises EngineError if it can't run here"""

    overlay = _clean_overlay(overlay)
    if problems := web_problems(overlay):
        raise EngineError(" ".join(problems))
    return engine.resolve(overlay, name)


def of_chat(thread_id: str) -> str | None:
    with lock:
        row = conn.execute("SELECT engine_id FROM chats WHERE thread_id = ?", (thread_id,)).fetchone()
    return row[0] if row else None


def remember(thread_id: str, engine_id: str):
    with lock, conn:
        conn.execute("UPDATE chats SET engine_id = ? WHERE thread_id = ?", (engine_id, thread_id))


def my_default(user_id: str) -> str | None:
    with lock:
        row = conn.execute("SELECT engine_id FROM engine_defaults WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else None


def set_my_default(user_id: str, engine_id: str | None):
    if engine_id is not None:
        picked(engine_id, user_id)
    with lock, conn:
        if engine_id is None:
            conn.execute("DELETE FROM engine_defaults WHERE user_id = ?", (user_id,))
        else:
            conn.execute("INSERT INTO engine_defaults (user_id, engine_id) VALUES (?, ?) "
                         "ON CONFLICT (user_id) DO UPDATE SET engine_id = excluded.engine_id", (user_id, engine_id))


def server_default() -> str | None:
    with lock:
        row = conn.execute("SELECT value FROM server_settings WHERE key = ?", (SERVER_DEFAULT_KEY,)).fetchone()
    return row[0] if row else None


def set_server_default(admin_id: str, engine_id: str | None):
    """An admin's choice for everyone, so it has to be one everyone can use"""

    with lock, conn:
        if engine_id is None:
            conn.execute("DELETE FROM server_settings WHERE key = ?", (SERVER_DEFAULT_KEY,))
    if engine_id is None:
        return
    found = visible(engine_id, admin_id)
    if found is None:
        raise EngineError("There's no such engine")
    if found["web_problems"]:
        raise EngineError(f"{found['name']} can't run here. " + " ".join(found["web_problems"]))
    if not found["built_in"] and not found["shared"]:
        raise EngineError("Share it first: everyone has to be able to see what they're using")
    with lock, conn:
        conn.execute("INSERT INTO server_settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                     (SERVER_DEFAULT_KEY, engine_id))


def for_run(user_id: str, thread_id: str | None, requested: str | None, has_agent_engine: bool) -> engine.Engine | None:
    """A chat's engine, as the top of this file says; None to leave it to the agent's. `requested`
    must already have been checked with `picked`."""

    if requested:
        return picked(requested, user_id)
    if thread_id and (kept := of_chat(thread_id)) and (run := to_run(visible(kept, user_id))):
        return run
    if has_agent_engine:
        return None
    for fallback in (my_default(user_id), server_default()):
        if fallback and (run := to_run(visible(fallback, user_id))):
            return run
    return None


def for_terminal(user_id: str) -> list[dict]:
    """What the terminal fetches: every engine you can see, by a name it can type"""

    seen, found = set(), []
    for item in list_for(user_id):
        if not item["built_in"] and item["slug"] not in seen:
            seen.add(item["slug"])
            found.append({"slug": item["slug"], "description": item["description"], "overlay": item["overlay"]})
    return found


# --- Prompt sets, for comparing engines ------------------------------------------------

def prompt_sets(user_id: str) -> list[dict]:
    with lock:
        rows = conn.execute("SELECT id, name, prompts, updated_at FROM engine_prompt_sets WHERE user_id = ? ORDER BY updated_at DESC",
                            (user_id,)).fetchall()
    return [{"id": i, "name": n, "prompts": json.loads(p), "updated_at": at} for i, n, p, at in rows]


def save_prompt_set(user_id: str, set_id: str | None, name, prompts) -> dict:
    name = _clean_name(name)
    prompts = [str(p).strip() for p in prompts or [] if str(p).strip()]
    if not prompts:
        raise EngineError("Add a prompt first")
    if len(prompts) > MAX_PROMPTS or any(len(p) > MAX_PROMPT_CHARS for p in prompts):
        raise EngineError(f"At most {MAX_PROMPTS} prompts of {MAX_PROMPT_CHARS:,} characters")
    at = now()
    with lock, conn:
        if set_id:
            if not conn.execute("UPDATE engine_prompt_sets SET name = ?, prompts = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                                (name, json.dumps(prompts), at, set_id, user_id)).rowcount:
                raise EngineError("There's no such set")
        else:
            if conn.execute("SELECT COUNT(*) FROM engine_prompt_sets WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_PROMPT_SETS:
                raise EngineError(f"You have {MAX_PROMPT_SETS} sets already; delete one first")
            set_id = uuid.uuid4().hex[:12]
            conn.execute("INSERT INTO engine_prompt_sets (id, user_id, name, prompts, updated_at) VALUES (?, ?, ?, ?, ?)",
                         (set_id, user_id, name, json.dumps(prompts), at))
    return {"id": set_id, "name": name, "prompts": prompts, "updated_at": at}


def delete_prompt_set(user_id: str, set_id: str) -> bool:
    with lock, conn:
        return conn.execute("DELETE FROM engine_prompt_sets WHERE id = ? AND user_id = ?", (set_id, user_id)).rowcount > 0
