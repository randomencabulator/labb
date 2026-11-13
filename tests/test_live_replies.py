"""Web replies keep going when the page goes away, and a page can join one again (server/live.py)."""

import asyncio
import json
import threading
import time

import pytest
from langchain_core.messages import AIMessage

from labb import model
from labb.agent import chat_agent
from labb.server import app as server
from labb.server import live
from conftest import FakeModel


class Waiting(FakeModel):
    """Answers once `go` is set"""

    go: threading.Event

    def _generate(self, *args, **kwargs):
        assert self.go.wait(10)
        return super()._generate(*args, **kwargs)


@pytest.fixture
def waiting(monkeypatch):
    go = threading.Event()
    fake = Waiting(messages=iter([AIMessage("Done.")]), go=go)
    monkeypatch.setattr(model, "model_with_tools", lambda *args: fake)
    monkeypatch.setattr(model, "constrainer", lambda *args: None)
    return go


def lines(response) -> list[dict]:
    return [json.loads(line) for line in response.iter_lines() if line]


def live_thread(before: set[str]) -> str:
    for _ in range(200):
        if new := set(live.replies) - before:
            return new.pop()
        time.sleep(0.02)
    raise AssertionError("no reply started")


def wait_until_done(thread_id: str):
    for _ in range(250):
        if thread_id not in live.replies and thread_id not in server.runs:
            return
        time.sleep(0.02)
    raise AssertionError("the reply didn't end")


def test_a_page_joins_a_reply_again_from_its_start(client, waiting):
    before = set(live.replies)
    sent = []
    first = threading.Thread(target=lambda: sent.append(lines(client.post("/chat", json={"message": "hi"}))))
    first.start()
    thread_id = live_thread(before)
    joined = []
    second = threading.Thread(target=lambda: joined.append(lines(client.get("/chat/live", params={"thread_id": thread_id}))))
    second.start()
    time.sleep(0.2)
    waiting.set()
    first.join(10)
    second.join(10)
    assert joined[0][0] == {"type": "user", "content": "hi", "files": [], "images": [], "turn": 0}
    assert joined[0][-1]["type"] == "done" and sent[0][-1]["type"] == "done"
    # Nothing is kept once it's done: /history has it
    assert client.get("/chat/live", params={"thread_id": thread_id}).status_code == 204


def test_leaving_the_page_doesnt_stop_the_reply(client, waiting):
    before = set(live.replies)
    with client.stream("POST", "/chat", json={"message": "hi"}) as response:
        first = json.loads(next(response.iter_lines()))
    thread_id = first["thread_id"]
    assert live_thread(before) == thread_id
    waiting.set()
    wait_until_done(thread_id)
    messages = chat_agent.get_state({"configurable": {"thread_id": thread_id}}).values["messages"]
    assert messages[-1].text == "Done."


def test_stop_still_stops_it(client, waiting):
    before = set(live.replies)
    done = []
    sender = threading.Thread(target=lambda: done.append(lines(client.post("/chat", json={"message": "hi"}))))
    sender.start()
    thread_id = live_thread(before)
    assert client.post("/stop", json={"thread_id": thread_id}).status_code < 300
    waiting.set()
    sender.join(10)
    assert done[0][-1]["type"] == "stopped"


def test_only_the_owner_can_join(client, accounts, waiting):
    from fastapi.testclient import TestClient

    before = set(live.replies)
    sender = threading.Thread(target=lambda: client.post("/chat", json={"message": "hi"}))
    sender.start()
    thread_id = live_thread(before)
    bob = TestClient(server.app, headers={"Authorization": f"Bearer {accounts['member'][1]}"})
    assert bob.get("/chat/live", params={"thread_id": thread_id}).status_code == 404
    waiting.set()
    sender.join(10)


def test_a_quiet_reply_pings(monkeypatch):
    monkeypatch.setattr(live, "PING_SECONDS", 0.05)
    reply = live.LiveReply("u", {"type": "user"})
    reply.emit({"type": "token", "content": "a"})

    async def read():
        seen = []
        async for event in reply.follow():
            seen.append(event)
            if len(seen) == 3:
                threading.Thread(target=reply.emit, args=(None,)).start()
        return seen

    seen = asyncio.run(asyncio.wait_for(read(), 5))
    assert seen[0] == {"type": "token", "content": "a"} and {"type": "ping"} in seen
