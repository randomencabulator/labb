"""Apps' pages: each app's frontend/, built apart from its server and served by its front (front/:
Caddy), so changing the page is only changing its files. A change to frontend/ builds it again by
itself a moment later (`changed`, DEBOUNCE_SECONDS after the last change, so a few files written
together build once); `settle` builds what's pending now and waits, for whoever needs to know how it went.

On the spawner's volume, next to the app's src/:
- `page-work/`: frontend/'s files copied, built there (`npm install`, `npm run build`), and kept
  with node_modules between builds, so the next one installs only what changed
- `pages/<n>/`: a build's dist/; `pages/current` points at the one served, swapped in one step, so
  the page never shows half a build, and a build that fails leaves the last good one
- `page.json`: the last build: {"build", "ok", "error", "at", "digest", "served", "log"}

The build itself is Docker's, given by app.py (`runner(app_id, work) -> (ok, log, error)`).
"""

import hashlib
import json
import os
import shutil
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from labb_spawner import sources

DEBOUNCE_SECONDS = float(os.getenv("APP_PAGE_DEBOUNCE_SECONDS", "1.5"))
MAX_LOG = 100_000
FRONTEND = "frontend"
# What a build leaves in page-work/ that isn't the app's own files, kept for the next one
KEPT = {"node_modules", "package-lock.json", "dist", ".npm"}

Runner = Callable[[str, Path], tuple[bool, str, str | None]]

_lock = threading.Lock()
_timers: dict[str, threading.Timer] = {}
_building: dict[str, threading.Lock] = {}


def site(app_id: str) -> Path:
    """What the front serves (mounted at /srv): the builds and `current`"""
    return sources.folder(app_id) / "pages"


def work(app_id: str) -> Path:
    return sources.folder(app_id) / "page-work"


def has_page(app_id: str) -> bool:
    return (sources.source(app_id) / FRONTEND / "package.json").is_file()


def _files(app_id: str) -> list[str]:
    prefix = FRONTEND + "/"
    return [f["path"][len(prefix):] for f in sources.files(app_id) if f["path"].startswith(prefix)]


def digest_of(app_id: str) -> str | None:
    """frontend/'s files, as one hash: a build of the same files would make the same page"""
    if not has_page(app_id):
        return None
    root = sources.source(app_id) / FRONTEND
    found = hashlib.sha256()
    for name in sorted(_files(app_id)):
        found.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return found.hexdigest()


def last(app_id: str) -> dict:
    try:
        return json.loads((sources.folder(app_id) / "page.json").read_text())
    except (OSError, ValueError):
        return {"build": 0, "ok": None, "error": None, "at": None, "digest": None, "served": None, "log": ""}


def state(app_id: str) -> dict:
    """The last build, and whether one is waiting or running"""
    with _lock:
        pending = app_id in _timers or (app_id in _building and _building[app_id].locked())
    return {**last(app_id), "page": has_page(app_id), "pending": pending,
            "changed": has_page(app_id) and digest_of(app_id) != last(app_id).get("digest")}


def changed(app_id: str, runner: Runner):
    """frontend/ changed: build it again in a moment (again from then, if it changes once more)"""
    with _lock:
        if timer := _timers.pop(app_id, None):
            timer.cancel()
        timer = threading.Timer(DEBOUNCE_SECONDS, _when_quiet, (app_id, runner))
        timer.daemon = True
        _timers[app_id] = timer
        timer.start()


def _when_quiet(app_id: str, runner: Runner):
    with _lock:
        if _timers.get(app_id) is threading.current_thread():
            _timers.pop(app_id)
    try:
        ensure(app_id, runner)
    except Exception as e:  # the page stays as it was; the next change or build tries again
        print(f"labb-spawner: app {app_id}'s page didn't build: {e}", file=sys.stderr)


def settle(app_id: str, runner: Runner, again: bool = False) -> dict:
    """What's pending built now (or the build running finished), and how the last build went;
    `again` builds once more after a failed build of the same files (a download that failed, say)"""
    with _lock:
        if timer := _timers.pop(app_id, None):
            timer.cancel()
    return ensure(app_id, runner, again)


def forget(app_id: str):
    with _lock:
        if timer := _timers.pop(app_id, None):
            timer.cancel()
        _building.pop(app_id, None)


def ensure(app_id: str, runner: Runner, again: bool = False) -> dict:
    """The page built from frontend/ as it is now, unless the last build was of the same files
    (which, worked or not, would go the same way again; `again` tries a failed one once more)"""
    with _lock:
        held = _building.setdefault(app_id, threading.Lock())
    with held:
        digest = digest_of(app_id)
        before = last(app_id)
        if digest is None or (before.get("digest") == digest and (before.get("ok") or not again)):
            return before
        return _build(app_id, digest, before, runner)


def _sync(app_id: str) -> Path:
    """page-work/ as frontend/ is, but for what builds leave there"""
    root, target = sources.source(app_id) / FRONTEND, work(app_id)
    target.mkdir(parents=True, exist_ok=True)
    wanted = set(_files(app_id))
    for path in sorted(target.rglob("*"), reverse=True):
        relative = path.relative_to(target)
        if relative.parts[0] in KEPT:
            continue
        if path.is_symlink() or (path.is_file() and relative.as_posix() not in wanted):
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for name in wanted:
        data = (root / name).read_bytes()
        path = target / name
        if not path.is_file() or path.read_bytes() != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    shutil.rmtree(target / "dist", ignore_errors=True)
    return target


def _build(app_id: str, digest: str, before: dict, runner: Runner) -> dict:
    built = _sync(app_id)
    ok, log, error = runner(app_id, built)
    dist = built / "dist"
    if ok and not (dist / "index.html").is_file():
        ok, error = False, "The page's build made no dist/index.html (frontend/'s `npm run build` should, with Vite)"
    number = int(before.get("build") or 0) + 1
    served = before.get("served")
    if ok:
        folder = site(app_id)
        folder.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(folder / str(number), ignore_errors=True)
        os.replace(dist, folder / str(number))
        # A link made apart and moved over the old one: the front sees one or the other, never neither
        (folder / "current.new").unlink(missing_ok=True)
        os.symlink(str(number), folder / "current.new")
        os.replace(folder / "current.new", folder / "current")
        # This build and the one before it
        for old in folder.iterdir():
            if old.name.isdigit() and int(old.name) not in (number, served):
                shutil.rmtree(old, ignore_errors=True)
        served = number
    if error:
        log += f"\nThe page's build failed: {error}\n"
    found = {"build": number, "ok": ok, "error": error, "at": datetime.now(UTC).isoformat(timespec="seconds"),
             "digest": digest, "served": served, "log": log[-MAX_LOG:]}
    sources.folder(app_id).mkdir(parents=True, exist_ok=True)
    (sources.folder(app_id) / "page.json").write_text(json.dumps(found))
    return found
