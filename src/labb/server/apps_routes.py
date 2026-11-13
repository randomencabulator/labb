"""The Apps page's API: people's apps, through labb-spawner (see apps.py). Who the user is comes
from their session or key, never from the request, so people only see and change their own."""

import re
import uuid
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from labb import chats
from labb.server import app_keys, apps, notes
from labb.server.apps import checked, forward
from labb.server.auth_routes import current_user

router = APIRouter(prefix="/apps")


class NewApp(BaseModel):
    name: str
    starter: str = "react"
    # The starter's choices (color, style, font), checked by the spawner
    choices: dict[str, str] = {}


class File(BaseModel):
    content: str


class Variable(BaseModel):
    name: str
    # None for a secret that keeps its value
    value: str | None = None
    secret: bool = False


class Settings(BaseModel):
    variables: list[Variable]


class Query(BaseModel):
    sql: str
    write: bool = False


class LabbUse(BaseModel):
    # What the app may use of labb (app_keys.SCOPES); none: no key at all
    scopes: list[str]


# A build can take minutes (pulling its base image, installing packages)
BUILD_TIMEOUT = 660


# What the page gets of an app; the owner is the user asking, and where it runs stays here
PUBLIC = ("id", "name", "port", "status", "detail", "restarts", "created", "build", "legacy", "memory_limit", "memory_used",
          "database", "server", "page")


def public(app: dict) -> dict:
    return {key: app[key] for key in PUBLIC if key in app}


@router.get("")
async def list_apps(user: dict = Depends(current_user)):
    response = await forward("GET", "/apps", params={"owner": user["id"]})
    return [public(app) for app in response.json()]


@router.post("", status_code=201)
async def new_app(body: NewApp, user: dict = Depends(current_user)):
    response = await forward("POST", "/apps", json={"owner": user["id"], "name": body.name, "starter": body.starter,
                                                     "choices": body.choices},
                             timeout=BUILD_TIMEOUT)
    return public(response.json())


@router.get("/starters")
async def list_starters(user: dict = Depends(current_user)):
    """What a new app can start from"""
    return (await forward("GET", "/starters")).json()


@router.get("/{app_id}")
async def get_app(app_id: str, user: dict = Depends(current_user)):
    response = await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})
    return public(response.json())


@router.post("/{app_id}/open")
async def open_app(app_id: str, request: Request, user: dict = Depends(current_user)):
    """Where to open the app: its own port on labb's host. Waits until it answers first."""
    app = (await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})).json()
    if not app.get("port"):
        raise HTTPException(409, "This app was made before apps had a port of their own, so it has no address. "
                                 "Remove it and make a new one.")
    # Stopped (by its owner, idle, or after a crash): opening it starts it again
    if app["status"] in ("exited", "created"):
        app = (await forward("POST", f"/apps/{app_id}/start", params={"owner": user["id"]})).json()
    origin = apps.app_origin(request, app["port"])
    ready = await apps.answers(app.get("upstream", ""))
    return {"url": f"{origin}/", "origin": origin, "ready": ready}


@router.get("/{app_id}/sessions")
async def app_sessions(app_id: str, user: dict = Depends(current_user)):
    """Its chats on its page (sessions: app-<id>, then app-<id>--<session>), newest first"""
    await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})
    first = f"app-{app_id}"
    return [{"thread_id": c["thread_id"], "title": c["title"], "updated_at": c["updated_at"]}
            for c in chats.list_chats(user["id"]) if c["thread_id"] == first or c["thread_id"].startswith(first + "--")]


@router.get("/{app_id}/notes")
async def get_app_notes(app_id: str, user: dict = Depends(current_user)):
    """The notes its chat keeps (only it: the person's own aren't in them), with the digest to change them with"""
    await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})
    return notes.shown(notes.store.read(user["id"], app_id))


@router.post("/{app_id}/notes")
async def change_app_notes(app_id: str, change: notes.Change, user: dict = Depends(current_user)):
    await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})
    return notes.changing(lambda: notes.store.change(user["id"], change.action, change.text, change.topic, change.base, app_id))


@router.get("/{app_id}/fresh-start")
def fresh_start(app_id: str, thread: str, user: dict = Depends(current_user)):
    """The first message for a new session that starts again from a stuck one (`thread`, one of this
    app's sessions): what was asked, the steps left and where the app is now"""
    from labb.server import app_tools
    first = f"app-{checked(app_id)}"
    if app_tools.owned(app_id, user["id"]) is None:
        raise HTTPException(404, "No such app")
    if not (thread == first or thread.startswith(first + "--")) or chats.owner(thread)[1] != user["id"]:
        raise HTTPException(404, "No such session")
    try:
        return {"message": app_tools.fresh_start(app_id, user["id"], thread)}
    except ValueError as e:
        raise HTTPException(502, str(e))


