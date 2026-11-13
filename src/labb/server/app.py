import asyncio
import base64
import binascii
import json
import os
import queue
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage, messages_from_dict
from pydantic import BaseModel, Field

# Importing agent loads .env and builds the graph; chat_agent saves each conversation by thread_id
from labb.agent import chat_agent as agent
from labb import inspection
from labb.runner import (attachments_of, can_take_back, run_reply, split_user_message, thinking_of, user_message, user_turns,
                             with_attachments)
from labb.export import file_name, to_markdown
from labb.server.auth_routes import bearer_key, current_user
from labb.server import auth
from labb import chats
from labb import connections as app_connections
from labb.server import gateway
from labb.server import health as health_checks
from labb.server import system as system_usage
from labb.server import services as service_settings
from labb.server import install
from labb.server import prices
from labb.server import live
from labb import llm
from labb import memories
from labb import skills
from labb.server import agents
from labb.server import agents_routes
from labb.server import defaults, defaults_routes
from labb.server import app_keys, app_tools, apps_routes, artifact_tools, artifacts_routes
from labb.server import auth_routes
from labb.server import browser_routes, browser_sites
from labb.server import connections
from labb.server import connections_routes
from labb.server import documents
from labb.server import documents_routes
from labb.server import engines
from labb.server import engines_routes
from labb.server import speech, speech_routes
from labb.server import limits
from labb.server import limits_routes
from labb.server import mcp
from labb.server import mcp_routes
from labb.server import pc_team, pcs, pcs_routes
from labb.server import memories_routes
from labb.server import inbox, inbox_routes, watch
from labb.server import embedding, embedding_routes
from labb.server import notes as notes_routes
from labb.server import skills as skill_store
from labb.server import skills_routes
from labb.server import tasks
from labb.server import tasks_routes
from labb.server import providers
from labb.server import providers_routes
from labb.server.studio import engine as studio_engine
from labb.server.studio import routes as studio_routes
from labb.server.studio import sandbox as studio_sandbox
from labb.server.studio import team as studio_team
from labb.tools import custom as custom_tools
from labb.server.studio import triggers as studio_triggers
from labb.server.memory_index import MemoryIndex
from labb.server import usage_routes
from labb import usage
from labb import tools as server_toolsets
from labb.tools import ToolRules
from labb.tools import browser as browser_tool
from labb.tools import delegate as delegate_tool
from labb.tools import pcs as pcs_tool
from labb.tools import documents as documents_tool
from labb.tools import notes as notes_tools



@asynccontextmanager
async def lifespan(app: FastAPI):
    # The task and workflow schedulers start with the server, not at import: the tests use the app without them
    tasks.start()
    studio_triggers.start()
    # The inbox's look at the apps and services (watch.py)
    watch.start()
    # The agents and skills every account starts with, also for accounts made before there were any
    defaults.enabled = True
    await asyncio.to_thread(defaults.ensure_all)
    # The MCP servers' tools for the web chat (Settings → MCP servers); a server that can't be reached says so there
    await asyncio.to_thread(mcp.apply)
    # The server's own Settings → Services (the services keep theirs)
    service_settings.apply()
    # speaches starts empty: download the speech models picked of it
    speech.fetch_models(retry=True)
    if token := await asyncio.to_thread(auth.setup_token):
        where = os.getenv("LABB_PUBLIC_URL", "").rstrip("/") or f"http://<this server>:{os.getenv('PORT', '8000')}"
        print(f"labb: no accounts yet. Create the admin account at {where}/?setup={token}", file=sys.stderr)
    if default_database_password():
        print("labb: the database uses the default password (labb). Run ./setup.sh to see how to change it.",
              file=sys.stderr)
    yield


def default_database_password() -> bool:
    """Whether the Postgres the server uses has docker compose's default password"""
    url = os.getenv("LABB_DATABASE_URL", "")
    return url.startswith("postgres") and urlsplit(url).password == "labb"


# The largest request body: uploads come as base64 in JSON (a 20 MB document is about 27 MB)
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_MB", "50")) * 1_000_000


class BodyLimit:
    """Refuses request bodies over MAX_REQUEST_BYTES before they're read into memory: by their
    Content-Length, or (sent in chunks) as soon as they pass it"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = MAX_REQUEST_BYTES
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > limit:
            response = JSONResponse({"detail": too_large(limit)}, status_code=413)
            return await response(scope, receive, send)
        received = 0

        async def limited():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(413, too_large(limit))
            return message

        await self.app(scope, limited, send)


def too_large(limit: int) -> str:
    return f"The request is over {limit / 1_000_000:g} MB"


# On every answer: no guessing types, no other site framing labb (clickjacking), no plugins, no
# <base> pointing elsewhere, and addresses not passed on to other sites
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"same-origin"),
    (b"content-security-policy", b"frame-ancestors 'self'; object-src 'none'; base-uri 'self'"),
]


class SecurityHeaders:
    """Adds SECURITY_HEADERS to every response. Plain ASGI, so streamed replies aren't buffered."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def with_headers(message):
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = [*message.get("headers", []), *((n, v) for n, v in SECURITY_HEADERS if n not in present)]
            await send(message)

        await self.app(scope, receive, with_headers)


app = FastAPI(lifespan=lifespan)
app.add_middleware(BodyLimit)
app.add_middleware(SecurityHeaders)
app.include_router(auth_routes.router)
app.include_router(artifacts_routes.router)
app.include_router(gateway.router)
app.include_router(usage_routes.router)
app.include_router(documents_routes.router)
app.include_router(speech_routes.router)
app.include_router(memories_routes.router)
app.include_router(agents_routes.router)
app.include_router(tasks_routes.router)
app.include_router(inbox_routes.router)
app.include_router(embedding_routes.router)
app.include_router(skills_routes.router)
app.include_router(defaults_routes.router)
app.include_router(studio_routes.router)
app.include_router(providers_routes.router)
app.include_router(connections_routes.router)
app.include_router(mcp_routes.router)
app.include_router(limits_routes.router)
app.include_router(engines_routes.router)
app.include_router(browser_routes.router)
app.include_router(pcs_routes.router)
app.include_router(notes_routes.router)
app.include_router(apps_routes.router)
app.include_router(app_keys.router)

