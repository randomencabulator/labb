"""Studio's tables, in the server's database.

- `workflows`: the draft being edited (JSON), the published version's number, sharing, and the
  automatic triggers' state (the webhook token's hash, the next scheduled time).
- `workflow_versions`: each published definition, never changed; runs say which one they used.
- `workflow_runs` and `workflow_steps`: every run, and every step of it with its input, output,
  branch, tokens and time, written as the step ends (so a run can pause and go on).
- `actions`: custom steps, HTTP requests (made by admins) and saved prompts.
- `secrets`: values HTTP actions use, encrypted (secrets.py), managed by admins.
"""

import json
import uuid
from datetime import datetime, timezone

from labb.chats import conn, lock


with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS workflows ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, emoji TEXT NOT NULL, description TEXT NOT NULL, "
        "draft TEXT NOT NULL, published_version INTEGER, shared INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1, "
        "webhook_hash TEXT UNIQUE, next_run TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS workflows_by_user ON workflows (user_id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS workflow_versions ("
        "workflow_id TEXT NOT NULL, version INTEGER NOT NULL, definition TEXT NOT NULL, published_at TEXT NOT NULL, "
        "PRIMARY KEY (workflow_id, version))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS workflow_runs ("
        "id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, user_id TEXT NOT NULL, version INTEGER, definition TEXT NOT NULL, "
        "trigger TEXT NOT NULL, inputs TEXT NOT NULL, status TEXT NOT NULL, error TEXT, result TEXT, "
        "queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, resume_at TEXT, "
        "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS workflow_runs_by_workflow ON workflow_runs (workflow_id, queued_at)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS workflow_steps ("
        "run_id TEXT NOT NULL, node_id TEXT NOT NULL, key TEXT NOT NULL, status TEXT NOT NULL, "
        "input TEXT, output TEXT, handle TEXT, error TEXT, trace TEXT, attempt INTEGER NOT NULL DEFAULT 0, "
        "started_at TEXT, finished_at TEXT, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (run_id, node_id))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS actions ("
        "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, emoji TEXT NOT NULL, "
        "description TEXT NOT NULL, config TEXT NOT NULL, shared INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS secrets ("
        "name TEXT PRIMARY KEY, value TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )


def now() -> str:
    """UTC, to the microsecond, so runs queued in the same second keep their order"""
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _loads(text, default=None):
    return json.loads(text) if text else default


# --- Workflows -----------------------------------------------------------------------

WORKFLOW_COLUMNS = ("w.id, w.user_id, u.username, w.name, w.emoji, w.description, w.draft, w.published_version, "
                    "w.shared, w.enabled, w.webhook_hash, w.next_run, w.created_at, w.updated_at")
WORKFLOW_FROM = "workflows w LEFT JOIN users u ON u.id = w.user_id"


def _workflow(row, viewer: str | None = None) -> dict:
    (wid, user_id, username, name, emoji, description, draft, published, shared, enabled, webhook_hash,
     next_run, created_at, updated_at) = row
    return {"id": wid, "owner_id": user_id, "owner": username, "mine": viewer is None or viewer == user_id,
            "name": name, "emoji": emoji, "description": description, "draft": _loads(draft, {}),
            "published_version": published, "shared": bool(shared), "enabled": bool(enabled),
            "webhook": webhook_hash is not None, "next_run": next_run, "created_at": created_at, "updated_at": updated_at}


def get_workflow(workflow_id: str, viewer: str | None = None) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {WORKFLOW_COLUMNS} FROM {WORKFLOW_FROM} WHERE w.id = ?", (workflow_id,)).fetchone()
    return _workflow(row, viewer) if row else None


def visible_workflow(workflow_id: str, user_id: str) -> dict | None:
    """A workflow this user can see: their own, or a shared one"""

    workflow = get_workflow(workflow_id, user_id)
    return workflow if workflow and (workflow["mine"] or workflow["shared"]) else None


def list_workflows(user_id: str) -> list[dict]:
    """Yours and the shared ones, most recently changed first"""

    with lock:
        rows = conn.execute(
            f"SELECT {WORKFLOW_COLUMNS} FROM {WORKFLOW_FROM} WHERE w.user_id = ? OR w.shared = 1 ORDER BY w.updated_at DESC",
            (user_id,),
        ).fetchall()
    return [_workflow(row, user_id) for row in rows]


def count_workflows(user_id: str) -> int:
    with lock:
        return conn.execute("SELECT COUNT(*) FROM workflows WHERE user_id = ?", (user_id,)).fetchone()[0]


