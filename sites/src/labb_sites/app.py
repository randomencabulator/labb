"""labb-sites: takes static websites as archives, for the Caddy in front of it to serve at
/artifacts/<name>/.

`PUT /artifacts/<name>` with a zip or tar (gz, bz2, xz) of a site's files unpacks it into a folder of
its own and swaps it in at once, so visitors see the old site or the new one, never half of each.
`GET /artifacts` lists them, `GET /artifacts/<name>` says what one has, `DELETE /artifacts/<name>`
removes it.

On disk (SITES_DIR, the volume Caddy serves read-only), each site is a link, `<name>`, to the folder
of its latest upload, `.versions/<name>/<stamp>`; the one before stays until the next upload, so a
page that was loading when the link moved still finds its files. Uploads are unpacked in
`.staging/` first, on the same disk, so the move is a rename. Only folders and plain files are
taken from an archive: links, devices and paths outside it are refused.
"""

import json
import os
import re
import secrets
import shutil
import stat
import sys
import tarfile
import threading
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

SITES_DIR = Path(os.getenv("SITES_DIR", "/srv/sites"))
# Where the token is kept when SITES_TOKEN isn't set: made on the first start, on a volume of its
# own (never the one that's served)
TOKEN_FILE = os.getenv("SITES_TOKEN_FILE", "")
# A site's size unpacked, and its files: the defaults of labb's Settings → Limits, which keeps what an
# admin changes them to in limits.json next to the token (`GET|PUT /limits`)
MAX_BYTES = int(os.getenv("SITES_MAX_MB", "100")) * 1024 * 1024
MAX_FILES = int(os.getenv("SITES_MAX_FILES", "10000"))
# The limits: (key, label, description, default's name, from it to the number shown, back, min, max, unit)
LIMITS = [
    ("site_mb", "Published page size", "How big a published page may be, unpacked.", "MAX_BYTES",
     lambda b: b // 1024 // 1024, lambda mb: mb * 1024 * 1024, 1, 100_000, "MB"),
    ("site_files", "Files per published page", "How many files a published page may have.", "MAX_FILES",
     int, int, 1, 1_000_000, ""),
]
# The address sites are at, for the answers' `url`; else what the request came to
PUBLIC_URL = os.getenv("SITES_PUBLIC_URL", "").rstrip("/")
# A site's name is its path: lowercase letters, digits and hyphens, like a DNS label
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# What zipping a folder on a Mac or Windows adds, never part of the site
JUNK = {"__MACOSX", ".DS_Store", "Thumbs.db"}
CHUNK = 1024 * 1024

app = FastAPI()
_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def load_token() -> str:
    """SITES_TOKEN, or the one in TOKEN_FILE, made there if there's none yet; "" without either"""

    if token := os.getenv("SITES_TOKEN", ""):
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
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(token + "\n")
    os.replace(partial, path)
    return token


TOKEN = load_token()
if not TOKEN:
    print("labb-sites: no SITES_TOKEN or SITES_TOKEN_FILE, so anything that can reach it can change the sites",
          file=sys.stderr)


class Refused(Exception):
    """An upload that can't be a site, with why; answered 400 (413 when it's too big)"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _limits_file() -> Path | None:
    return Path(TOKEN_FILE).with_name("limits.json") if TOKEN_FILE else None


# What admins saved, when there's no file to keep it in
_saved_here: dict[str, int] = {}


def saved_limits() -> dict[str, int]:
    path = _limits_file()
    if path is None:
        return dict(_saved_here)
    try:
        values = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    known = {limit[0]: limit for limit in LIMITS}
    return {key: value for key, value in (values.items() if isinstance(values, dict) else [])
            if key in known and isinstance(value, int) and not isinstance(value, bool) and known[key][6] <= value <= known[key][7]}


def _limit(key: str) -> int:
    """In bytes or files"""
    _, _, _, name, shown, back, *_ = next(limit for limit in LIMITS if limit[0] == key)
    found = saved_limits()
    return back(found[key]) if key in found else globals()[name]


def max_bytes() -> int:
    return _limit("site_mb")


def max_files() -> int:
    return _limit("site_files")


def described_limits() -> list[dict]:
    values = saved_limits()
    found = []
    for key, label, description, name, shown, _, least, most, unit in LIMITS:
        first = shown(globals()[name])
        found.append({"key": key, "group": "Artifacts", "label": label, "description": description, "default": first,
                      "min": least, "max": most, "unit": unit, "value": values.get(key, first)})
    return found


def authorized(request: Request):
    if TOKEN and not secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {TOKEN}"):
        raise HTTPException(401, "A token is needed: Authorization: Bearer <token>")


def checked(name: str) -> str:
    if not NAME.match(name):
        raise HTTPException(400, "A site's name is lowercase letters, digits and hyphens (up to 63), "
                                 "not starting or ending with a hyphen")
    return name


def lock_for(name: str) -> threading.Lock:
    with _locks_lock:
        return _locks.setdefault(name, threading.Lock())


def versions_of(name: str) -> Path:
    return SITES_DIR / ".versions" / name


def staging() -> Path:
    path = SITES_DIR / ".staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def parts_of(name: str) -> list[str] | None:
    """An archive member's path as its parts, None for junk to leave out; Refused for one that
    would land outside the site"""

    name = name.replace("\\", "/")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if name.startswith("/") or ".." in parts or any("\0" in part for part in parts):
        raise Refused(f"{name!r} is outside the archive's folder")
    if not parts or any(part in JUNK or part.startswith("._") for part in parts):
        return None
    return parts


def members(archive: Path):
    """(parts, is_folder, open) for each member of a zip or tar; Refused for anything else in one"""

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zipped:
            for info in zipped.infolist():
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise Refused(f"{info.filename!r} is a link; a site can only have files and folders")
                if (parts := parts_of(info.filename)) is not None:
                    yield parts, info.is_dir(), lambda info=info: zipped.open(info)
        return
    try:
        tarred = tarfile.open(archive, "r:*")
    except tarfile.TarError:
        raise Refused("That isn't a zip or tar archive") from None
    with tarred:
        try:
            for member in tarred:
                if not (member.isfile() or member.isdir()):
                    raise Refused(f"{member.name!r} is a link or a device; a site can only have files and folders")
                if (parts := parts_of(member.name)) is not None:
                    yield parts, member.isdir(), lambda member=member: tarred.extractfile(member)
        except tarfile.TarError as error:
            raise Refused(f"The archive is broken: {error}") from None


def unpack(archive: Path, into: Path) -> tuple[int, int]:
    """The archive's files under `into` (made), as (files, bytes), within MAX_FILES and MAX_BYTES"""

    into.mkdir(mode=0o755)
    try:
        return _unpack(archive, into)
    except (FileExistsError, NotADirectoryError, IsADirectoryError) as error:
        raise Refused(f"The archive has a file and a folder of the same name ({Path(error.filename or '').name})") from None


def _unpack(archive: Path, into: Path) -> tuple[int, int]:
    files = total = 0
    for parts, is_folder, opened in members(archive):
        path = into.joinpath(*parts)
        if is_folder:
            path.mkdir(mode=0o755, parents=True, exist_ok=True)
            continue
        files += 1
        if files > max_files():
            raise Refused(f"A site can have up to {max_files()} files", 413)
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        # Counted as it's written, not from the archive's own sizes, which it could lie about
        with opened() as source, open(path, "wb") as target:
            while chunk := source.read(CHUNK):
                total += len(chunk)
                if total > max_bytes():
                    raise Refused(f"A site can be up to {max_bytes() // 1024 // 1024} MB unpacked", 413)
                target.write(chunk)
        path.chmod(0o644)
    if not files:
        raise Refused("The archive has no files")
    return files, total


def root_of(unpacked: Path) -> Path:
    """The folder the site starts at: one folder holding everything (a zipped folder) is that folder"""

    if (unpacked / "index.html").exists():
        return unpacked
    entries = list(unpacked.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return unpacked


def publish(name: str, archive: Path) -> dict:
    """The archive unpacked and swapped in as the site `name`, with the previous upload kept until
    the next one"""

    work = staging() / f"{name}-{secrets.token_hex(6)}"
    try:
        unpack(archive, work)
        root = root_of(work)
        with lock_for(name):
            versions = versions_of(name)
            versions.mkdir(parents=True, exist_ok=True)
            # In the order they were made, even two in the same second, so the one before is known
            now = time.time_ns()
            stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now // 10**9)) + f".{now % 10**9:09d}"
            if (newest := max((path.name for path in versions.iterdir()), default="")) >= stamp:
                stamp = newest + "0"
            os.rename(root, versions / stamp)
            versions.chmod(0o755)
            (versions / stamp).chmod(0o755)
            # A link made next to the site's, then put in its place in one step
            link = SITES_DIR / name
            new = SITES_DIR / f".{name}.new"
            new.unlink(missing_ok=True)
            os.symlink(f".versions/{name}/{stamp}", new)
            os.replace(new, link)
            served = {stamp, previous_of(name, stamp)}
            for old in versions.iterdir():
                if old.name not in served:
                    shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return describe(name)


def previous_of(name: str, stamp: str) -> str | None:
    older = sorted(path.name for path in versions_of(name).iterdir() if path.name < stamp)
    return older[-1] if older else None


def describe(name: str) -> dict | None:
    link = SITES_DIR / name
    if not link.is_symlink():
        return None
    folder = SITES_DIR / os.readlink(link)
    files = total = 0
    for root, _, names in os.walk(folder):
        for each in names:
            files += 1
            total += os.lstat(os.path.join(root, each)).st_size
    updated = datetime.fromtimestamp(os.lstat(link).st_mtime, UTC).isoformat(timespec="seconds")
    return {"name": name, "files": files, "bytes": total, "updated": updated,
            "index": (folder / "index.html").is_file()}


def remove(name: str) -> bool:
    with lock_for(name):
        link = SITES_DIR / name
        if not link.is_symlink():
            return False
        link.unlink()
        shutil.rmtree(versions_of(name), ignore_errors=True)
        return True


def tidy():
    """What an upload cut off left behind: its staging folders, and versions no site links to"""

    shutil.rmtree(SITES_DIR / ".staging", ignore_errors=True)
    for new in SITES_DIR.glob(".*.new"):
        new.unlink(missing_ok=True)
    for versions in (SITES_DIR / ".versions").glob("*"):
        if not (SITES_DIR / versions.name).is_symlink():
            shutil.rmtree(versions, ignore_errors=True)


def with_url(site: dict, request: Request) -> dict:
    base = PUBLIC_URL
    if not base:
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        base = f"{scheme}://{host}"
    return {**site, "url": f"{base}/artifacts/{site['name']}/"}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/limits", dependencies=[Depends(authorized)])
def get_limits():
    """For labb's Settings → Limits"""
    return described_limits()


@app.put("/limits", dependencies=[Depends(authorized)])
def save_limits(values: dict[str, int | None]):
    """Change some; null puts one back to its default. Refused whole if one can't be."""
    global _saved_here
    current, shown = saved_limits(), {limit["key"]: limit for limit in described_limits()}
    for key, value in values.items():
        if key not in shown:
            raise HTTPException(400, f"There's no limit {key}")
        limit = shown[key]
        if value is None or value == limit["default"]:
            current.pop(key, None)
        elif isinstance(value, bool) or not limit["min"] <= value <= limit["max"]:
            raise HTTPException(400, f"{limit['label']} is a whole number from {limit['min']} to {limit['max']}")
        else:
            current[key] = value
    if (path := _limits_file()) is None:
        _saved_here = current
    else:
        partial = path.with_name(path.name + ".partial")
        partial.write_text(json.dumps(current))
        os.replace(partial, path)
    return described_limits()


@app.get("/artifacts", dependencies=[Depends(authorized)])
def list_sites(request: Request):
    names = sorted(path.name for path in SITES_DIR.iterdir() if path.is_symlink() and NAME.match(path.name)) \
        if SITES_DIR.is_dir() else []
    return {"artifacts": [with_url(site, request) for name in names if (site := describe(name))]}


@app.get("/artifacts/{name}", dependencies=[Depends(authorized)])
def get_site(name: str, request: Request):
    if not (site := describe(checked(name))):
        raise HTTPException(404, f"There's no site {name!r}")
    return with_url(site, request)


@app.put("/artifacts/{name}", dependencies=[Depends(authorized)])
async def put_site(name: str, request: Request):
    checked(name)
    if int(request.headers.get("content-length") or 0) > max_bytes():
        raise HTTPException(413, f"An archive can be up to {max_bytes() // 1024 // 1024} MB")
    archive = staging() / f".upload-{secrets.token_hex(8)}"
    try:
        size = 0
        with open(archive, "wb") as file:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes():
                    raise HTTPException(413, f"An archive can be up to {max_bytes() // 1024 // 1024} MB")
                file.write(chunk)
        if not size:
            raise HTTPException(400, "Send the site as a zip or tar archive in the request's body")
        try:
            site = await run_in_threadpool(publish, name, archive)
        except Refused as refused:
            raise HTTPException(refused.status, str(refused)) from None
    finally:
        archive.unlink(missing_ok=True)
    return with_url(site, request)


@app.delete("/artifacts/{name}", dependencies=[Depends(authorized)])
def delete_site(name: str):
    if not remove(checked(name)):
        raise HTTPException(404, f"There's no site {name!r}")
    return {"deleted": name}


def main():
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    tidy()
    if TOKEN_FILE and not os.getenv("SITES_TOKEN"):
        print(f"labb-sites: the token for uploads is in {TOKEN_FILE}", file=sys.stderr)
    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "4200")))


if __name__ == "__main__":
    main()