# The providers and default model the admins set (Settings → Models), and who may use which
providers.apply()
llm.check_access = providers.check
# Each reply is costed at the prices the admins set (Settings → Models → Prices)
prices.apply()

# The web chat's search_documents and save_document use this server's store; documents embedded with another
# model (or cut off by a restart) are indexed again
documents_tool.local_search = documents.search
documents_tool.local_save = documents.save
documents.reindex_stale()
# Memories are per account here, and recalled by meaning
memories.on_server = True
memories.max_chars = lambda: limits.get("memory_chars")
memories.max_memories = lambda: limits.get("memories_per_person")
memories.index = MemoryIndex()
memories.index.reindex_stale()


def embed_again():
    """The embedding model changed (Settings → Models → Search): documents and memories again, with it"""
    documents.reset_store()
    documents.reindex_stale()
    if isinstance(memories.index, MemoryIndex):
        memories.index.reset()
        memories.index.reindex_stale()


embedding.on_change.append(embed_again)
# Skills are per account here too
skills.on_server = True
skills.store = skill_store.ServerStore()
# The web chat's notes, and tasks' and workflows': each person's own
notes_tools.store = notes_routes.store
# The browser is the service's here: one in this process would be on the server's own network
browser_tool.service_only = True
# The apps people connect (Google), for the calendar and mail tools
app_connections.backend = connections.Backend()

# Chats with an agent being edited ("Try it"), which aren't kept: whose each one is. A
# restart forgets them, so their conversations go too.
TRY_PREFIX = "try-"
trials: dict[str, str] = {}
for old in chats.checkpointed_threads(TRY_PREFIX):
    chats.delete_chat(old)


@app.exception_handler(auth.AuthError)
def auth_error(request: Request, error: auth.AuthError):
    """Something the person did wrong, said for them"""
    status = 403 if isinstance(error, auth.SetupTokenError) else 400
    return JSONResponse({"detail": str(error)}, status_code=status)
# The web app lives in frontend/web (with the design system, one npm workspace)
WEB_DIST = Path(__file__).resolve().parents[3] / "frontend" / "web" / "dist"

# The stop flag of each chat's latest reply
runs: dict[str, threading.Event] = {}

# Tasks run in the background, after the replies people are waiting for
tasks.busy = lambda: bool(runs)
studio_engine.busy = lambda: bool(runs)
# A document added starts the workflows waiting for that
documents.on_added.append(studio_triggers.document_added)
# Agents ask the agents and use the actions they were given
delegate_tool.ask = studio_team.ask
delegate_tool.act = studio_team.act
pcs_tool.backend = pc_team.Backend()
server_toolsets.parallel_limit = lambda: limits.get("tool_parallel")
# An engine's Python only ever runs in the sandbox here, and its action tools as whoever is chatting
custom_tools.sandbox = studio_sandbox.run
custom_tools.action = studio_team.engine_action

IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class Attachment(BaseModel):
    name: str
    kind: Literal["text", "image"]
    # The file's text, or the image as base64
    data: str
    mime_type: str | None = None


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None
    # "<provider>:<model>", from the model picker; the server's default if not given
    model: str | None = None
    # How much the model thinks first (llm.ANSWER_MODES); "auto" if not given
    answer_mode: Literal["fast", "auto", "thinking"] | None = None
    # Replace the conversation from this user message on (0 is the first): editing a message, or regenerating
    replace_from: int | None = None
    # With replace_from: keep the replaced message's attachments (the page only has their names)
    keep_attachments: bool = False
    attachments: list[Attachment] = Field(default_factory=list)
    # A new chat's agent (see agents.py); a chat keeps the one it started with
    agent_id: str | None = None
    # Trying out an agent in its editor, as it is there: the chat isn't kept (thread "try-…")
    draft: agents_routes.AgentFields | None = None
    # The engine to use from this message on (see engines.py); else the one the chat has
    engine_id: str | None = None
    # Trying out an engine in its editor, as it is there, or comparing saved ones: not kept either
    engine_draft: dict | None = None
    trial: bool = False
    # The message box's Web toggle: off leaves out web search and reading pages for this reply
    web: bool = True
    # The message box's Browser switch: on offers the browser tools for this reply
    browser: bool = False
    # The message box's Where: a new chat on this connected PC, in this folder (see pcs.py)
    pc_id: str | None = None
    folder: str | None = None
    # Its Don't ask switch: for this reply, the browser doesn't ask (passwords and the like stay with the user)
    browser_auto: bool = False
    # The chat of one of the user's apps (its page's): its thread is app-<id>, with the app's tools
    app_id: str | None = None


# An app's chats (see tools/apps.py), beside it on its page: sessions, each starting afresh. The
# first is app-<id>; New session makes app-<id>--<session>
APP_PREFIX = "app-"
APP_THREAD = re.compile(r"app-([0-9a-f]{12})(?:--([0-9a-z]{6,32}))?")


def app_of_thread(thread_id: str) -> str | None:
    """The app a thread is a session of, if it's one"""
    found = APP_THREAD.fullmatch(thread_id)
    return found.group(1) if found else None


