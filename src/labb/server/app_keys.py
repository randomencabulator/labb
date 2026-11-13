"""Apps that use labb: a key per app, and the small API it opens (`/app-api`).

An app's owner turns on what it may use, on its page (`SCOPES`); the first time, the app gets a
key of its own (`lba_…`), which labb gives the spawner to put in its environment as LABB_API_KEY,
next to LABB_URL (labb's address from the apps' network). The key is kept here as a hash only.

An app's key opens `/app-api` and nothing else: auth_routes only takes `lb_` keys, so the rest of
labb's API refuses it, and each `/app-api` route checks its scope. What the app asks runs as its
owner, and counts in their usage as the source "app". The key goes when the app is removed, when
everything is turned off, or when a new one is made; a key's owner disabled stops it too.

The key is the app's server's: an app that sent it to browsers would hand it to anyone who opens
the app. The starters' helpers (labb.py, labb.js) are for its server side.
"""

import json
import os
from datetime import datetime, timedelta
import re
import secrets
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from labb import llm, memories
from labb.agent import chat_agent
from labb.chats import conn, lock
from labb.runner import run_reply
from labb.server import agents, auth, documents, limits, providers
from labb.server.auth_routes import bearer_key
from labb.tools import ToolRules

KEY_PREFIX = "lba_"
# What an app may be given, and what each opens
SCOPES = {
    "ask": "Ask labb's models (POST /app-api/ask)",
    "agents": "Run your agents (GET /app-api/agents, POST /app-api/agents/{id}/ask)",
    "documents": "Search your documents (POST /app-api/documents/search)",
    "memories": "Read your memories (GET /app-api/memories, POST /app-api/memories/recall)",
    "remember": "Save memories (POST /app-api/memories)",
}
# labb's address as the apps see it, on their network
LABB_URL = os.getenv("APPS_LABB_URL", "http://labb-web:8000").rstrip("/")
# A question an app asks gets this long, and these many model calls (an agent's tools count)
ASK_SECONDS = 300
CONVERSATION = re.compile(r"[A-Za-z0-9_-]{1,64}")

with lock, conn:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_keys ("
        "id TEXT PRIMARY KEY, app_id TEXT NOT NULL, user_id TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE, "
        "shown TEXT NOT NULL, scopes TEXT NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS app_keys_by_app ON app_keys (app_id)")


def _info(row) -> dict:
    key_id, app_id, user_id, shown, scopes, created_at, last_used_at = row
    return {"id": key_id, "app_id": app_id, "user_id": user_id, "shown": shown, "scopes": json.loads(scopes),
            "created_at": created_at, "last_used_at": last_used_at}


COLUMNS = "id, app_id, user_id, shown, scopes, created_at, last_used_at"


def current(app_id: str, user_id: str) -> dict | None:
    """The app's key (not the key itself: only its start), if it has one"""
    with lock:
        row = conn.execute(f"SELECT {COLUMNS} FROM app_keys WHERE app_id = ? AND user_id = ? AND revoked_at IS NULL",
                           (app_id, user_id)).fetchone()
    return _info(row) if row else None


def checked_scopes(scopes: list[str]) -> list[str]:
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise HTTPException(400, f"No such scope: {', '.join(unknown)}")
    return [s for s in SCOPES if s in scopes]


def make(app_id: str, user_id: str, scopes: list[str]) -> str:
    """A new key for the app (the one it had goes): the key itself, only ever given out now"""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    with lock, conn:
        conn.execute("UPDATE app_keys SET revoked_at = ? WHERE app_id = ? AND revoked_at IS NULL", (auth.stamp(auth.now()), app_id))
        conn.execute(
            "INSERT INTO app_keys (id, app_id, user_id, key_hash, shown, scopes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), app_id, user_id, auth.digest(key), key[:len(KEY_PREFIX) + 6], json.dumps(scopes),
             auth.stamp(auth.now())),
        )
    return key


def set_scopes(app_id: str, user_id: str, scopes: list[str]):
    with lock, conn:
        conn.execute("UPDATE app_keys SET scopes = ? WHERE app_id = ? AND user_id = ? AND revoked_at IS NULL",
                     (json.dumps(scopes), app_id, user_id))


def revoke(app_id: str) -> bool:
    with lock, conn:
        return conn.execute("UPDATE app_keys SET revoked_at = ? WHERE app_id = ? AND revoked_at IS NULL",
                            (auth.stamp(auth.now()), app_id)).rowcount > 0


def caller_of(key: str) -> dict | None:
    """Who an app key is: its app, owner and scopes; None if it isn't one, or is revoked, or its owner can't log in"""
    if not key.startswith(KEY_PREFIX):
        return None
    with lock:
        row = conn.execute(f"SELECT {COLUMNS} FROM app_keys WHERE key_hash = ? AND revoked_at IS NULL",
                           (auth.digest(key),)).fetchone()
    if row is None:
        return None
    found = _info(row)
    user = auth.get_user(found["user_id"])
    if user is None or user["disabled"]:
        return None
    if found["last_used_at"] is None or auth.now() - datetime.fromisoformat(found["last_used_at"]) > timedelta(minutes=1):
        with lock, conn:
            conn.execute("UPDATE app_keys SET last_used_at = ? WHERE id = ?", (auth.stamp(auth.now()), found["id"]))
    return {**found, "user": user}


