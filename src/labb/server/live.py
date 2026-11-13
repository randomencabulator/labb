"""Replies in the web chat keep going when the page goes away (a reload, a dropped connection,
a laptop put to sleep): `POST /stop` is what stops one. Meanwhile each reply's events are
kept here, so a page can join it again from its start (`GET /chat/live`) the way it joins a
reply on a PC: a `user` event says which turn it is, then everything so far, then what comes.

A reply's events stay only while it runs: once it's done, what it wrote is in /history. Like
`app.runs`, this lives in memory, so the server must stay one process.
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Callable

# How often a quiet stream says it's still there, so proxies and browsers keep it open
PING_SECONDS = 15


class LiveReply:
    """One reply's events so far, and the streams following it"""

    def __init__(self, user_id: str, start: dict):
        self.user_id = user_id
        # The `user` event the page shows the turn with, and which turn it is
        self.start = start
        self.events: list[dict] = []
        self.done = False
        self.lock = threading.Lock()
        self.listeners: set[Callable] = set()

    def emit(self, event: dict | None):
        """An event of the reply, from its thread; None when it has ended"""
        with self.lock:
            if event is None:
                self.done = True
            else:
                self.events.append(event)
            listeners = list(self.listeners)
        for push in listeners:
            push(event)

    async def follow(self, after: int = 0) -> AsyncIterator[dict]:
        """The events from the `after`-th on, then each as it comes, with a ping when it's quiet"""

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def push(event):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                # The page's stream has gone and its loop with it
                pass

        with self.lock:
            backlog, done = self.events[after:], self.done
            if not done:
                self.listeners.add(push)
        try:
            for event in backlog:
                yield event
            if done:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), PING_SECONDS)
                except TimeoutError:
                    yield {"type": "ping"}
                    continue
                if event is None:
                    return
                yield event
        finally:
            with self.lock:
                self.listeners.discard(push)


replies: dict[str, LiveReply] = {}
_lock = threading.Lock()


def start(thread_id: str, user_id: str, start_event: dict) -> LiveReply:
    reply = LiveReply(user_id, start_event)
    with _lock:
        replies[thread_id] = reply
    return reply


def end(thread_id: str, reply: LiveReply):
    """The reply has ended: tell those following it, and forget it (unless a newer one took its place)"""
    reply.emit(None)
    with _lock:
        if replies.get(thread_id) is reply:
            del replies[thread_id]


def of(thread_id: str, user_id: str) -> LiveReply | None:
    """The reply being written in this chat of the user's, if there's one"""
    with _lock:
        reply = replies.get(thread_id)
    return reply if reply is not None and reply.user_id == user_id and not reply.done else None
