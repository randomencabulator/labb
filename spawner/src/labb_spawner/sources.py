"""Apps' source: a folder of files per app, made from a starter and kept on the spawner's volume at
SPAWNER_DATA/apps/<id>/src, next to its last build's log: its page in frontend/ (built by itself,
pages.py), and its server, if it has one, in backend/ (backend/Dockerfile, built from the app's
folder so it can copy db/ too).

Paths given to read or change a file are relative to the app's folder, and can never leave it:
no absolute paths, no `..`, no following a symlink out. Files and the folder have size limits.
"""

import html
import json
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException

from labb_spawner import limits

DATA = Path(os.getenv("SPAWNER_DATA", "/data"))
# The starters: spawner/starters/<name>/, each with a starter.json ({"title", "description", "order",
# "frontend"}). With "frontend", the app gets FRONTEND's React app as its frontend/ first (the
# starter's own frontend/ files go over it), and its choices.json is asked when the app is made.
STARTERS = Path(os.getenv("SPAWNER_STARTERS", Path(__file__).resolve().parents[2] / "starters"))
DEFAULT_STARTER = "react"
FRONTEND = "_frontend"
MAX_FILE = 1_000_000
MAX_SOURCE = 20_000_000
MAX_FILES = 500
# Text files the starter's {{APP_NAME}} is filled in, escaped for HTML
NAMED = (".html", ".htm")


def folder(app_id: str) -> Path:
    return DATA / "apps" / app_id


def source(app_id: str) -> Path:
    return folder(app_id) / "src"


def starters() -> list[dict]:
    found = []
    for meta in sorted(STARTERS.glob("*/starter.json")):
        about = json.loads(meta.read_text())
        frontend = bool(about.get("frontend"))
        found.append({"name": meta.parent.name, "title": about.get("title", meta.parent.name),
                      "description": about.get("description", ""), "order": about.get("order", 99),
                      "frontend": frontend, "choices": frontend_choices() if frontend else []})
    return sorted(found, key=lambda s: (s["order"], s["name"]))


def frontend_choices() -> list[dict]:
    """What's asked of a new app with the React frontend: [{"name", "title", "options": [{"name", "title", ...}]}]"""
    return json.loads((STARTERS / FRONTEND / "choices.json").read_text())


def picked(choices: list[dict], given: dict[str, str]) -> dict[str, str]:
    """Each choice's option: the one given, else its default (else its first); refused if not one of them"""
    names = {c["name"] for c in choices}
    if unknown := sorted(set(given) - names):
        raise HTTPException(400, f"Nothing to choose called {unknown[0]!r}")
    found = {}
    for choice in choices:
        options = [o["name"] for o in choice["options"]]
        value = given.get(choice["name"]) or next((o["name"] for o in choice["options"] if o.get("default")), options[0])
        if value not in options:
            raise HTTPException(400, f"{choice['title']} can be {', '.join(options)}, not {value!r}")
        found[choice["name"]] = value
    return found


def make(app_id: str, starter: str, name: str, choices: dict[str, str] | None = None):
    """A new app's folder, from a starter (and the React frontend, if it has one), with its name and
    the choices made in its HTML"""
    about = next((s for s in starters() if s["name"] == starter), None)
    if about is None:
        raise HTTPException(400, f"No starter called {starter!r}")
    values = picked(about["choices"], choices or {})
    target = source(app_id)
    # Not what running a starter's code by hand leaves behind
    leftovers = shutil.ignore_patterns("starter.json", "choices.json", "__pycache__", "*.pyc", "node_modules", "dist")
    if about["frontend"]:
        shutil.copytree(STARTERS / FRONTEND, target / "frontend", ignore=leftovers)
    shutil.copytree(STARTERS / starter, target, ignore=leftovers, dirs_exist_ok=True)
    fills = {"{{APP_NAME}}": name, **{f"{{{{{key.upper()}}}}}": value for key, value in values.items()}}
    for path in target.rglob("*"):
        if path.is_file() and path.suffix in NAMED:
            text = path.read_text()
            for placeholder, value in fills.items():
                text = text.replace(placeholder, html.escape(value))
            path.write_text(text)


def remove(app_id: str):
    shutil.rmtree(folder(app_id), ignore_errors=True)


def has_source(app_id: str) -> bool:
    """Whether it has files of its own: a page (frontend/), a server (backend/), or a Dockerfile at
    its top (from before pages were built apart)"""
    root = source(app_id)
    return (root / "frontend").is_dir() or (root / "backend").is_dir() or (root / "Dockerfile").is_file()


def inside(app_id: str, relative: str) -> Path:
    """The file at `relative` in the app's folder; refused if it would be anywhere else"""
    root = source(app_id)
    # As written: "a/b.txt", never "/a", "a//b", "./a", "a/../b" or backslashes
    parts = relative.split("/")
    if "\\" in relative or any(part in ("", ".", "..") for part in parts):
        raise HTTPException(400, f"Not a path in the app: {relative!r}")
    path = root.joinpath(*parts)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise HTTPException(400, f"Not a path in the app: {relative!r}")
    return path