def app_chat(request: ChatRequest, user: dict) -> dict | None:
    """The app this chat belongs to, checked to be the user's: named, or by its thread (one of
    its sessions), so a message sent from anywhere into that chat has the app's tools. Sets the
    thread: the app's first session if none is given."""
    app_id = request.app_id
    if request.thread_id and request.thread_id.startswith(APP_PREFIX):
        if not (of_thread := app_of_thread(request.thread_id)):
            raise HTTPException(400, "Not one of an app's chats")
        if app_id and of_thread != app_id:
            raise HTTPException(400, "That chat belongs to another app")
        app_id = of_thread
    if not app_id:
        return None
    if request.pc_id or request.agent_id or request.draft is not None or request.engine_draft is not None or request.trial:
        raise HTTPException(400, "An app's chat is with labb, here on the server")
    if not (found := app_tools.owned(app_id, user["id"])):
        raise HTTPException(404, "No such app")
    if app_of_thread(request.thread_id or "") != app_id:
        request.thread_id = APP_PREFIX + app_id
    return {"id": app_id, "name": found["name"]}


class StopRequest(BaseModel):
    thread_id: str


class ApprovalAnswer(BaseModel):
    # The approval event's id
    id: str
    allow: bool
    # Why not, or what to do instead, for the model
    reason: str | None = Field(None, max_length=2_000)


# Tool calls waiting for a yes in the chat (an MCP tool that changes something): id → (user id, answer)
approvals: dict[str, tuple[str, queue.Queue]] = {}


def chat_approver(user_id: str, emit, stop_requested: threading.Event):
    """The run's `approve` (see server/mcp.py): asks in the chat with an `approval` event, and waits
    for POST /chat/approve; a stop, or no answer in time, is a no"""

    def approve(detail: str, needed: list[str], always_ask: str | None, title: str = "Use this tool?", view: str | None = None, handover: bool = False):
        approval_id = str(uuid4())
        answer: queue.Queue = queue.Queue(maxsize=1)
        approvals[approval_id] = (user_id, answer)
        event = {"type": "approval", "id": approval_id, "title": title, "detail": detail}
        # A question about the browser shows it: `view` is the chat, and `handover` that it's the user's turn
        emit({**event, "view": view, "handover": handover} if view else event)
        # How long a question waits for its answer before the tool call is refused (Settings → Limits)
        deadline = time.monotonic() + limits.get("approval_minutes") * 60
        try:
            while not stop_requested.is_set():
                try:
                    return answer.get(timeout=0.25)
                except queue.Empty:
                    if time.monotonic() > deadline:
                        return ("no", "nobody answered in time")
            return ("no", None)
        finally:
            approvals.pop(approval_id, None)

    return approve


class ChatUpdate(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    # Everyone on the server can read it at /shared/<thread id>
    shared: bool | None = None


class SyncedSession(BaseModel):
    """A terminal session another PC's labb sends (see sync.py)"""

    machine: str
    directory: str
    title: str
    updated_at: str
    # As langchain_core.messages.messages_to_dict makes them
    messages: list[dict]
    # Reply stats by message id
    stats: dict[str, dict] = Field(default_factory=dict)


def check_access(thread_id: str, user: dict, must_exist: bool = True):
    """Only the owner can see or change a conversation; the server's own terminal sessions
    only admins. Others get a 404, so they
    can't tell what exists."""

    if thread_id.startswith(TRY_PREFIX):
        if trials.get(thread_id, user["id"]) != user["id"]:
            raise HTTPException(404, "No such conversation")
        return
    exists, owner = chats.owner(thread_id)
    if not exists:
        if must_exist:
            raise HTTPException(404, "No such conversation")
        return
    if owner != user["id"] and not (owner is None and user["role"] == "admin"):
        raise HTTPException(404, "No such conversation")


def conversation(thread_id: str) -> tuple[list, dict[str, dict]]:
    """A conversation's messages and reply stats: a session another PC synced, or one of
    this server's own"""

    if synced := chats.get_synced(thread_id):
        return messages_from_dict(synced["messages"]), synced["stats"]
    messages = agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])
    return messages, chats.get_stats(thread_id)


@app.get("/health")
def health():
    """For Docker's health check"""
    return {"ok": True}


@app.get("/admin/health")
def admin_health(user: dict = Depends(auth_routes.admin_user)):
    """Settings → Health: whether Ollama, the models, the crawler, the sandbox, the database,
    the search index, the PCs and the MCP servers are working"""
    return {"checks": health_checks.report()}


@app.get("/admin/services")
def admin_services(problems: bool = False, user: dict = Depends(auth_routes.admin_user)):
    """Settings → Services: the server's settings and the crawler's, the browser's and the sandbox's
    (server/services.py). With ?problems=true, {"settings", "problems"}: which service didn't answer."""
    found: list[str] = []
    shown = service_settings.described(found)
    return {"settings": shown, "problems": found} if problems else shown


@app.put("/admin/services")
def save_admin_services(values: dict[str, bool | int | float | None], problems: bool = False,
                        user: dict = Depends(auth_routes.admin_user)):
    """Change some; null puts one back to its default"""
    try:
        service_settings.save(values)
    except service_settings.SettingError as e:
        raise HTTPException(400, str(e))
    return admin_services(problems, user)


@app.get("/admin/system")
def admin_system(user: dict = Depends(auth_routes.admin_user)):
    """Settings → Health's resources: the machine's CPU, memory and disk, the server's process,
    the database, Ollama's loaded models and each container's usage (server/system.py)"""
    return system_usage.report()


@app.get("/version")
def version():
    """labb's version, and the wheel other PCs install it from (null if it isn't built)"""

    wheel = install.wheel()
    return {"version": install.version(), "wheel": wheel.name if wheel else None}


@app.get("/wheels/{name}")
def wheel_file(name: str):
    wheel = install.wheel()
    if wheel is None or name != wheel.name:
        raise HTTPException(404, "No such wheel. Build it with `uv build --wheel --out-dir wheels` (the Docker image does).")
    return FileResponse(wheel, media_type="application/octet-stream")