@router.get("/{app_id}/files")
async def list_files(app_id: str, user: dict = Depends(current_user)):
    return (await forward("GET", f"/apps/{checked(app_id)}/files", params={"owner": user["id"]})).json()


@router.get("/{app_id}/search")
async def search_files(app_id: str, q: str, case: bool = False, regex: bool = False, word: bool = False,
                       user: dict = Depends(current_user)):
    """Where some text is in the app's files: the lines it's on, and the files whose path has it"""
    params = {"owner": user["id"], "q": q, "case": case, "regex": regex, "word": word}
    return (await forward("GET", f"/apps/{checked(app_id)}/search", params=params)).json()


@router.get("/{app_id}/files/{path:path}")
async def read_file(app_id: str, path: str, user: dict = Depends(current_user)):
    return (await forward("GET", f"/apps/{checked(app_id)}/files/{quote(path)}", params={"owner": user["id"]})).json()


@router.put("/{app_id}/files/{path:path}")
async def write_file(app_id: str, path: str, body: File, user: dict = Depends(current_user)):
    response = await forward("PUT", f"/apps/{checked(app_id)}/files/{quote(path)}", params={"owner": user["id"]},
                             json={"content": body.content})
    return response.json()


@router.delete("/{app_id}/files/{path:path}", status_code=204)
async def delete_file(app_id: str, path: str, user: dict = Depends(current_user)):
    await forward("DELETE", f"/apps/{checked(app_id)}/files/{quote(path)}", params={"owner": user["id"]})
    return Response(status_code=204)


@router.post("/{app_id}/build")
async def build_app(app_id: str, user: dict = Depends(current_user)):
    """Build its files again; if that works, the new build runs in its place. `ok`, `error`, `log`, `app`"""
    response = await forward("POST", f"/apps/{checked(app_id)}/build", params={"owner": user["id"]},
                             timeout=BUILD_TIMEOUT)
    built = response.json()
    return {**built, "app": public(built["app"])}


@router.get("/{app_id}/build")
async def last_build(app_id: str, user: dict = Depends(current_user)):
    return (await forward("GET", f"/apps/{checked(app_id)}/build", params={"owner": user["id"]})).json()


@router.get("/{app_id}/page")
async def page_build(app_id: str, user: dict = Depends(current_user)):
    """Its page's last build (frontend/, built by itself after each change): `build`, `ok`, `error`, `at`, `log`, `pending`"""
    return (await forward("GET", f"/apps/{checked(app_id)}/page", params={"owner": user["id"]})).json()


VERSION = re.compile(r"[0-9a-f]{40}")


def checked_version(version: str) -> str:
    if not VERSION.fullmatch(version):
        raise HTTPException(404, "No such version")
    return version


@router.get("/{app_id}/versions")
async def list_versions(app_id: str, user: dict = Depends(current_user)):
    """Its versions (one at each build), newest first, and the files changed since the last"""
    return (await forward("GET", f"/apps/{checked(app_id)}/versions", params={"owner": user["id"]})).json()


@router.get("/{app_id}/versions/unsaved/diff")
async def unsaved_diff(app_id: str, user: dict = Depends(current_user)):
    return (await forward("GET", f"/apps/{checked(app_id)}/versions/unsaved/diff", params={"owner": user["id"]})).json()


@router.get("/{app_id}/versions/{version}/diff")
async def version_diff(app_id: str, version: str, user: dict = Depends(current_user)):
    path = f"/apps/{checked(app_id)}/versions/{checked_version(version)}/diff"
    return (await forward("GET", path, params={"owner": user["id"]})).json()


@router.post("/{app_id}/versions/{version}/restore")
async def restore_version(app_id: str, version: str, user: dict = Depends(current_user)):
    """Its files as they were then, saved as a new version: build to run them"""
    path = f"/apps/{checked(app_id)}/versions/{checked_version(version)}/restore"
    return (await forward("POST", path, params={"owner": user["id"]})).json()


def with_api_url(found: dict, request: Request) -> dict:
    """Its API's address, as this page's host on its port"""
    if found.get("api", {}).get("port"):
        found["api"]["origin"] = apps.app_origin(request, found["api"]["port"])
        found["api"]["url"] = found["api"]["origin"] + "/rest/"
    return found


@router.get("/{app_id}/database")
async def get_database(app_id: str, request: Request, user: dict = Depends(current_user)):
    """Its database: whether it has one, its tables, migrations and size, and its API"""
    return with_api_url((await forward("GET", f"/apps/{checked(app_id)}/database", params={"owner": user["id"]})).json(), request)


