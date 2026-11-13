"""A shell command tool for the terminal (labb).

Commands run with bash in the working directory. Read-only ones run right away;
others need the user's approval (see permissions.py), which the terminal asks
for through the `approve` function in the run's config.
"""

import atexit
import itertools
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from langchain.tools import tool
from langchain_core.runnables import RunnableConfig

from labb import permissions
from labb.tools.files import resolve

NEEDS_WORKDIR = True

MAX_TIMEOUT = 600
MAX_OUTPUT_CHARS = 30_000
# read_output: how long it may wait for more output
MAX_WAIT = 60


@dataclass
class Job:
    """A command running in the background (run_command with background=True)"""

    id: str
    command: str
    process: subprocess.Popen
    log: Path
    started: float = field(default_factory=time.time)
    # How much of the log read_output has given back already
    read: int = 0

    def status(self) -> str:
        code = self.process.poll()
        return "running" if code is None else f"exited with code {code}"


# The background commands of this process (one labb session), by id
jobs: dict[str, Job] = {}
_job_numbers = itertools.count(1)
_jobs_lock = threading.Lock()


def kill(process: subprocess.Popen, grace: float = 0):
    """Stop a command and everything it started: SIGTERM first if there's a grace period, then SIGKILL"""

    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 5)) if grace else ((signal.SIGKILL, 5),):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


@atexit.register
def stop_all():
    """When labb exits, its background commands go with it"""
    with _jobs_lock:
        running = list(jobs.values())
        jobs.clear()
    for job in running:
        if job.process.poll() is None:
            kill(job.process, grace=1)
        job.log.unlink(missing_ok=True)


def shorten(output: str) -> str:
    """Keep the start and the end of long output"""
    if len(output) <= MAX_OUTPUT_CHARS:
        return output
    half = MAX_OUTPUT_CHARS // 2
    return f"{output[:half]}\n… {len(output) - MAX_OUTPUT_CHARS:,} characters cut …\n{output[-half:]}"


def ask_permission(command: str, workdir: str, configurable: dict) -> str | None:
    """None if the command may run, otherwise why not (for the model)"""

    if configurable.get("mode") == "plan":
        # Plan mode: only the read-only commands, not even the ones allowed in permissions.json
        result = permissions.check(command, set(), workdir)
        if result.ok:
            return None
        # A read that only needs asking for its path (a secret, or outside the folder) is still a read
        if result.needed or not (result.always_ask or "").startswith(permissions.READS):
            return (
                "Not run: you're in plan mode, where only read-only commands run. Don't change anything "
                "yet; finish your plan and the user will switch modes when they approve it."
            )
    else:
        if configurable.get("allow_all"):
            return None
        result = permissions.check(command, set(permissions.allowed(workdir)), workdir)
        if result.ok:
            return None

    approve = configurable.get("approve")
    if approve is None:
        return (
            "Not run: this command needs the user's approval, which isn't possible here. "
            "Start labb without -p, run it with --allow-all, or add the command to .labb/permissions.json."
        )
    answer = approve(command, result.needed, result.always_ask)
    if answer == "always" and not result.always_ask:
        permissions.allow(workdir, result.needed)
    if answer in ("yes", "always"):
        return None
    reason = answer[1] if isinstance(answer, tuple) and len(answer) > 1 and answer[1] else None
    said = f" They said: {reason.rstrip('.')}." if reason else ""
    return f"Not run: the user didn't allow this command.{said} Don't run it again unless they ask."


@tool
def run_command(command: str, timeout: int = 120, background: bool = False, config: RunnableConfig = None) -> str:
    """Run a shell command (bash) in the working directory and return its exit code and output.

    Use it for anything the shell can do: creating folders and files, moving or deleting
    them, running scripts, tests, builds, git, package managers. Commands that change
    something may need the user's approval.

    Args:
        command: The command line, e.g. `mkdir -p src/utils` or `npm test`
        timeout: Seconds before the command is killed (default 120, at most 600); not for background commands
        background: Start it and return right away, for commands that keep running (dev servers, watchers); read its output with read_output and stop it with stop_command
    """
    try:
        root, _ = resolve(config, ".")
    except ValueError as e:
        return f"Error: {e}"
    configurable = config.get("configurable") or {}

    if refusal := ask_permission(command, str(root), configurable):
        return refusal
    if background:
        return start_job(command, root)

    process = subprocess.Popen(
        ["bash", "-c", command],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # Its own process group, so stopping kills everything it started
        start_new_session=True,
    )
    # Read in a thread, so a command with lots of output can't block on a full pipe
    output = []
    reader = threading.Thread(target=lambda: output.append(process.stdout.read()), daemon=True)
    reader.start()

    timeout = max(1, min(timeout, MAX_TIMEOUT))
    started = time.monotonic()
    stop_requested = configurable.get("stop_requested")
    ended = None
    while process.poll() is None:
        if stop_requested is not None and stop_requested.is_set():
            ended = f"Stopped by the user after {time.monotonic() - started:.0f}s."
        elif time.monotonic() - started > timeout:
            ended = f"Timed out after {timeout}s and was killed."
        if ended:
            kill(process)
            break
        time.sleep(0.1)
    reader.join(timeout=5)

    text = output[0].decode("utf-8", errors="replace") if output and output[0] else ""
    head = ended or f"Exit code {process.returncode}"
    return f"{head}\n{shorten(text)}" if text else f"{head}\n(no output)"