def insert_workflow(user_id: str, name: str, emoji: str, description: str, draft: dict, shared: bool) -> str:
    workflow_id, at = new_id(), now()
    with lock, conn:
        conn.execute(
            "INSERT INTO workflows (id, user_id, name, emoji, description, draft, shared, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (workflow_id, user_id, name, emoji, description, json.dumps(draft), int(shared), at, at),
        )
    return workflow_id


def update_workflow(workflow_id: str, **fields):
    """Change columns (`draft` as a dict); also marks it as just changed"""

    if "draft" in fields:
        fields["draft"] = json.dumps(fields["draft"])
    for flag in ("shared", "enabled"):
        if flag in fields:
            fields[flag] = int(fields[flag])
    fields["updated_at"] = now()
    with lock, conn:
        conn.execute(f"UPDATE workflows SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     (*fields.values(), workflow_id))


def set_workflow_state(workflow_id: str, **fields):
    """Change the triggers' state (next_run, webhook_hash) without counting it as an edit"""

    with lock, conn:
        conn.execute(f"UPDATE workflows SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     (*fields.values(), workflow_id))


def delete_workflow(workflow_id: str) -> list[str]:
    """Delete a workflow with its versions and runs; returns the deleted runs' ids"""

    with lock, conn:
        runs = [row[0] for row in conn.execute("SELECT id FROM workflow_runs WHERE workflow_id = ?", (workflow_id,))]
        conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
        conn.execute("DELETE FROM workflow_versions WHERE workflow_id = ?", (workflow_id,))
        conn.execute("DELETE FROM workflow_runs WHERE workflow_id = ?", (workflow_id,))
        conn.executemany("DELETE FROM workflow_steps WHERE run_id = ?", [(r,) for r in runs])
    return runs


def workflow_by_webhook(token_hash: str) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {WORKFLOW_COLUMNS} FROM {WORKFLOW_FROM} WHERE w.webhook_hash = ?", (token_hash,)).fetchone()
    return _workflow(row) if row else None


def scheduled_due(at: str) -> list[dict]:
    with lock:
        rows = conn.execute(
            f"SELECT {WORKFLOW_COLUMNS} FROM {WORKFLOW_FROM} WHERE w.enabled = 1 AND w.next_run IS NOT NULL AND w.next_run <= ?",
            (at,),
        ).fetchall()
    return [_workflow(row) for row in rows]


def published_by_owner(user_id: str) -> list[dict]:
    with lock:
        rows = conn.execute(
            f"SELECT {WORKFLOW_COLUMNS} FROM {WORKFLOW_FROM} WHERE w.user_id = ? AND w.enabled = 1 AND w.published_version IS NOT NULL",
            (user_id,),
        ).fetchall()
    return [_workflow(row) for row in rows]


# --- Versions ------------------------------------------------------------------------

def add_version(workflow_id: str, definition: dict) -> int:
    with lock, conn:
        latest = conn.execute("SELECT MAX(version) FROM workflow_versions WHERE workflow_id = ?", (workflow_id,)).fetchone()[0]
        version = (latest or 0) + 1
        conn.execute("INSERT INTO workflow_versions (workflow_id, version, definition, published_at) VALUES (?, ?, ?, ?)",
                     (workflow_id, version, json.dumps(definition), now()))
        conn.execute("UPDATE workflows SET published_version = ? WHERE id = ?", (version, workflow_id))
    return version


def get_version(workflow_id: str, version: int) -> dict | None:
    with lock:
        row = conn.execute("SELECT definition FROM workflow_versions WHERE workflow_id = ? AND version = ?",
                           (workflow_id, version)).fetchone()
    return json.loads(row[0]) if row else None


def list_versions(workflow_id: str) -> list[dict]:
    with lock:
        rows = conn.execute("SELECT version, published_at FROM workflow_versions WHERE workflow_id = ? ORDER BY version DESC",
                            (workflow_id,)).fetchall()
    return [{"version": v, "published_at": at} for v, at in rows]


# --- Runs and steps ------------------------------------------------------------------

RUN_COLUMNS = ("id, workflow_id, user_id, version, definition, trigger, inputs, status, error, result, queued_at, "
               "started_at, finished_at, resume_at, input_tokens, output_tokens")
STEP_COLUMNS = ("node_id, key, status, input, output, handle, error, trace, attempt, started_at, finished_at, "
                "input_tokens, output_tokens")


def _run(row) -> dict:
    (rid, workflow_id, user_id, version, definition, trigger, inputs, status, error, result, queued_at,
     started_at, finished_at, resume_at, input_tokens, output_tokens) = row
    return {"id": rid, "workflow_id": workflow_id, "user_id": user_id, "version": version,
            "definition": _loads(definition, {}), "trigger": trigger, "inputs": _loads(inputs, {}), "status": status,
            "error": error, "result": _loads(result), "queued_at": queued_at, "started_at": started_at,
            "finished_at": finished_at, "resume_at": resume_at, "input_tokens": input_tokens, "output_tokens": output_tokens}


def _step(row) -> dict:
    (node_id, key, status, input_, output, handle, error, trace, attempt, started_at, finished_at,
     input_tokens, output_tokens) = row
    return {"node_id": node_id, "key": key, "status": status, "input": _loads(input_), "output": _loads(output),
            "handle": handle, "error": error, "trace": _loads(trace, []), "attempt": attempt, "started_at": started_at,
            "finished_at": finished_at, "input_tokens": input_tokens, "output_tokens": output_tokens}


def insert_run(workflow_id: str, user_id: str, version: int | None, definition: dict, trigger: str, inputs: dict) -> str:
    run_id = new_id()
    with lock, conn:
        conn.execute(
            "INSERT INTO workflow_runs (id, workflow_id, user_id, version, definition, trigger, inputs, status, queued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)",
            (run_id, workflow_id, user_id, version, json.dumps(definition), trigger, json.dumps(inputs), now()),
        )
    return run_id


def get_run(run_id: str) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {RUN_COLUMNS} FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
    return _run(row) if row else None


def update_run(run_id: str, **fields):
    if "result" in fields:
        fields["result"] = json.dumps(fields["result"])
    with lock, conn:
        conn.execute(f"UPDATE workflow_runs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     (*fields.values(), run_id))


def claim_run(run_id: str, from_statuses: tuple[str, ...]) -> bool:
    """Mark a run as running if it's in one of these states (only one worker gets it)"""

    marks = ", ".join("?" for _ in from_statuses)
    with lock, conn:
        return conn.execute(
            f"UPDATE workflow_runs SET status = 'running', started_at = COALESCE(started_at, ?), resume_at = NULL "
            f"WHERE id = ? AND status IN ({marks})",
            (now(), run_id, *from_statuses),
        ).rowcount > 0


def kept_runs() -> int:
    # Imported here: limits needs the providers, which need the secrets, which need this module
    from labb.server import limits
    return limits.get("workflow_runs_kept")


def list_runs(workflow_id: str | None = None, user_id: str | None = None, limit: int | None = None) -> list[dict]:
    clauses, params = [], []
    if workflow_id:
        clauses.append("workflow_id = ?")
        params.append(workflow_id)
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    # Before taking the lock: the first read of the limits takes it too
    limit = limit or kept_runs()
    with lock:
        rows = conn.execute(f"SELECT {RUN_COLUMNS} FROM workflow_runs{where} ORDER BY queued_at DESC LIMIT ?",
                            (*params, limit)).fetchall()
    return [_run(row) for row in rows]


def runs_in(statuses: tuple[str, ...]) -> list[dict]:
    marks = ", ".join("?" for _ in statuses)
    with lock:
        rows = conn.execute(f"SELECT {RUN_COLUMNS} FROM workflow_runs WHERE status IN ({marks}) ORDER BY queued_at",
                            statuses).fetchall()
    return [_run(row) for row in rows]


def active_runs(user_id: str) -> int:
    with lock:
        return conn.execute("SELECT COUNT(*) FROM workflow_runs WHERE user_id = ? AND status IN ('queued', 'running')",
                            (user_id,)).fetchone()[0]


def old_runs(workflow_id: str, keep: int | None = None) -> list[str]:
    """Finished runs beyond the latest `keep`, to delete"""

    with lock:
        rows = conn.execute(
            "SELECT id FROM workflow_runs WHERE workflow_id = ? AND status NOT IN ('queued', 'running', 'waiting') "
            "ORDER BY queued_at DESC", (workflow_id,),
        ).fetchall()
    return [row[0] for row in rows[keep or kept_runs():]]


def delete_runs(run_ids: list[str]):
    with lock, conn:
        conn.executemany("DELETE FROM workflow_runs WHERE id = ?", [(r,) for r in run_ids])
        conn.executemany("DELETE FROM workflow_steps WHERE run_id = ?", [(r,) for r in run_ids])


def steps(run_id: str) -> list[dict]:
    with lock:
        rows = conn.execute(f"SELECT {STEP_COLUMNS} FROM workflow_steps WHERE run_id = ? ORDER BY started_at, key",
                            (run_id,)).fetchall()
    return [_step(row) for row in rows]


def save_step(run_id: str, node_id: str, key: str, **fields):
    """Write a step's state, adding it the first time"""

    for name in ("input", "output", "trace"):
        if name in fields:
            fields[name] = json.dumps(fields[name], ensure_ascii=False, default=str)
    columns = ["run_id", "node_id", "key", *fields]
    updates = ", ".join(f"{k} = excluded.{k}" for k in ["key", *fields])
    with lock, conn:
        conn.execute(
            f"INSERT INTO workflow_steps ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT (run_id, node_id) DO UPDATE SET {updates}",
            (run_id, node_id, key, *fields.values()),
        )


def add_run_tokens(run_id: str, input_tokens: int, output_tokens: int):
    with lock, conn:
        conn.execute("UPDATE workflow_runs SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ? WHERE id = ?",
                     (input_tokens, output_tokens, run_id))


# --- Actions -------------------------------------------------------------------------

ACTION_COLUMNS = "a.id, a.user_id, u.username, a.kind, a.name, a.emoji, a.description, a.config, a.shared, a.created_at, a.updated_at"
ACTION_FROM = "actions a LEFT JOIN users u ON u.id = a.user_id"


def _action(row, viewer: str | None = None) -> dict:
    aid, user_id, username, kind, name, emoji, description, config, shared, created_at, updated_at = row
    return {"id": aid, "owner_id": user_id, "owner": username, "mine": viewer is None or viewer == user_id, "kind": kind,
            "name": name, "emoji": emoji, "description": description, "config": _loads(config, {}),
            "shared": bool(shared), "created_at": created_at, "updated_at": updated_at}


def get_action(action_id: str, viewer: str | None = None) -> dict | None:
    with lock:
        row = conn.execute(f"SELECT {ACTION_COLUMNS} FROM {ACTION_FROM} WHERE a.id = ?", (action_id,)).fetchone()
    return _action(row, viewer) if row else None


def list_actions(user_id: str) -> list[dict]:
    """Yours and the shared ones (HTTP actions are always shared: admins make them for everyone)"""

    with lock:
        rows = conn.execute(f"SELECT {ACTION_COLUMNS} FROM {ACTION_FROM} WHERE a.user_id = ? OR a.shared = 1 ORDER BY a.name",
                            (user_id,)).fetchall()
    return [_action(row, user_id) for row in rows]


def count_actions() -> int:
    with lock:
        return conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]


def insert_action(user_id: str, kind: str, name: str, emoji: str, description: str, config: dict, shared: bool) -> str:
    action_id, at = new_id(), now()
    with lock, conn:
        conn.execute(
            "INSERT INTO actions (id, user_id, kind, name, emoji, description, config, shared, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (action_id, user_id, kind, name, emoji, description, json.dumps(config), int(shared), at, at),
        )
    return action_id


def update_action(action_id: str, **fields):
    if "config" in fields:
        fields["config"] = json.dumps(fields["config"])
    if "shared" in fields:
        fields["shared"] = int(fields["shared"])
    fields["updated_at"] = now()
    with lock, conn:
        conn.execute(f"UPDATE actions SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), action_id))


def delete_action(action_id: str) -> bool:
    with lock, conn:
        return conn.execute("DELETE FROM actions WHERE id = ?", (action_id,)).rowcount > 0


# --- Secrets (values encrypted by secrets.py) ----------------------------------------

def list_secret_names() -> list[dict]:
    with lock:
        rows = conn.execute("SELECT name, created_at, updated_at FROM secrets ORDER BY name").fetchall()
    return [{"name": n, "created_at": c, "updated_at": u} for n, c, u in rows]


def secret_values() -> dict[str, str]:
    with lock:
        return dict(conn.execute("SELECT name, value FROM secrets").fetchall())


def put_secret(name: str, value: str, user_id: str):
    at = now()
    with lock, conn:
        conn.execute(
            "INSERT INTO secrets (name, value, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (name, value, user_id, at, at),
        )


def delete_secret(name: str) -> bool:
    with lock, conn:
        return conn.execute("DELETE FROM secrets WHERE name = ?", (name,)).rowcount > 0

