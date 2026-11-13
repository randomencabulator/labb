"""labb connect: lets you start labb on this PC from the server's web page.

The PC dials out, so it works from behind a router: a POST to /pcs/connect stays open and
brings messages, answers, stops and the web page's questions (a folder's notes, a session's
context) as lines of JSON, and each reply's events go back with POST /pcs/events.
Each session is its own `labb --channel` process in its folder, with this PC's settings and permissions,
which stays between messages and closes after a while idle (the conversation is saved, and comes back).

What's decided here, not on the server: which folders can be used (only the ones named when
starting), which models (only this PC's), and whether anything runs without asking (only
--allow-all, given here). A job can't pick tools or permissions."""

import argparse
import collections
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

import httpx

from labb import llm, remote, settings
from labb.tools import notes
from labb.cli.render import console

# Replies written at once, and the processes kept, most of them idle
MAX_REPLIES = 3
MAX_SESSIONS = 10
IDLE_MINUTES = 30
IDLE_CHECK_SECONDS = 30
# The server pings more often than this, so silence means the connection is gone
READ_TIMEOUT_SECONDS = 60
MAX_BACKOFF_SECONDS = 30
SEND_EVERY_SECONDS = 0.4
# Text is joined into events up to this size, and what's longer is cut, saying so
MAX_MERGED_CHARS = 8_000
MAX_FIELD_CHARS = 200_000
BATCH_EVENTS = 200
STOP_WAIT_SECONDS = 5
RUN_ID = re.compile(r"^[0-9a-f-]{36}$")
THREAD_ID = re.compile(r"^cli-[0-9a-f-]{36}$")


def cut(text: str) -> str:
    if len(text) <= MAX_FIELD_CHARS:
        return text
    return f"{text[:MAX_FIELD_CHARS]}\n… ({len(text) - MAX_FIELD_CHARS} more characters not shown)"


def trimmed(event: dict) -> dict:
    """An event small enough to send: long approvals say they're cut, and long arguments are dropped"""

    if isinstance(event.get("detail"), str):
        event = {**event, "detail": cut(event["detail"])}
    if event.get("type") == "tool_call" and len(json.dumps(event.get("args", {}))) > MAX_FIELD_CHARS:
        event = {**event, "args": {"note": "too long to show"}}
    return event


class Outbox:
    """One reply's events on their way to the server, in order. Text is joined while it waits; a batch
    keeps its numbers until the server has it, so sending it twice is harmless"""

    def __init__(self, run_id: str, on_refused, on_sent=lambda: None):
        self.run_id = run_id
        self.on_refused = on_refused
        self.on_sent = on_sent
        self.pending: list[dict] = []
        self.sent = 0
        self.closed = False
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._send_loop, daemon=True)
        self.thread.start()

    def add(self, event: dict):
        with self.lock:
            last = self.pending[-1] if self.pending else None
            if (last and last["type"] == event["type"] and event["type"] in ("token", "thinking")
                    and len(last["content"]) < MAX_MERGED_CHARS):
                last["content"] += event["content"]
            else:
                self.pending.append(trimmed(event))

    def _send_loop(self):
        try:
            self._send_all()
        finally:
            self.on_sent()

    def _send_all(self):
        backoff = SEND_EVERY_SECONDS
        while True:
            with self.lock:
                batch = self.pending[:BATCH_EVENTS]
                del self.pending[:len(batch)]
                done = self.closed and not batch
            if done:
                return
            if not batch:
                time.sleep(SEND_EVERY_SECONDS)
                continue
            body = {"run_id": self.run_id, "events": [{"seq": self.sent + i + 1, "event": e} for i, e in enumerate(batch)]}
            while True:
                try:
                    response = httpx.post(f"{remote.server()}/pcs/events", json=body, headers=remote.headers(), timeout=30)
                    if response.status_code < 500:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            backoff = SEND_EVERY_SECONDS
            if response.is_success:
                self.sent += len(batch)
            else:
                # The server doesn't want this reply: it was deleted, or the key was revoked
                self.on_refused()
                with self.lock:
                    self.closed = True
                    self.pending.clear()
                return

    def close(self, wait: float | None = None):
        with self.lock:
            self.closed = True
        self.thread.join(wait)


