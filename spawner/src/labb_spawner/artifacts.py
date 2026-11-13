"""Artifacts: pages the agent builds in a labb chat, each a folder of its own on the spawner's volume,
apart from the apps: SPAWNER_DATA/artifacts/<name>/, with

- `artifact.json`: whose it is (`owner`), the chat that made it (`thread`), its title, when it was
  made and its last build;
- `src/`: its files, made from the template (spawner/artifacts/template: Next.js, Base UI
  components, charts and diagrams, with an AGENTS.md), which the model changes;
- `out/` and `build.log`: the last build's static site and what it wrote.

The name (a slug of the title and 6 random characters) is also where labb-sites serves it,
/artifacts/<name>/, so it can't be guessed. Like an app, an artifact is only its folder: no table.
Every call takes the owner, and someone else's artifact is a 404.

A build runs the builder image (app.py's artifact_image, from spawner/artifacts/) with the files
mounted read-only and no network: its node_modules are the image's, so an artifact can only use the
template's dependencies, and one whose package.json asks for others is refused before it builds.
"""

import io
import json
import os
import re
import secrets
import shutil
import tarfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from fastapi import HTTPException

from labb_spawner import limits, sources

TEMPLATE = Path(os.getenv("SPAWNER_ARTIFACT_TEMPLATE", Path(__file__).resolve().parents[2] / "artifacts" / "template"))
MAX_PER_OWNER = int(os.getenv("ARTIFACT_MAX", "50"))
MAX_FILE = 1_000_000
MAX_SOURCE = 10_000_000
MAX_FILES = 300
MAX_LOG = 200_000
# What a template copy leaves out: its local install and builds
NOT_COPIED = shutil.ignore_patterns("node_modules", ".next", "out", "next-env.d.ts", "*.tsbuildinfo", ".gitignore", "package-lock.json")
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# A build: (name, its src/, where its site goes) → (worked, log, error)
Runner = Callable[[str, Path, Path], tuple[bool, str, str | None]]

_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def root() -> Path:
    return sources.DATA / "artifacts"


def folder(name: str) -> Path:
    return root() / name


def source(name: str) -> Path:
    return folder(name) / "src"


def lock_for(name: str) -> threading.Lock:
    with _locks_lock:
        return _locks.setdefault(name, threading.Lock())


def slug(title: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return words[:40].rstrip("-") or "page"


def _about(name: str) -> dict | None:
    try:
        return json.loads((folder(name) / "artifact.json").read_text())
    except (OSError, ValueError):
        return None


def _save(about: dict):
    path = folder(about["name"]) / "artifact.json"
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(about))
    os.replace(partial, path)


def mine(name: str, owner: str) -> dict:
    """The artifact, if it's this owner's; a 404 otherwise, whether it's someone else's or not there"""
    about = _about(name) if NAME.match(name) else None
    if about is None or about.get("owner") != owner:
        raise HTTPException(404, f"No artifact {name!r}")
    return about


def listing(owner: str, thread: str | None = None) -> list[dict]:
    found = []
    if root().is_dir():
        for meta in root().glob("*/artifact.json"):
            about = _about(meta.parent.name)
            if about and about.get("owner") == owner and (thread is None or about.get("thread") == thread):
                found.append(about)
    return sorted(found, key=lambda about: about.get("created", ""), reverse=True)


def make(owner: str, thread: str | None, title: str) -> dict:
    """A new artifact: the template's files, under a name made from the title"""
    if len(listing(owner)) >= limits.get("artifact_count"):
        raise HTTPException(409, f"You have {limits.get('artifact_count')} artifacts, the most there can be: remove some first")
    if not TEMPLATE.is_dir():
        raise HTTPException(503, "The artifact template isn't here")
    root().mkdir(parents=True, exist_ok=True)
    while True:
        name = f"{slug(title)}-{''.join(secrets.choice('abcdefghijkmnpqrstuvwxyz23456789') for _ in range(6))}"
        if not folder(name).exists():
            break
    folder(name).mkdir()
    shutil.copytree(TEMPLATE, source(name), ignore=NOT_COPIED)
    about = {"name": name, "owner": owner, "thread": thread, "title": title.strip()[:200],
             "created": datetime.now(UTC).isoformat(timespec="seconds"), "build": None}
    _save(about)
    return about


def remove(name: str, owner: str):
    mine(name, owner)
    with lock_for(name):
        shutil.rmtree(folder(name), ignore_errors=True)


def inside(name: str, relative: str) -> Path:
    """The file at `relative` in the artifact's files; refused if it would be anywhere else"""
    base = source(name)
    parts = relative.split("/")
    if "\\" in relative or any(part in ("", ".", "..") for part in parts) or parts[0] in ("node_modules", ".next", "out"):
        raise HTTPException(400, f"Not a path in the artifact: {relative!r}")
    path = base.joinpath(*parts)
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise HTTPException(400, f"Not a path in the artifact: {relative!r}")
    return path


def files(name: str) -> list[dict]:
    base = source(name)
    if not base.is_dir():
        return []
    return [{"path": path.relative_to(base).as_posix(), "size": path.stat().st_size}
            for path in sorted(base.rglob("*")) if path.is_file() and not path.is_symlink()]


def read(name: str, relative: str) -> dict:
    path = inside(name, relative)
    if not path.is_file():
        raise HTTPException(404, f"No file {relative!r}")
    if path.stat().st_size > limits.get("artifact_file_mb"):
        raise HTTPException(413, f"{relative} is over {limits.get('artifact_file_mb') // 1_000_000} MB")
    try:
        return {"path": relative, "content": path.read_bytes().decode("utf-8")}
    except UnicodeDecodeError:
        raise HTTPException(415, f"{relative} isn't text")