@router.post("/{app_id}/api")
async def make_api(app_id: str, request: Request, user: dict = Depends(current_user)):
    """Its API: /rest/ its tables, as its migrations grant them to anon, at its port + 100"""
    found = (await forward("POST", f"/apps/{checked(app_id)}/api", params={"owner": user["id"]}, timeout=300)).json()
    return with_api_url(found, request)


@router.delete("/{app_id}/api")
async def remove_api(app_id: str, request: Request, user: dict = Depends(current_user)):
    found = (await forward("DELETE", f"/apps/{checked(app_id)}/api", params={"owner": user["id"]}, timeout=120)).json()
    return with_api_url(found, request)


class ApiSettings(BaseModel):
    signups: bool


@router.patch("/{app_id}/api")
async def change_api(app_id: str, body: ApiSettings, request: Request, user: dict = Depends(current_user)):
    """Whether new users may sign up on its API"""
    found = (await forward("PATCH", f"/apps/{checked(app_id)}/api", params={"owner": user["id"]}, json=body.model_dump(),
                           timeout=300)).json()
    return with_api_url(found, request)


def checked_user(user_id: str) -> str:
    """An app's user's id is a UUID; anything else isn't put in the spawner's URL"""
    try:
        return str(uuid.UUID(user_id))
    except ValueError:
        raise HTTPException(404, "No such user")


@router.get("/{app_id}/users")
async def list_users(app_id: str, offset: int = 0, user: dict = Depends(current_user)):
    """Its own users, who signed up on its API"""
    return (await forward("GET", f"/apps/{checked(app_id)}/users", params={"owner": user["id"], "offset": offset})).json()


class UserChange(BaseModel):
    disabled: bool


@router.patch("/{app_id}/users/{user_id}")
async def change_user(app_id: str, user_id: str, body: UserChange, user: dict = Depends(current_user)):
    """One of its users disabled (signed out everywhere) or enabled again"""
    await forward("PATCH", f"/apps/{checked(app_id)}/users/{checked_user(user_id)}", params={"owner": user["id"]},
                  json=body.model_dump())
    return Response(status_code=204)


@router.delete("/{app_id}/users/{user_id}")
async def remove_user(app_id: str, user_id: str, user: dict = Depends(current_user)):
    await forward("DELETE", f"/apps/{checked(app_id)}/users/{checked_user(user_id)}", params={"owner": user["id"]})
    return Response(status_code=204)


@router.post("/{app_id}/database")
async def make_database(app_id: str, user: dict = Depends(current_user)):
    """A database of its own: its migrations applied, and the app started again with DATABASE_URL"""
    found = (await forward("POST", f"/apps/{checked(app_id)}/database", params={"owner": user["id"]}, timeout=180)).json()
    return {**found, "app": public(found["app"])}


@router.delete("/{app_id}/database")
async def remove_database(app_id: str, user: dict = Depends(current_user)):
    found = (await forward("DELETE", f"/apps/{checked(app_id)}/database", params={"owner": user["id"]}, timeout=120)).json()
    return {"app": public(found["app"])}


@router.get("/{app_id}/database/rows")
async def database_rows(app_id: str, table: str, schema: str = "public", offset: int = 0, limit: int = 100,
                        user: dict = Depends(current_user)):
    params = {"owner": user["id"], "table": table, "schema": schema, "offset": offset, "limit": limit}
    return (await forward("GET", f"/apps/{checked(app_id)}/database/rows", params=params)).json()


@router.post("/{app_id}/database/query")
async def database_query(app_id: str, body: Query, user: dict = Depends(current_user)):
    """SQL as the app: read-only unless `write`"""
    return (await forward("POST", f"/apps/{checked(app_id)}/database/query", params={"owner": user["id"]},
                          json=body.model_dump(), timeout=60)).json()


@router.get("/{app_id}/database/download")
async def download_database(app_id: str, user: dict = Depends(current_user)):
    """Its database as SQL"""
    app = (await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})).json()
    response = await apps.download(f"/apps/{app_id}/database/dump", owner=user["id"])
    name = "".join(c if (c.isascii() and c.isalnum()) or c in "-_" else "-" for c in app["name"]).strip("-") or "app"
    return StreamingResponse(response.aiter_raw(), media_type="application/sql", background=BackgroundTask(response.aclose),
                             headers={"Content-Disposition": f'attachment; filename="{name}-database.sql"'})


@router.get("/{app_id}/settings")
async def get_settings(app_id: str, user: dict = Depends(current_user)):
    """Its variables (a secret's value never comes back) and its data's size"""
    return (await forward("GET", f"/apps/{checked(app_id)}/settings", params={"owner": user["id"]})).json()