@app.get("/install.sh", response_class=PlainTextResponse)
def install_script(request: Request):
    """The script that installs labb on another PC: curl -fsSL http://<server>:8000/install.sh | sh"""

    wheel = install.wheel()
    if wheel is None:
        raise HTTPException(404, "The labb wheel isn't built. Build it with `uv build --wheel --out-dir wheels` (the Docker image does).")
    server_url = str(request.base_url).rstrip("/")
    # Model calls and page reads go through this server (gateway.py), with the PC's API key
    return install.script(server_url, wheel.name, {
        "LABB_SERVER": server_url,
        "OLLAMA_HOST": f"{server_url}/ollama",
        "CRAWLER_URL": f"{server_url}/crawler",
        "BROWSER_URL": f"{server_url}/browser-api",
    })


@app.get("/threads")
def threads(user: dict = Depends(current_user)):
    """Your saved chats, newest first"""
    return chats.list_chats(user["id"])


@app.patch("/threads/{thread_id}", status_code=204)
def update_thread(thread_id: str, update: ChatUpdate, user: dict = Depends(current_user)):
    """Rename, (un)pin or (un)share a chat"""

    check_access(thread_id, user)
    if update.title is not None and not update.title.strip():
        raise HTTPException(400, "The title can't be empty")
    if not chats.update_chat(thread_id, title=update.title, pinned=update.pinned, shared=update.shared):
        raise HTTPException(404, "No such chat")
    return Response(status_code=204)