def write(name: str, relative: str, content: str) -> dict:
    path = inside(name, relative)
    data = content.encode("utf-8")
    if len(data) > limits.get("artifact_file_mb"):
        raise HTTPException(413, f"A file can be {limits.get('artifact_file_mb') // 1_000_000} MB at most")
    with lock_for(name):
        existing = files(name)
        shown = path.relative_to(source(name)).as_posix()
        before = next((f["size"] for f in existing if f["path"] == shown), None)
        if before is None and len(existing) >= limits.get("artifact_files"):
            raise HTTPException(413, f"An artifact can have {limits.get('artifact_files')} files at most")
        if sum(f["size"] for f in existing) - (before or 0) + len(data) > limits.get("artifact_source_mb"):
            raise HTTPException(413, f"An artifact's files can be {limits.get('artifact_source_mb') // 1_000_000} MB in all")
        if path.is_dir():
            raise HTTPException(400, f"{relative} is a folder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {"path": relative, "size": len(data), "created": before is None}


def delete(name: str, relative: str):
    path = inside(name, relative)
    with lock_for(name):
        if not path.is_file():
            raise HTTPException(404, f"No file {relative!r}")
        path.unlink()
        base, parent = source(name), path.parent
        while parent != base and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


def other_packages(name: str) -> str | None:
    """Why its package.json can't build with the image's node_modules, or None when it has the
    template's dependencies (the only ones there are: builds have no network)"""
    if not (source(name) / "package.json").is_file():
        shutil.copy(TEMPLATE / "package.json", source(name) / "package.json")
    try:
        theirs = json.loads((source(name) / "package.json").read_text())
    except ValueError as e:
        return f"package.json isn't valid JSON: {e}"
    ours = json.loads((TEMPLATE / "package.json").read_text())
    for field in ("dependencies", "devDependencies"):
        if (theirs.get(field) or {}) != (ours.get(field) or {}):
            have = ", ".join(sorted({**ours.get("dependencies", {}), **ours.get("devDependencies", {})}))
            return (f"package.json's {field} differ from the template's. Only what's installed can be used "
                    f"(there's no internet when it builds): {have}. Put package.json back as it was.")
    return None


def build(name: str, owner: str, runner: Runner) -> dict:
    """Builds its site into out/: {"ok", "error", "log", "at", "files"}; the last good out/ stays
    when one fails"""
    about = mine(name, owner)
    with lock_for(name):
        problem = other_packages(name)
        if problem:
            ok, log, error = False, "", problem
        else:
            fresh = folder(name) / "out.new"
            shutil.rmtree(fresh, ignore_errors=True)
            fresh.mkdir()
            # The builder runs as its own user, which writes the site here
            fresh.chmod(0o777)
            ok, log, error = runner(name, source(name), fresh)
            if ok and not (fresh / "index.html").is_file():
                ok, error = False, "The build made no index.html: app/page.tsx should be the page"
            if ok:
                shutil.rmtree(folder(name) / "out", ignore_errors=True)
                os.replace(fresh, folder(name) / "out")
            else:
                shutil.rmtree(fresh, ignore_errors=True)
        (folder(name) / "build.log").write_text(log[-MAX_LOG:])
        result = {"ok": ok, "error": error, "at": datetime.now(UTC).isoformat(timespec="seconds")}
        _save({**about, "build": result})
    return {**result, "log": log[-MAX_LOG:], "files": len(site_files(name)) if ok else 0}


def has_site(name: str) -> bool:
    """Whether a build of it worked: its last good site is in out/"""
    return (folder(name) / "out" / "index.html").is_file()


def site_files(name: str) -> list[Path]:
    out = folder(name) / "out"
    return [path for path in sorted(out.rglob("*")) if path.is_file() and not path.is_symlink()] if out.is_dir() else []


def site(name: str, owner: str) -> bytes:
    """The last good build's site as a .tar.gz, for labb to publish"""
    mine(name, owner)
    found = site_files(name)
    if not found:
        raise HTTPException(409, "It hasn't been built yet")
    out = folder(name) / "out"
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as archive:
        for path in found:
            archive.add(path, arcname=path.relative_to(out).as_posix(), recursive=False)
    return packed.getvalue()


def next_error(log: str, code: int) -> str:
    """What a failed `next build` says went wrong, without the noise around it: its type errors
    (`app/page.tsx(4,64): error TS2820: …`), else Turbopack's error with the file, line and the code
    around it, else its last lines"""
    lines = [line.rstrip() for line in log.replace("/template/", "").splitlines()]
    typed = [line for line in lines if re.search(r"\): error TS\d+:", line)]
    if typed:
        more = f"\n… and {len(typed) - 20} more" if len(typed) > 20 else ""
        return "The type check failed:\n" + "\n".join(typed[:20]) + more
    start = next((i for i, line in enumerate(lines) if "Build error occurred" in line or "Failed to compile" in line), None)
    if start is not None:
        found = []
        for line in lines[start + 1:start + 40]:
            if line.lstrip().startswith("at ") or line.startswith("Next.js build worker exited"):
                break
            found.append(line)
        return "The build failed:\n" + "\n".join(found).strip()
    return f"The build failed (exit code {code}):\n" + "\n".join(lines[-15:])