# The API apps call

router = APIRouter(prefix="/app-api")


def app_caller(request: Request) -> dict:
    caller = caller_of(bearer_key(request) or "")
    if caller is None:
        raise HTTPException(401, "This needs an app's key: LABB_API_KEY, in an app that was given labb to use")
    return caller


def scope(name: str):
    def needed(caller: dict = Depends(app_caller)) -> dict:
        if name not in caller["scopes"]:
            raise HTTPException(403, f"This app wasn't given that: turn on \"{SCOPES[name]}\" on its page")
        return caller
    return needed


class Ask(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    # "<provider>:<model>"; the server's default (or the agent's) if not given
    model: str | None = None
    # Go on with an earlier conversation: its `conversation` from an answer
    conversation: str | None = None


class Search(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(5, ge=1, le=20)


class Remember(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def ask(caller: dict, body: Ask, agent: dict | None = None) -> dict:
    """One message, answered as the app's owner: a plain answer (no tools), or an agent's (with its tools)"""
    user = caller["user"]
    if body.conversation is not None and not CONVERSATION.fullmatch(body.conversation):
        raise HTTPException(400, "conversation is letters, digits, - and _, up to 64")
    conversation = body.conversation or uuid.uuid4().hex
    thread_id = f"appk-{caller['app_id']}-{(agent or {}).get('id', 'ask')}-{conversation}"
    configurable = {"user_id": user["id"], "task": {"id": caller["app_id"], "name": "app"}}
    if agent:
        configurable["agent"] = agents.for_run(agent)
        if agent["model"]:
            configurable["model"] = agent["model"]
    else:
        configurable["tools"] = ToolRules.of(allow=())
    if body.model:
        try:
            configurable["model"] = llm.load(body.model).spec
        except ValueError as e:
            raise HTTPException(400, str(e))
    if spec := configurable.get("model"):
        if reason := providers.why_not(spec, user["role"]):
            raise HTTPException(403, reason)
    events, stop = [], threading.Event()
    timer = threading.Timer(ASK_SECONDS, stop.set)
    timer.daemon = True
    timer.start()
    try:
        run_reply(thread_id, body.message, stop, events.append, configurable=configurable, name=False,
                  recursion_limit=limits.recursion(limits.get("task_model_calls")), source="app")
    finally:
        timer.cancel()
    if error := next((e["message"] for e in events if e["type"] == "error"), None):
        raise HTTPException(502, error)
    end = next((e for e in reversed(events) if e["type"] in ("done", "stopped")), {})
    messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])
    text = messages[-1].text if messages and messages[-1].type == "ai" else ""
    stats = end.get("stats") or {}
    return {"text": text, "conversation": conversation, "model": stats.get("model"), "stopped": end.get("type") == "stopped",
            "input_tokens": stats.get("input_tokens", 0), "output_tokens": stats.get("output_tokens", 0)}


@router.get("/me")
def me(caller: dict = Depends(app_caller)):
    """Which app this is, and what it was given"""
    return {"app_id": caller["app_id"], "scopes": caller["scopes"]}


@router.post("/ask")
def ask_model(body: Ask, caller: dict = Depends(scope("ask"))):
    """A model's answer to `message` (no tools); `conversation` from an answer goes on with it"""
    return ask(caller, body)


@router.get("/agents")
def list_agents(caller: dict = Depends(scope("agents"))):
    return [{"id": a["id"], "name": a["name"], "description": a.get("description", "")}
            for a in agents.list_for(caller["user"]["id"])]


@router.post("/agents/{agent_id}/ask")
def ask_agent(agent_id: str, body: Ask, caller: dict = Depends(scope("agents"))):
    """One of the owner's agents (or one shared with them) answers, with its own tools"""
    agent = agents.visible(agent_id, caller["user"]["id"])
    if agent is None:
        raise HTTPException(404, "No such agent")
    return ask(caller, body, agent)


@router.post("/documents/search")
def search_documents(body: Search, caller: dict = Depends(scope("documents"))):
    return documents.search(caller["user"]["id"], body.query, body.k)


@router.get("/memories")
def list_memories(caller: dict = Depends(scope("memories"))):
    return memories.list_for(caller["user"]["id"])


@router.post("/memories/recall")
def recall(body: Search, caller: dict = Depends(scope("memories"))):
    return memories.recall_for(caller["user"]["id"], body.query, body.k, memories.MIN_SCORE, False)


@router.post("/memories", status_code=201)
def remember(body: Remember, caller: dict = Depends(scope("remember"))):
    try:
        return memories.add_for(caller["user"]["id"], body.text, False, False)
    except memories.MemoryError as e:
        raise HTTPException(400, str(e))
