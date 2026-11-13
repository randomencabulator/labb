"""The Artifacts page: the web pages the model built in your chats (tools/artifacts.py), all in one
place, to open, look through and delete. They're on the spawner (each one's files and last build)
and labb-sites (the published site); this only ever shows and changes the person's own.

    GET    /artifacts                      yours, newest first, each with its link and the chat it's from
    GET    /artifacts/{name}               one, with its files' paths and sizes
    GET    /artifacts/{name}/files/{path}  a file's text
    PUT    /artifacts/{name}/files/{path}  write a file (the page's file editor, the same as the apps')
    DELETE /artifacts/{name}/files/{path}  delete a file
    GET    /artifacts/{name}/search        where some text is in its files
    POST   /artifacts/{name}/publish       build it and publish it, as publish_artifact does
    DELETE /artifacts/{name}               its files and its published site
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from labb.chats import conn, lock
from labb.server import app_tools, artifact_tools
from labb.server.auth_routes import current_user

router = APIRouter(prefix="/artifacts")


def _name(name: str) -> str:
    if not artifact_tools.NAME.match(name or ""):
        raise HTTPException(404, "No such artifact")
    return name


def _call(method: str, path: str, user_id: str, **kwargs):
    if not artifact_tools.enabled():
        raise HTTPException(503, "This server can't build web pages (the spawner or labb-sites isn't set up)")
    try:
        return app_tools.call(method, path, user_id, **kwargs)
    except ValueError as e:
        raise HTTPException(404 if str(e).startswith("No artifact") or str(e).startswith("No file") else 502, str(e))


def _workflow(thread: str | None, user_id: str) -> str | None:
    """The name of the workflow whose Web page step made it (its thread is wf-<run>-<step>)"""
    if not thread or not thread.startswith("wf-"):
        return None
    from labb.server.studio import store
    run = store.get_run(thread.split("-")[1])
    found = store.get_workflow(run["workflow_id"]) if run and run.get("user_id") == user_id else None
    return found["name"] if found else "a workflow"


def _shown(artifact: dict, base: str, chats: dict[str, str]) -> dict:
    build = artifact.get("build") or {}
    thread = artifact.get("thread")
    # Published once a build worked; a failed build since leaves the last good one up
    published = bool(artifact.get("site"))
    return {
        "name": artifact["name"], "title": artifact.get("title") or artifact["name"], "created": artifact.get("created"),
        "thread": thread, "chat": chats.get(thread or ""), "workflow": _workflow(thread, artifact.get("owner", "")), "url": artifact_tools.url_of(base, artifact["name"]) if published else None,
        "build": {"ok": build.get("ok"), "at": build.get("at"), "error": build.get("error")} if build else None,
    }


def _chat_titles(user_id: str, threads: list[str]) -> dict[str, str]:
    """The titles of the chats they came from, those that are still there"""
    if not threads:
        return {}
    marks = ",".join("?" * len(threads))
    with lock:
        rows = conn.execute(f"SELECT thread_id, title FROM chats WHERE user_id = ? AND thread_id IN ({marks})",
                            (user_id, *threads)).fetchall()
    return {thread: title or "Untitled chat" for thread, title in rows}


def _base(request: Request) -> str:
    return artifact_tools.public_base(request.url.scheme, request.headers.get("host", ""))


@router.get("")
def list_artifacts(request: Request, user: dict = Depends(current_user)):
    if not artifact_tools.enabled():
        return {"available": False, "artifacts": []}
    made = _call("GET", "/artifacts", user["id"]).json()
    chats = _chat_titles(user["id"], sorted({a["thread"] for a in made if a.get("thread")}))
    return {"available": True, "artifacts": [_shown(a, _base(request), chats) for a in made]}


@router.get("/{name}")
def get_artifact(name: str, request: Request, user: dict = Depends(current_user)):
    name = _name(name)
    artifact = _call("GET", f"/artifacts/{name}", user["id"]).json()
    files = _call("GET", f"/artifacts/{name}/files", user["id"]).json()
    shown = _shown(artifact, _base(request), _chat_titles(user["id"], [artifact["thread"]] if artifact.get("thread") else []))
    # The template's examples are there to copy from, not the page's own
    return {**shown, "files": [f for f in files if not f["path"].startswith("examples/")],
            "examples": sum(f["path"].startswith("examples/") for f in files)}


@router.get("/{name}/files/{path:path}")
def read_file(name: str, path: str, user: dict = Depends(current_user)):
    name = _name(name)
    return _call("GET", artifact_tools._file(name, path), user["id"]).json()


class File(BaseModel):
    content: str = Field(max_length=5_000_000)


@router.put("/{name}/files/{path:path}")
def write_file(name: str, path: str, body: File, user: dict = Depends(current_user)):
    name = _name(name)
    return _call("PUT", f"/artifacts/{name}/files/{quote(path)}", user["id"], json={"content": body.content}).json()


@router.delete("/{name}/files/{path:path}", status_code=204)
def delete_file(name: str, path: str, user: dict = Depends(current_user)):
    name = _name(name)
    _call("DELETE", f"/artifacts/{name}/files/{quote(path)}", user["id"])
    return Response(status_code=204)


@router.get("/{name}/search")
def search_files(name: str, q: str, case: bool = False, regex: bool = False, word: bool = False,
                 user: dict = Depends(current_user)):
    """Where some text is in its files: the lines it's on, and the files whose path has it"""
    name = _name(name)
    found = _call("GET", f"/artifacts/{name}/search", user["id"],
                  params={"q": q, "case": case, "regex": regex, "word": word}).json()
    # As in its file list: the template's examples aren't the page's own
    return {**found, "matches": [m for m in found["matches"] if not m["path"].startswith("examples/")],
            "files": [f for f in found["files"] if not f.startswith("examples/")]}


@router.post("/{name}/publish")
def publish(name: str, request: Request, user: dict = Depends(current_user)):
    """Builds it and, if that worked, publishes it: {"ok", "message"} (the build's error, with the file's lines)"""
    name = _name(name)
    if not artifact_tools.enabled():
        raise HTTPException(503, "This server can't build web pages (the spawner or labb-sites isn't set up)")
    # As the chat's tool does, so the link and the build's error come out the same
    config = {"configurable": {"user_id": user["id"], "sites_base": _base(request)}}
    try:
        said = artifact_tools.Backend().publish(config, name)
    except ValueError as e:
        raise HTTPException(502, str(e))
    failed = said.startswith("Error:")
    return {"ok": not failed, "message": said.removeprefix("Error: ") if failed else "Published"}


@router.delete("/{name}", status_code=204)
def delete_artifact(name: str, user: dict = Depends(current_user)):
    name = _name(name)
    _call("DELETE", f"/artifacts/{name}", user["id"])
    try:
        artifact_tools.sites("DELETE", f"/artifacts/{name}")
    except ValueError as e:
        # The files are gone; the site is only a copy, and would stay up until it's removed by hand
        raise HTTPException(502, f"Its files are deleted, but its published page couldn't be: {e}")
    return Response(status_code=204)
