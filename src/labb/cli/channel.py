"""A session that `labb connect` drives (hidden flag `--channel`): one process in one folder, which
stays between messages, so background commands and MCP servers keep running as in the terminal.

The first line on stdin says which session; after that stdin brings messages, answers, stops and
the web page's questions about the session's context, and each reply's events (and those answers)
go out on stdout. A closed stdin means whoever started this is gone, or that
the session has been idle for a while: what's running stops, and the process ends.

Approvals are for this one action: nothing here can answer "always" or turn the checks off."""

import json
import queue
import sys
import threading
from dataclasses import dataclass
from uuid import uuid4


# stdout carries a reply's events and the answers about the context, from different threads
_stdout_lock = threading.Lock()


def write_line(item: dict):
    """One line of JSON on stdout, whole"""
    line = json.dumps(item, ensure_ascii=False, default=str) + "\n"
    with _stdout_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


@dataclass
class Message:
    run_id: str
    content: str | list
    title: str
    questions: bool = True
    model: str | None = None
    answer_mode: str | None = None
    replace_from: int | None = None
    keep_attachments: bool = False

    @classmethod
    def of(cls, line: dict) -> "Message":
        return cls(line["run_id"], line["content"], str(line.get("title") or "A message"), bool(line.get("questions", True)),
                   line.get("model"), line.get("answer_mode"), line.get("replace_from"), bool(line.get("keep_attachments")))


class Channel:
    def __init__(self, stream):
        self.thread_id: str = json.loads(stream.readline())["thread_id"]
        self.stream = stream
        self.messages: queue.Queue[Message | None] = queue.Queue()
        self.waiting: dict[str, queue.Queue] = {}
        self.current: Message | None = None
        self.emit = None
        self.stop_requested: threading.Event | None = None
        self.stopped_runs: set[str] = set()
        self.lock = threading.Lock()
        self.closed = threading.Event()
        # Answers the context panel's questions about this session (cli/app.py's answer_context)
        self.on_context = None
        threading.Thread(target=self._listen, daemon=True).start()

    def next_message(self) -> Message | None:
        """The next message to answer; None once the channel is closed"""
        return self.messages.get()

    def attach(self, message: Message, configurable: dict, emit, stop_requested: threading.Event):
        """Make this reply ask through the channel"""

        with self.lock:
            self.current, self.emit, self.stop_requested = message, emit, stop_requested
            # A stop that came before the reply started
            if message.run_id in self.stopped_runs or self.closed.is_set():
                stop_requested.set()
        configurable["approve"] = self.approve
        if message.questions:
            configurable["ask_user"] = self.ask_user

    def _listen(self):
        for line in self.stream:
            try:
                message = json.loads(line)
                kind = message.get("type")
                if kind == "message":
                    self.messages.put(Message.of(message))
                elif kind == "stop":
                    self._stop(message.get("run_id"))
                elif kind == "answer" and (waiting := self.waiting.get(message.get("id"))):
                    waiting.put_nowait(message)
                elif kind == "context" and self.on_context is not None:
                    threading.Thread(target=self._context, args=(message,), daemon=True).start()
            except (ValueError, KeyError, AttributeError, queue.Full):
                continue
        with self.lock:
            self.closed.set()
            if self.stop_requested:
                self.stop_requested.set()
        self.messages.put(None)

    def _context(self, message: dict):
        try:
            answer = self.on_context(message)
        except Exception as e:
            answer = {"error": f"Couldn't look at the context: {e}"}
        write_line({"type": "context_answer", "id": message.get("id"), **answer})

    def _stop(self, run_id: str | None):
        with self.lock:
            if self.current and self.current.run_id == run_id and self.stop_requested:
                self.stop_requested.set()
            else:
                self.stopped_runs.add(run_id)

    def relay(self, events: queue.Queue, before_final) -> dict | None:
        """Pass a reply's events on, to the reply's end. `before_final` runs before its last event, so
        whoever sees the reply end can count on what that does (the server having the session)."""

        final = None
        failed = False
        while (event := events.get()) is not None:
            if event["type"] == "error":
                failed = True
            if event["type"] in ("done", "stopped"):
                before_final()
                final = {**event, "failed": failed}
            write_line(event)
        return final

    def _wait(self, event: dict) -> dict | None:
        """Send a question and wait for its answer; None if the reply is stopped first"""

        stop_requested, emit = self.stop_requested, self.emit
        answer: queue.Queue = queue.Queue(maxsize=1)
        self.waiting[event["id"]] = answer
        emit(event)
        try:
            while not stop_requested.is_set():
                try:
                    message = answer.get(timeout=0.25)
                except queue.Empty:
                    continue
                emit({"type": "answered", "id": event["id"], "allow": message.get("allow"), "text": message.get("text")})
                return message
            return None
        finally:
            self.waiting.pop(event["id"], None)

    def approve(self, command: str, needed: list[str], always_ask: str | None, title: str = "Run this command?",
                view: str | None = None, handover: bool = False):
        answer = self._wait({"type": "approval", "id": str(uuid4()), "title": title, "detail": command, "because": always_ask})
        if answer is None:
            return ("no", None)
        return "yes" if answer.get("allow") is True else ("no", answer.get("reason"))

    def ask_user(self, question: str, options: list[str]) -> str | None:
        answer = self._wait({"type": "question", "id": str(uuid4()), "question": question, "options": options})
        return answer.get("text") if answer else None
