"""The server as the way to labb-browser: for the live view, where people watch the browser and
take over, and for labb on the PCs. Who the user is comes from their session or key, never from
the request, so people only ever reach their own browser."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from labb.server import browser_sites
from labb.server.auth_routes import current_user
from labb.tools import browser as browser_tool

router = APIRouter(prefix="/browser-api")
# Loading a page can take a while; the frames of the live view are quick
client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10))


class Act(BaseModel):
    # From labb on a PC; `user` is ignored, if it's sent
    thread: str
    action: str
    args: dict = {}


class Input(BaseModel):
    event: dict


class Site(BaseModel):
    site: str


async def forward(method: str, path: str, **kwargs) -> httpx.Response:
    url = browser_tool.BROWSER_URL
    if not url:
        raise HTTPException(503, "This server has no browser (BROWSER_URL isn't set)")
    try:
        return await client.request(method, f"{url}{path}", headers=browser_tool.service_headers(), **kwargs)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Couldn't reach the browser: {e}")


def answer(response: httpx.Response) -> Response:
    return Response(response.content, status_code=response.status_code, media_type=response.headers.get("content-type"),
                    headers={"Cache-Control": "no-store"})


@router.get("/trusted")
def trusted(user: dict = Depends(current_user)):
    """The sites the browser may click and type on without asking first"""
    return {"sites": browser_sites.listed(user["id"])}


@router.post("/trusted")
def trust(body: Site, user: dict = Depends(current_user)):
    site = browser_tool.clean_site(body.site)
    if not site:
        raise HTTPException(400, f"That isn't a site: {body.site[:100]}")
    browser_sites.add(user["id"], site)
    return {"sites": browser_sites.listed(user["id"])}


@router.delete("/trusted/{site}", status_code=204)
def distrust(site: str, user: dict = Depends(current_user)):
    browser_sites.remove(user["id"], site)
    return Response(status_code=204)


@router.post("/act")
async def act(body: Act, user: dict = Depends(current_user)):
    return answer(await forward("POST", "/act", json={"user": user["id"], "thread": body.thread, "action": body.action, "args": body.args}))


@router.get("/{thread_id}/state")
async def state(thread_id: str, user: dict = Depends(current_user)):
    return answer(await forward("GET", "/view/state", params={"user": user["id"], "thread": thread_id}))


@router.get("/{thread_id}/frame")
async def frame(thread_id: str, user: dict = Depends(current_user)):
    return answer(await forward("GET", "/view/frame", params={"user": user["id"], "thread": thread_id}))


@router.post("/{thread_id}/input")
async def view_input(thread_id: str, body: Input, user: dict = Depends(current_user)):
    return answer(await forward("POST", "/view/input", json={"user": user["id"], "thread": thread_id, "event": body.event}))


@router.post("/view/done", status_code=204)
async def done(user: dict = Depends(current_user)):
    await forward("POST", "/view/done", json={"user": user["id"]})
    return Response(status_code=204)


@router.delete("/profile", status_code=204)
async def forget(user: dict = Depends(current_user)):
    """Sign out of every site in the browser"""
    await forward("DELETE", "/profile", params={"user": user["id"]})
    return Response(status_code=204)
