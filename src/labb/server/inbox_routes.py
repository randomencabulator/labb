"""The inbox's endpoints (see inbox.py): your notifications, marking them read, and following
them live. Nobody else sees them, admins included."""

import json

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from labb.server import inbox
from labb.server.auth_routes import current_user

router = APIRouter()


class ReadRequest(BaseModel):
    # Which to mark read; without it, all of them
    ids: list[str] | None = None


@router.get("/inbox")
def list_inbox(unread: bool = False, limit: int = 100, user: dict = Depends(current_user)):
    """Your notifications, newest first, and how many are unread"""
    return {"items": inbox.list_for(user["id"], limit=limit, unread_only=unread), "unread": inbox.unread_count(user["id"])}


@router.post("/inbox/read")
def read(body: ReadRequest, user: dict = Depends(current_user)):
    inbox.mark_read(user["id"], body.ids)
    return {"unread": inbox.unread_count(user["id"])}


@router.delete("/inbox", status_code=204)
def clear_read(user: dict = Depends(current_user)):
    """Delete the ones already read"""
    inbox.delete_read(user["id"])
    return Response(status_code=204)


@router.delete("/inbox/{notification_id}", status_code=204)
def delete(notification_id: str, user: dict = Depends(current_user)):
    if not inbox.delete(user["id"], notification_id):
        raise HTTPException(404, "No such notification")
    return Response(status_code=204)


@router.get("/inbox/stream")
async def stream(user: dict = Depends(current_user)):
    """Server-sent events: the unread count first, then each new notification and change (inbox.follow)"""

    async def events():
        async for event in inbox.follow(user["id"]):
            # A comment line keeps the connection open without waking the page
            yield ": ping\n\n" if event["type"] == "ping" else f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    # No buffering on the way (Caddy, nginx), so each event arrives when it happens
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