def files(app_id: str) -> list[dict]:
    root = source(app_id)
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            found.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size})
    return found


def read(app_id: str, relative: str) -> dict:
    path = inside(app_id, relative)
    if not path.is_file():
        raise HTTPException(404, f"No file {relative!r}")
    if path.stat().st_size > limits.get("app_file_mb"):
        raise HTTPException(413, f"{relative} is over {limits.get('app_file_mb') // 1_000_000} MB")
    try:
        return {"path": relative, "content": path.read_bytes().decode("utf-8")}
    except UnicodeDecodeError:
        raise HTTPException(415, f"{relative} isn't text")


def write(app_id: str, relative: str, content: str) -> dict:
    path = inside(app_id, relative)
    data = content.encode("utf-8")
    if len(data) > limits.get("app_file_mb"):
        raise HTTPException(413, f"A file can be {limits.get('app_file_mb') // 1_000_000} MB at most")
    existing = files(app_id)
    before = next((f["size"] for f in existing if f["path"] == path.relative_to(source(app_id)).as_posix()), None)
    if before is None and len(existing) >= limits.get("app_files"):
        raise HTTPException(413, f"An app can have {limits.get('app_files')} files at most")
    if sum(f["size"] for f in existing) - (before or 0) + len(data) > limits.get("app_source_mb"):
        raise HTTPException(413, f"An app's files can be {limits.get('app_source_mb') // 1_000_000} MB in all")
    if path.is_dir():
        raise HTTPException(400, f"{relative} is a folder")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": relative, "size": len(data)}


def delete(app_id: str, relative: str):
    path = inside(app_id, relative)
    if not path.is_file():
        raise HTTPException(404, f"No file {relative!r}")
    path.unlink()
    # Folders it leaves empty go too
    root = source(app_id)
    parent = path.parent
    while parent != root and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


# How much a search gives back: lines (with the matches in them) and how long a line's text can be
MAX_MATCHES = 500
MAX_LINE = 240


def search(app_id: str, query: str, case: bool = False, regex: bool = False, word: bool = False) -> dict:
    """Where `query` is in the app's text files: {"matches": [{path, line, text, ranges}], "files", "more"}.
    `line` counts from 1; `ranges` are [start, end] in `text`, which is the line, cut when long.
    Files whose path has it are in `files`."""
    return search_in(source(app_id), files(app_id), query, case, regex, word, limits.get("app_file_mb"))


def search_in(root, listed_files: list[dict], query: str, case: bool, regex: bool, word: bool, largest: int) -> dict:
    """search, over any folder's files (`listed_files` as `files` gives them; ones over `largest` bytes skipped):
    the artifacts' search uses it too"""
    if not query:
        return {"matches": [], "files": [], "more": False}
    pattern = query if regex else re.escape(query)
    if word:
        pattern = rf"\b(?:{pattern})\b"
    try:
        found = re.compile(pattern, 0 if case else re.IGNORECASE)
    except re.error as e:
        raise HTTPException(400, f"Not a regular expression: {e}")
    matches, names, more = [], [], False
    for listed in listed_files:
        if found.search(listed["path"]):
            names.append(listed["path"])
        if more or listed["size"] > largest:
            continue
        try:
            text = (root / listed["path"]).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            ranges = [[m.start(), m.end()] for m in found.finditer(line) if m.end() > m.start()]
            if not ranges:
                continue
            if len(matches) == MAX_MATCHES:
                more = True
                break
            # A long line: from a little before its first match
            start = max(0, ranges[0][0] - 40) if len(line) > MAX_LINE else 0
            cut = line[start:start + MAX_LINE]
            matches.append({"path": listed["path"], "line": number, "column": ranges[0][0], "text": cut,
                            "ranges": [[a - start, min(b - start, len(cut))] for a, b in ranges if a - start < len(cut)]})
    return {"matches": matches, "files": names, "more": more}


def size(app_id: str) -> int:
    return sum(f["size"] for f in files(app_id))


# The last build: its log, and whether it worked

def save_build(app_id: str, number: int, ok: bool, log: str, error: str | None):
    folder(app_id).mkdir(parents=True, exist_ok=True)
    (folder(app_id) / "build.log").write_text(log)
    (folder(app_id) / "build.json").write_text(json.dumps({
        "build": number, "ok": ok, "error": error, "at": datetime.now(UTC).isoformat(timespec="seconds")}))


def last_build(app_id: str) -> dict:
    try:
        about = json.loads((folder(app_id) / "build.json").read_text())
        log = (folder(app_id) / "build.log").read_text()
    except (OSError, ValueError):
        return {"build": 0, "ok": None, "error": None, "at": None, "log": ""}
    return {**about, "log": log}
