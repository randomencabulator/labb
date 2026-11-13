"""The HTTP API, for labb's server. It trusts the server on who the user is, so it's only on a
network the server shares, and asks for a token.

    POST /act            {user, thread, action, args} → {text, image?} or {error}
    GET  /view/state     ?user&thread: the tabs
    GET  /view/frame     ?user&thread: what the current tab shows, as a JPEG
    POST /view/input     {user, thread, event}: what the user did in the live view
    POST /view/done      {user}: the user took over and is done: keep what they signed in to
    DELETE /chat         ?user&thread: close the chat's tabs
    DELETE /profile      ?user: sign out of everything
    GET  /health
"""

import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from labb_browser import settings
from labb_browser.driver import Driver

DATA = Path(os.getenv("BROWSER_DATA", "data"))
ALLOW_PRIVATE = os.getenv("ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")
TOKEN_FILE = os.getenv("BROWSER_TOKEN_FILE", "")
TOKEN_OWNER = int(os.getenv("BROWSER_TOKEN_OWNER", "1000"))


def load_token() -> str:
    """BROWSER_TOKEN, or the one in BROWSER_TOKEN_FILE, made there if there's none yet; "" without either"""

    if token := os.getenv("BROWSER_TOKEN", ""):
        return token
    if not TOKEN_FILE:
        return ""
    path = Path(TOKEN_FILE)
    try:
        if token := path.read_text().strip():
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640), "w") as file:
        file.write(token + "\n")
    os.replace(partial, path)
    return token


TOKEN = load_token()
if not TOKEN:
    print("labb-browser: no BROWSER_TOKEN or BROWSER_TOKEN_FILE, so anything that can reach it can use people's sign-ins",
          file=sys.stderr, flush=True)

driver = Driver(DATA, allow_private=ALLOW_PRIVATE)


def _follow_settings():
    # The driver was made with the address rule at start; Settings → Services may change it
    driver.allow_private = ALLOW_PRIVATE


settings.after_apply.append(_follow_settings)
settings.apply()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await driver.close()


app = FastAPI(title="labb-browser", lifespan=lifespan)


def authorized(request: Request):
    if TOKEN and not secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {TOKEN}"):
        raise HTTPException(401, "A token is needed")


class Act(BaseModel):
    user: str
    thread: str
    action: str
    args: dict = Field(default_factory=dict)


class Input(BaseModel):
    user: str
    thread: str
    event: dict


class Done(BaseModel):
    user: str


@app.get("/settings", dependencies=[Depends(authorized)])
def get_settings():
    """For labb's Settings → Services (settings.py)"""
    return settings.described()


@app.put("/settings", dependencies=[Depends(authorized)])
def save_settings(values: dict[str, bool | int | float | None]):
    """Change some; null puts one back to its default"""
    return settings.save(values)


@app.post("/act", dependencies=[Depends(authorized)])
async def act(body: Act):
    return await driver.act(body.user, body.thread, body.action, body.args)


@app.get("/view/state", dependencies=[Depends(authorized)])
async def view_state(user: str, thread: str):
    state = await driver.view_state(user, thread)
    return state or {"tabs": [], "width": 0, "height": 0}


@app.get("/view/frame", dependencies=[Depends(authorized)])
async def view_frame(user: str, thread: str, quality: int = 60):
    try:
        image = await driver.view_frame(user, thread, max(10, min(quality, 90)))
    except Exception:
        image = None
    if image is None:
        raise HTTPException(404, "No page is open in this chat")
    return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/view/input", dependencies=[Depends(authorized)])
async def view_input(body: Input):
    problem = await driver.view_input(body.user, body.thread, body.event)
    if problem:
        raise HTTPException(400, problem)
    return {"ok": True}


@app.post("/view/done", dependencies=[Depends(authorized)])
async def view_done(body: Done):
    await driver.save_signins(body.user)
    return {"ok": True}


@app.delete("/chat", status_code=204, dependencies=[Depends(authorized)])
async def close_chat(user: str, thread: str):
    await driver.close_chat(user, thread)


@app.delete("/profile", status_code=204, dependencies=[Depends(authorized)])
async def forget(user: str):
    await driver.forget(user)


@app.get("/health")
def health():
    return {"status": "ok"}


def main():
    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", 3100)))


if __name__ == "__main__":
    main()
