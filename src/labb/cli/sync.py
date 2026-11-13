"""Sends labb's sessions to the server (LABB_SERVER) so its web chat can show them.

After each reply, and after /undo, /rewind and Esc Esc, the whole session is sent again:
it replaces the server's copy, so sending twice is harmless and removed messages go too.
A session is marked unsynced before it's sent and unmarked once the server has it, so a
crash or an unreachable server only delays it: unsynced sessions are sent again when
labb starts, and after the next session that gets through. Sending happens in the
background, so it never slows down the chat. Without LABB_SERVER, nothing is sent.
"""

import queue
import socket
import threading

import httpx
from langchain_core.messages import messages_to_dict

from labb import chats
from labb import remote
from labb.agent import chat_agent

TIMEOUT_SECONDS = 15
# How long labb waits at exit for sessions still being sent
EXIT_WAIT_SECONDS = 5


def payload(thread_id: str) -> dict | None:
    """What the server stores for a session, or None if it's gone (deleted, or never started)"""

    session = next((s for s in chats.list_all_cli() if s["thread_id"] == thread_id), None)
    if session is None:
        return None
    messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values.get("messages", [])
    return {
        "machine": socket.gethostname(),
        "directory": session["directory"],
        "title": session["title"],
        "updated_at": session["updated_at"],
        "messages": messages_to_dict(messages),
        "stats": chats.get_stats(thread_id),
    }


def send(thread_id: str) -> bool:
    """Send one session now; True once the server has it (or there's nothing to send)"""

    url = remote.server()
    if url is None:
        return False
    data = payload(thread_id)
    if data is None:
        chats.mark_synced(thread_id)
        return True
    try:
        response = httpx.post(f"{url}/sync/{thread_id}", json=data, headers=remote.headers(), timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    chats.mark_synced(thread_id)
    return True


class Syncer:
    """Sends sessions in a background thread, one at a time"""

    def __init__(self):
        self.queue: queue.Queue[str] = queue.Queue()
        self.thread = threading.Thread(target=self.work, daemon=True, name="sync")
        self.thread.start()
        # Sessions left unsent last time
        for thread_id in chats.list_unsynced():
            self.queue.put(thread_id)

    def changed(self, thread_id: str):
        """The session changed: send it soon"""

        chats.mark_unsynced(thread_id)
        self.queue.put(thread_id)

    def work(self):
        while True:
            thread_id = self.queue.get()
            # A failure stays marked unsynced, for later; nothing stops this thread
            try:
                sent = send(thread_id)
            except Exception:
                sent = False
            if sent:
                # The server is reachable: send what's still waiting from before
                for waiting in chats.list_unsynced():
                    if waiting != thread_id:
                        self.queue.put(waiting)
            self.queue.task_done()

    def unsent(self) -> int:
        return len(chats.list_unsynced())

    def finish(self):
        """At exit: give the sessions still in the queue a few seconds to go out"""

        done = threading.Event()
        threading.Thread(target=lambda: (self.queue.join(), done.set()), daemon=True).start()
        done.wait(EXIT_WAIT_SECONDS)


def start() -> Syncer | None:
    """The syncer, if there's a server to sync to"""
    return Syncer() if remote.server() else None
