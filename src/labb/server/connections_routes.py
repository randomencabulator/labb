"""Settings → Integrations: yours (everyone) and the server's (admins); see connections.py. The
routes still say connections (yours) and apps (the server's)."""

import html
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from labb.server import connections
from labb.server.auth_routes import admin_user, authenticate, base_url, current_user

router = APIRouter()

SETTINGS = "/settings/integrations"


class Pasted(BaseModel):
    url: str


class ClientFields(BaseModel):
    client_id: str
    # Empty keeps the saved one
    client_secret: str | None = None


def _setup(work):
    try:
        return work()
    except connections.SetupError as e:
        raise HTTPException(400, str(e))


@router.get("/connections")
def list_connections(user: dict = Depends(current_user)):
    return connections.listing(user["id"])


@router.post("/connections/{app}/start")
def start(app: str, request: Request, user: dict = Depends(current_user)):
    return _setup(lambda: connections.start(user["id"], app, base_url(request)))


@router.get("/connections/{app}/callback")
def callback(app: str, request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    """Where the app sends the person back after they've allowed labb in"""

    user = authenticate(request)
    if user is None:
        # A browser that isn't logged in here: most likely the address was opened on another
        # machine than the one it's for, so say how to finish by pasting it
        page = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>labb</title>"
                f"<body style='font:16px system-ui;max-width:40em;margin:3em auto;padding:0 16px'>"
                f"<h1>Almost connected</h1><p>Copy this page's address from the address bar and paste it into labb, "
                f"in Settings → Integrations.</p><p><code style='word-break:break-all'>{html.escape(str(request.url))}</code></p>")
        return HTMLResponse(page)
    try:
        connections.finish(user["id"], app, code, state, error)
    except connections.SetupError as e:
        return RedirectResponse(f"{SETTINGS}?error={quote(str(e))}", status_code=303)
    return RedirectResponse(f"{SETTINGS}?connected={quote(app)}", status_code=303)


@router.post("/connections/{app}/finish")
def finish(app: str, body: Pasted, user: dict = Depends(current_user)):
    """Finish connecting with the address the person was sent back to, pasted in"""
    return _setup(lambda: connections.finish_pasted(user["id"], app, body.url))


@router.delete("/connections/{app}", status_code=204)
def disconnect(app: str, user: dict = Depends(current_user)):
    if not connections.disconnect(user["id"], app):
        raise HTTPException(404, "That app isn't connected")
    return Response(status_code=204)


@router.get("/admin/apps")
def list_apps(request: Request, user: dict = Depends(admin_user)):
    return connections.apps_listing(base_url(request))


@router.put("/admin/apps/{app}")
def save_app(app: str, body: ClientFields, request: Request, user: dict = Depends(admin_user)):
    _setup(lambda: connections.save_client(app, body.client_id, body.client_secret))
    return connections.apps_listing(base_url(request))


@router.delete("/admin/apps/{app}", status_code=204)
def remove_app(app: str, user: dict = Depends(admin_user)):
    if not connections.remove_client(app):
        raise HTTPException(404, "Nothing is saved for that app")
    return Response(status_code=204)