def start_job(command: str, root: Path) -> str:
    descriptor, log = tempfile.mkstemp(prefix="labb-job-", suffix=".log")
    with os.fdopen(descriptor, "wb") as out:
        process = subprocess.Popen(["bash", "-c", command], cwd=root, stdin=subprocess.DEVNULL, stdout=out,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    with _jobs_lock:
        job = Job(f"job{next(_job_numbers)}", command, process, Path(log))
        jobs[job.id] = job
    return (f"Started in the background as {job.id}. Read its output with read_output(\"{job.id}\"), "
            f"and stop it with stop_command(\"{job.id}\") when it's no longer needed.")


def find_job(job_id: str) -> Job | str:
    with _jobs_lock:
        job = jobs.get(job_id.strip())
    if job is None:
        listed = ", ".join(jobs) or "none"
        return f"Error: there's no background command {job_id} (running or finished: {listed})"
    return job


def new_output(job: Job) -> str:
    """What the job printed since the last read, keeping the start and end if it's long"""
    try:
        with job.log.open("rb") as file:
            file.seek(job.read)
            data = file.read()
    except OSError:
        return ""
    job.read += len(data)
    return shorten(data.decode("utf-8", errors="replace"))


@tool
def read_output(job_id: str, wait: int = 0, config: RunnableConfig = None) -> str:
    """Read what a background command (run_command with background=True) printed since you last read it, and whether it's still running.

    Args:
        job_id: The id run_command gave, e.g. `job1`
        wait: Seconds to wait first for it to print something or end, e.g. while a server starts (at most 60)
    """
    job = find_job(job_id)
    if isinstance(job, str):
        return job
    deadline = time.monotonic() + max(0, min(wait, MAX_WAIT))
    stop_requested = ((config or {}).get("configurable") or {}).get("stop_requested")
    while time.monotonic() < deadline and job.process.poll() is None:
        if job.log.stat().st_size > job.read or (stop_requested is not None and stop_requested.is_set()):
            break
        time.sleep(0.2)
    text = new_output(job)
    return f"{job.id} ({job.command}): {job.status()}\n{text or '(no new output)'}"


@tool
def stop_command(job_id: str, config: RunnableConfig = None) -> str:
    """Stop a background command (run_command with background=True) and everything it started.

    Args:
        job_id: The id run_command gave, e.g. `job1`
    """
    job = find_job(job_id)
    if isinstance(job, str):
        return job
    was = job.status()
    if job.process.poll() is None:
        kill(job.process, grace=3)
    text = new_output(job)
    with _jobs_lock:
        jobs.pop(job.id, None)
    job.log.unlink(missing_ok=True)
    said = f"Stopped {job.id}" if was == "running" else f"{job.id} had already {was}; removed it"
    last = f"Its last output:\n{text}" if text else "(no new output)"
    return f"{said} ({job.command}).\n{last}"


TOOLS = [run_command, read_output, stop_command]

PROMPT = (
    "You can run shell commands in the working directory with `run_command`. Use the file tools to look "
    "around, and `run_command` to run things (tests, builds, git) and for changes no other tool makes. Commands can't read input, so don't start "
    "interactive programs. Each command starts in the working directory: `cd` doesn't carry over to the "
    "next one, so chain with `&&` instead. Commands that change something may need the user's approval; "
    "if they don't allow one, don't try to get around it. For commands that keep running (dev "
    "servers, watchers), use `background=True`, check them with `read_output`, and stop them "
    "with `stop_command` when you're done."
)