@app.delete("/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: str, user: dict = Depends(current_user)):
    check_access(thread_id, user)
    chats.delete_chat(thread_id)
    # The web pages it made go with it, in the background: they're on the spawner and labb-sites
    if artifact_tools.enabled():
        threading.Thread(target=artifact_tools.remove_all, args=(user["id"], thread_id), daemon=True).start()
    return Response(status_code=204)


@app.delete("/agents/try/{thread_id}", status_code=204)
def end_trial(thread_id: str, user: dict = Depends(current_user)):
    """Forget a chat that tried out an agent (the editor starts over, or closes)"""

    if not thread_id.startswith(TRY_PREFIX) or trials.get(thread_id) != user["id"]:
        raise HTTPException(404, "No such conversation")
    run = runs.get(thread_id)
    if run is not None:
        run.set()
    del trials[thread_id]
    chats.delete_chat(thread_id)
    return Response(status_code=204)


@app.get("/cli_sessions")
def cli_sessions(user: dict = Depends(current_user)):
    """Your labb sessions, newest first, with their machine and directory: the ones your
    PCs synced (`synced`), and for admins any run on this server itself"""

    on_pcs = pcs.sessions_for(user["id"])
    synced = [{**s, "synced": True, "pc_id": on_pcs.pop(s["thread_id"], {}).get("pc_id")} for s in chats.list_synced(user["id"])]
    # A session on a PC is listed from the start, before the PC has sent it
    synced += [{**s, "synced": False} for s in on_pcs.values()]
    seen = {s["thread_id"] for s in synced}
    local = [{**s, "machine": "this server", "synced": False} for s in chats.list_all_cli() if s["thread_id"] not in seen]
    if user["role"] != "admin":
        local = []
    return sorted(synced + local, key=lambda s: s["updated_at"], reverse=True)


@app.post("/sync/{thread_id}", status_code=204)
def sync(thread_id: str, session: SyncedSession, user: dict = Depends(current_user)):
    """Save (or replace) one of your terminal sessions from another PC"""

    if not thread_id.startswith("cli-"):
        raise HTTPException(400, "Only terminal sessions (cli-…) are synced")
    # A new session becomes the sender's. One that's here without an owner (labb run on this server
    # itself, which only admins see) can be taken by an admin only
    exists, owner = chats.owner(thread_id)
    if (owner is not None and owner != user["id"]) or (exists and owner is None and user["role"] != "admin"):
        raise HTTPException(403, "That session belongs to someone else")
    try:
        messages_from_dict(session.messages)
    except Exception as e:
        raise HTTPException(400, f"Messages that can't be read: {e}")
    chats.save_synced(thread_id, session.machine, session.directory, session.title, session.updated_at,
                      session.messages, session.stats, user_id=user["id"])
    usage.record_session(thread_id, session.stats, user_id=user["id"], machine=session.machine, updated_at=session.updated_at)
    return Response(status_code=204)


@app.delete("/sync/{thread_id}", status_code=204)
def delete_synced(thread_id: str, user: dict = Depends(current_user)):
    """Delete the server's copy of a synced session (the PC keeps its own)"""

    try:
        was_on_pc = pcs.delete_session(thread_id, user["id"])
    except pcs.PcError as e:
        raise HTTPException(409, str(e))
    if not was_on_pc:
        check_access(thread_id, user)
    if not chats.delete_synced(thread_id) and not was_on_pc:
        raise HTTPException(404, "No such synced session")
    return Response(status_code=204)


@app.get("/models")
def models(user: dict = Depends(current_user)):
    """The models this person can pick (Settings → Models decides: all the providers' models,
    or a chosen list, some for admins only), the default first, with their labels and whether
    each can see images (null if unknown). Models that can't be set up are left out."""

    usable = []
    for spec in providers.offered(user["role"]):
        try:
            usable.append(llm.load(spec).spec)
        except ValueError:
            pass
    return {
        "default": llm.default.spec,
        # Whether the message box has the Browser switch
        "browser": browser_tool.enabled(),
        "models": [{"spec": spec, "name": providers.label_of(spec), "vision": llm.vision(spec), "tools": llm.tool_support(spec) is not False} for spec in usable],
    }


@app.get("/history")
def history(thread_id: str, user: dict = Depends(current_user)):
    """A saved conversation as the same events /chat streams, plus `user` for the questions
    and `stats` after each reply that has them"""

    check_access(thread_id, user, must_exist=False)
    # Opening a chat a task wrote to reads it, and what the inbox said about it
    chats.set_unread(thread_id, False)
    inbox.mark_read_about(link=f"/chat/{thread_id}", user_id=user["id"])
    return history_events(thread_id)


@app.get("/shared/{thread_id}")
def shared(thread_id: str, user: dict = Depends(current_user)):
    """A chat its owner shared, for anyone on the server to read: its title, whose it is, and
    its events as /history has them"""

    found = chats.shared_chat(thread_id)
    if found is None:
        raise HTTPException(404, "No such shared chat")
    owner = auth.get_user(found["user_id"]) if found["user_id"] else None
    return {"title": found["title"], "owner": owner["username"] if owner else None,
            "mine": found["user_id"] == user["id"], "events": history_events(thread_id)}


def user_event(message: HumanMessage) -> dict:
    """A user message as the event the page shows it with"""
    text, files, images = split_user_message(message)
    return {
        "type": "user",
        "content": text,
        "files": files,
        "images": [f"data:{image.get('mime_type', 'image/png')};base64,{image.get('base64', '')}" for image in images],
    }


def history_events(thread_id: str) -> list[dict]:
    messages, stats = conversation(thread_id)
    events = []
    tool_names = {}
    for message in messages:
        if message.type == "human":
            events.append(user_event(message))
        elif message.type == "ai":
            if thinking := thinking_of(message):
                events.append({"type": "thinking", "content": thinking})
            if message.text:
                events.append({"type": "token", "content": message.text})
            for tool_call in message.tool_calls:
                tool_names[tool_call["id"]] = tool_call["name"]
                events.append({"type": "tool_call", "id": tool_call["id"], "name": tool_call["name"], "args": tool_call["args"]})
            if message.id in stats:
                events.append({"type": "stats", **stats[message.id]})
        elif message.type == "tool":
            events.append({
                "type": "tool_result",
                "id": message.tool_call_id,
                "name": tool_names.get(message.tool_call_id),
                "chars": len(message.content),
                "first_line": message.text.partition("\n")[0][:200],
                "error": message.status == "error" or message.text.startswith("Error:"),
            })
    return events


@app.get("/export")
def export(thread_id: str, user: dict = Depends(current_user)):
    """A conversation as a Markdown file"""

    check_access(thread_id, user)
    messages, stats = conversation(thread_id)
    if not messages:
        raise HTTPException(404, "No such conversation")
    titles = {c["thread_id"]: c["title"] for c in chats.list_chats(user["id"]) + chats.list_all_cli() + chats.list_synced(user["id"])}
    title = titles.get(thread_id, "Conversation")
    return Response(
        to_markdown(messages, title, stats),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{file_name(title)}"'},
    )


@app.get("/tool_result")
def tool_result(thread_id: str, tool_call_id: str, user: dict = Depends(current_user)):
    """One tool call of a conversation in full: its arguments and what it returned (of a shared
    chat too, for anyone on the server)"""

    if chats.shared_chat(thread_id) is None:
        check_access(thread_id, user)
    messages, _ = conversation(thread_id)
    call = next((c for m in messages if m.type == "ai" for c in m.tool_calls if c["id"] == tool_call_id), None)
    result = next((m for m in messages if m.type == "tool" and m.tool_call_id == tool_call_id), None)
    if call is None:
        raise HTTPException(404, "No such tool call")
    return {"name": call["name"], "args": call["args"], "content": result.text if result else None}


@app.post("/chat/approve", status_code=204)
def approve_tool(body: ApprovalAnswer, http: Request, user: dict = Depends(current_user)):
    """Answer a tool call's question in the chat (an `approval` event)"""

    waiting = approvals.get(body.id)
    if waiting is None or waiting[0] != user["id"]:
        # Asked by a reply on a PC: running things there takes someone logged in to the page
        if not bearer_key(http) and pcs.answer_question(user["id"], body.id, allow=body.allow, reason=body.reason):
            return Response(status_code=204)
        raise HTTPException(404, "That question isn't waiting any more")
    try:
        waiting[1].put_nowait("yes" if body.allow else ("no", body.reason))
    except queue.Full:
        raise HTTPException(409, "It's already been answered")


class QuestionAnswer(BaseModel):
    # The question event's id
    id: str
    text: str = Field(min_length=1, max_length=10_000)


@app.post("/chat/answer", status_code=204)
def answer_question(body: QuestionAnswer, http: Request, user: dict = Depends(current_user)):
    """Answer the question an agent on a PC asked (a `question` event)"""

    if bearer_key(http) or not pcs.answer_question(user["id"], body.id, text=body.text):
        raise HTTPException(404, "That question isn't waiting any more")


@app.post("/stop", status_code=204)
def stop(request: StopRequest, http: Request, user: dict = Depends(current_user)):
    """Stop the reply that's being generated in a chat"""

    check_access(request.thread_id, user, must_exist=False)
    # A reply on a PC: it can be stopped from any tab, but not with an API key
    if on_pc := pcs.active_run(request.thread_id, user["id"]):
        if bearer_key(http):
            raise HTTPException(403, "An API key can't do this: it takes logging in to the web page")
        pcs.stop(on_pc["id"], user["id"])
        return Response(status_code=204)
    run = runs.get(request.thread_id)
    if run is None:
        # A task writing in it
        if tasks.stop_thread(request.thread_id):
            return Response(status_code=204)
        raise HTTPException(404, "Nothing is running in this chat")
    run.set()
    return Response(status_code=204)


def pc_chat(request: ChatRequest, http: Request, user: dict):
    """Send a message in a session on a PC, and follow its reply. Leaving doesn't stop it. The model
    and answer mode are all that's passed on of the message box's choices: the rest is the PC's."""

    if bearer_key(http):
        raise HTTPException(403, "An API key can't do this: it takes logging in to the web page")
    content = message_content(request, user["id"], on_pc=True)
    found = pcs.session(request.thread_id, user["id"]) if request.thread_id else None
    try:
        if found is None:
            if not request.folder:
                raise pcs.PcError("Say which folder.")
            found = pcs.new_session(user["id"], request.pc_id, request.folder, request.message)
        run = pcs.start(user["id"], found["thread_id"], request.message, content, model=request.model, answer_mode=request.answer_mode,
                        replace_from=request.replace_from, keep_attachments=request.keep_attachments, turn=turn_of(found["thread_id"], request.replace_from))
    except LookupError:
        raise HTTPException(404, "No such PC")
    except pcs.PcError as e:
        # A new session that couldn't start leaves nothing behind
        if found and not request.thread_id:
            pcs.delete_session(found["thread_id"], user["id"])
        raise HTTPException(409, str(e))
    first = [] if request.thread_id else [{"type": "thread", "thread_id": found["thread_id"]}]
    return pcs_routes.reply_lines(run, user["id"], first)


def turn_of(thread_id: str, replace_from: int | None) -> int:
    """Which turn of the chat a message will be, from the server's copy of what the PC sent"""

    if replace_from is not None:
        return replace_from
    synced = chats.get_synced(thread_id)
    return len(user_turns(messages_from_dict(synced["messages"]))) if synced else 0


def message_content(request: ChatRequest, user_id: str, on_pc: bool = False) -> str | list:
    """The message with its attachments: text files are added to the text, and images
    make it a list of content blocks. A message that starts with /name uses that skill: its
    instructions go with it like an attached file. `on_pc`: a PC has its own skills, and
    keeps an edited message's attachments itself."""

    if len(request.attachments) > (most := limits.get("attachments_per_message")):
        raise HTTPException(400, f"At most {most} attachments")
    files = {}
    images = []
    for attachment in request.attachments:
        if attachment.kind == "text":
            if len(attachment.data) > (most := limits.get("attachment_text_chars")):
                raise HTTPException(400, f"{attachment.name} is over {most:,} characters")
            files[attachment.name] = attachment.data
            continue
        if attachment.mime_type not in IMAGE_TYPES:
            raise HTTPException(400, f"{attachment.name}: only PNG, JPEG, GIF and WebP images are supported")
        try:
            size = len(base64.b64decode(attachment.data, validate=True))
        except (binascii.Error, ValueError):
            raise HTTPException(400, f"{attachment.name} isn't valid base64")
        if size > (most := limits.get("attachment_image_mb")) * 1_000_000:
            raise HTTPException(400, f"{attachment.name} is over {most} MB")
        images.append({"type": "image", "base64": attachment.data, "mime_type": attachment.mime_type})

    # An edited message keeps the skill it had, with its other attachments
    if request.message.startswith("/") and not request.keep_attachments and not on_pc:
        name = (request.message[1:].split(maxsplit=1) or [""])[0]
        config = {"configurable": {"user_id": user_id}}
        if name and (skill := skills.find(config, name)):
            files = {**skills.invoke_note(skill["name"], skills.instructions(config, skill)), **files}
    text = with_attachments(request.message, files)
    if request.keep_attachments and request.replace_from is not None and not on_pc:
        attached, kept_images = attachments_of(user_message(request.thread_id, request.replace_from))
        text += attached
        images = kept_images + images
    return [{"type": "text", "text": text}, *images] if images else text


def agent_and_model(chosen: dict | None, model: str | None, user: dict) -> dict:
    """The run's agent and model for `configurable`: the model asked for, else the agent's"""

    configurable = {}
    if chosen:
        configurable["agent"] = agents.for_run(chosen)
    if spec := model or (chosen or {}).get("model"):
        try:
            configurable["model"] = llm.load(spec).spec
        except ValueError as e:
            raise HTTPException(400, str(e))
        if reason := providers.why_not(configurable["model"], user["role"]):
            raise HTTPException(403, reason)
    return configurable


class ContextOptions(BaseModel):
    """What the next message would be sent with: the message box's choices, or a what-if's"""
    model: str | None = None
    answer_mode: Literal["fast", "auto", "thinking"] | None = None
    web: bool = True
    browser: bool = False
    engine_id: str | None = None


def check_engine_models(used, configurable: dict, user: dict):
    """The models an engine names are refused before a reply starts, like one asked for by name"""

    for spec in filter(None, [None if "model" in configurable else used and used.model("model"), used and used.model("summary_model")]):
        if reason := providers.why_not(spec, user["role"]):
            raise HTTPException(403, reason)


def check_inspectable(thread_id: str, user: dict):
    check_access(thread_id, user)
    # Synced and the server's own terminal sessions were run elsewhere, with a prompt and
    # tools this server can't rebuild; an agent being tried isn't saved
    if thread_id.startswith(TRY_PREFIX) or chats.get_synced(thread_id) or chats.owner(thread_id)[1] is None:
        raise HTTPException(400, "Only chats on this server can be inspected")


def inspection_config(thread_id: str, user: dict, options: ContextOptions) -> dict:
    """The config the chat's next reply would run with, built as /chat builds it"""

    check_inspectable(thread_id, user)
    agent_id = chats.agent_of(thread_id)
    chosen = agents.visible(agent_id, user["id"]) if agent_id else None
    configurable = {"user_id": user["id"], "thread_id": thread_id} | agent_and_model(chosen, options.model, user)
    try:
        run_engine = engines.for_run(user["id"], thread_id, options.engine_id, bool((configurable.get("agent") or {}).get("engine")))
    except engines.EngineError as e:
        raise HTTPException(400, str(e))
    if run_engine:
        configurable["engine"] = run_engine
    check_engine_models(run_engine or (configurable.get("agent") or {}).get("engine"), configurable, user)
    if options.answer_mode:
        configurable["answer_mode"] = options.answer_mode
    if not options.web:
        configurable["tools"] = ToolRules.of(deny=("web",))
        configurable["web_off"] = True
    if options.browser:
        configurable["browser"] = True
    if pcs.any_online(user["id"]):
        configurable["pcs"] = True
    if (app_id := app_of_thread(thread_id)) and (found := app_tools.owned(app_id, user["id"])):
        configurable["app"] = {"id": found["id"], "name": found["name"]}
    if thread_id.startswith("task-"):
        configurable["task"] = {"thread_id": thread_id}
    return {"configurable": configurable}


@app.get("/context")
def context(thread_id: str, options: Annotated[ContextOptions, Query()], scale: float | None = None, user: dict = Depends(current_user)):
    """What the chat's next model call would get with these choices, part by part with estimated
    tokens: the system prompt's sections, the tools, the summary and the messages as compacted.
    It sends nothing to the model, so it also answers what another model or engine would change:
    `scale` is then the chat's own, from its inspection, so the two can be compared. A session on a
    PC is asked of the PC, which has its prompt, tools and settings."""

    if on_pc := pc_session_of(thread_id, user):
        return ask_pc(on_pc, user, "seen", options, scale=scale)
    return inspection.seen(inspection_config(thread_id, user, options), scale)


def pc_session_of(thread_id: str, user: dict) -> dict | None:
    """The PC session this chat is, if it's one (yours only: someone else's is a 404, as any chat).
    It's in the chat list only once it's synced, so it's looked up first."""
    if found := pcs.session(thread_id, user["id"]):
        return found
    check_access(thread_id, user)
    return None


def ask_pc(session: dict, user: dict, what: str, options: ContextOptions | None = None, **more):
    """The context panel's question, answered by the session's own process on its PC"""
    asked = {"what": what, **more}
    if options is not None:
        asked |= {"model": options.model or None, "answer_mode": options.answer_mode, "web": options.web}
    try:
        answer = pcs.ask_context(user["id"], session, asked)
    except pcs.PcConflict as e:
        raise HTTPException(409, str(e))
    except pcs.PcError as e:
        raise HTTPException(502, str(e))
    if answer.get("missing"):
        raise HTTPException(404, answer["missing"])
    return answer.get("result")


@app.get("/context/raw")
def context_raw(thread_id: str, options: Annotated[ContextOptions, Query()], user: dict = Depends(current_user)):
    """The next model call's whole input as JSON, before each provider's own formatting"""

    if on_pc := pc_session_of(thread_id, user):
        return ask_pc(on_pc, user, "raw", options)
    return inspection.raw(inspection_config(thread_id, user, options))


@app.get("/context/message")
def context_message(thread_id: str, number: int, user: dict = Depends(current_user)):
    """A message as it's saved, by its index: what a summary or a cleared note stands in for"""

    if on_pc := pc_session_of(thread_id, user):
        return ask_pc(on_pc, user, "message", number=number)
    check_inspectable(thread_id, user)
    if (found := inspection.message(thread_id, number)) is None:
        raise HTTPException(404, "No such message")
    return found


@app.get("/context/traces")
def context_traces(thread_id: str, user: dict = Depends(current_user)):
    """What each of the chat's replies did and when, with the one being written last"""

    if on_pc := pc_session_of(thread_id, user):
        return ask_pc(on_pc, user, "traces")
    check_inspectable(thread_id, user)
    return inspection.traces(thread_id)


@app.get("/context/work")
def context_work(thread_id: str, user: dict = Depends(current_user)):
    """What the chat is working on now: its todo list, goal and approved plan. A session on a PC
    keeps them in its project there, so it's asked."""

    if on_pc := pc_session_of(thread_id, user):
        return ask_pc(on_pc, user, "work")
    if chats.get_synced(thread_id):
        raise HTTPException(400, "This session's work is kept on its PC: it shows here while the PC is connected.")
    check_inspectable(thread_id, user)
    return inspection.work({"configurable": {"thread_id": thread_id}})


class SummarizeRequest(ContextOptions):
    thread_id: str


@app.post("/context/summarize")
def context_summarize(request: SummarizeRequest, user: dict = Depends(current_user)):
    """Summarize every turn not summarized yet, now. The saved messages stay complete, but what's
    summarized can't be edited or regenerated. Returns the inspection as it is afterwards."""

    if on_pc := pc_session_of(request.thread_id, user):
        return ask_pc(on_pc, user, "summarize", request)
    config = inspection_config(request.thread_id, user, request)
    # Replies and tasks hold this lock while they write in the chat; a message sent meanwhile waits
    try:
        return inspection.summarize(config, busy=request.thread_id in runs)
    except inspection.Busy as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(502, f"Couldn't summarize: {e}")


@app.post("/chat")
async def chat(request: ChatRequest, http: Request, user: dict = Depends(current_user)):
    """Stream the agent's tool calls and reply as NDJSON events"""

    the_app = await asyncio.to_thread(app_chat, request, user)
    if request.thread_id:
        check_access(request.thread_id, user, must_exist=False)
        if pcs.owned_by_someone_else(request.thread_id, user["id"]):
            raise HTTPException(404, "No such conversation")
    # A chat that runs on a PC
    if request.pc_id or (request.thread_id and pcs.session(request.thread_id, user["id"])):
        return pc_chat(request, http, user)
    trying = request.draft is not None or request.engine_draft is not None or request.trial
    if request.thread_id and trying != request.thread_id.startswith(TRY_PREFIX):
        raise HTTPException(400, "Trying out an agent has a chat of its own")
    # user_id: the reply goes into the usage stats as theirs
    configurable = {"user_id": user["id"]}
    if the_app:
        configurable["app"] = the_app
    # Web pages the model builds (tools/artifacts.py), opened on labb-sites at this server's host name
    elif artifact_tools.enabled():
        configurable["artifacts"] = True
        configurable["sites_base"] = artifact_tools.public_base(http.url.scheme, http.headers.get("host", ""))
    agent_id = None
    if request.draft is not None:
        try:
            chosen = agents.draft(user["id"], agents_routes.fields(request.draft))
        except agents.AgentError as e:
            raise HTTPException(400, str(e))
    elif trying:
        chosen = None
    else:
        exists, _ = chats.owner(request.thread_id) if request.thread_id else (False, None)
        agent_id = chats.agent_of(request.thread_id) if exists else request.agent_id
        # A chat whose agent was deleted (or isn't shared any more) goes on without it
        chosen = agents.visible(agent_id, user["id"]) if agent_id else None
        if agent_id and chosen is None and not exists:
            raise HTTPException(404, "No such agent")
    configurable |= agent_and_model(chosen, request.model, user)
    try:
        if request.engine_draft is not None:
            run_engine = engines.draft(request.engine_draft)
        else:
            run_engine = engines.for_run(user["id"], None if trying else request.thread_id, request.engine_id,
                                            bool((configurable.get("agent") or {}).get("engine")))
    except engines.EngineError as e:
        raise HTTPException(400, str(e))
    if run_engine:
        configurable["engine"] = run_engine
    check_engine_models(run_engine or (configurable.get("agent") or {}).get("engine"), configurable, user)
    if request.answer_mode:
        configurable["answer_mode"] = request.answer_mode
    if not request.web:
        configurable["tools"] = ToolRules.of(deny=("web",))
        configurable["web_off"] = True
    if request.browser:
        configurable["browser"] = True
    # Here and not in tasks or workflows, where the browser only reads
    if request.browser_auto:
        configurable["browser_auto"] = True
    if sites := browser_sites.listed(user["id"]):
        configurable["browser_sites"] = sites
    # Only with someone logged in to the page, who can approve it: not an API key
    if not bearer_key(http) and pcs.any_online(user["id"]):
        configurable["pcs"] = True
    if request.replace_from is not None:
        if not request.thread_id:
            raise HTTPException(400, "replace_from needs a thread_id")
        if reason := can_take_back(request.thread_id, request.replace_from):
            raise HTTPException(409, f"Can't replace that message: {reason}")
    content = message_content(request, user["id"])

    thread_id = request.thread_id or (TRY_PREFIX if trying else "") + str(uuid4())
    if trying:
        trials[thread_id] = user["id"]
    # Which turn this reply is, for a page that joins it again (live.py)
    turn = request.replace_from if request.replace_from is not None else \
        len(user_turns(agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])))
    reply = live.start(thread_id, user["id"], {**user_event(HumanMessage(content=content)), "turn": turn})
    stop_requested = runs[thread_id] = threading.Event()
    emit = reply.emit

    configurable["approve"] = chat_approver(user["id"], emit, stop_requested)

    def run():
        try:
            if not trying:
                chats.touch(thread_id, request.message, user_id=user["id"], agent_id=agent_id)
                if request.engine_id:
                    engines.remember(thread_id, request.engine_id)
            run_reply(thread_id, content, stop_requested, emit, configurable=configurable,
                      replace_from=request.replace_from, name=not trying,
                      recursion_limit=limits.recursion(limits.get("chat_model_calls")))
        finally:
            if runs.get(thread_id) is stop_requested:
                del runs[thread_id]
            live.end(thread_id, reply)

    threading.Thread(target=run, daemon=True).start()

    async def lines():
        # The reply goes on if the page goes away: POST /stop stops it, and GET /chat/live joins it again
        if not request.thread_id:
            yield json.dumps({"type": "thread", "thread_id": thread_id}) + "\n"
        async for event in reply.follow():
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.get("/chat/live")
def chat_live(thread_id: str, user: dict = Depends(current_user)):
    """The reply being written in a chat, from its start, for a page that lost its stream or opens
    the chat meanwhile: a `user` event with the turn it is, then its events. Empty if there's none."""

    check_access(thread_id, user, must_exist=False)
    reply = live.of(thread_id, user["id"])
    if reply is None:
        return Response(status_code=204)

    async def lines():
        yield json.dumps(reply.start, ensure_ascii=False) + "\n"
        async for event in reply.follow():
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