@router.put("/{app_id}/settings")
async def put_settings(app_id: str, body: Settings, user: dict = Depends(current_user)):
    """New variables: the app is started again with them"""
    response = await forward("PUT", f"/apps/{checked(app_id)}/settings", params={"owner": user["id"]},
                             json=body.model_dump(), timeout=120)
    found = response.json()
    return {**found, "app": public(found["app"])}


@router.get("/{app_id}/data/download")
async def download_data(app_id: str, user: dict = Depends(current_user)):
    """Its data folder, as a tar"""
    app = (await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})).json()
    response = await apps.download(f"/apps/{app_id}/data/download", owner=user["id"])
    name = "".join(c if (c.isascii() and c.isalnum()) or c in "-_" else "-" for c in app["name"]).strip("-") or "app"
    return StreamingResponse(response.aiter_raw(), media_type="application/x-tar", background=BackgroundTask(response.aclose),
                             headers={"Content-Disposition": f'attachment; filename="{name}-data.tar"'})


@router.get("/{app_id}/source/download")
async def download_source(app_id: str, user: dict = Depends(current_user)):
    """Its files with their history as a .git (a git working copy), as a zip"""
    app = (await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})).json()
    name = "".join(c if (c.isascii() and c.isalnum()) or c in "-_" else "-" for c in app["name"]).strip("-") or "app"
    response = await apps.download(f"/apps/{app_id}/source/download", owner=user["id"], name=name)
    return StreamingResponse(response.aiter_raw(), media_type="application/zip", background=BackgroundTask(response.aclose),
                             headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})


def labb_state(app_id: str, user_id: str) -> dict:
    key = app_keys.current(app_id, user_id)
    return {
        "scopes": key["scopes"] if key else [],
        "key": {k: key[k] for k in ("shown", "created_at", "last_used_at")} if key else None,
        "url": app_keys.LABB_URL,
        "available": [{"name": name, "description": text} for name, text in app_keys.SCOPES.items()],
    }


async def give_key(app_id: str, user_id: str, scopes: list[str]):
    """A new key for the app, given to it (it restarts with it); the one it had goes"""
    key = app_keys.make(app_id, user_id, scopes)
    try:
        await forward("PUT", f"/apps/{app_id}/labb", params={"owner": user_id}, timeout=120,
                      json={"env": {"LABB_URL": app_keys.LABB_URL, "LABB_API_KEY": key}})
    except HTTPException:
        app_keys.revoke(app_id)
        raise


@router.get("/{app_id}/labb")
async def get_labb(app_id: str, user: dict = Depends(current_user)):
    """What the app may use of labb, and its key (only its start)"""
    await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})
    return labb_state(app_id, user["id"])


@router.put("/{app_id}/labb")
async def put_labb(app_id: str, body: LabbUse, user: dict = Depends(current_user)):
    """Change what it may use: the first time, it gets a key (and restarts with it); none, and its key goes"""
    await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})
    scopes = app_keys.checked_scopes(body.scopes)
    had = app_keys.current(app_id, user["id"])
    if not scopes:
        if had:
            app_keys.revoke(app_id)
            await forward("PUT", f"/apps/{app_id}/labb", params={"owner": user["id"]}, json={"env": None}, timeout=120)
    elif had:
        app_keys.set_scopes(app_id, user["id"], scopes)
    else:
        await give_key(app_id, user["id"], scopes)
    return labb_state(app_id, user["id"])


@router.post("/{app_id}/labb/key")
async def new_labb_key(app_id: str, user: dict = Depends(current_user)):
    """A new key, for one that may have got out: the old one stops working now"""
    await forward("GET", f"/apps/{checked(app_id)}", params={"owner": user["id"]})
    had = app_keys.current(app_id, user["id"])
    if not had:
        raise HTTPException(409, "This app has no key: turn on something it may use first")
    await give_key(app_id, user["id"], had["scopes"])
    return labb_state(app_id, user["id"])


@router.post("/{app_id}/{action}")
async def change_app(app_id: str, action: Literal["stop", "start", "restart"], user: dict = Depends(current_user)):
    response = await forward("POST", f"/apps/{checked(app_id)}/{action}", params={"owner": user["id"]})
    return public(response.json())


@router.get("/{app_id}/logs")
async def app_logs(app_id: str, tail: int = 300, user: dict = Depends(current_user)):
    """The end of what the app wrote, for its page"""
    response = await forward("GET", f"/apps/{checked(app_id)}/logs", params={"owner": user["id"], "tail": tail})
    return response.json()


@router.delete("/{app_id}", status_code=204)
async def remove_app(app_id: str, user: dict = Depends(current_user)):
    await forward("DELETE", f"/apps/{checked(app_id)}", params={"owner": user["id"]})
    app_keys.revoke(app_id)
    notes.store.forget_app(app_id)
    return Response(status_code=204)