class Session:
    """One session's `labb --channel` process, which stays between messages, and passing its events on.
    It answers one message at a time; `reply` is the one in progress."""

    def __init__(self, thread_id: str, folder: str, allow_all: bool, new_outbox, on_reply_end, on_exit):
        self.thread_id = thread_id
        self.folder = folder
        self.new_outbox = new_outbox
        self.on_reply_end = on_reply_end
        self.on_exit = on_exit
        self.reply: Outbox | None = None
        self.last_used = time.monotonic()
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.notes = collections.deque(maxlen=15)
        command = [sys.executable, "-m", "labb.cli.app", "--channel", *(["--allow-all"] if allow_all else [])]
        # Its own session: Ctrl-C here goes through stop(), so what's written so far is saved
        self.process = subprocess.Popen(command, cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, encoding="utf-8", errors="replace", bufsize=1,
                                        start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        self.write({"thread_id": thread_id})
        threading.Thread(target=self._read_notes, daemon=True).start()
        self.pump = threading.Thread(target=self._pump, daemon=True)
        self.pump.start()

    def alive(self) -> bool:
        return self.process.poll() is None

    def write(self, message: dict):
        with self.write_lock:
            try:
                self.process.stdin.write(json.dumps(message) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass

    def send(self, job: dict) -> bool:
        """Start answering this message; False if it's busy with another"""

        with self.lock:
            if self.reply is not None:
                return False
            self.reply = self.new_outbox(job["run_id"], self)
            self.last_used = time.monotonic()
            self.write({"type": "message", "run_id": job["run_id"], "content": job["content"], "title": job.get("title"),
                        "questions": job.get("questions", True), "model": job.get("model"), "answer_mode": job.get("answer_mode"),
                        "replace_from": job.get("replace_from"), "keep_attachments": job.get("keep_attachments", False)})
            return True

    def stop(self, run_id: str | None = None):
        self.write({"type": "stop", "run_id": run_id or (self.reply.run_id if self.reply else None)})

    def close(self):
        """No more messages: the process finishes what it's doing and ends"""
        with self.write_lock:
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass

    def _read_notes(self):
        for line in self.process.stderr:
            self.notes.append(line.rstrip())

    def _end_reply(self, kind: str):
        with self.lock:
            outbox, self.reply = self.reply, None
            self.last_used = time.monotonic()
        if outbox:
            # Without waiting for it to be sent: the next message shouldn't have to
            threading.Thread(target=outbox.close, daemon=True).start()
            self.on_reply_end(self, kind)

    def _pump(self):
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                continue
            if event["type"] == "context_answer":
                # For the web page's context panel, not part of a reply
                ask_id = event.pop("id", None)
                event.pop("type")
                threading.Thread(target=post_answer, args=(ask_id, event), daemon=True).start()
                continue
            with self.lock:
                outbox = self.reply
            if outbox is None:
                continue
            outbox.add(event)
            if event["type"] in ("done", "stopped"):
                self._end_reply(event["type"])
        code = self.process.wait()
        with self.lock:
            outbox = self.reply
        if outbox:
            why = "\n".join(self.notes) or f"it exited with code {code}"
            outbox.add({"type": "error", "message": f"labb on this PC stopped unexpectedly: {why}"})
            outbox.add({"type": "stopped", "stats": None})
            self._end_reply("crashed")
        self.on_exit(self)


class Connection:
    def __init__(self, folders: list[str], allow_all: bool):
        self.folders = folders
        self.allow_all = allow_all
        self.machine = socket.gethostname()
        # Found when it starts serving: that asks the providers
        self.models: list[str] = []
        self.sessions: dict[str, Session] = {}
        # The replies that aren't all on the server yet, so a reconnect can say they're still going
        self.outboxes: dict[str, Outbox] = {}
        self.seen: set[str] = set()
        self.lock = threading.Lock()
        threading.Thread(target=self._close_idle_loop, daemon=True).start()

    def _outbox(self, run_id: str, on_refused) -> Outbox:
        def sent():
            with self.lock:
                self.outboxes.pop(run_id, None)

        outbox = Outbox(run_id, on_refused, sent)
        with self.lock:
            self.outboxes[run_id] = outbox
        return outbox

    def _new_outbox(self, run_id: str, session: Session) -> Outbox:
        return self._outbox(run_id, lambda: session.stop(run_id))

    def refuse(self, run_id: str, why: str):
        outbox = self._outbox(run_id, lambda: None)
        outbox.add({"type": "error", "message": why})
        outbox.add({"type": "stopped", "stats": None})
        threading.Thread(target=outbox.close, daemon=True).start()
        console.print(f"[yellow]Refused a message: {why}[/]")

    def problem_with(self, job: dict) -> str | None:
        """Why this PC won't do this: what's checked here isn't up to the server"""

        if job.get("folder") not in self.folders:
            return f"{self.machine} doesn't serve that folder ({job.get('folder')})."
        if not THREAD_ID.match(str(job.get("thread_id", ""))):
            return "That isn't a session this PC can work on."
        if job.get("model") is not None and job["model"] not in self.models:
            return f"{self.machine} doesn't have the model {job['model']}."
        if job.get("answer_mode") not in (None, *llm.ANSWER_MODES):
            return f"There's no answer mode {job['answer_mode']}."
        if not isinstance(job.get("content"), (str, list)) or not job["content"]:
            return "That message is empty."
        return None

    def start(self, job: dict):
        run_id = job.get("run_id", "")
        if not RUN_ID.match(run_id):
            return
        with self.lock:
            if run_id in self.seen:
                return
            self.seen.add(run_id)
            replying = sum(session.reply is not None for session in self.sessions.values())
            session = self.sessions.get(job.get("thread_id"))
        if problem := self.problem_with(job):
            return self.refuse(run_id, problem)
        if replying >= MAX_REPLIES:
            return self.refuse(run_id, f"{self.machine} is already writing {MAX_REPLIES} replies; try again when one is done.")
        if session and session.folder != job["folder"]:
            return self.refuse(run_id, f"That session is in {session.folder}.")
        if session is None or not session.alive():
            self._make_room()
            try:
                session = Session(job["thread_id"], job["folder"], self.allow_all, self._new_outbox, self._reply_ended, self._exited)
            except OSError as e:
                return self.refuse(run_id, f"Couldn't start labb in {job['folder']}: {e}")
            with self.lock:
                self.sessions[job["thread_id"]] = session
        if not session.send(job):
            return self.refuse(run_id, "That session is still answering the last message.")
        console.print(f"[dim]In {job['folder']}:[/] {str(job.get('title') or '')[:60]}", highlight=False)

    def _make_room(self):
        """At most MAX_SESSIONS processes: the longest idle goes. Its conversation is saved, and comes back."""

        with self.lock:
            idle = sorted((s for s in self.sessions.values() if s.reply is None), key=lambda s: s.last_used)
            extra = idle[:max(0, len(self.sessions) + 1 - MAX_SESSIONS)]
            for session in extra:
                self.sessions.pop(session.thread_id, None)
        for session in extra:
            session.close()

    def close_idle(self, now: float | None = None):
        cutoff = (now if now is not None else time.monotonic()) - IDLE_MINUTES * 60
        with self.lock:
            idle = [s for s in self.sessions.values() if s.reply is None and s.last_used < cutoff]
            for session in idle:
                self.sessions.pop(session.thread_id, None)
        for session in idle:
            session.close()

    def _close_idle_loop(self):
        while True:
            time.sleep(IDLE_CHECK_SECONDS)
            self.close_idle()

    def _reply_ended(self, session: Session, kind: str):
        console.print(f"[dim]Finished ({kind}) in {session.folder}[/]")

    def _exited(self, session: Session):
        with self.lock:
            if self.sessions.get(session.thread_id) is session:
                del self.sessions[session.thread_id]

    def handle(self, message: dict) -> bool:
        """One line from the server; False to stop connecting"""

        match message.get("type"):
            case "job":
                self.start(message)
            case "answer" | "stop":
                with self.lock:
                    run_id = message.get("run_id", "")
                    session = next((s for s in self.sessions.values() if s.reply and s.reply.run_id == run_id), None)
                if session and message["type"] == "stop":
                    session.stop(run_id)
                elif session:
                    session.write({k: message.get(k) for k in ("type", "id", "allow", "reason", "text")})
            case "notes":
                threading.Thread(target=self.answer_notes, args=(message,), daemon=True).start()
            case "context":
                threading.Thread(target=self.answer_context, args=(message,), daemon=True).start()
            case "replaced":
                console.print(f"[yellow]Another labb connect took over as {self.machine}; this one stops.[/]")
                return False
        return True

    def answer_notes(self, message: dict):
        """Read or change a folder's notes for the web page's Notes"""
        answer = notes_answer(self.folders, message)
        if message.get("action") not in (None, "read") and "error" not in answer:
            console.print(f"[dim]The notes of {message.get('folder')} were changed from the web page.[/]")
        post_answer(message.get("id"), answer)

    def answer_context(self, message: dict):
        """The web page's context panel asks about a session: its own process answers, as it has
        the prompt, tools and settings (started for it if it isn't running)"""

        question = {k: message.get(k) for k in ("id", "thread_id", "what", "model", "answer_mode", "web", "number", "scale")}
        if problem := self.problem_with({**question, "folder": message.get("folder"), "content": "-"}):
            return post_answer(message.get("id"), {"error": problem})
        with self.lock:
            session = self.sessions.get(question["thread_id"])
        if session and session.folder != message["folder"]:
            return post_answer(message.get("id"), {"error": f"That session is in {session.folder}."})
        if session is None or not session.alive():
            self._make_room()
            try:
                session = Session(question["thread_id"], message["folder"], self.allow_all, self._new_outbox, self._reply_ended, self._exited)
            except OSError as e:
                return post_answer(message.get("id"), {"error": f"Couldn't start labb in {message['folder']}: {e}"})
            with self.lock:
                self.sessions[question["thread_id"]] = session
        session.last_used = time.monotonic()
        session.write({"type": "context", **question})

    def serve(self) -> int:
        """Stay connected until Ctrl-C (0), or until the server says not to (1)"""

        self.models = available_models(self.folders)
        backoff = 1
        announced = False
        while True:
            with self.lock:
                active = list(self.outboxes)
            body = {"machine": self.machine, "folders": self.folders, "version": remote.version(), "allow_all": self.allow_all,
                    "active": active, "models": self.models}
            try:
                timeout = httpx.Timeout(10, read=READ_TIMEOUT_SECONDS)
                with httpx.stream("POST", f"{remote.server()}/pcs/connect", json=body, headers=remote.headers(), timeout=timeout) as response:
                    if response.status_code == 401:
                        console.print("[red]The server didn't accept this PC's key:[/] run labb --login.")
                        return 1
                    if response.status_code >= 400:
                        response.read()
                        if response.status_code < 500:
                            console.print(f"[red]The server refused: {response.text[:300]}[/]")
                            return 1
                        raise httpx.HTTPError(f"{response.status_code}")
                    for line in response.iter_lines():
                        message = json.loads(line)
                        if message.get("type") == "hello":
                            backoff = 1
                            if not announced:
                                console.print(f"[green]Connected[/] to {remote.server()} as [bold]{self.machine}[/]. Ctrl-C to disconnect.")
                                announced = True
                        elif not self.handle(message):
                            return 1
            except (httpx.HTTPError, ValueError) as e:
                if announced:
                    console.print(f"[yellow]Lost the connection ({e.__class__.__name__}); trying again…[/]")
                    announced = False
                else:
                    console.print(f"[dim]Can't reach the server yet; trying again in {backoff}s…[/]")
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    def shut_down(self):
        """Stop what's running, so each saves what it has and tells the server"""

        with self.lock:
            sessions = list(self.sessions.values())
        for session in sessions:
            session.stop()
            session.close()
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        for session in sessions:
            try:
                session.process.wait(max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                session.process.kill()
            # Nothing may print while Python exits
            session.pump.join(max(1, deadline - time.monotonic()))
        with self.lock:
            outboxes = list(self.outboxes.values())
        for outbox in outboxes:
            outbox.close(max(1, deadline - time.monotonic()))


def post_answer(ask_id: str | None, answer: dict):
    """Answer what the server asked (POST /pcs/answers/{id}); the page gives up on its own if this fails"""
    try:
        httpx.post(f"{remote.server()}/pcs/answers/{ask_id}", json=answer, headers=remote.headers(), timeout=30)
    except httpx.HTTPError:
        pass


def notes_answer(folders: list[str], message: dict) -> dict:
    """A folder's notes (.labb/notes.md), after the change the message asks for: only in the
    folders this PC serves, named exactly"""

    folder = message.get("folder")
    if not isinstance(folder, str) or folder not in folders:
        return {"error": "This PC doesn't serve that folder."}
    path = notes.notes_file(folder)
    action = message.get("action") or "read"
    try:
        if action == "read":
            return {"text": notes.read_file(path)}
        return {"text": notes.change_file(path, action, str(message.get("text") or ""), str(message.get("topic") or ""), message.get("base"))}
    except notes.NotesError as e:
        return {"error": str(e), "conflict": isinstance(e, notes.Conflict)}
    except OSError as e:
        return {"error": f"Can't use the notes: {e}"}


def available_models(folders: list[str]) -> list[str]:
    """The models this PC can use, for the web page's picker, and all a message may ask for"""

    found = [llm.default.spec, *llm.listed_models()]
    for folder in folders:
        found += [spec for spec in settings.load(folder)[0].get("models", []) if isinstance(spec, str)]
    try:
        found += llm.provider_models()
    except Exception:
        pass
    return list(dict.fromkeys(found))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="labb connect", description="Let the server's web page start labb on this PC, in the folders you name, until you press Ctrl-C.")
    parser.add_argument("folders", nargs="*", metavar="FOLDER", help="the folders it can work in (default: this one)")
    parser.add_argument("--allow-all", action="store_true", help="run every command and edit without asking (careful: whoever gets into your account can then do anything in these folders, and more)")
    args = parser.parse_args(argv)

    if not remote.server():
        console.print("[red]No server to connect to.[/] Set LABB_SERVER in ~/.labb/.env, or install with the server's install.sh.")
        return 1
    if not remote.api_key():
        console.print("[red]Not logged in:[/] run labb --login first.")
        return 1
    folders = list(dict.fromkeys(os.path.realpath(folder) for folder in args.folders or ["."]))
    if missing := [folder for folder in folders if not os.path.isdir(folder)]:
        console.print(f"[red]Not a folder:[/] {', '.join(missing)}")
        return 1

    console.print("From your account on the server, sessions can run in:")
    for folder in folders:
        console.print(f"  {folder}", highlight=False)
    if args.allow_all:
        console.print("[bold yellow]--allow-all: nothing will ask first. Whoever gets into your account can run anything on this PC.[/]")
    else:
        console.print("[dim]Commands and edits still ask, in the web page; this PC's permissions apply.[/]")

    connection = Connection(folders, args.allow_all)
    try:
        return connection.serve()
    except KeyboardInterrupt:
        console.print("\n[dim]Disconnecting…[/]")
        connection.shut_down()
        return 0
