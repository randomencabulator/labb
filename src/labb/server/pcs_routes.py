"""The endpoints for PCs connected with labb connect (see pcs.py); a session on a PC is written to
through /chat and /stop like any chat, in server/app.py.

Two kinds of caller, kept apart on purpose. A PC connects and reports with its API key. Sending
messages and answering their questions takes a person's browser session, so a key that leaks from one
PC can't run anything on the others. Everything is limited to your own PCs: another's is a 404."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from labb.runner import ReplyStats
from labb.server import auth, pcs
from labb.server.auth_routes import bearer_key, current_user

router = APIRouter(prefix="/pcs")

PING_SECONDS = 20
FOLLOW_SECONDS = 0.15


def pc_user(request: Request, user: dict = Depends(current_user)) -> dict:
    """A PC, with its API key"""
    if not bearer_key(request):
        raise HTTPException(403, "Only labb connect, with a PC's API key, does this")
    return user


def person(request: Request, user: dict = Depends(current_user)) -> dict:
    """Someone in the web page, with their session"""
    if bearer_key(request):
        raise HTTPException(403, "An API key can't do this: it takes logging in to the web page")
    return user


class Connect(BaseModel):
    machine: str
    folders: list[str]
    version: str | None = None
    allow_all: bool = False
    # The replies it's still writing
    active: list[str] = []
    models: list[str] = []


class Events(BaseModel):
    run_id: str
    events: list[dict]


def reply_lines(run: dict, user_id: str, first: list[dict] = ()):
    """A reply's events as /chat streams them, from the start, until it's over. A person leaving
    doesn't stop it: it's on the PC, and they can come back to it."""

    async def lines():
        for event in first:
            yield json.dumps(event, ensure_ascii=False) + "\n"
        after = 0
        ended = False
        while not ended:
            # In this order: if it's over now, these are all its events
            now = await asyncio.to_thread(pcs.get_run, run["id"], user_id)
            found = await asyncio.to_thread(pcs.events_after, run["id"], after)
            for item in found:
                after = item["seq"]
                event = item["event"]
                if event.get("type") in ("done", "stopped"):
                    ended = True
                    event = {**event, "stats": event.get("stats") or ReplyStats("").to_dict(event["type"] == "stopped")}
                yield json.dumps(event, ensure_ascii=False) + "\n"
            if ended:
                return
            if now is None or now["status"] in pcs.FINISHED:
                # Ended without the PC saying so: it was lost, it never picked it up, or it was stopped while away
                if now and now["error"] and now["status"] != "stopped":
                    yield json.dumps({"type": "error", "message": now["error"]}, ensure_ascii=False) + "\n"
                yield json.dumps({"type": "stopped", "stats": ReplyStats("").to_dict(True)}) + "\n"
                return
            await asyncio.sleep(FOLLOW_SECONDS)

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@router.post("/connect")
async def connect(body: Connect, request: Request, user: dict = Depends(pc_user)):
    """The open request that brings a PC its work, as lines of JSON"""

    if len(body.folders) > pcs.MAX_FOLDERS:
        raise HTTPException(400, f"At most {pcs.MAX_FOLDERS} folders")
    key = bearer_key(request)
    pc_id = await asyncio.to_thread(pcs.register, user["id"], body.machine, body.folders, body.version, body.allow_all, body.models)
    line = pcs.Line(asyncio.get_running_loop())
    await asyncio.to_thread(pcs.connected, pc_id, line, body.active)

    async def lines():
        try:
            yield json.dumps({"type": "hello", "pc_id": pc_id}) + "\n"
            while True:
                try:
                    message = await asyncio.wait_for(line.queue.get(), PING_SECONDS)
                except TimeoutError:
                    # A revoked key or a disabled account ends the connection too
                    if not await asyncio.to_thread(auth.key_owner, key):
                        return
                    message = {"type": "ping"}
                yield json.dumps(message, ensure_ascii=False) + "\n"
                if message["type"] == "replaced":
                    return
        finally:
            pcs.disconnected(pc_id, line, user["id"])

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@router.post("/events", status_code=204)
def events(body: Events, user: dict = Depends(pc_user)):
    """A reply's events, from the PC writing it"""

    if len(body.events) > pcs.MAX_EVENTS_PER_CALL:
        raise HTTPException(400, f"At most {pcs.MAX_EVENTS_PER_CALL} events at a time")
    try:
        pcs.add_events(body.run_id, user["id"], body.events)
    except LookupError:
        raise HTTPException(404, "No such run")
    except (KeyError, TypeError) as e:
        raise HTTPException(400, f"Events that can't be read: {e}")
    return Response(status_code=204)


@router.get("")
def list_pcs(user: dict = Depends(current_user)):
    return pcs.list_for(user["id"])


@router.get("/live")
def live(thread_id: str, user: dict = Depends(current_user)):
    """The reply being written in a session, from its start, for a page that opens it meanwhile: its
    `user` event says which turn it is. Empty if there's none."""

    run = pcs.active_run(thread_id, user["id"])
    if run is None:
        return Response(status_code=204)
    return reply_lines(run, user["id"], [{"type": "user", "content": run["message"], "turn": run["turn"]}])


@router.post("/answers/{ask_id}", status_code=204)
def asked(ask_id: str, answer: dict, user: dict = Depends(pc_user)):
    """A PC's answer to what the server asked it (pcs.ask): a folder's notes, a session's context"""
    if not pcs.answered(user["id"], ask_id, answer):
        raise HTTPException(404, "Nobody's waiting for that")
    return Response(status_code=204)


@router.delete("/{pc_id}", status_code=204)
def forget(pc_id: str, user: dict = Depends(person)):
    try:
        if not pcs.forget(pc_id, user["id"]):
            raise HTTPException(404, "No such PC")
    except pcs.PcError as e:
        raise HTTPException(409, str(e))
    return Response(status_code=204)