# The web app's pages have addresses of their own (frontend/web/src/router.tsx), and some are API routes too:
# /agents is the agents page, and the list of them. A browser loading such a page gets the app.
PAGE_ROOTS = {"chat", "inbox", "browser", "shared", "settings", "studio", "agents", "engines", "pcs", "tasks", "skills", "documents", "memories", "notes", "usage", "apps", "artifacts"}
# Files opened by following a link, not pages
NOT_PAGES = ("/download", "/export")


def page_root(scope) -> str | None:
    """The page root of a GET, if it's one"""
    root = scope["path"].strip("/").split("/")[0]
    return root if scope["method"] == "GET" and root in PAGE_ROOTS else None


def is_page_load(scope) -> bool:
    """The browser loading the page at this address, rather than the app fetching from it"""
    if not page_root(scope) or scope["path"].endswith(NOT_PAGES):
        return False
    headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in scope["headers"]}
    if mode := headers.get("sec-fetch-mode"):
        return mode == "navigate"
    # Browsers without Fetch Metadata: fetch() asks for */*
    return "text/html" in headers.get("accept", "")


class WebPages:
    """Answers page loads of the app's addresses with the app. Plain ASGI, so streamed replies aren't buffered."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not page_root(scope):
            return await self.app(scope, receive, send)
        index = WEB_DIST / "index.html"
        if is_page_load(scope) and index.is_file():
            return await FileResponse(index, headers={"Cache-Control": "no-cache"})(scope, receive, send)

        # The same address answers JSON and the page: a cached answer is only good for the same kind of request
        async def vary(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"vary", b"Sec-Fetch-Mode, Accept")]
            await send(message)

        await self.app(scope, receive, vary)


app.add_middleware(WebPages)


# The React app (web/); API routes above take precedence
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:
    @app.get("/")
    def not_built():
        return HTMLResponse(
            "<p>The web app isn't built yet. Run <code>cd frontend && npm install && npm run web:build</code>, "
            "then restart the server.</p>"
        )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
