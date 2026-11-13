"""labb-sandbox: runs a workflow's Python step and answers with its `result`.

`POST /run {"code", "inputs", "timeout"}` → `{"ok": true, "result", "stdout"}` or
`{"ok": false, "error"}`. Each run is a fresh `python -I` process in a folder of its own, with
limits on CPU time, memory, processes, file size and open files, killed with everything it
started when its time is up.

Run as root (as in Docker), each run also gets a user of its own from a small pool, so runs
can't see each other. The container adds the rest: no network, a read-only system, and only
the capabilities switching users needs (see compose.yaml). Run as anyone else (a checkout), the
limits still apply, but there's no network isolation: don't expose it.
"""

import json
import os
import resource
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from labb_sandbox import settings

# Where the token is kept when SANDBOX_TOKEN isn't set: made on the first start, on a volume the
# server reads it from (see compose.yaml), readable only by the server's user, not by the runs
TOKEN_FILE = os.getenv("SANDBOX_TOKEN_FILE", "")
TOKEN_OWNER = int(os.getenv("SANDBOX_TOKEN_OWNER", "1000"))
MEMORY_MB = int(os.getenv("SANDBOX_MEMORY_MB", "256"))
JOBS = max(1, int(os.getenv("SANDBOX_JOBS", "4")))
FIRST_UID = int(os.getenv("SANDBOX_FIRST_UID", "20000"))
MAX_TIMEOUT = 120
MAX_CODE = 20_000
MAX_INPUT = 2_000_000
MAX_OUTPUT = 64_000
MAX_RESULT = 1_000_000
WRAPPER = Path(__file__).with_name("wrapper.py")
JOBS_DIR = Path(os.getenv("SANDBOX_DIR", tempfile.gettempdir())) / "labb-sandbox"

app = FastAPI()
_uids = list(range(FIRST_UID, FIRST_UID + JOBS))
_uids_lock = threading.Lock()
_slots = threading.BoundedSemaphore(JOBS)
ISOLATED = os.geteuid() == 0


def load_token() -> str:
    """SANDBOX_TOKEN, or the one in TOKEN_FILE, made there if there's none yet; "" without either"""

    if token := os.getenv("SANDBOX_TOKEN", ""):
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
    if ISOLATED:
        os.chown(partial, TOKEN_OWNER, TOKEN_OWNER)
    os.replace(partial, path)
    return token


TOKEN = load_token()
if not TOKEN:
    print("labb-sandbox: no SANDBOX_TOKEN or SANDBOX_TOKEN_FILE, so anything that can reach it can run code",
          file=sys.stderr)


class Job(BaseModel):
    code: str
    inputs: dict = {}
    timeout: int = 30


def authorized(request: Request):
    if TOKEN and not secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {TOKEN}"):
        raise HTTPException(401, "A token is needed")


def end_everything_of(uid: int):
    """Before a user goes back to the pool: kill every process it still has (one that left its
    session with setsid outlives killpg, and would see the next run of that user's files), and
    delete what it left outside its folder (in /tmp, say)"""

    for _ in range(5):
        found = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                status = (entry / "status").read_text()
            except OSError:
                continue
            line = next((l for l in status.splitlines() if l.startswith("Uid:")), "")
            if line and int(line.split()[1]) == uid:
                found = True
                try:
                    os.kill(int(entry.name), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if not found:
            break
        time.sleep(0.05)
    for top in {Path(tempfile.gettempdir()), Path("/dev/shm")}:
        for root, folders, files in os.walk(top, topdown=False):
            for name in files + folders:
                path = Path(root) / name
                try:
                    if path.lstat().st_uid == uid:
                        path.unlink() if not path.is_dir() or path.is_symlink() else shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    pass


def _limits(timeout: int, uid: int | None):
    """In the child, before Python starts: its own session (so it can be killed whole), limits, its user"""

    def apply():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (timeout + 1, timeout + 1))
        memory = MEMORY_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if uid is not None:
            resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)

    return apply


def run(job: Job) -> dict:
    timeout = max(1, min(job.timeout, MAX_TIMEOUT))
    with _slots:
        with _uids_lock:
            uid = _uids.pop() if ISOLATED else None
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        if uid is not None:
            # Runs can go into their own folder but not list the others
            JOBS_DIR.chmod(0o711)
        folder = Path(tempfile.mkdtemp(dir=JOBS_DIR))
        try:
            (folder / "code.py").write_text(job.code, encoding="utf-8")
            (folder / "inputs.json").write_text(json.dumps(job.inputs), encoding="utf-8")
            if uid is not None:
                for path in (folder, folder / "code.py", folder / "inputs.json"):
                    os.chown(path, uid, uid)
                folder.chmod(0o700)
            env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(folder), "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONIOENCODING": "utf-8", "LANG": "C.UTF-8"}
            process = subprocess.Popen(
                [sys.executable, "-I", str(WRAPPER), str(folder)], cwd=folder, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, preexec_fn=_limits(timeout, uid),
            )
            started = time.monotonic()
            try:
                output, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                return {"ok": False, "error": f"The code took longer than {timeout} seconds"}
            finally:
                # Anything it started in the background goes too
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            stdout = output.decode("utf-8", errors="replace")[-MAX_OUTPUT:]
            error_file, result_file = folder / "error.txt", folder / "result.json"
            if error_file.exists():
                return {"ok": False, "error": error_file.read_text(encoding="utf-8", errors="replace"), "stdout": stdout}
            if process.returncode != 0 or not result_file.exists():
                reason = "it ran out of memory or time" if process.returncode in (-9, -24, 137) else f"exit code {process.returncode}"
                return {"ok": False, "error": f"The code stopped ({reason})", "stdout": stdout}
            if result_file.stat().st_size > MAX_RESULT:
                return {"ok": False, "error": f"result is over {MAX_RESULT // 1000} KB", "stdout": stdout}
            return {"ok": True, "result": json.loads(result_file.read_text(encoding="utf-8")), "stdout": stdout,
                    "seconds": round(time.monotonic() - started, 3)}
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            if uid is not None:
                end_everything_of(uid)
                with _uids_lock:
                    _uids.append(uid)


@app.get("/settings", dependencies=[Depends(authorized)])
def get_settings():
    """For labb's Settings → Services (settings.py)"""
    return settings.described()


@app.put("/settings", dependencies=[Depends(authorized)])
def save_settings(values: dict[str, bool | int | float | None]):
    """Change some; null puts one back to its default"""
    return settings.save(values)


@app.post("/run", dependencies=[Depends(authorized)])
def run_job(job: Job):
    if len(job.code) > MAX_CODE:
        raise HTTPException(400, f"The code is over {MAX_CODE} characters")
    if len(json.dumps(job.inputs)) > MAX_INPUT:
        raise HTTPException(400, "The inputs are too big")
    return run(job)


@app.get("/health")
def health():
    return {"ok": True, "isolated": ISOLATED}


# What an admin changed in labb's Settings → Services
settings.apply()


def main():
    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "4000")))


if __name__ == "__main__":
    main()
